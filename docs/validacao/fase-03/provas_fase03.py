"""Provas antes/depois da Fase 03 — reproduz os cenários da auditoria.

Roda contra o código que estiver em /app (HEAD antigo ou branch da fase), com
um banco NOVO migrado pelo Alembic daquela versão (schema real, não
create_all). Usa as mesmas chamadas HTTP/serviço nas duas versões; o que muda
é o desfecho observado.

A Ailos NUNCA é chamada: ``requests.request`` do cliente Ailos é substituído
por um falso que responde conforme o cenário, e os tokens são fixos.

    TEST_DATABASE_URL=postgresql+psycopg://... python provas_fase03.py > saida.json

Só para PostgreSQL descartável: cria e apaga bancos no servidor apontado.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from unittest.mock import patch
from uuid import uuid4

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BASE_URL = make_url(os.environ['TEST_DATABASE_URL'])
ADMIN = create_engine(BASE_URL, isolation_level='AUTOCOMMIT')


def novo_banco() -> str:
    nome = f'prova_{uuid4().hex[:10]}'
    with ADMIN.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nome}"'))
    return nome


def apagar_banco(nome: str) -> None:
    with ADMIN.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))


def url_de(nome: str) -> str:
    return BASE_URL.set(database=nome).render_as_string(hide_password=False)


PRINCIPAL = novo_banco()
os.environ['DATABASE_URL'] = url_de(PRINCIPAL)
os.environ.setdefault('AILOS_GATEWAY_BASE_URL', 'https://ailos.invalid')
_r = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                    env=dict(os.environ), capture_output=True, text=True, cwd='/app')
assert _r.returncode == 0, _r.stderr[-2000:]

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.deps import get_current_user  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.session import get_db  # noqa: E402
import app.db.session as db_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.ailos_boleto import AilosBoleto  # noqa: E402
from app.models.billing import Billing  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.contract import Contract  # noqa: E402
from app.models.enums import BillingStatus, ClientStatus, UserRole  # noqa: E402
from app.models.payable import Payable  # noqa: E402
from app.models.plan import Plan  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import ailos_boletos, ailos_client  # noqa: E402

settings.ailos_gateway_base_url = 'https://ailos.invalid'
ENGINE = create_engine(url_de(PRINCIPAL))
Sessions = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False)
db_session.SessionLocal = Sessions  # log Ailos em sessão própria cai no banco da prova


def _db():
    s = Sessions()
    try:
        yield s
    finally:
        s.close()


with Sessions() as s:
    admin = User(name='Admin prova', email='admin-prova@test.local', role=UserRole.ADMIN,
                 active=True, is_deleted=False, password_hash='x')
    s.add(admin)
    s.commit()
    s.refresh(admin)
    s.expunge(admin)
app.dependency_overrides[get_db] = _db
app.dependency_overrides[get_current_user] = lambda: admin
HTTP = TestClient(app, raise_server_exceptions=False)
HOJE = date.today()
_SEQ = count(100000001)


def _cpf() -> str:
    base = [int(d) for d in str(next(_SEQ))]
    for peso in (10, 11):
        soma = sum(d * (peso - i) for i, d in enumerate(base))
        base.append((soma * 10 % 11) % 10)
    return ''.join(map(str, base))


def cliente(s, status=ClientStatus.ACTIVE) -> Client:
    c = Client(name=f'Cliente {uuid4().hex[:6]}', cpf_cnpj=_cpf(), type='pf', status=status,
               zip_code='89201-000', address_line='Rua A', address_number='1',
               neighborhood='Centro', city='Joinville', state='SC')
    s.add(c)
    s.flush()
    return c


def contrato(s) -> Contract:
    c = cliente(s)
    p = Plan(name=f'Plano {uuid4().hex[:6]}', price=Decimal('100.00'))
    s.add(p)
    s.flush()
    k = Contract(client_id=c.id, plan_id=p.id, start_date=date(2025, 1, 10), status='ativo', billing_day=10)
    s.add(k)
    s.flush()
    return k


def cobranca(s, k: Contract, **kw) -> Billing:
    dados = dict(contract_id=k.id, client_id=k.client_id, amount=Decimal('100.00'),
                 due_date=HOJE + timedelta(days=20), status=BillingStatus.PENDING,
                 billing_type='recorrente', period_label=(HOJE + timedelta(days=20)).strftime('%m/%Y'))
    dados.update(kw)
    b = Billing(**dados)
    s.add(b)
    s.flush()
    return b


def registrar_boleto(s, b: Billing) -> None:
    s.add(AilosBoleto(billing_id=b.id, numero_convenio='102004', numero_documento=str(b.id),
                      nosso_numero=f'NN{b.id:08d}', linha_digitavel=f'LD{b.id}',
                      codigo_barras=f'CB{b.id}', status_ailos='REGISTRADO',
                      valor_nominal=b.amount, data_vencimento=b.due_date))


class _Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body
        self.headers = {'Content-Type': 'application/json'}
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


def _boleto_ailos(billing_id: int, *, pago: bool = False, valor=100.0) -> dict:
    return {'boleto': {
        'documento': {'numeroDocumento': billing_id, 'nossoNumero': f'NN{billing_id:08d}'},
        'codigoBarras': {'linhaDigitavel': f'LD{billing_id}', 'codigoBarras': f'CB{billing_id}'},
        'indicadorSituacaoBoleto': 'REGISTRADO',
        'valorBoleto': {'valorNominal': valor, 'valorPago': valor if pago else 0},
        'pagamento': {'dataPagamento': HOJE.isoformat() if pago else '0001-01-01'},
        'vencimento': {'dataVencimento': (HOJE + timedelta(days=20)).isoformat()},
    }}


def ailos_falso(responder):
    """Substitui só o transporte HTTP e os tokens — o resto do cliente Ailos roda."""
    return [
        patch.object(ailos_client, 'get_valid_client_token', return_value='t'),
        patch.object(ailos_client, 'get_valid_cooperado_token', return_value='t'),
        patch.object(ailos_client.requests, 'request', side_effect=responder),
    ]


class _Ctx:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def status_boleto(billing_id: int) -> str | None:
    with Sessions() as s:
        ab = s.query(AilosBoleto).filter_by(billing_id=billing_id).first()
        return ab.status_ailos if ab else None


# ---------------------------------------------------------------------------

def prova_fin02_excluir_registrada():
    with Sessions() as s:
        k = contrato(s)
        b = cobranca(s, k)
        registrar_boleto(s, b)
        s.commit()
        bid, kid, cid, rotulo, venc = b.id, k.id, k.client_id, b.period_label, b.due_date
    delete = HTTP.delete(f'/api/v1/billings/{bid}')
    nova = HTTP.post('/api/v1/billings/', json={
        'client_id': cid, 'contract_id': kid, 'billing_type': 'recorrente', 'amount': 100,
        'due_date': venc.isoformat(), 'period_label': rotulo, 'status': 'pendente'})
    with Sessions() as s:
        efetivas = s.query(Billing).filter(Billing.contract_id == kid, Billing.is_deleted.is_(False),
                                           Billing.status != BillingStatus.CANCELED).count()
    return {'delete': delete.status_code, 'delete_code': _code(delete),
            'nova_mensalidade_mesmo_mes': nova.status_code,
            'obrigacoes_efetivas_no_mes': efetivas,
            'boleto_original_ativo': True}


def prova_fin02_cancelar_liberar_registrada():
    with Sessions() as s:
        k = contrato(s)
        b = cobranca(s, k)
        registrar_boleto(s, b)
        s.commit()
        bid = b.id
    cancel = HTTP.post(f'/api/v1/billings/{bid}/cancel', json={
        'reason': 'cliente desistiu', 'confirmar_boleto_ailos': True, 'liberar_competencia': True})
    cancel_sem_liberar = None
    if cancel.status_code != 200:
        cancel_sem_liberar = HTTP.post(f'/api/v1/billings/{bid}/cancel', json={
            'reason': 'cliente desistiu', 'confirmar_boleto_ailos': True}).status_code
    liberar = HTTP.post(f'/api/v1/billings/{bid}/liberar-competencia', json={'justificativa': 'recobrar'})
    with Sessions() as s:
        liberada = s.get(Billing, bid).competencia_liberada
    return {'cancel_com_liberacao': cancel.status_code, 'cancel_code': _code(cancel),
            'cancel_sem_liberacao': cancel_sem_liberar, 'liberar_depois': liberar.status_code,
            'liberar_code': _code(liberar), 'competencia_liberada_com_boleto_ativo': liberada}


def prova_fin03_timeout_apos_aceite():
    with Sessions() as s:
        k = contrato(s)
        b = cobranca(s, k)
        s.commit()
        bid = b.id

    def responder(method, url, **_kw):
        raise requests.ReadTimeout('read timed out (simulado: Ailos pode ter aceitado)')

    with _Ctx(ailos_falso(responder)):
        gerar = HTTP.post('/api/v1/ailos/boletos', json={'billing_id': bid})
    estado = status_boleto(bid)
    put = HTTP.put(f'/api/v1/billings/{bid}', json={'amount': 130, 'justification': 'ajuste'})
    cancel = HTTP.post(f'/api/v1/billings/{bid}/cancel', json={'reason': 'x'})
    with Sessions() as s:
        valor = str(s.get(Billing, bid).amount)
    return {'gerar': gerar.status_code, 'estado_local': estado, 'put_valor': put.status_code,
            'valor_local_depois': valor, 'cancelar': cancel.status_code}


def prova_fin03_log_falha_apos_sucesso():
    with Sessions() as s:
        k = contrato(s)
        b = cobranca(s, k)
        s.commit()
        bid = b.id

    def responder(method, url, **_kw):
        return _Resp(200, _boleto_ailos(bid))

    def log_quebrado(*_a, **_kw):
        raise RuntimeError('banco de log indisponível (simulado)')

    with _Ctx(ailos_falso(responder) + [patch.object(ailos_client, '_log_call', side_effect=log_quebrado)]):
        gerar = HTTP.post('/api/v1/ailos/boletos', json={'billing_id': bid})
    return {'gerar': gerar.status_code, 'estado_local': status_boleto(bid)}


def prova_fin05_carteira_1001():
    with Sessions() as s:
        k = contrato(s)
        ids = []
        for i in range(1001):
            b = cobranca(s, k, billing_type='avulsa', period_label=None,
                         due_date=HOJE + timedelta(days=5))
            registrar_boleto(s, b)
            ids.append(b.id)
        s.commit()
    primeiros = set(ids[:300])
    consultados: list[int] = []

    def responder(method, url, **_kw):
        numero = int(url.rstrip('/').rsplit('/', 1)[-1])
        consultados.append(numero)
        return _Resp(200, _boleto_ailos(numero, pago=numero not in primeiros))

    execucoes = []
    with _Ctx(ailos_falso(responder)):
        for _ in range(4):
            with Sessions() as s:
                execucoes.append(ailos_boletos.conciliar_boletos_abertos(s, limit=300))
    with Sessions() as s:
        pagas = s.query(Billing).filter(Billing.id.in_(ids), Billing.status == BillingStatus.PAID).count()
    distintos = set(consultados)
    return {'execucoes': [{k: v for k, v in e.items() if k in ('consultados', 'baixados', 'erros')}
                          for e in execucoes],
            'titulos_distintos_consultados': len(distintos),
            'titulo_301_consultado': ids[300] in distintos,
            'baixados_total': pagas, 'esperado_baixar': 701}


def prova_fin07_cnab_selecao_invalida():
    with Sessions() as s:
        k = contrato(s)
        paga = cobranca(s, k, status=BillingStatus.PAID, payment_date=HOJE, paid_amount=Decimal('100'),
                        billing_type='avulsa', period_label=None)
        cancelada = cobranca(s, k, status=BillingStatus.CANCELED, billing_type='avulsa', period_label=None)
        via_api = cobranca(s, k, billing_type='avulsa', period_label=None)
        registrar_boleto(s, via_api)
        s.commit()
        ids = [paga.id, paga.id, cancelada.id, via_api.id]
    resp = HTTP.post('/api/v1/boletos/cnab240', json=ids)
    linhas_detalhe = None
    if resp.status_code == 200:
        linhas = resp.content.decode('latin-1').splitlines()
        linhas_detalhe = sum(1 for linha in linhas if len(linha) >= 14 and linha[7] == '3' and linha[13] == 'P')
    habilitado = getattr(settings, 'cnab_remessa_habilitada', None)
    resp_hab = None
    if habilitado is not None:
        settings.cnab_remessa_habilitada = True
        try:
            r2 = HTTP.post('/api/v1/boletos/cnab240', json=ids)
            resp_hab = {'status': r2.status_code, 'code': _code(r2)}
        finally:
            settings.cnab_remessa_habilitada = habilitado
    return {'status': resp.status_code, 'code': _code(resp), 'segmentos_p_no_arquivo': linhas_detalhe,
            'com_canal_habilitado_para_homologacao': resp_hab}


def prova_fin06_pagamento_parcial():
    with Sessions() as s:
        k = contrato(s)
        b = cobranca(s, k, billing_type='avulsa', period_label=None)
        s.commit()
        bid = b.id
    r = HTTP.post(f'/api/v1/billings/{bid}/receive', json={
        'paid_amount': 1, 'payment_date': HOJE.isoformat(), 'payment_method': 'pix'})
    with Sessions() as s:
        b = s.get(Billing, bid)
        estado = {'status': b.status.value, 'paid_amount': str(b.paid_amount)}
    return {'receber_1_de_100': r.status_code, 'code': _code(r), **estado}


def prova_fin08_contas_a_pagar():
    def nova() -> int:
        r = HTTP.post('/api/v1/payables/', json={'description': 'Aluguel', 'amount': 500,
                                                 'due_date': HOJE.isoformat()})
        return r.json()['id']

    a = nova()
    HTTP.post(f'/api/v1/payables/{a}/cancel')
    pagar_cancelada = HTTP.post(f'/api/v1/payables/{a}/pay', json={'payment_date': HOJE.isoformat(),
                                                                   'payment_method': 'pix'})
    b = nova()
    HTTP.post(f'/api/v1/payables/{b}/pay', json={'payment_date': HOJE.isoformat(), 'payment_method': 'pix'})
    editar_paga = HTTP.put(f'/api/v1/payables/{b}', json={'amount': 1})
    excluir_paga = HTTP.delete(f'/api/v1/payables/{b}')
    with Sessions() as s:
        pa, pb = s.get(Payable, a), s.get(Payable, b)
        estado = {'a_status': pa.status, 'b_amount': str(pb.amount), 'b_removida': pb.is_deleted}
    return {'pagar_cancelada': pagar_cancelada.status_code, 'editar_valor_paga': editar_paga.status_code,
            'excluir_paga': excluir_paga.status_code, **estado}


def prova_fin10_contrato_removido():
    with Sessions() as s:
        k = contrato(s)
        b = cobranca(s, k, status=BillingStatus.PAID, payment_date=HOJE, paid_amount=Decimal('100'),
                     receipt_number='RCB-PROVA', due_date=HOJE - timedelta(days=3),
                     period_label=(HOJE - timedelta(days=3)).strftime('%m/%Y'))
        s.commit()
        bid, kid, cid = b.id, k.id, k.client_id
    antes = len(HTTP.get(f'/api/v1/billings/?client_id={cid}').json())
    rem = HTTP.delete(f'/api/v1/contracts/{kid}')
    depois = HTTP.get(f'/api/v1/billings/?client_id={cid}')
    detalhe = HTTP.get(f'/api/v1/billings/{bid}')
    recibo = HTTP.get(f'/api/v1/billings/{bid}/receipt')
    return {'excluir_contrato': rem.status_code, 'lista_antes': antes,
            'lista_depois': len(depois.json()) if depois.status_code == 200 else depois.status_code,
            'detalhe': detalhe.status_code, 'recibo': recibo.status_code}


def prova_prod01_canceladas_e_bases():
    mes = date(2031, 8, 1)
    with Sessions() as s:
        k = contrato(s)
        a = cobranca(s, k, billing_type='avulsa', period_label=None, due_date=mes.replace(day=10))
        b = cobranca(s, k, billing_type='avulsa', period_label=None, due_date=mes.replace(day=12))
        pago_depois = cobranca(s, k, billing_type='avulsa', period_label=None, due_date=mes.replace(day=15),
                               status=BillingStatus.PAID, payment_date=date(2031, 9, 3),
                               paid_amount=Decimal('100'))
        s.commit()
        ids, cid = [a.id, b.id], k.client_id
    unif = HTTP.post('/api/v1/billings/unificar', json={'billing_ids': ids, 'due_date': '2031-08-20'})
    antiga = {r['label']: r for r in HTTP.get('/api/v1/billings/reports/revenue?period=monthly').json()}
    nova = HTTP.get('/api/v1/reports/revenue?date_from=2031-08-01&date_to=2031-09-30').json()
    extrato = HTTP.get(f'/api/v1/reports/client-statement/{cid}').json()
    return {
        'unificar': unif.status_code,
        'rota_antiga_08_2031': antiga.get('08/2031'),
        'rota_antiga_09_2031': antiga.get('09/2031'),
        'rota_relatorios_meses': [{k: m.get(k) for k in ('label', 'total_emitido', 'total_recebido',
                                                          'total_recebido_caixa')} for m in nova['meses']],
        'extrato_total_cobrado': extrato['resumo']['total_cobrado'],
        'esperado_emitido_sem_canceladas': 300.0,
    }


def prova_prod02_dashboard():
    with Sessions() as s:
        s.execute(text('UPDATE clients SET is_deleted = true'))
        for _ in range(5):
            cliente(s, ClientStatus.ACTIVE)
        for _ in range(5):
            cliente(s, ClientStatus.SUSPENDED)
        s.execute(text('UPDATE billings SET is_deleted = true'))
        k = contrato(s)
        for d in range(5):
            cobranca(s, k, billing_type='avulsa', period_label=None, status=BillingStatus.OVERDUE,
                     due_date=HOJE - timedelta(days=200 + d))
        proxima = cobranca(s, k, billing_type='avulsa', period_label=None, due_date=HOJE + timedelta(days=3))
        s.commit()
        proxima_id = proxima.id
    d = HTTP.get('/api/v1/dashboard/').json()
    clientes = d['clients']
    # Denominador que a tela usa: 'total' do backend quando existe (Fase 03);
    # antes, só ativos + inativos + inadimplentes (suspensos fora).
    denominador = clientes.get('total') or (clientes['active'] + clientes['inactive'] + clientes['delinquent'])
    return {
        'clientes': {k: clientes.get(k) for k in ('active', 'inactive', 'delinquent', 'suspended', 'total')},
        'saudaveis_pct': round(clientes['active'] / denominador * 100),
        'proximos_vencimentos_ids': [b['id'] for b in d['upcoming_billings']],
        'titulo_de_3_dias_aparece': proxima_id in [b['id'] for b in d['upcoming_billings']],
        'vencidas_separadas': 'overdue_billings' in d,
    }


def _code(resp):
    try:
        detail = resp.json().get('detail')
    except Exception:  # noqa: BLE001 — resposta não-JSON (arquivo) não tem código
        return None
    return detail.get('code') if isinstance(detail, dict) else None


resultados = {}
for nome_prova, fn in [
    ('FIN-02 excluir_registrada', prova_fin02_excluir_registrada),
    ('FIN-02 cancelar_liberar_registrada', prova_fin02_cancelar_liberar_registrada),
    ('FIN-03 timeout_apos_aceite', prova_fin03_timeout_apos_aceite),
    ('FIN-03 log_falha_apos_sucesso', prova_fin03_log_falha_apos_sucesso),
    ('FIN-05 carteira_1001', prova_fin05_carteira_1001),
    ('FIN-07 cnab_selecao_invalida', prova_fin07_cnab_selecao_invalida),
    ('FIN-06 pagamento_parcial', prova_fin06_pagamento_parcial),
    ('FIN-08 contas_a_pagar', prova_fin08_contas_a_pagar),
    ('FIN-10 contrato_removido', prova_fin10_contrato_removido),
    ('PROD-01 canceladas_e_bases', prova_prod01_canceladas_e_bases),
    ('PROD-02 dashboard', prova_prod02_dashboard),
]:
    try:
        resultados[nome_prova] = fn()
    except Exception:  # noqa: BLE001 — a prova registra a falha em vez de parar
        resultados[nome_prova] = {'erro': traceback.format_exc()[-800:]}

ENGINE.dispose()
apagar_banco(PRINCIPAL)
print(json.dumps(resultados, indent=1, ensure_ascii=False, default=str))
