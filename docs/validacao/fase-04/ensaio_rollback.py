"""Ensaio de rollback da Fase 04 em PostgreSQL descartável.

Fases (cada uma num container com o backend da versão indicada em /app):
  novo-importa   código NOVO: alembic upgrade head + importação sintética
                 (desconto, pagamento entre rodadas, transferência pendente)
  novo-downgrade código NOVO: alembic downgrade b7d3e1f5a902
  antigo-opera   código ANTIGO (731461e): lê as cobranças como a API lista e
                 simula o importador antigo sobre a mesma origem
  novo-volta     código NOVO: alembic upgrade head + mesma importação

    DATABASE_URL=postgresql+psycopg://... python ensaio_rollback.py <fase>

Só para banco descartável. Nenhuma rede além do PostgreSQL.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, '/app')
os.environ.setdefault('MULTIPORTAL_ENABLED', 'false')
os.environ.setdefault('SECRET_KEY', 'ensaio-fase04-sem-segredo-real-0123456789abcdef')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import registry_all  # noqa: E402,F401
from app.models.enums import ClientStatus, TrackerStatus, VehicleStatus  # noqa: E402
from app.services.sgr_migration import importer  # noqa: E402
from app.services.sgr_migration.poc import ClientNode, PocRunResult, TrackerNode, VehicleNode, achatar_boletos  # noqa: E402


def boleto(cod, linhas, valor, **kw):
    disc = [{'valor': v, 'placa': p, 'mes_referente': '10/2026', 'produto': pr} for v, p, pr in linhas]
    return {'cod_boleto': str(cod), 'cod_cliente': kw.get('cliente', '1'), 'nosso_numero': str(cod), 'valor': valor,
            'valor_pagamento': kw.get('pago', '0,00'), 'data_vencimento': '15/10/2026',
            'data_pagamento': kw.get('data_pag'), 'situacao': {'descricao': kw.get('situacao', 'ABERTO')},
            'parcela': '1 de 1', 'mes_referente': '10/2026', 'discriminacao': disc}


def no(cod, cpf, placa, boletos):
    t = TrackerNode(raw={}, issues=[], mapped={'imei': f'35548802090{cod:0>4}', 'external_id': f'r{cod}',
                                               'status': TrackerStatus.INSTALLED.value},
                    contract={'external_id': f'v{cod}', 'plan_external_id': '17', 'start_date': '2024-01-01',
                              'billing_day': 15, 'status': 'ativo', 'billing_modality': 'boleto'})
    v = VehicleNode(raw={}, issues=[], trackers=[t], mapped={'external_id': f'{cod}0', 'plate': placa,
                                                             'status': VehicleStatus.ACTIVE.value})
    return ClientNode(raw={'cod_cliente': cod}, issues=[], vehicles=[v], billings=achatar_boletos(boletos, []),
                      mapped={'external_id': cod, 'name': f'CLIENTE {cod}', 'cpf_cnpj': cpf, 'type': 'pf',
                              'status': ClientStatus.ACTIVE.value})


def origem(pago: bool):
    desconto = boleto(9001, [('100,00', 'ABC1234', 'MENSALIDADE'), ('-20,00', 'ABC1234', 'DESCONTO')], '80,00')
    mensal = boleto(9002, [('50,00', 'ABC1234', 'SERVICO')], '50,00',
                    **({'situacao': 'BAIXADO', 'pago': '50,00', 'data_pag': '10/10/2026'} if pago else {}))
    outro = boleto(9003, [('70,00', 'ABC1234', 'MENSALIDADE')], '70,00', cliente='2')
    return PocRunResult(limit=10, request_count=0, request_log=[], plans=[
        {'external_id': '17', 'name': 'MENSALIDADE 100', 'price': 100.0, 'billing_interval_months': 1}],
        clients=[no('1', '11144477735', 'ABC1234', [desconto, mensal]),
                 no('2', '52998224725', 'ABC1234', [outro])])


def cobrancas() -> list:
    with engine.connect() as conn:
        return [list(r) for r in conn.execute(text(
            "SELECT id, receipt_number, amount::text, status FROM billings WHERE NOT is_deleted ORDER BY id"))]


def alembic(*args) -> str:
    r = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd='/app', capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    return (r.stdout.strip() or r.stderr.strip()).splitlines()[-1]


def tabelas() -> list[str]:
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND "
            "(tablename LIKE 'sgr_%' OR tablename LIKE 'fase04_%') ORDER BY 1"))]


def stats_resumo(stats) -> dict:
    campos = ('billings_created', 'billings_reused', 'billings_updated', 'documentos_inalterados',
              'documentos_bloqueados', 'conflitos', 'status_execucao')
    return {c: getattr(stats, c, None) for c in campos}


def main(fase: str) -> dict:
    saida: dict = {'fase': fase}
    if fase == 'novo-importa':
        saida['alembic'] = alembic('upgrade', 'head')
        with SessionLocal() as db:
            saida['rodada_1'] = stats_resumo(importer.import_poc_result(db, origem(False), dry_run=False))
        with SessionLocal() as db:
            saida['rodada_2'] = stats_resumo(importer.import_poc_result(db, origem(True), dry_run=False))
    elif fase == 'novo-downgrade':
        saida['alembic'] = alembic('downgrade', 'b7d3e1f5a902')
    elif fase == 'antigo-opera':
        saida['alembic_current'] = alembic('current')
        from app.api.v1.endpoints.billings import base_query, serialize_billing
        with SessionLocal() as db:
            saida['lista_api'] = len([serialize_billing(b) for b in base_query(db).all()])
            stats = importer.import_poc_result(db, origem(True), dry_run=True)
            saida['importador_antigo_simulado'] = {'billings_created': stats.billings_created,
                                                   'billings_reused': stats.billings_reused}
    elif fase == 'novo-volta':
        saida['alembic'] = alembic('upgrade', 'head')
        with SessionLocal() as db:
            saida['rodada_3'] = stats_resumo(importer.import_poc_result(db, origem(True), dry_run=False))
    saida['tabelas_fase04'] = tabelas()
    saida['cobrancas'] = cobrancas()
    return saida


if __name__ == '__main__':
    print(json.dumps(main(sys.argv[1]), ensure_ascii=False, default=str))
