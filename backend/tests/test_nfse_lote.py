"""Testes da emissão de NFS-e em lote (a partir de um fechamento financeiro)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
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

    def fake(db_, billing_, client_, cod_trib_nacional=None):
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


def test_elegiveis_filtra_por_lote_de_fechamento(db):
    c = _client(db, 'CLIENTE')
    _billing(db, c, period='07/2026')
    _billing(db, c, period='08/2026')

    res = nfse_lote.listar_elegiveis(db, '07/2026')
    assert res['total_elegiveis'] == 1


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
    _nota(db, b, status='erro')

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


def test_criar_lote_sem_selecao_falha(db):
    with pytest.raises(nfse_lote.LoteError, match='Nenhuma cobrança'):
        nfse_lote.criar_lote(db, '07/2026', [], emitir_async=False)


def test_criar_lote_tudo_ja_emitido_avisa_lote_processado(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    _nota(db, b, status='emitida')

    with pytest.raises(nfse_lote.LoteError, match='já processado|Nenhum registro'):
        nfse_lote.criar_lote(db, '07/2026', [b.id], emitir_async=False)


def test_criar_lote_reaproveita_nota_de_erro(db):
    c = _client(db, 'CLIENTE')
    b = _billing(db, c)
    nota_antiga = _nota(db, b, status='erro')

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

    def fake_ok(db_, billing_, client_, cod_trib_nacional=None):
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

    def fake(db_, billing_, client_, cod_trib_nacional=None):
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

    def fake_falha(db_, billing_, client_, cod_trib_nacional=None):
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
