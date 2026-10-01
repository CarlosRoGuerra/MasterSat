"""Provas antes/depois da Fase 04 — cenários SGR-01..07 da auditoria.

Roda contra o backend montado em /app (HEAD antigo ou branch da fase) usando
SÓ a interface que as duas versões têm em comum: ``run_poc`` com um dublê do
SGR e ``import_poc_result(db, resultado, dry_run)``. Banco SQLite em memória
criado pelos models daquela versão; nenhuma chamada de rede: o transporte
HTTP (``requests.Session.request``), o DNS e o storage são substituídos.

    python provas_fase04.py > provas-<versao>.json
"""
from __future__ import annotations

import json
import os
import socket
import sys

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('MULTIPORTAL_ENABLED', 'false')
os.environ.setdefault('SECRET_KEY', 'prova-fase04-sem-segredo-real-0123456789abcdef')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ['SGR_DOWNLOAD_HOSTS'] = 'boletos.exemplo,interno.exemplo'  # a versão antiga ignora
sys.path.insert(0, '/app')

import requests  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.services.storage as storage  # noqa: E402
from app.db.session import Base  # noqa: E402
from app.models import registry_all  # noqa: E402,F401
from app.models.billing import Billing  # noqa: E402
from app.models.contract import Contract  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.enums import ClientStatus, TrackerStatus, VehicleStatus  # noqa: E402
from app.models.vehicle import Vehicle  # noqa: E402
from app.services.sgr_migration import importer  # noqa: E402
from app.services.sgr_migration.poc import ClientNode, PocRunResult, TrackerNode, VehicleNode, achatar_boletos, run_poc  # noqa: E402

try:  # só existe na versão nova
    import app.models.sgr_migracao  # noqa: F401
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Ambiente falso: HTTP, DNS, storage
# ---------------------------------------------------------------------------

class RespostaFalsa:
    """Roteiro de uma resposta: status, corpo e cabeçalhos."""

    def __init__(self, status=200, corpo=b'%PDF-1.4 prova', headers=None):
        self.status, self.corpo, self.headers = status, corpo, headers or {}


TRANSPORTE = {'urls': [], 'roteiro': {}}


def _send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
    """No lugar do socket: a lógica do requests (redirect automático,
    streaming) roda de verdade em cima desta resposta."""
    import io
    from requests.structures import CaseInsensitiveDict

    TRANSPORTE['urls'].append(request.url)
    acao = TRANSPORTE['roteiro'].get(request.url) or RespostaFalsa()
    if callable(acao):
        acao()
    resposta = requests.Response()
    resposta.status_code = acao.status
    resposta.headers = CaseInsensitiveDict(acao.headers)
    resposta.raw = io.BytesIO(acao.corpo)
    resposta.url = request.url
    resposta.request = request
    resposta.connection = self
    resposta.encoding = None
    return resposta


requests.adapters.HTTPAdapter.send = _send
_DNS = {'boletos.exemplo': '93.184.216.34', 'interno.exemplo': '169.254.169.254'}
socket.getaddrinfo = lambda host, porta, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (_DNS.get(host, '93.184.216.34'), porta))]
STORAGE: list[str] = []


def _upload(object_name, content, content_type):
    STORAGE.append(object_name)
    return object_name


storage.upload_bytes = _upload


