"""
Backfill da Fase 04: proveniência das cobranças importadas do SGR ANTES da
identidade de origem existir — e relatório do que a importação antiga deixou
errado. Nada é inventado: tudo sai do ``billings.sgr_payload`` preservado.

O que faz (simulação por padrão — só lê e dá rollback):
  1. agrupa as cobranças com ``sgr_payload`` por documento (``cod_boleto``);
  2. reconstrói as linhas do documento a partir da discriminação guardada e
     tenta casar cada obrigação com exatamente uma cobrança (título,
     competência, valor). Casou tudo → documento ``conciliado`` com linhas;
     senão → documento ``bloqueado`` com o motivo (ambiguidade, falta, desconto
     descartado). Cliente/veículo/rastreador NÃO ganham identidade aqui: o
     payload não traz o código de origem deles — a próxima rodada da
     importação faz isso por chave natural, com dono conferido;
  3. lista vínculos cruzados já existentes (contrato/cobrança/rastreador cujo
     cliente difere do dono do veículo) — só ids, para revisão humana;
  4. com ``--compensar-descontos`` (exige --aplicar e --operador): leva ao
     valor LÍQUIDO cada cobrança aberta/paga de documento cujo desconto a
     importação antiga descartou (SGR-01), com ``billing_change_logs``
     (antes/depois/justificativa). Operação compensatória revisável: nada é
     apagado e o valor anterior fica no histórico. Documento com cobrança
     cancelada, com título bancário, bonificada (líquido zero) ou com valor
     pago próprio fica de fora e listado.

Uso (a partir de backend/):
  python scripts/sgr_backfill.py                                   # relatório
  python scripts/sgr_backfill.py --aplicar                         # grava proveniência
  python scripts/sgr_backfill.py --aplicar --compensar-descontos --operador "Fulano"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, _BACKEND_DIR)
os.chdir(_BACKEND_DIR)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass
os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres:postgres@localhost:5432/rastreamento')

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import registry_all  # noqa: E402,F401
from app.models.billing import Billing  # noqa: E402
from app.models.billing_change_log import BillingChangeLog  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.contract import Contract  # noqa: E402
from app.models.enums import BillingStatus  # noqa: E402
from app.models.sgr_migracao import SgrDocumento, SgrDocumentoLinha  # noqa: E402
from app.models.tracker import Tracker  # noqa: E402
from app.models.vehicle import Vehicle  # noqa: E402
from app.services import titulo_bancario  # noqa: E402
from app.services.sgr_migration.importer import (  # noqa: E402
    ImportStats,
    _centavos_do_banco,
    _Contexto,
    _hash,
    _reais,
    _snapshot_documento,
    _total_do_documento,
    _trunc,
    adotar_legado,
    conciliar,
    linhas_do_documento,
)
from app.services.sgr_migration.poc import achatar_boletos  # noqa: E402

_HOSTS_LOCAIS = {'localhost', '127.0.0.1', '::1', 'db'}


def _linhas_do_payload(payload: dict) -> list[dict]:
    """O payload guardado é o boleto como o SGR devolveu (só `situacao` foi
    achatada em texto): passa pelo MESMO mapeamento da importação."""
    boleto = {**payload, 'situacao': {'descricao': payload.get('situacao')}}
    return achatar_boletos([boleto], [])


def _grupos(db: Session) -> dict[tuple[int, str], list[Billing]]:
    ligadas = set(db.scalars(select(SgrDocumentoLinha.billing_id).where(SgrDocumentoLinha.billing_id.is_not(None))))
    grupos: dict[tuple[int, str], list[Billing]] = defaultdict(list)
    consulta = select(Billing).where(Billing.is_deleted.is_(False), Billing.sgr_payload.is_not(None)).order_by(Billing.id)
    for billing in db.scalars(consulta).yield_per(500):
        payload = billing.sgr_payload
        if not isinstance(payload, dict) or billing.id in ligadas:
            continue
        cod = payload.get('cod_boleto')
        if cod in (None, ''):
            continue
        grupos[(billing.client_id, str(cod))].append(billing)
    return grupos


def _compensar(db: Session, cod: str, linhas, total, legados: list[Billing], operador: str) -> tuple[bool, str]:
    """Leva as cobranças do documento ao líquido. (ok, motivo_se_não)."""
    plano, motivo, _ = conciliar(linhas, total)
    if motivo:
        return False, motivo
    livres = list(legados)
    alteracoes: list[tuple[Billing, int, int]] = []
    for linha in linhas:
        if (linha.centavos or 0) <= 0:
            continue
        titulo = _trunc(linha.mapped.get('title'), 160)
        achou = [b for b in livres if b.title == titulo
                 and (b.period_label or None) == _trunc(linha.mapped.get('period_label'), 20)
                 and _centavos_do_banco(b.amount) == linha.centavos]
        if not achou:
            return False, 'cobranca_nao_encontrada'
        billing = achou[0]
        livres.remove(billing)
        liquido = plano.liquido[linha.chave]
        if liquido == linha.centavos:
            continue
        if liquido == 0:
            return False, 'bonificacao_integral_requer_decisao'
        if billing.status == BillingStatus.CANCELED:
            return False, 'cobranca_cancelada'
        if billing.paid_amount is not None and _centavos_do_banco(billing.paid_amount) != linha.centavos:
            return False, 'valor_pago_proprio'
        alteracoes.append((billing, linha.centavos, liquido))
    try:
        titulo_bancario.exigir(db, titulo_bancario.ALTERAR_VALOR, [b.id for b, _, _ in alteracoes])
    except titulo_bancario.PoliticaBancariaError as exc:
        return False, f'titulo_bancario:{exc.code}'
    for billing, bruto, liquido in alteracoes:
        justificativa = (f'Fase 04 SGR-01 ({operador}): desconto de R$ {(bruto - liquido) / 100:.2f} do boleto '
                         f'SGR {cod} descartado pela importação antiga; valor levado ao líquido do documento')
        db.add(BillingChangeLog(billing_id=billing.id, field_name='amount', previous_value=str(_reais(bruto)),
                                new_value=str(_reais(liquido)), justification=justificativa))
        billing.amount = _reais(liquido)
        if billing.paid_amount is not None:
            db.add(BillingChangeLog(billing_id=billing.id, field_name='paid_amount',
                                    previous_value=str(billing.paid_amount), new_value=str(_reais(liquido)),
                                    justification=justificativa))
            billing.paid_amount = _reais(liquido)
        marca = f'[{justificativa}]'
        billing.notes = f'{billing.notes}\n{marca}' if billing.notes else marca
    db.flush()
    return True, ''


def vinculos_cruzados(db: Session) -> dict:
    """Só leitura: associações cujo cliente difere do dono atual do veículo."""
    contratos = db.execute(
        select(Contract.id).join(Vehicle, Vehicle.id == Contract.vehicle_id).where(
            Contract.is_deleted.is_(False), Contract.status == 'ativo', Contract.client_id != Vehicle.client_id,
        )
    ).scalars().all()
    cobrancas = db.execute(
        select(Billing.id).join(Vehicle, Vehicle.id == Billing.vehicle_id).where(
            Billing.is_deleted.is_(False), Billing.status != BillingStatus.CANCELED,
            Billing.client_id != Vehicle.client_id,
            (Billing.payer_client_id.is_(None)) | (Billing.payer_client_id != Vehicle.client_id),
        )
    ).scalars().all()
    rastreadores = db.execute(
        select(Tracker.id).join(Vehicle, Vehicle.id == Tracker.vehicle_id).where(
            Tracker.is_deleted.is_(False), Tracker.client_id != Vehicle.client_id,
        )
    ).scalars().all()
    return {
        'contratos_ativos': {'total': len(contratos), 'ids': contratos[:200]},
        'cobrancas_em_aberto_ou_pagas': {'total': len(cobrancas), 'ids': cobrancas[:200]},
        'rastreadores': {'total': len(rastreadores), 'ids': rastreadores[:200]},
    }


def executar(db: Session, *, aplicar: bool, compensar: bool, operador: str | None) -> dict:
    ctx = _Contexto(db=db, dry_run=not aplicar, execucao=None)
    stats = ImportStats()
    resultado: dict = {'documentos': 0, 'conciliados': 0, 'bloqueados': defaultdict(int), 'compensados': 0,
                       'descontos_descartados_centavos': 0, 'bloqueios': [], 'compensacao_recusada': []}
    for (client_id, cod), legados in sorted(_grupos(db).items(), key=lambda item: item[0][1]):
        resultado['documentos'] += 1
        cliente = db.get(Client, client_id)
        existente = db.scalar(select(SgrDocumento).where(SgrDocumento.cod_boleto == cod))
        if existente is not None and existente.client_id not in (None, client_id):
            resultado['bloqueados']['documento_em_dois_clientes'] += 1
            resultado['bloqueios'].append({'cod_boleto': cod, 'client_id': client_id,
                                           'motivo': 'documento_em_dois_clientes',
                                           'billing_ids': sorted(b.id for b in legados)})
            continue
        entradas = _linhas_do_payload(legados[0].sgr_payload)
        if not entradas:
            resultado['bloqueados']['payload_sem_linhas'] += 1
            resultado['bloqueios'].append({'cod_boleto': cod, 'client_id': client_id, 'motivo': 'payload_sem_linhas'})
            continue
        linhas = linhas_do_documento(cod, entradas)
        total = _total_do_documento(entradas[0])
        snapshot = _snapshot_documento(entradas[0], linhas, total)
        descontos = -sum(linha.centavos for linha in linhas if (linha.centavos or 0) < 0)
        compensado = False
        if descontos and compensar:
            compensado, motivo = _compensar(db, cod, linhas, total, legados, operador or '')
            if compensado:
                resultado['compensados'] += 1
            else:
                resultado['compensacao_recusada'].append({'cod_boleto': cod, 'client_id': client_id, 'motivo': motivo})
        motivo = adotar_legado(
            ctx, stats, cliente, cod, entradas[0], linhas, total, snapshot, _hash(snapshot), legados,
            criado_por='backfill', conferir_status=False, descontos_compensados=compensado,
        )
        if motivo:
            resultado['bloqueados'][motivo] += 1
            if motivo == 'legado_com_desconto_descartado':
                resultado['descontos_descartados_centavos'] += descontos
            resultado['bloqueios'].append({'cod_boleto': cod, 'client_id': client_id, 'motivo': motivo,
                                           'billing_ids': sorted(b.id for b in legados)})
        else:
            resultado['conciliados'] += 1
    resultado['bloqueados'] = dict(resultado['bloqueados'])
    resultado['vinculos_cruzados'] = vinculos_cruzados(db)
    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--aplicar', action='store_true', help='Grava (sem isto é só relatório)')
    parser.add_argument('--compensar-descontos', action='store_true')
    parser.add_argument('--operador', type=str, default=None)
    parser.add_argument('--relatorio', type=str, default=None, metavar='ARQUIVO')
    parser.add_argument('--permitir-banco-remoto', action='store_true')
    args = parser.parse_args()

    partes = urlsplit(settings.database_url)
    host = (partes.hostname or '').lower()
    print(f'Banco: {host}:{partes.port or 5432}{partes.path}  ({"LOCAL" if host in _HOSTS_LOCAIS else "REMOTO"})')
    if args.aplicar and host not in _HOSTS_LOCAIS and not args.permitir_banco_remoto:
        print('RECUSADO: --aplicar em banco NÃO-LOCAL sem --permitir-banco-remoto.')
        return 1
    if args.compensar_descontos and (not args.aplicar or not (args.operador or '').strip()):
        print('RECUSADO: --compensar-descontos exige --aplicar e --operador (fica no histórico de cada cobrança).')
        return 1

    with SessionLocal() as db:
        resultado = executar(db, aplicar=args.aplicar, compensar=args.compensar_descontos, operador=args.operador)
        if args.aplicar:
            db.commit()
        else:
            db.rollback()

    destino = Path(args.relatorio) if args.relatorio else (
        Path(_BACKEND_DIR) / 'scripts' / 'sgr_saida' / f'backfill-{datetime.now():%Y%m%d-%H%M%S}.json')
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(f"Documentos com cobrança importada sem proveniência: {resultado['documentos']}")
    print(f"  conciliados: {resultado['conciliados']} | bloqueados: {sum(resultado['bloqueados'].values())} "
          f"{resultado['bloqueados'] or ''}")
    print(f"  descontos descartados pela importação antiga: R$ {resultado['descontos_descartados_centavos'] / 100:,.2f}")
    if args.compensar_descontos:
        print(f"  compensados: {resultado['compensados']} | recusados: {len(resultado['compensacao_recusada'])}")
    cruzados = resultado['vinculos_cruzados']
    print('Vínculos cruzados existentes (revisão humana; nada é alterado): '
          + ', '.join(f"{k}: {v['total']}" for k, v in cruzados.items()))
    print(f'Relatório (só ids e códigos): {destino}')
    if not args.aplicar:
        print('Simulação: nada foi gravado.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
