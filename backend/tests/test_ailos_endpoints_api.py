"""
Testes E2E (TestClient) dos endpoints /api/v1/ailos/*.

Cobre: 404 de billing inexistente, 403/401 por papel, AilosError (cooperado/
client token não configurado) → 400, AilosValidationError → 422, e que
GET /status nunca expõe tokens.
"""
from __future__ import annotations

from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto import encrypt_token
from app.models.ailos_integration import AilosIntegration

PREFIX = '/api/v1/ailos'

VALID_KEY = Fernet.generate_key().decode()


def _preencher_endereco(cliente, db):
    cliente.zip_code = '28970-000'
    cliente.address_line = 'Rua Principal'
    cliente.address_number = '100'
    cliente.address_complement = 'Casa 2'
    cliente.neighborhood = 'Centro'
    cliente.city = 'Araruama'
    cliente.state = 'RJ'
    db.commit()
    db.refresh(cliente)


class TestNotFound:
    def test_gerar_boleto_billing_inexistente(self, http, cliente):
        r = http.post(f'{PREFIX}/boletos', json={'billing_id': 999999})
        assert r.status_code == 404

    def test_gerar_boleto_lote_billing_inexistente(self, http, cliente):
        r = http.post(f'{PREFIX}/boletos/lote', json={'billing_ids': [999999]})
        assert r.status_code == 404

    def test_gerar_carne_lote_billing_inexistente(self, http, cliente):
        r = http.post(f'{PREFIX}/carne/lote', json={'billing_ids': [999999]})
        assert r.status_code == 404

    def test_get_lote_status_inexistente(self, http):
        r = http.get(f'{PREFIX}/lotes/TICKET-INEXISTENTE')
        assert r.status_code == 404


class TestAuthorizationRoles:
    def test_status_negado_para_operacional(self, http_op):
        r = http_op.get(f'{PREFIX}/status')
        assert r.status_code == 403

    def test_status_nao_autenticado(self, http_unauth):
        r = http_unauth.get(f'{PREFIX}/status')
        assert r.status_code == 401

    def test_connect_negado_para_financeiro(self, http_fin):
        r = http_fin.post(f'{PREFIX}/connect')
        assert r.status_code == 403

    def test_connect_nao_autenticado(self, http_unauth):
        r = http_unauth.post(f'{PREFIX}/connect')
        assert r.status_code == 401

    def test_gerar_boleto_negado_para_operacional(self, http_op, billing_pendente):
        r = http_op.post(f'{PREFIX}/boletos', json={'billing_id': billing_pendente.id})
        assert r.status_code == 403

    def test_gerar_boleto_negado_para_cliente(self, http_cliente, billing_pendente):
        r = http_cliente.post(f'{PREFIX}/boletos', json={'billing_id': billing_pendente.id})
        assert r.status_code == 403

    def test_gerar_boleto_nao_autenticado(self, http_unauth, billing_pendente):
        r = http_unauth.post(f'{PREFIX}/boletos', json={'billing_id': billing_pendente.id})
        assert r.status_code == 401

    def test_gerar_boleto_lote_negado_para_operacional(self, http_op, billing_pendente):
        r = http_op.post(f'{PREFIX}/boletos/lote', json={'billing_ids': [billing_pendente.id]})
        assert r.status_code == 403

    def test_gerar_carne_lote_negado_para_operacional(self, http_op, billing_pendente):
        r = http_op.post(f'{PREFIX}/carne/lote', json={'billing_ids': [billing_pendente.id]})
        assert r.status_code == 403

    def test_consultar_boleto_negado_para_operacional(self, http_op):
        r = http_op.get(f'{PREFIX}/boletos/123')
        assert r.status_code == 403

    def test_consultar_boleto_nao_autenticado(self, http_unauth):
        r = http_unauth.get(f'{PREFIX}/boletos/123')
        assert r.status_code == 401

    def test_lote_status_negado_para_operacional(self, http_op):
        r = http_op.get(f'{PREFIX}/lotes/TICKET-1')
        assert r.status_code == 403

    def test_lote_status_nao_autenticado(self, http_unauth):
        r = http_unauth.get(f'{PREFIX}/lotes/TICKET-1')
        assert r.status_code == 401


class TestCooperadoNaoAutorizado:
    def test_gerar_boleto_sem_credenciais_retorna_400(self, http, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)

        r = http.post(f'{PREFIX}/boletos', json={'billing_id': billing_pendente.id})

        assert r.status_code == 400


class TestValidationError:
    def test_cpf_cnpj_vazio_retorna_422(self, http, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)
        cliente.cpf_cnpj = ''
        db.commit()

        r = http.post(f'{PREFIX}/boletos', json={'billing_id': billing_pendente.id})

        assert r.status_code == 422
        assert 'errors' in r.json()['detail']
        assert isinstance(r.json()['detail']['errors'], list)
        assert len(r.json()['detail']['errors']) >= 1


class TestStatusNuncaExpoeTokens:
    def test_status_sem_integracao(self, http):
        r = http.get(f'{PREFIX}/status')

        assert r.status_code == 200
        body = r.json()
        assert body['client_token_configured'] is False
        assert body['cooperado_status'] == 'pending'
        for chave in ('cooperado_token_encrypted', 'access_token_encrypted', 'access_token', 'cooperado_token'):
            assert chave not in body

    def test_status_com_cooperado_autorizado_nao_expoe_token(self, http, db, monkeypatch):
        monkeypatch.setattr(settings, 'ailos_token_encryption_key', VALID_KEY)
        segredo = 'SEGREDO-COOPERADO-SUPER-SECRETO'

        integration = AilosIntegration(
            numero_convenio=settings.ailos_numero_convenio,
            codigo_carteira=settings.ailos_default_carteira,
            status='authorized',
            cooperado_token_encrypted=encrypt_token(segredo),
        )
        db.add(integration)
        db.commit()

        r = http.get(f'{PREFIX}/status')

        assert r.status_code == 200
        body = r.json()
        assert body['cooperado_status'] == 'authorized'

        assert segredo not in r.text
        assert encrypt_token(segredo) != segredo  # sanity: token está de fato criptografado
        for chave in ('cooperado_token_encrypted', 'access_token_encrypted', 'access_token', 'cooperado_token'):
            assert chave not in body