def novo_banco():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def importar(db, *nos, dry_run=False):
    resultado = PocRunResult(limit=10, clients=list(nos), request_count=0, request_log=[], plans=[
        {'external_id': '17', 'name': 'MENSALIDADE 100', 'price': 100.0, 'billing_interval_months': 1}])
    try:
        return importer.import_poc_result(db, resultado, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 — o script do CLI faz rollback em qualquer erro
        db.rollback()
        return f'{type(exc).__name__}'


def boleto(cod, linhas, *, situacao='ABERTO', pago=None, data_pag=None, link=None, valor=None):
    disc = [{'valor': v, 'placa': p, 'mes_referente': '10/2026', 'produto': pr} for v, p, pr in linhas]
    disc.append({'valor': '0,00', 'placa': linhas[0][1]})
    return {'cod_boleto': str(cod), 'cod_cliente': '1', 'nosso_numero': str(cod), 'valor': valor,
            'valor_pagamento': pago or '0,00', 'data_vencimento': '15/10/2026', 'data_pagamento': data_pag,
            'situacao': {'descricao': situacao}, 'parcela': '1 de 1', 'mes_referente': '10/2026',
            'discriminacao': disc, 'link': link}


def no(cod='1', cpf='11144477735', placa='ABC1234', imei='355488020902005', boletos=()):
    tracker = TrackerNode(raw={}, issues=[], mapped={'imei': imei, 'external_id': f'r{cod}',
                                                     'status': TrackerStatus.INSTALLED.value},
                          contract={'external_id': f'v{cod}', 'plan_external_id': '17', 'start_date': '2024-01-01',
                                    'billing_day': 15, 'status': 'ativo', 'billing_modality': 'boleto'})
    veiculo = VehicleNode(raw={}, issues=[], trackers=[tracker], mapped={
        'external_id': f'{cod}0', 'plate': placa, 'status': VehicleStatus.ACTIVE.value})
    return ClientNode(raw={'cod_cliente': cod}, issues=[], vehicles=[veiculo],
                      billings=achatar_boletos(list(boletos), []),
                      mapped={'external_id': cod, 'name': f'CLIENTE {cod}', 'cpf_cnpj': cpf, 'type': 'pf',
                              'status': ClientStatus.ACTIVE.value})


def reset():
    TRANSPORTE['urls'].clear()
    TRANSPORTE['roteiro'].clear()
    STORAGE.clear()


# ---------------------------------------------------------------------------
# Cenários
# ---------------------------------------------------------------------------

def sgr01():
    db = novo_banco()
    importar(db, no(boletos=[boleto(9001, [('100,00', 'ABC1234', 'MENSALIDADE'), ('-20,00', 'ABC1234', 'DESCONTO')],
                                    valor='80,00')]))
    valores = [float(b.amount) for b in db.query(Billing).filter(Billing.is_deleted.is_(False))]
    return {'documento_origem': 80.0, 'soma_cobrancas_mastersat': sum(valores), 'cobrancas': valores}


def sgr02():
    db = novo_banco()
    linhas = [('100,00', 'ABC1234', 'MENSALIDADE')]
    importar(db, no(boletos=[boleto(9101, linhas, valor='100,00')]))
    importar(db, no(boletos=[boleto(9101, linhas, valor='100,00', situacao='BAIXADO', pago='100,00',
                                    data_pag='10/10/2026')]))
    b = db.query(Billing).one()
    return {'origem_rodada_2': 'BAIXADO', 'status_mastersat_rodada_2': b.status.name,
            'payment_date': b.payment_date.isoformat() if b.payment_date else None}


def sgr03():
    reset()
    db = novo_banco()
    url = 'https://boletos.exemplo/9201.pdf'
    bol = boleto(9201, [('100,00', 'ABC1234', 'MENSALIDADE')], valor='100,00', link=url)

    def timeout():
        raise requests.Timeout('prova')
    TRANSPORTE['roteiro'][url] = timeout  # chamável: levanta no lugar da resposta
    importar(db, no(boletos=[bol]))
    TRANSPORTE['roteiro'][url] = RespostaFalsa()
    importar(db, no(boletos=[bol]))
    return {'cobrancas': db.query(Billing).count(), 'documentos_apos_rodada_2': db.query(Document).count(),
            'tentativas_de_download': TRANSPORTE['urls'].count(url)}


def sgr04():
    db = novo_banco()
    importar(db, no(cod='1', placa='ABC1234', imei='355488020902005'))
    importar(db, no(cod='2', cpf='52998224725', placa='ABC1234', imei='355488020902005'))
    cruzados = [c.id for c in db.query(Contract).all() if c.client_id != db.get(Vehicle, c.vehicle_id).client_id]
    return {'contratos': db.query(Contract).count(), 'contratos_com_cliente_diferente_do_dono': len(cruzados)}


class FakeSGR:
    def __init__(self, n):
        self.veiculos = [{'cod_veiculo': str(i), 'cod_cliente': '1', 'placa_veiculo': f'AAA{i:04d}',
                          'situacao_veiculo': 'ATIVO'} for i in range(n)]
        self.request_count, self.request_log = 0, []

    def buscar_clientes(self, total, indice=0):
        return [{'cod_cliente': '1', 'nome_cliente': 'A', 'cpf_cliente': '11144477735',
                 'situacao': {'descricao': 'ATIVO'}}][indice:indice + total]

    def buscar_rastreadores(self, total=200, indice=0):
        return []

    def get_grupo_mensalidade(self, total=200, indice=0):
        return []

    get_grupo_adesao = get_grupo_mensalidade

    def get_vencimento(self, cod_cliente=None):
        return []

    def buscar_veiculos_por_cliente(self, cod_cliente, total=200, indice=0):
        return self.veiculos[indice:indice + total]

    def buscar_vinculos_por_placa(self, placa, total=200, indice=0):
        return []


def sgr05():
    resultado = run_poc(FakeSGR(201), limit=1)
    return {'veiculos_na_origem': 201, 'veiculos_coletados': len(resultado.clients[0].vehicles)}


def sgr06():
    reset()
    db = novo_banco()
    interno = 'https://interno.exemplo/latest/meta-data'
    redireciona = 'https://boletos.exemplo/9301.pdf'
    grande = 'https://boletos.exemplo/9302.pdf'
    TRANSPORTE['roteiro'][redireciona] = RespostaFalsa(302, b'', {'Location': interno})
    TRANSPORTE['roteiro'][interno] = RespostaFalsa(200, b'%PDF- segredo-de-metadados')
    TRANSPORTE['roteiro'][grande] = RespostaFalsa(200, b'%PDF-' + b'x' * (12 * 1024 * 1024))
    importar(db, no(boletos=[
        boleto(9301, [('100,00', 'ABC1234', 'MENSALIDADE')], valor='100,00', link=redireciona),
        boleto(9302, [('50,00', 'ABC1234', 'SERVICO')], valor='50,00', link=grande),
    ]))
    return {'urls_requisitadas': list(TRANSPORTE['urls']), 'documentos_gravados': db.query(Document).count(),
            'endereco_interno_acessado': any('interno.exemplo' in u for u in TRANSPORTE['urls'])}


def sgr07():
    reset()
    db = novo_banco()
    url = 'https://boletos.exemplo/9401.pdf'
    bol = boleto(9401, [('100,00', 'ABC1234', 'MENSALIDADE')], valor='100,00', link=url)
    commit = db.commit
    estado = {'falhar': True}

    def commit_que_falha_depois_do_upload():
        if STORAGE and estado['falhar']:
            estado['falhar'] = False
            raise RuntimeError('banco caiu depois do upload')
        return commit()
    db.commit = commit_que_falha_depois_do_upload
    rodada1 = importar(db, no(boletos=[bol]))
    rodada1 = rodada1 if isinstance(rodada1, str) else 'ok'
    importar(db, no(boletos=[bol]))
    documentos = {d.object_key for d in db.query(Document).all()}
    return {'rodada_1': rodada1, 'objetos_gravados': len(STORAGE), 'chaves_distintas': len(set(STORAGE)),
            'documentos': len(documentos), 'objetos_orfaos': len(set(STORAGE) - documentos)}


def dry_run():
    reset()
    db = novo_banco()
    importar(db, no(boletos=[boleto(9501, [('100,00', 'ABC1234', 'MENSALIDADE')], valor='100,00',
                                    link='https://boletos.exemplo/9501.pdf')]), dry_run=True)
    from app.models.client import Client
    return {'clientes': db.query(Client).count(), 'cobrancas': db.query(Billing).count(),
            'uploads': len(STORAGE), 'requisicoes': len(TRANSPORTE['urls'])}


if __name__ == '__main__':
    saida = {}
    for nome, fn in (('SGR-01', sgr01), ('SGR-02', sgr02), ('SGR-03', sgr03), ('SGR-04', sgr04),
                     ('SGR-05', sgr05), ('SGR-06', sgr06), ('SGR-07', sgr07), ('dry-run', dry_run)):
        try:
            saida[nome] = fn()
        except Exception as exc:  # noqa: BLE001 — a prova registra o erro em vez de parar
            saida[nome] = {'erro': f'{type(exc).__name__}: {exc}'}
    print(json.dumps(saida, ensure_ascii=False, indent=2, default=str))
