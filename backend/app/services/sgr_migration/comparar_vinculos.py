"""Compara os vínculos placa ↔ rastreador do SGR com os do MasterSat.

Somente leitura: recebe as duas listas já coletadas e devolve as
divergências. A coleta (SGR por /buscar_rastreador, MasterSat por SELECT em
transação READ ONLY) fica em scripts/sgr_comparar_vinculos.py.

Para cada placa, três visões:
  - SGR: IMEIs que o SGR dá como instalados na placa;
  - MasterSat instalado: rastreadores com vehicle_id na placa;
  - MasterSat contrato: rastreadores dos contratos ATIVOS da placa (é o que
    o fechamento cobra).
Divergência = as três não contam a mesma história.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.services.sgr_migration.normalize import normalize_plate

# Situações do SGR que significam equipamento no veículo (mesmo mapa da
# importação: mapping._TRACKER_STATUS_MAP → INSTALLED).
SITUACOES_INSTALADO = {'ATIVO', 'VINCULADO'}

TIPOS = {
    'imei_diferente': 'Placa nos dois sistemas com rastreador diferente',
    'so_no_sgr': 'SGR tem rastreador na placa; MasterSat não tem nenhum instalado',
    'so_no_mastersat': 'MasterSat tem rastreador na placa; SGR não tem',
    'contrato_divergente': 'Contrato ativo aponta para rastreador que não está instalado na placa',
    'placa_fora_do_mastersat': 'Placa com rastreador no SGR não existe (ativa) no MasterSat',
}


def normalizar_imei(valor) -> str:
    return re.sub(r'\D', '', str(valor or ''))


@dataclass
class Divergencia:
    placa: str
    tipos: list[str]
    cliente: str | None
    imeis_sgr: list[str] = field(default_factory=list)
    imeis_instalados: list[str] = field(default_factory=list)
    imeis_contratos: list[str] = field(default_factory=list)
    contratos: list[int] = field(default_factory=list)
    # IMEI do SGR desta placa que no MasterSat está instalado em outra placa.
    imei_sgr_em_outra_placa: dict[str, str] = field(default_factory=dict)

    def linha(self) -> dict:
        return {
            'placa': self.placa,
            'cliente_mastersat': self.cliente or '',
            'divergencias': ' | '.join(TIPOS[t] for t in self.tipos),
            'imei_sgr': ', '.join(self.imeis_sgr),
            'imei_instalado_mastersat': ', '.join(self.imeis_instalados),
            'imei_nos_contratos_ativos': ', '.join(self.imeis_contratos),
            'contratos_ativos': ', '.join(f'#{c}' for c in self.contratos),
            'imei_sgr_esta_no_mastersat_em': ', '.join(
                f'{imei}→{placa}' for imei, placa in sorted(self.imei_sgr_em_outra_placa.items())
            ),
        }


def comparar(
    sgr: list[dict],
    veiculos: list[dict],
    instalados: list[dict],
    contratos: list[dict],
) -> list[Divergencia]:
    """
    sgr:        [{'placa', 'imei', 'situacao'}] de /buscar_rastreador
    veiculos:   [{'placa', 'cliente'}] veículos ativos do MasterSat
    instalados: [{'placa', 'imei'}] rastreadores com veículo no MasterSat
    contratos:  [{'placa', 'contrato_id', 'imei' (ou None)}] contratos ativos
    """
    sgr_por_placa: dict[str, set[str]] = defaultdict(set)
    for r in sgr:
        placa = normalize_plate(r.get('placa'))
        imei = normalizar_imei(r.get('imei'))
        situacao = str(r.get('situacao') or '').strip().upper()
        if placa and imei and situacao in SITUACOES_INSTALADO:
            sgr_por_placa[placa].add(imei)

    cliente_por_placa = {normalize_plate(v['placa']): v.get('cliente') for v in veiculos if v.get('placa')}
    inst_por_placa: dict[str, set[str]] = defaultdict(set)
    placa_do_imei: dict[str, str] = {}
    for t in instalados:
        placa, imei = normalize_plate(t.get('placa')), normalizar_imei(t.get('imei'))
        if placa and imei:
            inst_por_placa[placa].add(imei)
            placa_do_imei[imei] = placa
    ctr_imeis: dict[str, set[str]] = defaultdict(set)
    ctr_ids: dict[str, list[int]] = defaultdict(list)
    for c in contratos:
        placa = normalize_plate(c.get('placa'))
        if not placa:
            continue
        ctr_ids[placa].append(c['contrato_id'])
        imei = normalizar_imei(c.get('imei'))
        if imei:
            ctr_imeis[placa].add(imei)

    divergencias: list[Divergencia] = []
    for placa in sorted(set(sgr_por_placa) | set(inst_por_placa) | set(ctr_imeis)):
        s, i, k = sgr_por_placa.get(placa, set()), inst_por_placa.get(placa, set()), ctr_imeis.get(placa, set())
        tipos: list[str] = []
        if placa not in cliente_por_placa and s:
            tipos.append('placa_fora_do_mastersat')
        elif s and i and s != i:
            tipos.append('imei_diferente')
        elif s and not i:
            tipos.append('so_no_sgr')
        elif i and not s:
            tipos.append('so_no_mastersat')
        if k - i:
            tipos.append('contrato_divergente')
        if not tipos:
            continue
        divergencias.append(Divergencia(
            placa=placa, tipos=tipos, cliente=cliente_por_placa.get(placa),
            imeis_sgr=sorted(s), imeis_instalados=sorted(i), imeis_contratos=sorted(k),
            contratos=sorted(ctr_ids.get(placa, [])),
            imei_sgr_em_outra_placa={
                imei: placa_do_imei[imei] for imei in s
                if imei in placa_do_imei and placa_do_imei[imei] != placa
            },
        ))
    return divergencias


def resumo(divergencias: list[Divergencia]) -> dict[str, int]:
    contagem = {tipo: 0 for tipo in TIPOS}
    for d in divergencias:
        for t in d.tipos:
            contagem[t] += 1
    return contagem
