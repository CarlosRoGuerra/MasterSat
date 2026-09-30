"""Saneamento de `cobranca_parcela_invalida` vinda do SGR (preflight da Fase 02).

O SGR exporta boleto de fechamento com parcela "0 de 1", "2 de 1", "0 de 0"
— não é parcelamento. O importador antigo copiava esses números para
installment_number/installment_total, e a migration e5c2a9d71f04 recusa
subir (ck_billings_parcela_no_intervalo) até isso ser corrigido.

O que faz, só em cobrança importada do SGR (sgr_payload preenchido) com
parcela fora de 1..total: installment_number/installment_total = NULL e uma
linha em billing_change_logs por cobrança. Valor, status, vencimento e
competência não mudam; o texto original continua em sgr_payload['parcela'].
Cobrança não-SGR com parcela inválida é só listada (revisão manual).

    python scripts/saneamento_parcela_sgr.py              # simulação (não altera)
    python scripts/saneamento_parcela_sgr.py --aplicar    # aplica, numa transação

Rode antes num snapshot. Guia: docs/financeiro/saneamento-fase-02.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parent.parent

_INVALIDA = """
    installment_number IS NOT NULL AND (
        installment_number < 1
        OR installment_total IS NULL OR installment_total < 1
        OR installment_number > installment_total
    )
"""

JUSTIFICATIVA = (
    'Saneamento Fase 02: parcela importada do SGR fora do intervalo (boleto de '
    'fechamento, não é parcelamento). Original preservado em sgr_payload.parcela.'
)


def resumo(conn) -> dict:
    grupos = conn.execute(text(f"""
        SELECT (sgr_payload IS NOT NULL) AS sgr, billing_type, installment_number, installment_total, count(*)
        FROM billings WHERE {_INVALIDA}
        GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC
    """)).all()
    nao_sgr = conn.execute(text(
        f'SELECT id FROM billings WHERE sgr_payload IS NULL AND {_INVALIDA} ORDER BY id'
    )).scalars().all()
    return {
        'sgr': sum(g[4] for g in grupos if g[0]),
        'nao_sgr': list(nao_sgr),
        'grupos': [tuple(g) for g in grupos],
    }


def aplicar(conn) -> int:
    conn.execute(text(f"""
        INSERT INTO billing_change_logs
            (billing_id, changed_by_user_id, field_name, previous_value, new_value, justification)
        SELECT id, NULL, 'parcela',
               installment_number::text || ' de ' || COALESCE(installment_total::text, '—'),
               NULL, :just
        FROM billings WHERE sgr_payload IS NOT NULL AND {_INVALIDA}
    """), {'just': JUSTIFICATIVA})
    return conn.execute(text(f"""
        UPDATE billings SET installment_number = NULL, installment_total = NULL
        WHERE sgr_payload IS NOT NULL AND {_INVALIDA}
    """)).rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--aplicar', action='store_true', help='grava (sem isto só simula)')
    args = parser.parse_args()
    sys.path.insert(0, str(BACKEND))
    from app.core.config import settings

    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as conn:
            r = resumo(conn)
            print(f'Cobranças do SGR com parcela inválida: {r["sgr"]}')
            for sgr, tipo, numero, total, qtd in r['grupos']:
                print(f'  {"SGR    " if sgr else "NÃO-SGR"} {tipo:<20} {numero} de {total}: {qtd}')
            if r['nao_sgr']:
                print(f'Não-SGR (revisar à mão, NÃO alteradas): {r["nao_sgr"][:50]}')
            if not args.aplicar:
                print('Simulação: nada foi alterado. Use --aplicar para gravar.')
                return 0
            alteradas = aplicar(conn)
            print(f'Aplicado: {alteradas} cobrança(s) com parcela = NULL e histórico gravado.')
    finally:
        engine.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
