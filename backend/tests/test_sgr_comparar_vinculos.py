"""Comparação de vínculos SGR × MasterSat (somente leitura).

Cenários montados com os casos reais de 08/10/2026: contrato importado
apontando para rastreador em estoque (TPV6I35) ou instalado em outra placa
(TPV5I85 × GGG0585).
"""
from __future__ import annotations

from app.services.sgr_migration.comparar_vinculos import comparar, normalizar_imei, resumo
from tests.test_database_concurrency_postgres import postgres_api  # noqa: F401 — fixture


def _por_placa(divergencias):
    return {d.placa: d for d in divergencias}


def test_placas_iguais_nao_aparecem():
    r = comparar(
        sgr=[{'placa': 'ABC-1234', 'imei': '111', 'situacao': 'ATIVO'}],
        veiculos=[{'placa': 'ABC1234', 'cliente': 'X'}],
        instalados=[{'placa': 'ABC1234', 'imei': '111'}],
        contratos=[{'placa': 'ABC1234', 'contrato_id': 1, 'imei': '111'}],
    )
    assert r == []


def test_imei_em_outra_placa_e_contrato_divergente():
    r = _por_placa(comparar(
        sgr=[
            {'placa': 'TPV5I85', 'imei': '864421065717244', 'situacao': 'ATIVO'},
            {'placa': 'GGG0585', 'imei': '999', 'situacao': 'ATIVO'},
        ],
        veiculos=[{'placa': 'TPV5I85', 'cliente': 'SEGURIDADE'}, {'placa': 'GGG0585', 'cliente': 'SEGURIDADE'}],
        instalados=[{'placa': 'GGG0585', 'imei': '864421065717244'}],
        contratos=[
            {'placa': 'TPV5I85', 'contrato_id': 224, 'imei': '864421065717244'},
            {'placa': 'GGG0585', 'contrato_id': 15, 'imei': '864421065717244'},
        ],
    ))
    tpv = r['TPV5I85']
    assert tpv.tipos == ['so_no_sgr', 'contrato_divergente']
    assert tpv.imei_sgr_em_outra_placa == {'864421065717244': 'GGG0585'}
    assert tpv.linha()['contratos_ativos'] == '#224'
    assert r['GGG0585'].tipos == ['imei_diferente']


def test_estoque_com_contrato_e_so_no_mastersat():
    r = _por_placa(comparar(
        sgr=[{'placa': 'TPV6I35', 'imei': '864421065724315', 'situacao': 'ATIVO'}],
        veiculos=[{'placa': 'TPV6I35', 'cliente': 'SEGURIDADE'}, {'placa': 'QIV3234', 'cliente': 'ERICSON'}],
        instalados=[{'placa': 'QIV3234', 'imei': '869671075762888'}],
        contratos=[{'placa': 'TPV6I35', 'contrato_id': 222, 'imei': '864421065724315'}],
    ))
    assert r['TPV6I35'].tipos == ['so_no_sgr', 'contrato_divergente']
    assert r['QIV3234'].tipos == ['so_no_mastersat']


def test_situacao_de_estoque_no_sgr_nao_conta_como_instalado():
    r = comparar(
        sgr=[{'placa': 'ABC1234', 'imei': '111', 'situacao': 'ESTOQUE'}],
        veiculos=[{'placa': 'ABC1234', 'cliente': 'X'}],
        instalados=[{'placa': 'ABC1234', 'imei': '111'}],
        contratos=[],
    )
    assert [d.tipos for d in r] == [['so_no_mastersat']]


def test_placa_do_sgr_inexistente_no_mastersat_e_resumo():
    r = comparar(
        sgr=[{'placa': 'ZZZ9999', 'imei': '555', 'situacao': 'VINCULADO'}],
        veiculos=[], instalados=[], contratos=[],
    )
    assert r[0].tipos == ['placa_fora_do_mastersat']
    assert resumo(r)['placa_fora_do_mastersat'] == 1


def test_imei_normalizado():
    assert normalizar_imei(' 8644-2106 5717244 ') == '864421065717244'
    assert normalizar_imei(None) == ''


def test_leitura_do_mastersat_e_somente_leitura_no_postgres(postgres_api):
    """No banco real a leitura funciona e a mesma conexão recusa escrita."""
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from scripts.sgr_comparar_vinculos import conexao_somente_leitura, ler_mastersat

    _http, sessions, engine = postgres_api
    with sessions() as s:
        s.execute(text("INSERT INTO clients (name, cpf_cnpj, type, status, is_deleted) "
                       "VALUES ('Cli', '52998224725', 'pf', 'ACTIVE', false)"))
        s.commit()
    veiculos, instalados, contratos = ler_mastersat(engine=engine)
    assert (veiculos, instalados, contratos) == ([], [], [])

    with conexao_somente_leitura(engine) as conn:
        with pytest.raises(DBAPIError, match='read-only'):
            conn.execute(text("UPDATE clients SET name = 'alterado'"))
    with sessions() as s:
        assert s.execute(text('SELECT name FROM clients')).scalar() == 'Cli'
