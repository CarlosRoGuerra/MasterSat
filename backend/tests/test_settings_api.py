"""Testes das configurações de mensagens (/settings/mensagens)."""
from __future__ import annotations

PREFIX = '/api/v1/settings/mensagens'


def test_retorna_padrao_quando_nada_salvo(http):
    r = http.get(PREFIX)
    assert r.status_code == 200
    body = r.json()
    assert '{NOME}' in body['msg_boleto']
    assert '{VENCIMENTO}' in body['msg_boleto_assunto']


def test_salva_e_devolve_template_customizado(http):
    novo = 'Oi {NOME}! Boleto de {VALOR} vence em {VENCIMENTO}. Código: {CODIGO_BARRAS}'
    r = http.put(PREFIX, json={'msg_boleto': novo})
    assert r.status_code == 200
    assert r.json()['msg_boleto'] == novo
    # persiste no GET e mantém o assunto padrão
    body = http.get(PREFIX).json()
    assert body['msg_boleto'] == novo
    assert '{VENCIMENTO}' in body['msg_boleto_assunto']

    # segunda gravação sobrescreve (update, não duplica)
    r2 = http.put(PREFIX, json={'msg_boleto': 'v2 {NOME}'})
    assert r2.json()['msg_boleto'] == 'v2 {NOME}'


def test_chave_desconhecida_ignorada(http):
    r = http.put(PREFIX, json={'msg_boleto_assunto': 'Assunto novo'})
    assert r.status_code == 200
    assert r.json()['msg_boleto_assunto'] == 'Assunto novo'


def test_operacional_nao_edita(http_op):
    assert http_op.put(PREFIX, json={'msg_boleto': 'x'}).status_code == 403
