"""Testes da emissão de NFS-e em lote (a partir de um fechamento financeiro)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.ailos_boleto import AilosBoleto
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus, UserRole
from app.models.nfse_lote import NfseLote
from app.models.nfse_nota import NfseNota
from app.models.plan import Plan
from app.services import nfse_lote


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client(db, nome, *, emitir='sim', doc='11144477735', tipo='pf', deleted=False):
    c = Client(
        name=nome, cpf_cnpj=doc, type=tipo, status=ClientStatus.ACTIVE,
        issue_invoice=emitir, is_deleted=deleted,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _billing(db, client, *, period='07/2026', amount='120.00', deleted=False):
    b = Billing(
        client_id=client.id, amount=Decimal(amount), due_date=date(2026, 7, 10),
        status=BillingStatus.PENDING, period_label=period, title='MENSALIDADE',
        billing_type='recorrente', is_deleted=deleted,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _nota(db, billing, *, status, lote_id=None):
    n = NfseNota(billing_id=billing.id, status=status, lote_id=lote_id)
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def _billing_com_interveniente(db, owner, interveniente, *, period='07/2026'):
    """Billing recorrente cujo contrato aponta um interveniente financeiro —
    que passa a ser o tomador da NFS-e."""
    plan = Plan(name=f'PLANO {owner.id}-{interveniente.id}', price=Decimal('100.00'))
    db.add(plan)
    db.commit()
    db.refresh(plan)
    contrato = Contract(
        client_id=owner.id, plan_id=plan.id,
        interveniente_client_id=interveniente.id,
        start_date=date(2024, 1, 1), status='ativo', billing_day=10,
    )
    db.add(contrato)
    db.commit()
    db.refresh(contrato)
    b = Billing(
        client_id=owner.id, contract_id=contrato.id, amount=Decimal('120.00'),
        due_date=date(2026, 7, 10), status=BillingStatus.PENDING, period_label=period,
        title='MENSALIDADE', billing_type='recorrente',
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


# ---------------------------------------------------------------------------
# Interveniente financeiro = tomador da NFS-e
# ---------------------------------------------------------------------------

def test_elegiveis_tomador_e_o_interveniente(db):
    owner = _client(db, 'DONO VEICULO', emitir='sim', doc='111')
    interv = _client(db, 'FINANCEIRA XPTO', emitir='sim', doc='222')
    _billing_com_interveniente(db, owner, interv)

    item = nfse_lote.listar_elegiveis(db, '07/2026')['itens'][0]
    assert item['tomador'] == 'FINANCEIRA XPTO'
    assert item['cpf_cnpj'] == '222'


def test_elegiveis_segue_issue_invoice_do_interveniente(db):
    # Dono NÃO emite, mas o interveniente (tomador) SIM → elegível.
    owner = _client(db, 'DONO', emitir='nao', doc='111')
    interv = _client(db, 'FINANCEIRA', emitir='sim', doc='222')
    _billing_com_interveniente(db, owner, interv)

    res = nfse_lote.listar_elegiveis(db, '07/2026')
    assert res['total_elegiveis'] == 1
    assert res['itens'][0]['tomador'] == 'FINANCEIRA'


def test_elegiveis_exclui_quando_interveniente_nao_emite(db):
    # Dono emite, mas o interveniente (tomador) NÃO → fora do lote.
    owner = _client(db, 'DONO', emitir='sim', doc='111')
    interv = _client(db, 'FINANCEIRA', emitir='nao', doc='222')
    _billing_com_interveniente(db, owner, interv)

    assert nfse_lote.listar_elegiveis(db, '07/2026')['total_elegiveis'] == 0


def test_emitir_uma_usa_interveniente_como_tomador(db):
    owner = _client(db, 'DONO', emitir='sim', doc='111')
    interv = _client(db, 'FINANCEIRA', emitir='sim', doc='222')
    b = _billing_com_interveniente(db, owner, interv)
    lote = nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)
    nota = db.query(NfseNota).filter_by(lote_id=lote.id).first()

    capturado = {}

    def fake(db_, billing_, client_, cod_trib_nacional=None, **kwargs):
        capturado['tomador'] = client_.name
        n = db_.query(NfseNota).filter_by(billing_id=billing_.id).first()
        n.status = 'emitida'
        db_.commit()

    nfse_lote._emitir_uma(db, nota, fake)
    assert capturado['tomador'] == 'FINANCEIRA'


# ---------------------------------------------------------------------------
# Elegibilidade
# ---------------------------------------------------------------------------

def test_elegiveis_apenas_clientes_com_emitir_nf_sim(db):
    c_sim = _client(db, 'COM NF', emitir='sim', doc='111')
    c_nao = _client(db, 'SEM NF', emitir='nao', doc='222')
    c_nulo = _client(db, 'NULO', emitir=None, doc='333')
    _billing(db, c_sim)
    _billing(db, c_nao)
    _billing(db, c_nulo)

    res = nfse_lote.listar_elegiveis(db, '07/2026')
    nomes = {i['tomador'] for i in res['itens']}
    assert nomes == {'COM NF'}
    assert res['total_elegiveis'] == 1
    assert res['sem_configuracao'] == [{'client_id': c_nulo.id, 'nome': 'NULO'}]


def test_elegiveis_filtra_por_lote_de_fechamento(db):
    c = _client(db, 'CLIENTE')
    _billing(db, c, period='07/2026')
    _billing(db, c, period='08/2026')

    res = nfse_lote.listar_elegiveis(db, '07/2026')
    assert res['total_elegiveis'] == 1


def test_lote_servico_setembro_inclui_fechamento_individual_vencendo_outubro(db):
    c = _client(db, 'CLIENTE DO FECHAMENTO')
    individual = _billing(db, c, period='10/2026')
    individual.due_date = date(2026, 10, 10)
    individual.notes = 'Fechamento — 10/2026'
    manual = _billing(db, c, period='10/2026')
    manual.due_date = date(2026, 10, 10)
    manual.notes = 'Cobrança criada manualmente'
    db.commit()

    ids = {item['billing_id'] for item in nfse_lote.listar_elegiveis(db, '09/2026')['itens']}
    assert ids == {individual.id}
    lote = nfse_lote.criar_lote(db, '09/2026', [individual.id], emitir_async=False)
    assert lote.total_notas == 1


def test_elegiveis_ignora_cobranca_deletada(db):
    c = _client(db, 'CLIENTE')
    _billing(db, c, deleted=True)
    assert nfse_lote.listar_elegiveis(db, '07/2026')['total_elegiveis'] == 0


def test_elegiveis_ignora_cobranca_cancelada(db):
    # Cobrança cancelada (ex.: original consolidada em boleto único) não pode
    # gerar NFS-e — o serviço não foi efetivamente faturado nela.
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    b.status = BillingStatus.CANCELED
    db.commit()
    assert nfse_lote.listar_elegiveis(db, '07/2026')['total_elegiveis'] == 0


def test_criar_lote_ignora_cobranca_cancelada(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    b.status = BillingStatus.CANCELED
    db.commit()
    with pytest.raises(nfse_lote.LoteError):
        nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)


def test_idempotencia_nota_emitida_nao_reaparece(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    _nota(db, b, status='emitida')

    res = nfse_lote.listar_elegiveis(db, '07/2026')
    assert res['total_elegiveis'] == 0
    assert res['ja_emitidas'] == 1


@pytest.mark.parametrize('status_em_voo', ['pending', 'processing'])
def test_idempotencia_nota_em_voo_nao_reaparece(db, status_em_voo):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    _nota(db, b, status=status_em_voo)
    assert nfse_lote.listar_elegiveis(db, '07/2026')['total_elegiveis'] == 0


def test_nota_com_erro_reaparece_para_reprocessamento(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    nota = _nota(db, b, status='erro')
    nota.erro_tipo = 'local'
    db.commit()

    res = nfse_lote.listar_elegiveis(db, '07/2026')
    assert res['total_elegiveis'] == 1
    assert res['itens'][0]['reprocessamento'] is True


# ---------------------------------------------------------------------------
# Criação do lote (transacional)
# ---------------------------------------------------------------------------

def test_criar_lote_cria_notas_pendentes_vinculadas(db):
    c1 = _client(db, 'C1', doc='111')
    c2 = _client(db, 'C2', doc='222')
    b1 = _billing(db, c1)
    b2 = _billing(db, c2)

    lote = nfse_lote.criar_lote(
        db, '07/2026', [b1.id, b2.id],
        competencia=date(2026, 7, 1), codigo_servico='11.02',
        discriminacao='Monitoramento', criado_por=9, emitir_async=False,
    )

    assert lote.status == 'processando'
    assert lote.total_notas == 2
    notas = db.query(NfseNota).filter(NfseNota.lote_id == lote.id).all()
    assert len(notas) == 2
    assert all(n.status == 'pending' for n in notas)


def test_criar_lote_ignora_ids_nao_elegiveis(db):
    c_sim = _client(db, 'SIM', emitir='sim', doc='111')
    c_nao = _client(db, 'NAO', emitir='nao', doc='222')
    b_sim = _billing(db, c_sim)
    b_nao = _billing(db, c_nao)

    lote = nfse_lote.criar_lote(db, '07/2026', [b_sim.id, b_nao.id], emitir_async=False)
    assert lote.total_notas == 1


def test_worker_recusa_nota_se_tomador_desativou_emissao_apos_selecao(db):
    client = _client(db, 'MUDOU CADASTRO')
    billing = _billing(db, client)
    lote = nfse_lote.criar_lote(db, '07/2026', [billing.id], emitir_async=False)
    nota = db.query(NfseNota).filter_by(lote_id=lote.id).one()
    client.issue_invoice = 'nao'
    db.commit()
    chamadas = []

    nfse_lote._emitir_uma(db, nota, lambda *args, **kwargs: chamadas.append(True))

    db.refresh(nota)
    assert nota.status == 'erro'
    assert chamadas == []


def test_criar_lote_sem_selecao_falha(db):
    with pytest.raises(nfse_lote.LoteError, match='Nenhuma cobrança'):
        nfse_lote.criar_lote(db, '07/2026', [], emitir_async=False)


def test_criar_lote_tudo_ja_emitido_avisa_lote_processado(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    _nota(db, b, status='emitida')

    with pytest.raises(nfse_lote.LoteError, match='já processado|Nenhum registro'):
        nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)


def test_recuperar_notas_orfas_expiradas_exige_reconciliacao(db):
    # Simula um restart: notas ficaram presas em 'pending'/'processing'.
    c = _client(db, 'CLIENTE')
    b1, b2, b3 = _billing(db, c), _billing(db, c), _billing(db, c)
    lote = nfse_lote.criar_lote(db, '07/2026', [b1.id, b2.id, b3.id], emitir_async=False)
    notas = db.query(NfseNota).filter_by(lote_id=lote.id).order_by(NfseNota.id).all()
    notas[0].status = 'emitida'      # já saiu antes do restart
    notas[1].status = 'pending'      # órfã
    notas[2].status = 'processing'   # órfã
    from datetime import datetime, timedelta, timezone
    for nota in notas[1:]:
        nota.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.commit()

    recuperadas = nfse_lote.recuperar_notas_orfas(db)
    assert recuperadas == 2
    for n in notas:
        db.refresh(n)
    assert notas[0].status == 'emitida'                 # intacta
    assert notas[1].status == 'desconhecido' and notas[2].status == 'desconhecido'
    assert 'Lease expirada' in (notas[1].erro_mensagem or '')
    # Consulta fiscal ainda necessária, sem conclusão fictícia.
    db.refresh(lote)
    assert lote.status == 'processando'
    assert lote.concluido_em is None


def test_recuperar_notas_orfas_sem_orfas_retorna_zero(db):
    c = _client(db, 'CLIENTE')
    _nota(db, _billing(db, c), status='emitida')
    assert nfse_lote.recuperar_notas_orfas(db) == 0


def test_criar_lote_reaproveita_nota_de_erro(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    nota_antiga = _nota(db, b, status='erro')
    nota_antiga.erro_tipo = 'local'
    db.commit()

    lote = nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)
    db.refresh(nota_antiga)
    # mesma linha (billing_id é único), agora vinculada ao lote e rependente
    assert nota_antiga.lote_id == lote.id
    assert nota_antiga.status == 'pending'
    assert db.query(NfseNota).filter_by(billing_id=b.id).count() == 1


# ---------------------------------------------------------------------------
# Emissão de cada nota (worker) + fechamento
# ---------------------------------------------------------------------------

def test_emitir_uma_sucesso_marca_emitida(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    lote = nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)
    nota = db.query(NfseNota).filter_by(lote_id=lote.id).first()

    def fake_ok(db_, billing_, client_, cod_trib_nacional=None, **kwargs):
        n = db_.query(NfseNota).filter_by(billing_id=billing_.id).first()
        n.status = 'emitida'
        n.numero_nfse = '2026000001'
        db_.commit()

    nfse_lote._emitir_uma(db, nota, fake_ok)
    db.refresh(nota)
    assert nota.status == 'emitida'
    assert nota.numero_nfse == '2026000001'


def test_emitir_uma_repassa_codigo_de_tributacao(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    lote = nfse_lote.criar_lote(db, '07/2026', [b.id], codigo_servico='150307',
                                emitir_async=False)
    nota = db.query(NfseNota).filter_by(lote_id=lote.id).first()

    recebido = {}

    def fake(db_, billing_, client_, cod_trib_nacional=None, **kwargs):
        recebido['cod'] = cod_trib_nacional
        n = db_.query(NfseNota).filter_by(billing_id=billing_.id).first()
        n.status = 'emitida'
        db_.commit()

    # simula o worker: passa o código do lote
    nfse_lote._emitir_uma(db, nota, fake, lote.codigo_servico)
    assert recebido['cod'] == '150307'


def test_emitir_uma_erro_marca_erro_com_mensagem(db):
    from app.services.nfse_nacional import NfseError

    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    lote = nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)
    nota = db.query(NfseNota).filter_by(lote_id=lote.id).first()

    def fake_falha(db_, billing_, client_, cod_trib_nacional=None, **kwargs):
        raise NfseError('Certificado digital obrigatório para o Emissor Nacional.')

    nfse_lote._emitir_uma(db, nota, fake_falha)
    db.refresh(nota)
    assert nota.status == 'erro'
    assert 'Certificado' in nota.erro_mensagem


def test_fechar_lote_deriva_situacao_dos_contadores(db):
    c1 = _client(db, 'C1', doc='111')
    c2 = _client(db, 'C2', doc='222')
    b1, b2 = _billing(db, c1), _billing(db, c2)
    lote = nfse_lote.criar_lote(db, '07/2026', [b1.id, b2.id], emitir_async=False)
    notas = db.query(NfseNota).filter_by(lote_id=lote.id).order_by(NfseNota.id).all()
    notas[0].status = 'emitida'
    notas[1].status = 'erro'
    notas[1].erro_tipo = 'rejeicao'
    db.commit()

    nfse_lote._fechar_lote(db, lote.id)
    db.refresh(lote)
    assert lote.status == 'com_erro'
    assert lote.total_autorizadas == 1
    assert lote.total_erro == 1
    assert lote.concluido_em is not None


def test_fechar_lote_tudo_ok_fica_concluido(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    lote = nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)
    db.query(NfseNota).filter_by(lote_id=lote.id).first().status = 'emitida'
    db.commit()

    nfse_lote._fechar_lote(db, lote.id)
    db.refresh(lote)
    assert lote.status == 'concluido'
    assert lote.total_erro == 0


# ---------------------------------------------------------------------------
# Drill-down
# ---------------------------------------------------------------------------

def test_consultar_lote_traz_status_individual(db):
    c = _client(db, 'TOMADOR X')
    b = _billing(db, c, amount='89.90')
    lote = nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)

    detalhe = nfse_lote.consultar_lote(db, lote.id)
    assert detalhe['id'] == lote.id
    assert len(detalhe['itens']) == 1
    item = detalhe['itens'][0]
    assert item['tomador'] == 'TOMADOR X'
    assert item['valor'] == 89.90
    assert item['status'] == 'pending'


def test_consultar_lote_inexistente_retorna_none(db):
    assert nfse_lote.consultar_lote(db, 9999) is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_endpoint_elegiveis(db, http_fin):
    c = _client(db, 'CLIENTE API')
    _billing(db, c)
    r = http_fin.get('/api/v1/nfse/lotes/elegiveis', params={'period_label': '07/2026'})
    assert r.status_code == 200
    assert r.json()['total_elegiveis'] == 1


def test_endpoint_emitir_lote_cria_e_retorna(db, http_fin, monkeypatch):
    # Não dispara thread real: substitui o worker por no-op.
    monkeypatch.setattr(nfse_lote, '_emitir_lote_worker', lambda lote_id: None)
    c = _client(db, 'CLIENTE API')
    b = _billing(db, c)
    r = http_fin.post('/api/v1/nfse/lotes', json={
        'period_label': '07/2026', 'billing_ids': [b.id], 'codigo_servico': '11.02',
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body['total_notas'] == 1
    assert body['status'] == 'processando'


def test_endpoint_selecionados_emite_apenas_tomadores_sim_em_qualquer_periodo(db, http_fin, monkeypatch):
    monkeypatch.setattr(nfse_lote, '_emitir_lote_worker', lambda lote_id: None)
    sim_julho = _billing(db, _client(db, 'JULHO', doc='111'), period='07/2026')
    sim_agosto = _billing(db, _client(db, 'AGOSTO', doc='222'), period='08/2026')
    sim_avulsa = _billing(db, _client(db, 'AVULSA', doc='333'), period=None)
    nao = _billing(db, _client(db, 'NAO', emitir='nao', doc='444'))
    vazio = _billing(db, _client(db, 'VAZIO', emitir=None, doc='555'))
    ja_emitida = _billing(db, _client(db, 'JA EMITIDA', doc='666'))
    _nota(db, ja_emitida, status='emitida')

    selecionados = [sim_julho.id, sim_agosto.id, sim_avulsa.id, nao.id, vazio.id, ja_emitida.id]
    previa = http_fin.post('/api/v1/nfse/lotes/selecionados/previa', json={'billing_ids': selecionados})
    assert previa.status_code == 200
    assert previa.json()['elegiveis'] == sorted([sim_julho.id, sim_agosto.id, sim_avulsa.id])
    assert previa.json()['nao_emitem'] == [
        {'billing_id': nao.id, 'cliente': 'NAO'},
        {'billing_id': vazio.id, 'cliente': 'VAZIO'},
    ]
    assert previa.json()['outros_ignorados'] == [ja_emitida.id]
    resp = http_fin.post('/api/v1/nfse/lotes/selecionados', json={'billing_ids': selecionados})

    assert resp.status_code == 201, resp.text
    assert resp.json()['lote']['total_notas'] == 3
    assert resp.json()['ignorados'] == sorted([nao.id, vazio.id, ja_emitida.id])
    lote_id = resp.json()['lote']['id']
    assert {n.billing_id for n in db.query(NfseNota).filter_by(lote_id=lote_id)} == {
        sim_julho.id, sim_agosto.id, sim_avulsa.id,
    }


def test_emissao_individual_recusa_tomador_sem_sim(db, http_fin, monkeypatch):
    from app.services import nfse_provider

    nao = _billing(db, _client(db, 'SEM NOTA', emitir='nao'))
    chamado = []
    monkeypatch.setattr(nfse_provider, 'emitir_nfse', lambda *args, **kwargs: chamado.append(True))

    resp = http_fin.post(f'/api/v1/nfse/emitir/{nao.id}')

    assert resp.status_code == 409
    assert 'Sim' in resp.json()['detail']
    assert chamado == []


def test_endpoint_selecionados_sem_cliente_sim_nao_cria_lote(db, http_fin):
    nao = _billing(db, _client(db, 'SEM NOTA', emitir='nao'))

    resp = http_fin.post('/api/v1/nfse/lotes/selecionados', json={'billing_ids': [nao.id]})

    assert resp.status_code == 422
    assert db.query(NfseLote).count() == 0
    assert db.query(NfseNota).count() == 0


def test_envio_separado_de_nfse_usa_tomador_e_danfse_emitida(db, http_fin, monkeypatch):
    client = _client(db, 'DESTINATARIO NF')
    client.email = 'nota@example.com'
    billing = _billing(db, client)
    db.commit()
    _nota(db, billing, status='emitida')
    from app.api.v1.endpoints import nfse
    from app.services import email_smtp
    monkeypatch.setattr(nfse, '_danfse_local_bytes', lambda *args: b'%PDF-nota')
    enviados = []
    monkeypatch.setattr(email_smtp, 'enviar_email', lambda *args, **kwargs: enviados.append(kwargs))

    resp = http_fin.post(f'/api/v1/nfse/{billing.id}/enviar-email')

    assert resp.status_code == 200, resp.text
    assert enviados[0]['destinatario'] == 'nota@example.com'
    assert enviados[0]['anexo'][1] == b'%PDF-nota'
    assert enviados[0]['anexo'][0] == 'Notafical_DESTINATARIO_NF_07-2026.pdf'


def test_nome_nfse_usa_responsavel_e_mes_do_servico(db, http_fin, monkeypatch):
    owner = _client(db, 'DONO', doc='111')
    payer = _client(db, 'JOÃO & CIA LTDA', doc='222')
    billing = _billing_com_interveniente(db, owner, payer, period='09/2026')
    nota = _nota(db, billing, status='emitida')
    nota.xml_retorno = '<NFSe/>'
    db.commit()
    from app.api.v1.endpoints import nfse
    monkeypatch.setattr(nfse, '_danfse_local_bytes', lambda *args: b'%PDF-nota')

    resposta = http_fin.get(f'/api/v1/nfse/{billing.id}/danfse-local')

    assert resposta.status_code == 200
    assert 'Notafical_JOAO_CIA_LTDA_09-2026.pdf' in resposta.headers['content-disposition']


def test_carteira_exibe_estados_reais_do_boleto_e_da_nfse(db, http_fin):
    billing = _billing(db, _client(db, 'CARTEIRA'))
    antes = http_fin.get('/api/v1/billings/')
    assert antes.status_code == 200
    linha = next(item for item in antes.json() if item['id'] == billing.id)
    assert linha['boleto_ailos'] is False
    assert linha['nfse_status'] is None

    db.add(AilosBoleto(
        billing_id=billing.id, numero_convenio='102004', nosso_numero='000000301',
        linha_digitavel='123', codigo_barras='456',
    ))
    _nota(db, billing, status='emitida')
    depois = http_fin.get('/api/v1/billings/')

    assert depois.status_code == 200
    linha = next(item for item in depois.json() if item['id'] == billing.id)
    assert linha['boleto_ailos'] is True
    assert linha['nfse_status'] == 'emitida'


def test_carteira_filtra_combinacoes_de_boleto_e_nfse_antes_do_limite(db, http_fin):
    client = _client(db, 'FILTRO EMISSAO')
    sem_ambos = _billing(db, client)
    so_boleto = _billing(db, client)
    so_nfse = _billing(db, client)
    com_ambos = _billing(db, client)
    nfse_com_erro = _billing(db, client)
    for billing in (so_boleto, com_ambos):
        db.add(AilosBoleto(
            billing_id=billing.id, numero_convenio='102004',
            nosso_numero=str(billing.id), linha_digitavel='123', codigo_barras='456',
        ))
    db.commit()
    _nota(db, so_nfse, status='emitida')
    _nota(db, com_ambos, status='emitida')
    _nota(db, nfse_com_erro, status='erro')

    combinacoes = (
        ({'boleto_emitido': True, 'nfse_emitida': True}, {com_ambos.id}),
        ({'boleto_emitido': False, 'nfse_emitida': False}, {sem_ambos.id, nfse_com_erro.id}),
        ({'boleto_emitido': True, 'nfse_emitida': False}, {so_boleto.id}),
        ({'boleto_emitido': False, 'nfse_emitida': True}, {so_nfse.id}),
    )
    for filtros, esperados in combinacoes:
        resp = http_fin.get('/api/v1/billings/', params=filtros)
        assert resp.status_code == 200, resp.text
        assert {item['id'] for item in resp.json()} == esperados

    limitado = http_fin.get('/api/v1/billings/', params={
        'boleto_emitido': True, 'nfse_emitida': True, 'limit': 1,
    })
    assert [item['id'] for item in limitado.json()] == [com_ambos.id]


def test_envio_separado_recusa_nfse_ainda_nao_emitida(db, http_fin, monkeypatch):
    client = _client(db, 'SEM NOTA EMITIDA')
    client.email = 'nota@example.com'
    billing = _billing(db, client)
    db.commit()
    from app.services import email_smtp
    enviados = []
    monkeypatch.setattr(email_smtp, 'enviar_email', lambda *args, **kwargs: enviados.append(kwargs))

    resp = http_fin.post(f'/api/v1/nfse/{billing.id}/enviar-email')

    assert resp.status_code == 409
    assert enviados == []


def test_endpoint_emitir_lote_sem_elegiveis_retorna_422(db, http_fin):
    c = _client(db, 'SEM NF', emitir='nao')
    b = _billing(db, c)
    r = http_fin.post('/api/v1/nfse/lotes', json={
        'period_label': '07/2026', 'billing_ids': [b.id],
    })
    assert r.status_code == 422


def test_endpoint_listar_e_detalhar_lote(db, http_fin):
    c = _client(db, 'CLIENTE API')
    b = _billing(db, c)
    lote = nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)

    r = http_fin.get('/api/v1/nfse/lotes')
    assert r.status_code == 200
    assert any(l['id'] == lote.id for l in r.json())

    r = http_fin.get(f'/api/v1/nfse/lotes/{lote.id}')
    assert r.status_code == 200
    assert r.json()['itens'][0]['billing_id'] == b.id


def test_endpoint_lote_exige_perfil_autorizado(db, http_cliente):
    r = http_cliente.get('/api/v1/nfse/lotes/elegiveis', params={'period_label': '07/2026'})
    assert r.status_code == 403


def test_endpoint_emitir_recusa_cobranca_cancelada(db, http_fin):
    c = _client(db, 'CLIENTE API')
    b = _billing(db, c)
    b.status = BillingStatus.CANCELED
    db.commit()
    r = http_fin.post(f'/api/v1/nfse/emitir/{b.id}')
    assert r.status_code == 400
    assert 'cancelada' in r.json()['detail'].lower()


# ---------------------------------------------------------------------------
# Filtros na conferência (nome / tipo de pessoa)
# ---------------------------------------------------------------------------

def test_elegiveis_filtra_por_nome_ou_documento(db):
    a = _client(db, 'TRANSPORTES ALFA', doc='111')
    b = _client(db, 'BETA COMERCIO', doc='222')
    _billing(db, a)
    _billing(db, b)

    assert [i['tomador'] for i in
            nfse_lote.listar_elegiveis(db, '07/2026', busca='alfa')['itens']] == ['TRANSPORTES ALFA']
    assert [i['tomador'] for i in
            nfse_lote.listar_elegiveis(db, '07/2026', busca='222')['itens']] == ['BETA COMERCIO']


def test_elegiveis_filtra_por_tipo_de_pessoa(db):
    pf = _client(db, 'PESSOA FISICA', doc='111', tipo='pf')
    pj = _client(db, 'EMPRESA LTDA', doc='222', tipo='pj')
    _billing(db, pf)
    _billing(db, pj)

    assert [i['tomador'] for i in
            nfse_lote.listar_elegiveis(db, '07/2026', tipo='pj')['itens']] == ['EMPRESA LTDA']


def test_elegiveis_traz_nosso_numero_do_boleto(db):
    from app.models.ailos_boleto import AilosBoleto

    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    db.add(AilosBoleto(billing_id=b.id, numero_convenio='102004', nosso_numero='2587'))
    db.commit()

    assert nfse_lote.listar_elegiveis(db, '07/2026')['itens'][0]['nosso_numero'] == '2587'


# ---------------------------------------------------------------------------
# Listagem geral de notas + balanço
# ---------------------------------------------------------------------------

def test_listar_notas_pagina_e_conta_o_total(db):
    c = _client(db, 'CLIENTE')
    for _ in range(3):
        _nota(db, _billing(db, c), status='emitida')

    pagina = nfse_lote.listar_notas(db, limit=2, offset=0)
    assert pagina['total'] == 3
    assert len(pagina['itens']) == 2
    assert nfse_lote.listar_notas(db, limit=2, offset=2)['total'] == 3


def test_listar_notas_filtra_por_situacao_e_busca(db):
    ok = _client(db, 'CLIENTE OK', doc='111')
    ruim = _client(db, 'CLIENTE RUIM', doc='222')
    _nota(db, _billing(db, ok), status='emitida')
    _nota(db, _billing(db, ruim), status='erro')

    assert nfse_lote.listar_notas(db, situacao='erro')['total'] == 1
    assert nfse_lote.listar_notas(db, busca='OK')['itens'][0]['tomador'] == 'CLIENTE OK'


def test_listar_notas_expoe_contexto_da_cobranca(db):
    c = _client(db, 'TOMADOR X')
    b = _billing(db, c, amount='89.90')
    nota = _nota(db, b, status='emitida')
    nota.numero_nfse = '7'
    nota.xml_retorno = '<NFSe/>'
    db.commit()

    item = nfse_lote.listar_notas(db)['itens'][0]
    assert item['tomador'] == 'TOMADOR X'
    assert item['valor'] == 89.90
    assert item['numero_nfse'] == '7'
    assert item['tem_xml'] is True


def test_resumo_conta_autorizadas_e_negadas_do_mes(db):
    c = _client(db, 'CLIENTE')
    _nota(db, _billing(db, c), status='emitida')
    _nota(db, _billing(db, c), status='erro')
    _nota(db, _billing(db, c), status='pending')

    r = nfse_lote.resumo(db)
    assert (r['autorizadas'], r['negadas'], r['processando']) == (1, 1, 1)
    assert r['total'] == 3


# ---------------------------------------------------------------------------
# Endpoints do painel
# ---------------------------------------------------------------------------

def test_endpoint_resumo(db, http_fin):
    c = _client(db, 'CLIENTE')
    _nota(db, _billing(db, c), status='emitida')
    r = http_fin.get('/api/v1/nfse/resumo')
    assert r.status_code == 200
    assert r.json()['autorizadas'] == 1


def test_endpoint_notas(db, http_fin):
    c = _client(db, 'CLIENTE API')
    _nota(db, _billing(db, c), status='emitida')
    r = http_fin.get('/api/v1/nfse/notas', params={'limit': 10})
    assert r.status_code == 200
    assert r.json()['total'] == 1


def test_endpoint_xml_devolve_o_documento(db, http_fin):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    nota = _nota(db, b, status='emitida')
    nota.xml_retorno = '<NFSe versao="1.01"/>'
    db.commit()

    r = http_fin.get(f'/api/v1/nfse/{b.id}/xml')
    assert r.status_code == 200
    assert r.headers['content-type'].startswith('application/xml')
    assert '<NFSe' in r.text


def test_endpoint_xml_404_quando_nao_ha_nota(db, http_fin):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    assert http_fin.get(f'/api/v1/nfse/{b.id}/xml').status_code == 404
