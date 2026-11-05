"""Testes do ciclo Pix com respostas bancárias controladas; nenhuma chamada real."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.sicredi_pix import SicrediPixCobranca, SicrediPixRecebimento, SicrediPixRecorrencia
from app.services import sicredi_pix as svc, sicredi_pix_client as transport, titulo_bancario
from app.services.billing_closure import execute_closure
from app.services.sicredi_pix_client import PixError, PixResultadoIncerto

REC_ID = 'RN1234567820261105abcdefghijk'
NOW = datetime(2026, 11, 5, 15, tzinfo=timezone.utc)
E2E = 'E' + '1' * 31


class Bank:
    def __init__(self):
        self.calls = []
        self.rec_status = 'CRIADA'
        self.charges = {}
        self.create_error = None
        self.put_error = None
        self.read_error = None

    def __call__(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def request(self, method, path, *, params=None, json=None):
        self.calls.append((method, path, json, params))
        if method == 'POST' and path.endswith('/locrec'):
            return {'id': 10}
        if method == 'POST' and path.endswith('/rec'):
            if self.create_error:
                raise self.create_error
            return {'idRec': REC_ID}
        if method == 'GET' and path.endswith('/rec/' + REC_ID):
            return {'idRec': REC_ID, 'status': self.rec_status,
                    'dadosQR': {'jornada': 'JORNADA_2', 'pixCopiaECola': '000201autorizacaoSicredi'}}
        if method == 'PATCH' and '/rec/' in path:
            self.rec_status = 'CANCELADA'
            return {'idRec': REC_ID, 'status': self.rec_status}
        if method == 'PUT' and '/cobr/' in path:
            txid = path.rsplit('/', 1)[1]
            self.charges[txid] = {'idRec': REC_ID, 'txid': txid, 'status': 'ATIVA'}
            if self.put_error:
                raise self.put_error
            return self.charges[txid]
        if method == 'GET' and '/cobr/' in path:
            if self.read_error:
                raise self.read_error
            txid = path.rsplit('/', 1)[1]
            if txid not in self.charges:
                raise PixError('Não encontrado.', status_code=404)
            return self.charges[txid]
        if method == 'PATCH' and '/cobr/' in path:
            txid = path.rsplit('/', 1)[1]
            self.charges[txid]['status'] = 'CANCELADA'
            return self.charges[txid]
        raise AssertionError(f'Chamada inesperada: {method} {path}')

    def count(self, method):
        return len([c for c in self.calls if c[0] == method])


@pytest.fixture()
def bank(monkeypatch, tmp_path):
    path = tmp_path / 'certificate.pem'
    path.write_text('certificado usado apenas por transporte falso')
    for field, value in {
        'sicredi_pix_enabled': True, 'sicredi_pix_ambiente': 'homologacao',
        'sicredi_pix_client_id': 'id-teste', 'sicredi_pix_client_secret': 'segredo-teste',
        'sicredi_pix_cert_path': str(path), 'sicredi_pix_key_path': str(path), 'sicredi_pix_ca_path': str(path),
        'sicredi_pix_escrita_habilitada': True, 'sicredi_pix_producao_confirmada': False,
        'sicredi_pix_agencia': '2602', 'sicredi_pix_conta': '208865',
        'sicredi_pix_webhook_token': 'A' * 48,
    }.items():
        monkeypatch.setattr(settings, field, value)
    monkeypatch.setattr(svc, 'hoje', lambda: NOW.date())
    monkeypatch.setattr(svc, 'agora', lambda: NOW)
    fake = Bank()
    monkeypatch.setattr(svc, 'PixClient', fake)
    return fake


@pytest.fixture()
def rec(db, cliente, bank):
    cliente.forma_cobranca = 'pix_automatico'
    r = SicrediPixRecorrencia(client_id=cliente.id, ambiente='homologacao', referencia='MScontrato', id_rec=REC_ID,
        status='APROVADA', ativa=True, data_inicial=date(2026, 11, 15), objeto='Mensalidades MasterSat',
        valor_minimo=Decimal('0.01'), politica_retentativa='NAO_PERMITE')
    bank.rec_status = 'APROVADA'
    db.add(r); db.commit()
    return r


def make_charge(db, cliente, rec, due=date(2026, 11, 15), amount='64.99'):
    billing = Billing(client_id=cliente.id, payer_client_id=cliente.id, billing_type='avulsa',
        title='Fechamento', amount=Decimal(amount), due_date=due, status=BillingStatus.PENDING)
    db.add(billing); db.flush()
    svc.enfileirar_fechamento(db, [billing.id]); db.commit()
    return billing, db.scalar(select(SicrediPixCobranca).where(SicrediPixCobranca.billing_id == billing.id))


def payment(charge, **kwargs):
    return {'txid': charge.txid, 'endToEndId': E2E, 'valor': '64.99',
            'horario': '2026-11-16T01:30:00Z', **kwargs}


def test_forma_pix_e_uma_opcao_do_cadastro_sem_criar_autorizacao(http, db, cliente, bank):
    response = http.put(f'/api/v1/clients/{cliente.id}', json={
        'forma_cobranca': 'pix_automatico', 'boleto_format': 'individual', 'boleto_fee': 'sim'})
    assert response.status_code == 200
    assert response.json()['forma_cobranca'] == 'pix_automatico'
    assert response.json()['boleto_format'] == 'unico'
    assert response.json()['boleto_fee'] == 'nao'
    assert db.scalar(select(func.count()).select_from(SicrediPixRecorrencia)) == 0
    assert bank.calls == []


def test_cria_jornada2_mensal_com_devedor_do_cliente_e_qr_do_banco(db, cliente, bank):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    rec = svc.criar_recorrencia(db, cliente.id, data_inicial=date(2026, 11, 15),
                                valor_minimo=Decimal('0.01'), objeto='Mensalidades e serviços MasterSat')
    sent = next(c[2] for c in bank.calls if c[0] == 'POST' and c[1].endswith('/rec'))
    assert sent['vinculo']['devedor'] == {'cpf': cliente.cpf_cnpj, 'nome': cliente.name}
    assert sent['calendario'] == {'dataInicial': '2026-11-15', 'periodicidade': 'MENSAL'}
    assert sent['politicaRetentativa'] == 'NAO_PERMITE'
    assert sent['loc'] == 10
    assert rec.status == 'CRIADA' and rec.pix_copia_cola == '000201autorizacaoSicredi'
    assert rec.id_rec == REC_ID
    svc.criar_recorrencia(db, cliente.id, data_inicial=date(2026, 11, 15),
                         valor_minimo=Decimal('0.01'), objeto='Mensalidades MasterSat')
    assert bank.count('POST') == 2


def test_timeout_criacao_nao_repete_post(db, cliente, bank):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    bank.create_error = PixResultadoIncerto('timeout')
    args = dict(data_inicial=date(2026, 11, 15), valor_minimo=Decimal('0.01'), objeto='Mensalidades MasterSat')
    with pytest.raises(PixResultadoIncerto):
        svc.criar_recorrencia(db, cliente.id, **args)
    rec = svc.recorrencia_cliente(db, cliente.id)
    assert rec.status == 'DESFECHO_DESCONHECIDO' and rec.ativa
    svc.criar_recorrencia(db, cliente.id, **args)
    assert bank.count('POST') == 2


def test_producao_nao_e_liberada_por_credenciais_de_leitura(http, db, cliente, bank, monkeypatch):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    monkeypatch.setattr(settings, 'sicredi_pix_ambiente', 'producao')
    response = http.post(f'/api/v1/sicredi-pix/clientes/{cliente.id}/autorizacao', json={'data_inicial': '2026-11-15'})
    assert response.status_code == 409 and 'produção' in response.text
    assert bank.calls == []
    assert db.scalar(select(func.count()).select_from(SicrediPixRecorrencia)) == 0


def test_fechamento_consolida_e_reserva_apenas_um_pix_sem_rede(db, cliente, plan, rec, bank):
    cliente.boleto_format = 'individual'  # Legado também deve respeitar um pagamento por ciclo.
    for _ in range(2):
        db.add(Contract(client_id=cliente.id, plan_id=plan.id, status='ativo',
                        start_date=date(2026, 1, 1), billing_day=15))
    db.commit()
    result = execute_closure(db, date(2026, 11, 1), activity_month=date(2026, 10, 1))
    charges = list(db.scalars(select(SicrediPixCobranca)).all())
    assert len(charges) == 1
    assert len(result['payment_billing_ids']) == 1
    assert charges[0].valor == plan.price * 2
    assert db.get(Billing, charges[0].billing_id).payment_method == 'pix_automatico'
    assert bank.calls == []


@pytest.mark.parametrize('days,expected,puts', [(11, 'NA_FILA', 0), (10, 'ATIVA', 1), (2, 'ATIVA', 1), (1, 'PENDENCIA', 0)])
def test_janela_2_a_10_dias(db, cliente, rec, bank, monkeypatch, days, expected, puts):
    rec.data_inicial = date(2026, 10, 15); db.commit()
    billing, charge = make_charge(db, cliente, rec, due=NOW.date() + timedelta(days=days))
    svc.processar_cobranca(db, charge)
    assert charge.status == expected
    assert bank.count('PUT') == puts
    if puts:
        payload = next(call[2] for call in bank.calls if call[0] == 'PUT')
        assert payload['calendario']['dataDeVencimento'] == billing.due_date.isoformat()
        assert payload['ajusteDiaUtil'] is False
    assert billing.status == BillingStatus.PENDING


def test_cliente_ainda_nao_autorizou_nunca_agenda(db, cliente, rec, bank):
    rec.status = bank.rec_status = 'CRIADA'; db.commit()
    _, charge = make_charge(db, cliente, rec)
    svc.processar_cobranca(db, charge)
    assert charge.status == 'AGUARDANDO_AUTORIZACAO'
    assert bank.count('PUT') == 0


def test_um_agendamento_por_ciclo_e_por_cobranca(db, cliente, rec, bank):
    first, charge = make_charge(db, cliente, rec)
    second, _ = make_charge(db, cliente, rec, due=date(2026, 11, 20))
    assert db.scalar(select(func.count()).select_from(SicrediPixCobranca)) == 1
    assert charge.status == 'PENDENCIA' and str(second.id) in charge.pendencia
    svc.enfileirar_fechamento(db, [first.id]); db.commit()
    assert db.scalar(select(func.count()).select_from(SicrediPixCobranca)) == 1


@pytest.mark.parametrize('operation', [titulo_bancario.EMITIR_AILOS, titulo_bancario.EMITIR_SICREDI,
                                      titulo_bancario.EMITIR_CNAB, titulo_bancario.ALTERAR_VALOR,
                                      titulo_bancario.CANCELAR, titulo_bancario.RECEBER])
def test_reserva_pix_impede_outro_canal_e_mutacoes(db, cliente, rec, bank, operation):
    billing, charge = make_charge(db, cliente, rec)
    with pytest.raises(titulo_bancario.PoliticaBancariaError, match='Pix'):
        titulo_bancario.exigir(db, operation, [billing.id], confirmado=True)
    assert charge.status == 'NA_FILA'


def test_timeout_envio_so_consulta_na_segunda_rodada(db, cliente, rec, bank):
    _, charge = make_charge(db, cliente, rec)
    bank.put_error = PixResultadoIncerto('timeout no PUT')
    with pytest.raises(PixResultadoIncerto):
        svc.processar_cobranca(db, charge)
    assert charge.status == 'DESFECHO_DESCONHECIDO'
    bank.read_error = PixError('Ainda não encontrado', status_code=404)
    with pytest.raises(PixError):
        svc.processar_cobranca(db, charge)
    assert bank.count('PUT') == 1
    bank.read_error = None
    svc.processar_cobranca(db, charge)
    assert charge.status == 'ATIVA' and bank.count('PUT') == 1


def test_conclusao_sem_liquidacao_nao_da_baixa(db, cliente, rec, bank):
    billing, charge = make_charge(db, cliente, rec)
    bank.charges[charge.txid] = {'idRec': REC_ID, 'txid': charge.txid, 'status': 'CONCLUIDA'}
    svc.consultar_cobranca(db, charge)
    assert billing.status == BillingStatus.PENDING
    assert 'liquidação' in charge.pendencia


def test_liquidacao_idempotente_e_data_em_brasilia(db, cliente, rec, bank):
    billing, charge = make_charge(db, cliente, rec)
    bank.charges[charge.txid] = {'idRec': REC_ID, 'txid': charge.txid, 'status': 'CONCLUIDA', 'pix': [payment(charge)]}
    for _ in range(2):
        svc.consultar_cobranca(db, charge)
    assert billing.status == BillingStatus.PAID
    assert billing.payment_method == 'pix_automatico'
    assert billing.payment_date == date(2026, 11, 15)
    assert billing.paid_amount == Decimal('64.99')
    assert db.scalar(select(func.count()).select_from(SicrediPixRecebimento)) == 1
    assert db.scalar(select(SicrediPixRecebimento)).aplicado


@pytest.mark.parametrize('case', ['valor', 'cancelada', 'removida', 'recebida', 'devolucao', 'identificador'])
def test_recebimento_divergente_exige_conferencia(db, cliente, rec, bank, case):
    billing, charge = make_charge(db, cliente, rec)
    pix = payment(charge)
    if case == 'valor': pix['valor'] = '64.98'
    if case == 'cancelada': billing.status = BillingStatus.CANCELED
    if case == 'removida': billing.is_deleted = True
    if case == 'recebida': billing.status = BillingStatus.PAID; billing.payment_method = 'dinheiro'
    if case == 'devolucao': pix['devolucoes'] = [{'status': 'DEVOLVIDO'}]
    if case == 'identificador': pix['txid'] = 'outro'
    db.commit()
    previous = billing.status
    bank.charges[charge.txid] = {'idRec': REC_ID, 'txid': charge.txid, 'status': 'CONCLUIDA', 'pix': [pix]}
    svc.consultar_cobranca(db, charge)
    assert billing.status == previous
    assert charge.pendencia
    assert not db.scalar(select(SicrediPixRecebimento.aplicado).where(SicrediPixRecebimento.cobranca_id == charge.id))


def test_webhook_e_so_aviso_nao_confia_em_status_ou_valor(http_unauth, db, cliente, rec, bank):
    billing, charge = make_charge(db, cliente, rec)
    response = http_unauth.post('/api/v1/sicredi-pix/webhooks/' + 'A' * 48 + '/pix', json={
        'pix': [{'txid': charge.txid, 'endToEndId': E2E, 'valor': '64.99', 'horario': '2026-11-15T12:00:00Z'}],
        'recs': [{'idRec': REC_ID, 'status': 'CANCELADA'}]})
    assert response.status_code == 200
    assert billing.status == BillingStatus.PENDING and rec.status == 'APROVADA'
    assert charge.processar_evento and rec.processar_evento
    assert bank.calls == []
    assert http_unauth.post('/api/v1/sicredi-pix/webhooks/incorreto/pix', json={}).status_code == 404


def test_financeiro_pode_consultar(http_fin, cliente, bank):
    path = f'/api/v1/sicredi-pix/clientes/{cliente.id}'
    assert http_fin.get(path).status_code == 200


def test_operacional_nao_gerencia_autorizacoes(http_op, cliente, bank):
    path = f'/api/v1/sicredi-pix/clientes/{cliente.id}'
    assert http_op.get(path).status_code == 403


def test_cadastro_com_adesao_ativa_exige_cancelar_antes_de_mudar_forma(http, rec, cliente):
    assert http.put(f'/api/v1/clients/{cliente.id}', json={'forma_cobranca': 'cartao_credito'}).status_code == 409
    assert http.delete(f'/api/v1/clients/{cliente.id}').status_code == 409
    assert http.put(f'/api/v1/clients/{cliente.id}', json={'billing_day': 20}).status_code == 409


def test_cancelamento_da_fila_sem_envio_libera_cobranca(db, cliente, rec, bank):
    billing, charge = make_charge(db, cliente, rec)
    svc.cancelar_cobranca(db, charge)
    assert charge.status == 'CANCELADA' and bank.calls == []
    assert titulo_bancario.titulos_bancarios(db, [billing.id])[billing.id].estado == titulo_bancario.SEM_TITULO


def test_financeiro_exibe_canal_pix_sem_marcar_boleto_emitido(http, db, cliente, rec, bank):
    billing, _ = make_charge(db, cliente, rec)
    response = http.get(f'/api/v1/billings/{billing.id}')
    assert response.status_code == 200
    assert response.json()['titulo_bancario']['canal'] == 'sicredi_pix'
    assert response.json()['boleto_ailos'] is False


def test_sem_autorizacao_nao_libera_emissao_de_boleto(db, cliente, bank):
    cliente.forma_cobranca = 'pix_automatico'
    billing = Billing(client_id=cliente.id, billing_type='avulsa', amount=Decimal('64.99'),
                      due_date=date(2026, 11, 15), status=BillingStatus.PENDING)
    db.add(billing); db.flush()
    svc.enfileirar_fechamento(db, [billing.id]); db.commit()
    assert billing.payment_method == 'pix_automatico'
    assert not db.scalar(select(SicrediPixCobranca))
    with pytest.raises(titulo_bancario.PoliticaBancariaError, match='Pix'):
        titulo_bancario.exigir(db, titulo_bancario.EMITIR_AILOS, [billing.id])


def test_fechamento_confere_agendamento_pix_sem_exigir_boleto(db, cliente, rec, bank):
    from app.models.closure_job import ClosureJob
    from app.services.closure_delivery import conferir_lote
    cliente.issue_invoice = 'nao'
    billing, charge = make_charge(db, cliente, rec)
    batch = ClosureJob(reference_month='2026-10', status='completed', result={'payment_billing_ids': [billing.id]})
    db.add(batch); db.commit()
    item = conferir_lote(db, batch.id)['itens'][0]
    assert item['estado'] == 'bloqueado' and 'Pix Automático' in item['motivo']
    assert not item['pode_emitir_boleto'] and item['documentos'] == ['Pix Automático']
    charge.status = 'ATIVA'; charge.enviada_em = NOW; db.commit()
    item = conferir_lote(db, batch.id)['itens'][0]
    assert item['estado'] == 'pronto' and item['pix_agendado']
    assert not item['boleto_emitido']


def test_prepara_aviso_pix_sem_criar_boleto_ou_enviar_email(db, cliente, rec, bank):
    from app.api.v1.endpoints.boletos import preparar_envio_boleto_email
    billing, charge = make_charge(db, cliente, rec)
    charge.status = 'ATIVA'; charge.enviada_em = NOW; db.commit()
    recipient, subject, body, attachments = preparar_envio_boleto_email(db, billing.id, False)
    assert recipient == cliente.email
    assert 'Pix Automático' in subject and 'agendada' in body
    assert attachments == [] and bank.calls == []


def test_estorno_local_nao_e_quitado_silenciosamente_outra_vez(db, cliente, rec, bank):
    billing, charge = make_charge(db, cliente, rec)
    bank.charges[charge.txid] = {'idRec': REC_ID, 'txid': charge.txid, 'status': 'CONCLUIDA', 'pix': [payment(charge)]}
    svc.consultar_cobranca(db, charge)
    billing.status = BillingStatus.PENDING; db.commit()
    svc.consultar_cobranca(db, charge)
    assert billing.status == BillingStatus.PENDING
    assert 'estorno' in charge.pendencia


def test_worker_em_leitura_nao_emite_nem_processa_fila_nao_enviada(db, cliente, rec, bank, monkeypatch):
    _, charge = make_charge(db, cliente, rec)
    monkeypatch.setattr(settings, 'sicredi_pix_escrita_habilitada', False)
    svc.processar_fila(db)
    assert bank.count('PUT') == 0 and bank.count('POST') == 0
    assert charge.enviada_em is None


def test_segredo_webhook_nao_aparece_nos_logs():
    import logging
    from app.core.pix_webhook_logging import PixWebhookLogFilter
    secret = 'A' * 48
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 1, '%s %s',
        ('POST', '/api/v1/sicredi-pix/webhooks/' + secret + '/pix'), None)
    PixWebhookLogFilter().filter(record)
    assert secret not in record.getMessage() and '[redigido]' in record.getMessage()


@pytest.mark.parametrize('start,due,expected', [
    (date(2026, 1, 31), date(2026, 2, 28), date(2026, 2, 28)),
    (date(2026, 1, 31), date(2026, 3, 1), date(2026, 2, 28)),
    (date(2026, 1, 15), date(2026, 3, 14), date(2026, 2, 15)),
])
def test_ciclo_segue_ancora_da_autorizacao(start, due, expected):
    assert svc.ciclo_mensal(start, due) == expected
