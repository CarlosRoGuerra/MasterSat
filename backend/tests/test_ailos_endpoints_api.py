"""
Testes E2E (TestClient) dos endpoints /api/v1/ailos/*.

Cobre: 404 de billing inexistente, 403/401 por papel, AilosError (cooperado/
client token não configurado) → 400, AilosValidationError → 422, e que
GET /status nunca expõe tokens.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto import encrypt_token
from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_integration import AilosIntegration
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.services import ailos_client

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

    def test_registrar_parcela_lote_inexistente(self, http):
        r = http.post(f'{PREFIX}/lotes/999999/parcelas/1/registrar')
        assert r.status_code == 404

    def test_registrar_pendentes_lote_inexistente(self, http):
        r = http.post(f'{PREFIX}/lotes/999999/registrar-pendentes')
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

    def test_registrar_parcela_negado_para_operacional(self, http_op):
        r = http_op.post(f'{PREFIX}/lotes/1/parcelas/1/registrar')
        assert r.status_code == 403

    def test_registrar_parcela_nao_autenticado(self, http_unauth):
        r = http_unauth.post(f'{PREFIX}/lotes/1/parcelas/1/registrar')
        assert r.status_code == 401

    def test_registrar_pendentes_negado_para_operacional(self, http_op):
        r = http_op.post(f'{PREFIX}/lotes/1/registrar-pendentes')
        assert r.status_code == 403

    def test_registrar_pendentes_nao_autenticado(self, http_unauth):
        r = http_unauth.post(f'{PREFIX}/lotes/1/registrar-pendentes')
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
    def test_status_sem_integracao(self, http, monkeypatch):
        # Isola do .env real: este teste verifica o comportamento "sem
        # credenciais configuradas", não deve depender de o ambiente rodando
        # os testes ter (ou não) AILOS_CLIENT_ID/SECRET reais configurados.
        monkeypatch.setattr(settings, 'ailos_client_id', '')
        monkeypatch.setattr(settings, 'ailos_client_secret', '')

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


class TestLoteStatusProgresso:
    """A tela de acompanhamento do carnê precisa de 'X de Y confirmadas' mesmo
    enquanto o lote ainda está 'processing' — não só um status sem número."""

    def _billing(self, db, cliente, valor='129.98'):
        b = Billing(client_id=cliente.id, amount=Decimal(valor),
                   due_date=date.today(), status=BillingStatus.PENDING,
                   billing_type='carne', title='Parcela')
        db.add(b)
        db.commit()
        return b

    def test_processing_traz_prontas_e_total(self, http, db, cliente, monkeypatch):
        # Isola do .env real (ver test_status_sem_integracao): sem isso, se o
        # ambiente tiver AILOS_CLIENT_ID/SECRET reais configurados, a consulta
        # da parcela pendente tenta buscar um client token de verdade antes de
        # chegar no mock de app.services.ailos_client.requests, e quebra numa
        # camada que este teste não está testando.
        monkeypatch.setattr(settings, 'ailos_client_id', '')
        monkeypatch.setattr(settings, 'ailos_client_secret', '')

        b1 = self._billing(db, cliente)
        b2 = self._billing(db, cliente)
        lote = AilosLote(tipo='carne', ticket='TICKET-PROGRESSO', numero_convenio='102004',
                         billing_ids=[b1.id, b2.id], status='processing')
        db.add(lote)
        db.commit()
        # b1 já confirmou na Ailos; b2 ainda não.
        db.add(AilosBoleto(billing_id=b1.id, numero_convenio='102004', lote_id=lote.id,
                           linha_digitavel='LD-1', codigo_barras='CB-1',
                           payload_request={'documento': {'numeroDocumento': b1.id}}))
        db.add(AilosBoleto(billing_id=b2.id, numero_convenio='102004', lote_id=lote.id,
                           payload_request={'documento': {'numeroDocumento': b2.id}}))
        db.commit()

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = MagicMock(status_code=404, text='nao', headers={}, content=b'')
            r = http.get(f'{PREFIX}/lotes/TICKET-PROGRESSO')

        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'processing'
        assert body['total'] == 2
        assert body['prontas'] == 1
        assert body['boletos'] is None  # forma resumida enquanto processa
        assert body['lote_id'] == lote.id
        parcelas_by_billing = {p['billing_id']: p for p in body['parcelas']}
        assert parcelas_by_billing[b1.id]['status'] == 'registrado'
        assert parcelas_by_billing[b2.id]['status'] == 'processando'

    def test_completed_traz_prontas_igual_total(self, http, db, cliente):
        b1 = self._billing(db, cliente)
        lote = AilosLote(tipo='carne', ticket='TICKET-DONE', numero_convenio='102004',
                         billing_ids=[b1.id], status='completed')
        db.add(lote)
        db.commit()
        db.add(AilosBoleto(billing_id=b1.id, numero_convenio='102004', lote_id=lote.id,
                           linha_digitavel='LD-1', codigo_barras='CB-1'))
        db.commit()

        r = http.get(f'{PREFIX}/lotes/TICKET-DONE')
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'completed'
        assert body['total'] == 1
        assert body['prontas'] == 1
        assert len(body['boletos']) == 1


class TestRegistrarParcelaEPendentesEndpoints:
    """Retry individual e em massa de parcelas de um lote/carnê já criado —
    reusam gerar_boleto (idempotente) por baixo, expostos como endpoints
    HTTP para os botões da tela de acompanhamento."""

    @pytest.fixture(autouse=True)
    def _tokens(self, monkeypatch):
        # Sem isso, get_valid_client_token tentaria buscar um token de app de
        # verdade via AILOS_CLIENT_ID/SECRET do .env real do ambiente rodando
        # os testes — independente de o que este teste está exercitando.
        monkeypatch.setattr(ailos_client, 'get_valid_client_token', lambda db: 'client-token')
        monkeypatch.setattr(ailos_client, 'get_valid_cooperado_token', lambda db: 'coop-token')

    def _boleto_response(self, billing_id, linha='LD-X'):
        return {
            'documento': {'numeroDocumento': billing_id, 'nossoNumero': '1', 'identificadorUnicoTitulo': '1'},
            'codigoBarras': {'codigoBarras': 'CB', 'linhaDigitavel': linha},
            'indicadorSituacaoBoleto': 'REGISTRADO',
            'valorBoleto': {'valorNominal': 129.98},
            'vencimento': {'dataVencimento': '2099-12-31'},
        }

    def _lote_carne(self, db, ids):
        lote = AilosLote(tipo='carne', ticket='TICKET-RETRY-EP', numero_convenio='102004',
                         billing_ids=list(ids), status='processing')
        db.add(lote)
        db.commit()
        for bid in ids:
            db.add(AilosBoleto(billing_id=bid, numero_convenio='102004', lote_id=lote.id,
                               payload_request={'documento': {'numeroDocumento': bid}}))
        db.commit()
        db.refresh(lote)
        return lote

    def test_registrar_parcela_com_sucesso(self, http, db, cliente):
        _preencher_endereco(cliente, db)
        b1 = Billing(client_id=cliente.id, amount=Decimal('129.98'), due_date=date.today(),
                     status=BillingStatus.PENDING, billing_type='carne', title='Parcela')
        db.add(b1); db.commit(); db.refresh(b1)
        lote = self._lote_carne(db, [b1.id])

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = MagicMock(
                status_code=200, headers={'Content-Type': 'application/json'}, text='',
                content=b'{}', json=MagicMock(return_value=self._boleto_response(b1.id)),
            )
            r = http.post(f'{PREFIX}/lotes/{lote.id}/parcelas/{b1.id}/registrar')

        assert r.status_code == 200
        body = r.json()
        assert body['linha_digitavel'] == 'LD-X'
        assert body['lote_id'] == lote.id

    def test_registrar_parcela_fora_do_lote_retorna_400(self, http, db, cliente):
        _preencher_endereco(cliente, db)
        b1 = Billing(client_id=cliente.id, amount=Decimal('129.98'), due_date=date.today(),
                     status=BillingStatus.PENDING, billing_type='carne', title='Parcela')
        b_fora = Billing(client_id=cliente.id, amount=Decimal('50'), due_date=date.today(),
                         status=BillingStatus.PENDING, billing_type='avulsa', title='Outra')
        db.add(b1); db.add(b_fora); db.commit(); db.refresh(b1); db.refresh(b_fora)
        lote = self._lote_carne(db, [b1.id])

        r = http.post(f'{PREFIX}/lotes/{lote.id}/parcelas/{b_fora.id}/registrar')
        assert r.status_code == 400

    def test_registrar_pendentes_avanca_o_que_der(self, http, db, cliente):
        _preencher_endereco(cliente, db)
        b1 = Billing(client_id=cliente.id, amount=Decimal('129.98'), due_date=date.today(),
                     status=BillingStatus.PENDING, billing_type='carne', title='Parcela 1')
        b2 = Billing(client_id=cliente.id, amount=Decimal('129.98'), due_date=date.today(),
                     status=BillingStatus.PENDING, billing_type='carne', title='Parcela 2')
        db.add(b1); db.add(b2); db.commit(); db.refresh(b1); db.refresh(b2)
        lote = self._lote_carne(db, [b1.id, b2.id])

        respostas = {
            b1.id: MagicMock(status_code=200, headers={'Content-Type': 'application/json'}, text='',
                             content=b'{}', json=MagicMock(return_value=self._boleto_response(b1.id, linha='LD-1'))),
            b2.id: MagicMock(status_code=422, headers={'Content-Type': 'application/json'}, text='',
                             content=b'{}', json=MagicMock(return_value={'mensagem': 'Endereco invalido'})),
        }

        def _fake(method, url, **kw):
            numero_doc = (kw.get('json') or {}).get('documento', {}).get('numeroDocumento')
            return respostas.get(numero_doc, MagicMock(status_code=404, headers={}, text='nao', content=b''))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = _fake
            r = http.post(f'{PREFIX}/lotes/{lote.id}/registrar-pendentes')

        assert r.status_code == 200
        body = r.json()
        assert body['sucesso'] == [b1.id]
        assert len(body['falhas']) == 1
        assert body['falhas'][0]['billing_id'] == b2.id
