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


# ---------------------------------------------------------------------------
# IntegrityError — UNIQUE(key)
# ---------------------------------------------------------------------------
#
# put_mensagens faz um upsert manual (SELECT pela chave; INSERT só se não
# achar). Isso não é atômico: duas gravações concorrentes da mesma chave
# podem ambas cair no INSERT e disputar o UNIQUE de `system_settings.key`.

def _bypass_precheck_for_system_setting(monkeypatch):
    from sqlalchemy.orm import Query as SAQuery

    from app.models.system_setting import SystemSetting

    original_first = SAQuery.first

    def fake_first(self):
        descriptions = self.column_descriptions
        if descriptions and descriptions[0].get('type') is SystemSetting:
            return None
        return original_first(self)

    monkeypatch.setattr(SAQuery, 'first', fake_first)


def test_concurrent_save_same_key_returns_409_not_500(http, db, monkeypatch):
    from app.models.system_setting import SystemSetting

    db.add(SystemSetting(key='msg_boleto', value='já salvo por outra sessão'))
    db.commit()
    _bypass_precheck_for_system_setting(monkeypatch)

    r = http.put(PREFIX, json={'msg_boleto': 'tentativa concorrente'})

    assert r.status_code == 409
    assert 'unique' not in r.json()['detail'].lower()


def test_session_stays_usable_after_conflict(http, db, monkeypatch):
    from app.models.system_setting import SystemSetting

    db.add(SystemSetting(key='msg_boleto', value='já salvo por outra sessão'))
    db.commit()
    _bypass_precheck_for_system_setting(monkeypatch)

    r = http.put(PREFIX, json={'msg_boleto': 'tentativa concorrente'})
    assert r.status_code == 409

    assert db.query(SystemSetting).filter(SystemSetting.key == 'msg_boleto').count() == 1
    r2 = http.get(PREFIX)
    assert r2.status_code == 200
