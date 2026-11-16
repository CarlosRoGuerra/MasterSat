"""Automação do Pix Automático a partir do cadastro (respostas bancárias e SMTP controlados).

Nenhuma chamada real ao Sicredi nem envio real de e-mail. Cobre: cadastro →
autorização → convite → associação automática das cobranças do fechamento →
painel do Financeiro, preservando permissões e as regras de fila existentes.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import smtplib

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus
from app.models.sicredi_pix import SicrediPixCobranca, SicrediPixRecorrencia
from app.services import email_smtp, sicredi_pix as svc, sicredi_pix_convite as convite
from app.services.billing_closure import execute_closure
from app.services.sicredi_pix_client import PixResultadoIncerto
from app.services.sicredi_pix_painel import montar_painel, situacao_cobranca
from tests.test_sicredi_pix import E2E, NOW, REC_ID, Bank, bank, make_charge, payment, rec  # noqa: F401

pytestmark = pytest.mark.filterwarnings('ignore::DeprecationWarning')


class RecoveringBank(Bank):
    """Banco que aceita o POST /rec mas pode não responder; a consulta por
    vínculo recupera o mesmo idRec. Também pode omitir o QR na primeira consulta."""

    def __init__(self):
        super().__init__()
        self.contrato = None
        self.sem_qr = False

    def request(self, method, path, *, params=None, json=None):
        if method == 'POST' and path.endswith('/rec'):
            self.contrato = json['vinculo']['contrato']
        if method == 'GET' and path == '/api/v1/rec':
            self.calls.append((method, path, json, params))
            return {'recs': [{'idRec': REC_ID, 'vinculo': {'contrato': self.contrato}}]}
        if method == 'GET' and path.endswith('/rec/' + REC_ID) and self.sem_qr:
            self.calls.append((method, path, json, params))
            return {'idRec': REC_ID, 'status': self.rec_status}
        return super().request(method, path, params=params, json=json)


@pytest.fixture()
def recovering(monkeypatch, bank):
    fake = RecoveringBank()
    monkeypatch.setattr(svc, 'PixClient', fake)
    return fake


class Smtp:
    def __init__(self):
        self.sent = []
        self.error = None
        self.configured = True

    def config(self, db):
        return {'host': 'smtp.teste' if self.configured else '', 'port': 587, 'username': '', 'password': '',
                'password_set': False, 'from_email': 'cobranca@teste.local' if self.configured else '',
                'from_name': 'MasterSat', 'security': 'tls', 'enabled': True}

    def send(self, db, destinatario, assunto, corpo, html=None, anexo=None, config=None, anexos=None):
        if self.error:
            raise self.error
        self.sent.append({'to': destinatario, 'subject': assunto, 'body': corpo, 'html': html, 'anexos': anexos})


@pytest.fixture()
def smtp(monkeypatch):
    fake = Smtp()
    monkeypatch.setattr(email_smtp, 'load_config', fake.config)
    monkeypatch.setattr(email_smtp, 'enviar_email', fake.send)
    monkeypatch.setattr(convite, 'agora', lambda: NOW)
    return fake


def novo_cliente_payload(**extra):
    return {'name': 'Maria Pix', 'cpf_cnpj': '98765432100', 'type': 'pf', 'status': 'ativo',
            'email': 'maria@test.local', 'billing_day': 20, 'forma_cobranca': 'pix_automatico', **extra}


def autorizar(http, client_id, convite_=True, data_inicial='2026-11-20'):
    return http.post(f'/api/v1/sicredi-pix/clientes/{client_id}/autorizacao',
                     json={'data_inicial': data_inicial, 'enviar_convite': convite_})


# ── Cadastro ────────────────────────────────────────────────────────────────

def test_cliente_novo_com_pix_gera_autorizacao_e_envia_convite_uma_vez(http, db, bank, smtp):
    created = http.post('/api/v1/clients/', json=novo_cliente_payload())
    assert created.status_code == 200, created.text
    cid = created.json()['id']
    assert created.json()['boleto_format'] == 'unico' and created.json()['boleto_fee'] == 'nao'
    assert db.scalar(select(func.count()).select_from(SicrediPixRecorrencia)) == 0  # salvar não cria autorização

    response = autorizar(http, cid)
    assert response.status_code == 200, response.text
    rec = response.json()['recorrencia']
    assert rec['status'] == 'CRIADA' and rec['pix_copia_cola'] == '000201autorizacaoSicredi' and rec['qr_svg']
    assert rec['convite_status'] == 'enviado' and rec['convite_tentativas'] == 1
    assert rec['convite_destino'] == 'maria@test.local'
    assert response.json()['smtp_configurado'] is True
    assert len(smtp.sent) == 1
    mail = smtp.sent[0]
    assert '000201autorizacaoSicredi' in mail['body'] and mail['anexos'][0][2] == 'image/png'
    assert mail['anexos'][0][1].startswith(b'\x89PNG')
    for segredo in ('98765432100', REC_ID, 'segredo-teste', 'id-teste', 'A' * 48):
        assert segredo not in mail['body'] and segredo not in mail['html']

    # Segundo clique / segunda aba: mesma autorização, nenhum POST nem e-mail novo.
    again = autorizar(http, cid)
    assert again.status_code == 200
    assert bank.count('POST') == 2  # location + recorrência, uma única vez
    assert len(smtp.sent) == 1
    assert db.scalar(select(func.count()).select_from(SicrediPixRecorrencia)) == 1


def test_cliente_existente_passa_a_usar_pix_sem_converter_cobrancas_antigas(http, db, cliente, bank, smtp):
    antiga = Billing(client_id=cliente.id, billing_type='avulsa', title='Boleto antigo', amount=Decimal('50'),
                     due_date=date(2026, 12, 20), status=BillingStatus.PENDING, payment_method='boleto')
    db.add(antiga); db.commit()
    updated = http.put(f'/api/v1/clients/{cliente.id}', json={'forma_cobranca': 'pix_automatico', 'billing_day': 20})
    assert updated.status_code == 200, updated.text
    assert autorizar(http, cliente.id).status_code == 200
    bank.rec_status = 'APROVADA'
    svc.processar_fila(db)
    assert db.get(Billing, antiga.id).payment_method == 'boleto'
    assert db.scalar(select(func.count()).select_from(SicrediPixCobranca)) == 0


def test_editar_cliente_ja_autorizado_nao_recria_autorizacao(http, db, cliente, rec, bank, smtp):
    response = http.put(f'/api/v1/clients/{cliente.id}', json={'name': 'João Silva Atualizado'})
    assert response.status_code == 200
    again = autorizar(http, cliente.id)
    assert again.status_code == 200 and again.json()['recorrencia']['id'] == rec.id
    assert bank.count('POST') == 0 and smtp.sent == []
    blocked = http.put(f'/api/v1/clients/{cliente.id}', json={'billing_day': 5})
    assert blocked.status_code == 409


def test_cadastro_salvo_com_sicredi_indisponivel_recupera_sem_duplicar(http, db, recovering, smtp):
    cid = http.post('/api/v1/clients/', json=novo_cliente_payload()).json()['id']
    recovering.create_error = PixResultadoIncerto('timeout após aceite bancário')
    response = autorizar(http, cid)
    assert response.status_code in (409, 502)
    assert db.get(Client, cid) is not None and not db.get(Client, cid).is_deleted
    rec = db.scalar(select(SicrediPixRecorrencia).where(SicrediPixRecorrencia.client_id == cid))
    assert rec.status == 'DESFECHO_DESCONHECIDO' and rec.ativa and rec.convite_status == 'pendente'
    assert smtp.sent == []

    # Novo clique enquanto o desfecho é desconhecido: nada é reenviado ao banco.
    recovering.create_error = None
    assert autorizar(http, cid).status_code == 200
    assert recovering.count('POST') == 2

    # Worker: recupera pelo vínculo e então envia o convite pedido no cadastro.
    result = svc.processar_fila(db)
    db.refresh(rec)
    assert rec.id_rec == REC_ID and rec.status == 'CRIADA'
    assert rec.convite_status == 'enviado' and result['convites_enviados'] == 1
    assert recovering.count('POST') == 2 and len(smtp.sent) == 1
    svc.processar_fila(db)
    assert len(smtp.sent) == 1


def test_cadastro_duplicado_por_duas_requisicoes(http, db, bank):
    first = http.post('/api/v1/clients/', json=novo_cliente_payload())
    second = http.post('/api/v1/clients/', json=novo_cliente_payload())
    assert first.status_code == 200 and second.status_code == 409
    assert db.scalar(select(func.count()).select_from(Client).where(Client.cpf_cnpj == '98765432100')) == 1


def test_integracao_desligada_nao_inicia_autorizacao_e_mantem_cliente(http, db, bank, smtp, monkeypatch):
    monkeypatch.setattr(settings, 'sicredi_pix_escrita_habilitada', False)
    cid = http.post('/api/v1/clients/', json=novo_cliente_payload()).json()['id']
    response = autorizar(http, cid)
    assert response.status_code == 409 and 'desativada' in response.json()['detail']
    assert db.get(Client, cid).forma_cobranca == 'pix_automatico'
    assert db.scalar(select(func.count()).select_from(SicrediPixRecorrencia)) == 0
    assert bank.calls == [] and smtp.sent == []
    painel = http.get('/api/v1/sicredi-pix/painel').json()
    assert painel['escrita_habilitada'] is False
    assert painel['linhas'][0]['situacao'] == 'autorizacao_nao_gerada'


def test_operacional_cadastra_mas_autorizacao_continua_com_financeiro(http_op, db, bank, smtp):
    created = http_op.post('/api/v1/clients/', json=novo_cliente_payload())
    assert created.status_code == 200
    assert autorizar(http_op, created.json()['id']).status_code == 403
    assert http_op.get('/api/v1/sicredi-pix/painel').status_code == 403
    assert bank.calls == [] and smtp.sent == []


def test_responsavel_financeiro_diferente_recebe_convite_e_agendamento(http, db, cliente, outro_cliente, bank, smtp):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    assert autorizar(http, cliente.id).status_code == 200
    assert smtp.sent[0]['to'] == 'joao@test.local'
    billing = Billing(client_id=outro_cliente.id, payer_client_id=cliente.id, billing_type='consolidada',
                      title='Fechamento 11/2026 - Pix Automático', amount=Decimal('99.90'),
                      due_date=date(2026, 12, 20), status=BillingStatus.PENDING, payment_method='pix_automatico')
    db.add(billing); db.commit()
    bank.rec_status = 'APROVADA'
    svc.processar_fila(db)
    charge = db.scalar(select(SicrediPixCobranca).where(SicrediPixCobranca.billing_id == billing.id))
    assert charge is not None
    assert db.get(SicrediPixRecorrencia, charge.recorrencia_id).client_id == cliente.id


# ── Autorização e convite ──────────────────────────────────────────────────

def test_qr_indisponivel_mantem_convite_pendente_ate_o_banco_devolver(http, db, cliente, recovering, smtp):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    recovering.sem_qr = True
    body = autorizar(http, cliente.id).json()
    assert body['recorrencia']['status'] == 'CRIADA' and body['recorrencia']['pix_copia_cola'] is None
    assert body['recorrencia']['convite_status'] == 'pendente' and smtp.sent == []
    recovering.sem_qr = False
    svc.processar_fila(db)
    assert len(smtp.sent) == 1


def test_sem_smtp_registra_erro_e_aprovacao_associa_cobrancas(http, db, cliente, bank, smtp):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    smtp.configured = False
    body = autorizar(http, cliente.id).json()
    assert body['recorrencia']['convite_status'] == 'erro'
    assert 'SMTP' in body['recorrencia']['convite_erro']
    billing = Billing(client_id=cliente.id, payer_client_id=cliente.id, billing_type='consolidada', title='Fechamento',
                      amount=Decimal('64.99'), due_date=date(2026, 12, 20), status=BillingStatus.PENDING,
                      payment_method='pix_automatico')
    db.add(billing); db.commit()
    svc.processar_fila(db)  # ainda CRIADA: não reserva cobrança que seria cancelada numa recusa
    assert db.scalar(select(func.count()).select_from(SicrediPixCobranca)) == 0
    bank.rec_status = 'APROVADA'
    result = svc.processar_fila(db)
    assert result['associadas'] == 1
    charge = db.scalar(select(SicrediPixCobranca).where(SicrediPixCobranca.billing_id == billing.id))
    assert charge.status == 'NA_FILA' and charge.ciclo == date(2026, 12, 20)
    assert bank.count('PUT') == 0  # fora da janela de 2 a 10 dias
    svc.processar_fila(db)
    assert db.scalar(select(func.count()).select_from(SicrediPixCobranca)) == 1


def test_convite_pendente_e_dispensado_quando_cliente_ja_autorizou(db, cliente, rec, smtp):
    rec.convite_canal, rec.convite_status = 'email', 'pendente'; db.commit()
    svc.processar_fila(db)
    assert rec.convite_status == 'dispensado' and smtp.sent == []


@pytest.mark.parametrize('status,situacao', [('REJEITADA', 'autorizacao_recusada'), ('EXPIRADA', 'autorizacao_expirada'),
                                             ('CANCELADA', 'autorizacao_cancelada')])
def test_autorizacao_encerrada_aparece_para_contato(http, db, cliente, bank, smtp, status, situacao):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    autorizar(http, cliente.id)
    bank.rec_status = status
    svc.processar_fila(db)
    linha = montar_painel(db)['linhas'][0]
    assert linha['situacao'] == situacao and linha['acao'] == 'contatar_cliente'
    assert db.scalar(select(SicrediPixRecorrencia)).convite_status == 'enviado'


def test_reenvio_reutiliza_autorizacao_sem_chamar_o_banco(http, db, cliente, bank, smtp):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    autorizar(http, cliente.id)
    calls = len(bank.calls)
    response = http.post(f'/api/v1/sicredi-pix/clientes/{cliente.id}/convite')
    assert response.status_code == 200
    assert response.json()['recorrencia']['convite_tentativas'] == 2
    assert len(bank.calls) == calls and len(smtp.sent) == 2


def test_falha_incerta_de_email_nao_reenvia_sozinha(http, db, cliente, bank, smtp):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    smtp.error = smtplib.SMTPServerDisconnected('conexão caiu durante o envio')
    body = autorizar(http, cliente.id).json()
    assert body['recorrencia']['status'] == 'CRIADA'  # e-mail nunca desfaz a autorização
    assert body['recorrencia']['convite_status'] == 'desconhecido'
    smtp.error = None
    svc.processar_fila(db)
    assert smtp.sent == []
    assert http.post(f'/api/v1/sicredi-pix/clientes/{cliente.id}/convite').status_code == 200
    assert len(smtp.sent) == 1


def test_limite_do_smtp_deixa_convite_para_o_worker(http, db, cliente, bank, smtp):
    cliente.forma_cobranca = 'pix_automatico'; db.commit()
    smtp.error = email_smtp.EmailRateLimitError(NOW + timedelta(minutes=10))
    assert autorizar(http, cliente.id).json()['recorrencia']['convite_status'] == 'pendente'
    smtp.error = None
    svc.processar_fila(db)
    assert len(smtp.sent) == 1


def test_envio_interrompido_vira_desconhecido_e_nao_repete(db, cliente, rec, smtp):
    rec.status, rec.pix_copia_cola = 'CRIADA', '000201autorizacaoSicredi'
    rec.convite_canal, rec.convite_status = 'email', 'enviando'
    rec.convite_iniciado_em = NOW - timedelta(minutes=30)
    db.commit()
    assert convite.enviar_convite(db, rec.id) is None
    assert rec.convite_status == 'desconhecido' and smtp.sent == []


def test_convite_sem_email_do_responsavel(http, db, cliente, bank, smtp):
    cliente.forma_cobranca, cliente.email = 'pix_automatico', None; db.commit()
    body = autorizar(http, cliente.id).json()
    assert body['recorrencia']['convite_status'] == 'erro'
    assert 'sem e-mail' in body['recorrencia']['convite_erro']
    assert http.post(f'/api/v1/sicredi-pix/clientes/{cliente.id}/convite').status_code == 409


# ── Cobrança e fechamento ──────────────────────────────────────────────────

def _contrato(db, client, plan, billing_day=20):
    db.add(Contract(client_id=client.id, plan_id=plan.id, status='ativo', start_date=date(2026, 1, 1),
                    billing_day=billing_day))
    db.commit()


def test_fechamento_antes_da_aprovacao_e_associado_automaticamente(db, cliente, plan, outro_cliente, bank, rec):
    """Vários veículos/contratos e outro cliente com o mesmo responsável: um Pix por ciclo."""
    rec.status = bank.rec_status = 'CRIADA'; rec.data_inicial = date(2026, 11, 15); db.commit()
    for _ in range(2):
        _contrato(db, cliente, plan, billing_day=15)
    db.add(Contract(client_id=outro_cliente.id, interveniente_client_id=cliente.id, plan_id=plan.id, status='ativo',
                    start_date=date(2026, 1, 1), billing_day=15))
    db.commit()
    execute_closure(db, date(2026, 11, 1), activity_month=date(2026, 10, 1))
    charge = db.scalar(select(SicrediPixCobranca))
    assert charge.status == 'AGUARDANDO_AUTORIZACAO'  # comportamento existente do fechamento preservado
    assert charge.valor == plan.price * 3
    bank.rec_status = 'APROVADA'
    svc.processar_fila(db)
    assert db.scalar(select(func.count()).select_from(SicrediPixCobranca)) == 1


def test_fechamento_executado_duas_vezes_nao_duplica_agendamento(db, cliente, plan, rec, bank):
    _contrato(db, cliente, plan, billing_day=15)
    execute_closure(db, date(2026, 11, 1), activity_month=date(2026, 10, 1))
    execute_closure(db, date(2026, 11, 1), activity_month=date(2026, 10, 1))
    svc.processar_fila(db)
    assert db.scalar(select(func.count()).select_from(SicrediPixCobranca)) == 1


def test_cobranca_vencida_antes_da_autorizacao_fica_para_outro_meio(db, cliente, rec, bank):
    billing = Billing(client_id=cliente.id, billing_type='consolidada', title='Fechamento', amount=Decimal('64.99'),
                      due_date=date(2026, 11, 1), status=BillingStatus.OVERDUE, payment_method='pix_automatico')
    db.add(billing); db.commit()
    svc.processar_fila(db)
    assert db.scalar(select(func.count()).select_from(SicrediPixCobranca)) == 0  # sem cobrança retroativa
    linha = next(l for l in montar_painel(db)['linhas'] if l['billing_id'] == billing.id)
    assert linha['situacao'] == 'fora_da_autorizacao' and linha['acao'] == 'cobrar_outro_meio'


def test_corrida_na_reserva_nao_derruba_a_transacao(db, cliente, rec, bank, monkeypatch):
    """Se outra sessão reservar o mesmo ciclo entre a checagem e o INSERT, só
    aquela reserva é descartada (savepoint); o restante segue na transação."""
    _, primeira = make_charge(db, cliente, rec, due=date(2026, 11, 15))
    corrida = Billing(client_id=cliente.id, billing_type='avulsa', title='Mesmo ciclo', amount=Decimal('64.99'),
                      due_date=date(2026, 11, 16), status=BillingStatus.PENDING)
    outra = Billing(client_id=cliente.id, billing_type='avulsa', title='Ciclo seguinte', amount=Decimal('64.99'),
                    due_date=date(2026, 12, 15), status=BillingStatus.PENDING)
    db.add_all([corrida, outra]); db.flush()
    real_scalar = db.scalar

    def sem_conflito_visivel(stmt, *a, **k):
        if 'WHERE sicredi_pix_cobrancas.recorrencia_id' in str(stmt):
            return None  # simula a reserva concorrente ainda não visível
        return real_scalar(stmt, *a, **k)
    monkeypatch.setattr(db, 'scalar', sem_conflito_visivel)
    queued = svc.enfileirar_fechamento(db, [corrida.id, outra.id])
    monkeypatch.setattr(db, 'scalar', real_scalar)
    db.commit()
    assert len(queued) == 1
    assert db.scalar(select(SicrediPixCobranca.billing_id).where(SicrediPixCobranca.id == queued[0])) == outra.id
    assert db.get(SicrediPixCobranca, primeira.id).status == 'NA_FILA'


# ── Painel do Financeiro ───────────────────────────────────────────────────

def _charge(status, venc, consultada=None, **kw):
    return SicrediPixCobranca(status=status, vencimento=venc, consultada_em=consultada, **kw)


@pytest.mark.parametrize('status,venc,consultada,extra,esperado', [
    ('NA_FILA', date(2026, 11, 30), None, {}, 'aguardando_janela'),
    ('NA_FILA', date(2026, 11, 12), None, {}, 'aguardando_envio'),
    ('NA_FILA', date(2026, 11, 7), None, {}, 'envio_atrasado'),
    ('ATIVA', date(2026, 11, 10), None, {}, 'agendado'),
    # Vencimento ontem e última consulta no próprio dia: atraso de consulta, não falha.
    ('ATIVA', date(2026, 11, 4), datetime(2026, 11, 4, 20, tzinfo=timezone.utc), {}, 'aguardando_confirmacao'),
    ('ATIVA', date(2026, 11, 3), datetime(2026, 11, 5, 12, tzinfo=timezone.utc), {}, 'pagamento_nao_confirmado'),
    ('REJEITADA', date(2026, 11, 3), datetime(2026, 11, 4, 12, tzinfo=timezone.utc), {}, 'debito_nao_realizado'),
    ('REJEITADA', date(2026, 11, 12), None, {'erro': 'Dados inválidos'}, 'agendamento_recusado'),
    ('EXPIRADA', date(2026, 11, 3), datetime(2026, 11, 4, 12, tzinfo=timezone.utc), {}, 'debito_nao_realizado'),
    ('DESFECHO_DESCONHECIDO', date(2026, 11, 12), None, {}, 'resultado_desconhecido'),
    ('CONCLUIDA', date(2026, 11, 3), None, {'pendencia': 'Recebimento possui devolução; conferir.'}, 'devolucao'),
    ('CONCLUIDA', date(2026, 11, 3), None, {'pendencia': 'Pagamento Pix com valor diferente da cobrança.'}, 'divergencia'),
    ('CONCLUIDA', date(2026, 11, 3), None, {}, 'pago'),
    ('PENDENCIA', date(2026, 11, 12), None, {'pendencia': 'Cobrança local mudou'}, 'revisao_necessaria'),
    ('CANCELADA', date(2026, 11, 12), None, {}, 'agendamento_cancelado'),
])
def test_situacao_derivada_sem_confundir_atraso_com_recusa(status, venc, consultada, extra, esperado):
    assert situacao_cobranca(_charge(status, venc, consultada, **extra), NOW.date()) == esperado


def test_painel_resumo_para_o_financeiro(http_fin, db, cliente, outro_cliente, rec, bank, smtp, monkeypatch):
    # (operacional recebe 403: ver test_operacional_cadastra_mas_autorizacao_continua_com_financeiro)
    monkeypatch.setattr('app.services.sicredi_pix_painel.hoje', lambda: NOW.date())
    rec.data_inicial = date(2026, 10, 15); db.commit()
    falhou, charge = make_charge(db, cliente, rec, due=date(2026, 11, 3))
    charge.status, charge.enviada_em = 'REJEITADA', NOW - timedelta(days=5)
    charge.consultada_em = NOW - timedelta(days=1)
    atrasada = Billing(client_id=cliente.id, billing_type='avulsa', title='Atrasada', amount=Decimal('20.00'),
                       due_date=date(2026, 11, 20), status=BillingStatus.PENDING)
    db.add(atrasada); db.flush()
    svc.enfileirar_fechamento(db, [atrasada.id])
    outro_cliente.forma_cobranca = 'pix_automatico'
    db.commit()
    body = http_fin.get('/api/v1/sicredi-pix/painel').json()
    resumo = body['resumo']
    assert resumo['debitos_nao_realizados'] == 1
    assert resumo['autorizacoes_pendentes'] == 1  # outro_cliente sem autorização
    assert resumo['clientes_a_contatar'] == 1
    assert resumo['acoes_necessarias'] >= 2
    situacoes = {l['situacao'] for l in body['linhas']}
    assert {'debito_nao_realizado', 'autorizacao_nao_gerada'} <= situacoes
    assert 'aguardando_janela' not in situacoes  # oculto por padrão: nada a fazer
    todos = http_fin.get('/api/v1/sicredi-pix/painel?incluir_resolvidos=true').json()
    assert [l['situacao'] for l in todos['linhas'] if l['billing_id'] == atrasada.id] == ['aguardando_janela']
    assert bank.calls == [] and smtp.sent == []  # painel é somente leitura


def test_pagamento_confirmado_e_conciliacao_repetida(db, cliente, rec, bank, monkeypatch):
    monkeypatch.setattr('app.services.sicredi_pix_painel.hoje', lambda: NOW.date())
    billing, charge = make_charge(db, cliente, rec, due=date(2026, 11, 15))
    svc.processar_cobranca(db, charge)
    bank.charges[charge.txid].update(status='CONCLUIDA', pix=[payment(charge)])
    svc.consultar_cobranca(db, charge)
    svc.consultar_cobranca(db, charge)
    assert billing.status == BillingStatus.PAID
    linhas = montar_painel(db, incluir_resolvidos=True)['linhas']
    assert [l['situacao'] for l in linhas if l['billing_id'] == billing.id] == ['pago']
    assert not [l for l in montar_painel(db)['linhas'] if l['billing_id'] == billing.id]


def test_worker_em_leitura_associa_mas_nao_envia(db, cliente, rec, bank, smtp, monkeypatch):
    """Sem envio automático liberado, a associação local acontece; o débito não."""
    monkeypatch.setattr(settings, 'sicredi_pix_escrita_habilitada', False)
    billing = Billing(client_id=cliente.id, billing_type='consolidada', title='Fechamento', amount=Decimal('64.99'),
                      due_date=date(2026, 11, 15), status=BillingStatus.PENDING,
                      payment_method='pix_automatico')
    db.add(billing); db.commit()
    svc.processar_fila(db)
    charge = db.scalar(select(SicrediPixCobranca).where(SicrediPixCobranca.billing_id == billing.id))
    assert charge.status == 'NA_FILA' and charge.enviada_em is None
    assert bank.count('PUT') == 0


def test_cliente_inativo_nao_entra_no_painel(db, cliente, bank):
    cliente.forma_cobranca, cliente.status = 'pix_automatico', ClientStatus.INACTIVE
    db.commit()
    assert montar_painel(db)['linhas'] == []
