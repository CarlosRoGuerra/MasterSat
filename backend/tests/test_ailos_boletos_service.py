"""
Testes para app.services.ailos_boletos: gerar_boleto, gerar_boleto_lote,
gerar_carne_lote, consultar_boleto, consultar_lote.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus
from app.services import ailos_client
from app.services.ailos_boletos import (
    consultar_boleto,
    consultar_lote,
    gerar_boleto,
    gerar_boleto_lote,
    gerar_carne_lote,
    resolver_pagador,
)


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


def _criar_billing_futuro(db, contrato, due_date=date(2099, 6, 30), title='Plano Teste 2') -> Billing:
    b = Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        amount=Decimal('99.90'),
        due_date=due_date,
        status=BillingStatus.PENDING,
        billing_type='recorrente',
        period_label='06/2099',
        title=title,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@pytest.fixture(autouse=True)
def _ailos_settings(monkeypatch):
    monkeypatch.setattr(settings, 'ailos_gateway_base_url', 'https://gateway.ailos.test')
    monkeypatch.setattr(settings, 'ailos_timeout_seconds', 5)
    monkeypatch.setattr(settings, 'ailos_developer_key', 'dev-key')
    monkeypatch.setattr(settings, 'ailos_numero_convenio', '102004')


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setattr(ailos_client, 'get_valid_client_token', lambda db: 'client-token')
    monkeypatch.setattr(ailos_client, 'get_valid_cooperado_token', lambda db: 'coop-token')


def _resp(status_code=200, json_data=None, text='', headers=None, content=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers if headers is not None else {'Content-Type': 'application/json'}
    resp.text = text
    resp.content = content if content is not None else text.encode()
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError('no json')
    return resp


def _boleto_response(billing_id, nosso_numero='12345678', linha='LINHA-DIGITAVEL', codigo='CODIGO-BARRAS'):
    return {
        'documento': {
            'numeroDocumento': billing_id,
            'nossoNumero': nosso_numero,
            'identificadorUnicoTitulo': f'ID-{nosso_numero}',
        },
        'codigoBarras': {
            'codigoBarras': codigo,
            'linhaDigitavel': linha,
        },
        'indicadorSituacaoBoleto': 'REGISTRADO',
        'valorBoleto': {'valorNominal': 99.9},
        'vencimento': {'dataVencimento': '2099-12-31'},
    }


class TestPagadorInterveniente:
    """Quando o contrato tem interveniente financeiro, o boleto sai no nome
    dele (quem responde pela cobrança), não no do dono do veículo."""

    def _interveniente(self, db, deleted=False):
        i = Client(name='FINANCEIRA XPTO', cpf_cnpj='11144477735', type='pj',
                   status=ClientStatus.ACTIVE, is_deleted=deleted)
        db.add(i)
        db.commit()
        db.refresh(i)
        return i

    def test_resolver_usa_interveniente_do_contrato(self, db, billing_pendente, contrato):
        interv = self._interveniente(db)
        contrato.interveniente_client_id = interv.id
        db.commit()
        assert resolver_pagador(db, billing_pendente).id == interv.id

    def test_resolver_cai_no_cliente_sem_interveniente(self, db, billing_pendente, contrato):
        assert resolver_pagador(db, billing_pendente).id == contrato.client_id

    def test_resolver_ignora_interveniente_deletado(self, db, billing_pendente, contrato):
        interv = self._interveniente(db, deleted=True)
        contrato.interveniente_client_id = interv.id
        db.commit()
        assert resolver_pagador(db, billing_pendente).id == contrato.client_id

    def test_gerar_boleto_manda_interveniente_como_pagador(self, db, billing_pendente, contrato, cliente):
        _preencher_endereco(cliente, db)
        interv = self._interveniente(db)
        _preencher_endereco(interv, db)
        contrato.interveniente_client_id = interv.id
        db.commit()

        resp = _resp(200, json_data=_boleto_response(billing_pendente.id))
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            boleto = gerar_boleto(db, billing_pendente, cliente)

        pagador = boleto.payload_request['pagador']['entidadeLegal']
        assert pagador['nome'] == 'FINANCEIRA XPTO'
        assert pagador['identificadorReceitaFederal'] == '11144477735'


class TestGerarBoleto:
    def test_persiste_dados_oficiais(self, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)
        resp = _resp(200, json_data=_boleto_response(billing_pendente.id))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            boleto = gerar_boleto(db, billing_pendente, cliente)

        assert boleto.billing_id == billing_pendente.id
        assert boleto.numero_convenio == '102004'
        assert boleto.numero_documento == str(billing_pendente.id)
        assert boleto.nosso_numero == '12345678'
        assert boleto.identificador_unico_titulo == 'ID-12345678'
        assert boleto.linha_digitavel == 'LINHA-DIGITAVEL'
        assert boleto.codigo_barras == 'CODIGO-BARRAS'
        assert boleto.status_ailos == 'REGISTRADO'
        assert boleto.valor_nominal == Decimal('99.9')
        assert boleto.data_vencimento == date(2099, 12, 31)
        assert boleto.payload_request['documento']['numeroDocumento'] == billing_pendente.id
        assert boleto.payload_response['indicadorSituacaoBoleto'] == 'REGISTRADO'

    def test_desembrulha_envelope_boleto_e_pix(self, db, billing_pendente, cliente):
        # Produção responde envelopado em {"boleto": {...}} com nomes próprios
        # (dataVencimentoAtual, valorOriginalTitulo) e o EMV do Pix em "pix".
        _preencher_endereco(cliente, db)
        envelope = {'boleto': {
            'documento': {'numeroDocumento': billing_pendente.id, 'nossoNumero': '00454702000000002',
                          'identificadorUnicoTitulo': 0},
            'codigoBarras': {'codigoBarras': '0859...01', 'linhaDigitavel': '08591.02006 ... 1000'},
            'valorBoleto': {'valorOriginalTitulo': 10, 'valorAtual': 10},
            'vencimento': {'dataVencimentoAtual': '2026-06-26T00:00:00'},
            'indicadorSituacaoBoleto': 0,
            # bloco pix com imagem (base64 PNG) E o EMV — detecção por conteúdo
            'pix': {'qrCode': 'iVBORw0KGgoBASE64PNG==', 'emv': '00020126580014BR.GOV.BCB.PIX6304ABCD'},
        }}

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = _resp(200, json_data=envelope)
            boleto = gerar_boleto(db, billing_pendente, cliente)

        assert boleto.nosso_numero == '00454702000000002'
        assert boleto.linha_digitavel == '08591.02006 ... 1000'
        assert boleto.codigo_barras == '0859...01'
        assert boleto.status_ailos == '0'
        assert boleto.valor_nominal == Decimal('10')
        assert boleto.data_vencimento == date(2026, 6, 26)
        # EMV (começa com 000201) vai p/ pix_emv; a imagem PNG p/ pix_qr_base64
        assert boleto.pix_emv == '00020126580014BR.GOV.BCB.PIX6304ABCD'
        assert boleto.pix_qr_base64 == 'iVBORw0KGgoBASE64PNG=='

    def test_ja_cadastrado_recupera_via_consulta(self, db, billing_pendente, cliente):
        # A Ailos já tem o boleto (HTTP 400 "Boleto já cadastrado"), mas o
        # MasterSat não guardou localmente. Em vez de falhar, deve consultar
        # pelo numeroDocumento e persistir os dados oficiais (boleto pagável).
        _preencher_endereco(cliente, db)
        erro_400 = _resp(400, json_data={'mensagem': f'Boleto já cadastrado - {billing_pendente.id}'})
        consulta = _resp(200, json_data=_boleto_response(billing_pendente.id, nosso_numero='99999999'))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = [erro_400, consulta]
            boleto = gerar_boleto(db, billing_pendente, cliente)

        assert boleto.linha_digitavel == 'LINHA-DIGITAVEL'
        assert boleto.nosso_numero == '99999999'
        assert mock_requests.request.call_count == 2  # POST (falhou) + GET (recuperou)

    def test_ja_cadastrado_mas_consulta_vazia_propaga_erro(self, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)
        erro_400 = _resp(400, json_data={'mensagem': 'Boleto já cadastrado'})
        consulta_vazia = _resp(200, json_data=[])

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = [erro_400, consulta_vazia]
            with pytest.raises(ailos_client.AilosApiError):
                gerar_boleto(db, billing_pendente, cliente)

    def test_reexecucao_idempotente_nao_reregistra(self, db, billing_pendente, cliente):
        # Já registrado (tem linha digitável) → re-executar NÃO chama a Ailos de
        # novo (evita "título já cadastrado") e devolve o mesmo boleto.
        _preencher_endereco(cliente, db)

        resp1 = _resp(200, json_data=_boleto_response(billing_pendente.id, nosso_numero='11111111'))
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp1
            boleto1 = gerar_boleto(db, billing_pendente, cliente)

        with patch('app.services.ailos_client.requests') as mock_requests:
            boleto2 = gerar_boleto(db, billing_pendente, cliente)
            mock_requests.request.assert_not_called()

        assert boleto1.id == boleto2.id
        assert boleto2.nosso_numero == '11111111'
        assert db.query(AilosBoleto).count() == 1


class TestGerarBoletoLote:
    def test_cria_lote_processing(self, db, billing_pendente, contrato, cliente):
        _preencher_endereco(cliente, db)
        billing2 = _criar_billing_futuro(db, contrato)
        resp = _resp(202, json_data={'ticketLote': 'TICKET-BOLETO-1'})

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            lote = gerar_boleto_lote(db, [billing_pendente, billing2], {cliente.id: cliente})

        assert lote.tipo == 'boleto'
        assert lote.ticket == 'TICKET-BOLETO-1'
        assert lote.numero_convenio == '102004'
        assert lote.status == 'processing'
        assert lote.billing_ids == [billing_pendente.id, billing2.id]

        boletos = db.query(AilosBoleto).filter_by(lote_id=lote.id).all()
        assert {b.billing_id for b in boletos} == {billing_pendente.id, billing2.id}
        assert all(b.payload_response is None for b in boletos)


class TestGerarCarneLote:
    def test_cria_lote_carne_processing(self, db, billing_pendente, contrato, cliente):
        _preencher_endereco(cliente, db)
        billing2 = _criar_billing_futuro(db, contrato)
        resp = _resp(202, json_data={'ticketLote': 'TICKET-CARNE-1'})

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            lote = gerar_carne_lote(
                db,
                [(1, billing_pendente), (2, billing2)],
                {cliente.id: cliente},
            )

        assert lote.tipo == 'carne'
        assert lote.ticket == 'TICKET-CARNE-1'
        assert lote.status == 'processing'
        assert lote.billing_ids == [billing_pendente.id, billing2.id]

        _, kwargs = mock_requests.request.call_args
        body = kwargs['json']
        # v2: corpo envelopado com convenioCobranca + lista "carnes".
        assert 'convenioCobranca' in body
        carnes = body['carnes']
        assert carnes[0]['numeroParcela'] == 1
        assert carnes[0]['tipoVencimento'] == {
            'tipoVencimento': 1,
            'quantidadeXDias': 0,
            'diaXDeCadaMes': 0,
        }
        assert carnes[1]['numeroParcela'] == 2


class TestConsultarBoleto:
    def test_retorna_resposta_da_api(self, db):
        resp = _resp(200, json_data={'documento': {'numeroDocumento': 1}})

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            result = consultar_boleto(db, 'NUMERO-BOLETO-1')

        assert result == {'documento': {'numeroDocumento': 1}}


class TestConsultarLote:
    def test_processing_nao_altera_status(self, db):
        lote = AilosLote(
            tipo='boleto',
            ticket='TICKET-PROC',
            numero_convenio='102004',
            billing_ids=[1],
            status='processing',
        )
        db.add(lote)
        db.commit()
        db.refresh(lote)

        body = {'mensagem': '[99] Solicitação em processamento'}
        resp = _resp(400, json_data=body, text=str(body))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            result = consultar_lote(db, lote)

        assert result == {'status': 'processing'}
        db.refresh(lote)
        assert lote.status == 'processing'

    def test_completed_atualiza_lote_e_boletos(self, db, billing_pendente, contrato, cliente):
        _preencher_endereco(cliente, db)
        billing2 = _criar_billing_futuro(db, contrato)
        resp_lote = _resp(202, json_data={'ticketLote': 'TICKET-DONE'})
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp_lote
            lote = gerar_boleto_lote(db, [billing_pendente, billing2], {cliente.id: cliente})

        body = {
            'boletos': [
                _boleto_response(billing_pendente.id, nosso_numero='AAA', linha='LD-A', codigo='CB-A'),
                _boleto_response(billing2.id, nosso_numero='BBB', linha='LD-B', codigo='CB-B'),
            ]
        }
        resp_consulta = _resp(200, json_data=body)
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp_consulta
            result = consultar_lote(db, lote)

        assert result['status'] == 'completed'
        db.refresh(lote)
        assert lote.status == 'completed'
        assert lote.payload_response == body

        boleto_a = db.query(AilosBoleto).filter_by(billing_id=billing_pendente.id).first()
        boleto_b = db.query(AilosBoleto).filter_by(billing_id=billing2.id).first()
        assert boleto_a.linha_digitavel == 'LD-A'
        assert boleto_a.codigo_barras == 'CB-A'
        assert boleto_b.linha_digitavel == 'LD-B'
        assert boleto_b.codigo_barras == 'CB-B'


class TestVerificarPagamento:
    def test_extrair_pagamento_pago(self):
        from app.services.ailos_boletos import _extrair_pagamento
        env = {'boleto': {'valorBoleto': {'valorPago': 10.0},
                          'pagamento': {'dataPagamento': '2026-06-26T00:00:00'}}}
        info = _extrair_pagamento(env)
        assert info['pago'] is True
        assert info['valor_pago'] == 10.0
        assert info['data_pagamento'] == date(2026, 6, 26)

    def test_extrair_pagamento_nao_pago(self):
        from app.services.ailos_boletos import _extrair_pagamento
        env = {'boleto': {'valorBoleto': {'valorPago': 0},
                          'pagamento': {'dataPagamento': '0001-01-01T00:00:00'}}}
        assert _extrair_pagamento(env)['pago'] is False

    def test_verificar_pagamento_da_baixa(self, db, billing_pendente, cliente):
        from app.models.enums import BillingStatus
        from app.services.ailos_boletos import verificar_pagamento
        _preencher_endereco(cliente, db)

        # gera o boleto (cria AilosBoleto com nosso_numero)
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = _resp(200, json_data=_boleto_response(billing_pendente.id, nosso_numero='999'))
            gerar_boleto(db, billing_pendente, cliente)

        # consulta retorna PAGO
        consulta = {'boleto': {
            'documento': {'numeroDocumento': billing_pendente.id, 'nossoNumero': '999'},
            'codigoBarras': {'codigoBarras': 'X', 'linhaDigitavel': 'Y'},
            'valorBoleto': {'valorPago': 99.9},
            'pagamento': {'dataPagamento': '2026-06-26T00:00:00'},
        }}
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = _resp(200, json_data=consulta)
            res = verificar_pagamento(db, billing_pendente)

        assert res['pago'] is True
        db.refresh(billing_pendente)
        assert billing_pendente.status == BillingStatus.PAID
        assert billing_pendente.payment_date == date(2026, 6, 26)
        assert billing_pendente.receipt_number is not None

    def test_verificar_pagamento_sem_baixa_quando_nao_pago(self, db, billing_pendente, cliente):
        from app.models.enums import BillingStatus
        from app.services.ailos_boletos import verificar_pagamento
        _preencher_endereco(cliente, db)

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = _resp(200, json_data=_boleto_response(billing_pendente.id, nosso_numero='999'))
            gerar_boleto(db, billing_pendente, cliente)

        consulta = {'boleto': {
            'documento': {'numeroDocumento': billing_pendente.id, 'nossoNumero': '999'},
            'valorBoleto': {'valorPago': 0},
            'pagamento': {'dataPagamento': '0001-01-01T00:00:00'},
        }}
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = _resp(200, json_data=consulta)
            res = verificar_pagamento(db, billing_pendente)

        assert res['pago'] is False
        db.refresh(billing_pendente)
        assert billing_pendente.status != BillingStatus.PAID


class TestConsultarCarne:
    """A consulta de LOTE de carnê da Ailos devolve 404 no gateway; o status do
    carnê é obtido recuperando cada parcela pela consulta de boleto individual
    (numeroDocumento = billing.id)."""

    def _lote_carne(self, db, ids):
        lote = AilosLote(tipo='carne', ticket='TICKET-CARNE-X', numero_convenio='102004',
                         billing_ids=list(ids), status='processing')
        db.add(lote)
        db.commit()
        db.refresh(lote)
        for bid in ids:
            db.add(AilosBoleto(billing_id=bid, numero_convenio='102004', lote_id=lote.id,
                               payload_request={'documento': {'numeroDocumento': bid}}))
        db.commit()
        return lote

    def test_recupera_parcelas_por_boleto_individual(self, db, billing_pendente, contrato, cliente):
        billing2 = _criar_billing_futuro(db, contrato)
        lote = self._lote_carne(db, [billing_pendente.id, billing2.id])
        respostas = {
            str(billing_pendente.id): _resp(200, json_data=_boleto_response(billing_pendente.id, linha='LD-1', codigo='CB-1')),
            str(billing2.id): _resp(200, json_data=_boleto_response(billing2.id, linha='LD-2', codigo='CB-2')),
        }

        def _fake(method, url, **kw):
            ultimo = url.rstrip('/').split('/')[-1]
            return respostas.get(ultimo, _resp(404, text='nao'))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = _fake
            result = consultar_lote(db, lote)

        assert result['status'] == 'completed'
        assert result['lote'].status == 'completed'
        ab1 = db.query(AilosBoleto).filter_by(billing_id=billing_pendente.id).first()
        ab2 = db.query(AilosBoleto).filter_by(billing_id=billing2.id).first()
        assert ab1.linha_digitavel == 'LD-1'
        assert ab2.linha_digitavel == 'LD-2'

    def test_carne_parcial_nao_fecha_o_lote(self, db, billing_pendente, contrato, cliente):
        """Regressão do bug: com 1 de 2 parcelas registradas, o lote tinha que
        continuar 'processing' — não 'completed' com só 1 boleto pronto (o PDF
        sairia com uma parcela só em vez do carnê completo)."""
        billing2 = _criar_billing_futuro(db, contrato)
        lote = self._lote_carne(db, [billing_pendente.id, billing2.id])
        respostas = {
            str(billing_pendente.id): _resp(200, json_data=_boleto_response(billing_pendente.id, linha='LD-1', codigo='CB-1')),
            # billing2 ainda não está pronta do lado do banco.
            str(billing2.id): _resp(404, text='not found'),
        }

        def _fake(method, url, **kw):
            ultimo = url.rstrip('/').split('/')[-1]
            return respostas.get(ultimo, _resp(404, text='nao'))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = _fake
            result = consultar_lote(db, lote)

        assert result == {'status': 'processing'}
        assert lote.status == 'processing'
        ab1 = db.query(AilosBoleto).filter_by(billing_id=billing_pendente.id).first()
        assert ab1.linha_digitavel == 'LD-1'  # a que resolveu já foi salva

    def test_carne_parcial_fecha_apos_prazo_esgotado(self, db, billing_pendente, contrato, cliente):
        """Se uma parcela nunca resolve, o lote não pode ficar 'processing'
        para sempre — depois de um tempo, fecha com o que conseguiu."""
        billing2 = _criar_billing_futuro(db, contrato)
        lote = self._lote_carne(db, [billing_pendente.id, billing2.id])
        lote.created_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        db.commit()
        respostas = {
            str(billing_pendente.id): _resp(200, json_data=_boleto_response(billing_pendente.id, linha='LD-1', codigo='CB-1')),
            str(billing2.id): _resp(404, text='not found'),
        }

        def _fake(method, url, **kw):
            ultimo = url.rstrip('/').split('/')[-1]
            return respostas.get(ultimo, _resp(404, text='nao'))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = _fake
            result = consultar_lote(db, lote)

        assert result['status'] == 'completed'
        assert lote.status == 'completed'

    def test_carne_ainda_processando_fica_processing(self, db, billing_pendente, contrato, cliente):
        """Nenhuma parcela registrada ainda → segue 'processing', não estoura."""
        billing2 = _criar_billing_futuro(db, contrato)
        lote = self._lote_carne(db, [billing_pendente.id, billing2.id])
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = _resp(404, text='not found')
            result = consultar_lote(db, lote)
        assert result == {'status': 'processing'}
        assert lote.status == 'processing'

    def test_nao_reconsulta_parcela_ja_registrada(self, db, billing_pendente, contrato, cliente):
        """Parcela que já tem linha digitável não é consultada de novo."""
        billing2 = _criar_billing_futuro(db, contrato)
        lote = self._lote_carne(db, [billing_pendente.id, billing2.id])
        ab1 = db.query(AilosBoleto).filter_by(billing_id=billing_pendente.id).first()
        ab1.linha_digitavel = 'JA-TENHO'; ab1.codigo_barras = 'CB'; db.commit()
        respostas = {str(billing2.id): _resp(200, json_data=_boleto_response(billing2.id, linha='LD-2', codigo='CB-2'))}

        def _fake(method, url, **kw):
            ultimo = url.rstrip('/').split('/')[-1]
            assert ultimo != str(billing_pendente.id), 'não deveria reconsultar a parcela já registrada'
            return respostas.get(ultimo, _resp(404, text='nao'))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = _fake
            result = consultar_lote(db, lote)
        assert result['status'] == 'completed'
