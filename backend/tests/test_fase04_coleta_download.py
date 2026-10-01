"""Fase 04 — coleta exaustiva (SGR-05) e download controlado (SGR-06).

Sem rede: a API do SGR é um dublê que respeita (ou não) `total`/`indice`, e
o download usa sessão HTTP e resolvedor DNS falsos.
"""
from __future__ import annotations

import socket

import pytest

from app.core.config import settings
from app.services.sgr_migration import download
from app.services.sgr_migration.client import (
    ColetaPaginada,
    SGRNotFoundError,
    SGRPaginacaoError,
    SGRServerError,
    paginar,
)
from app.services.sgr_migration.importer import import_poc_result
from app.services.sgr_migration.poc import SGRIncompleteScan, run_poc


# ---------------------------------------------------------------------------
# SGR-05 — paginação
# ---------------------------------------------------------------------------

def _fonte(registros, *, teto=None, ignora_indice=False, falha_no_indice=None, erro_404_apos_fim=False):
    """buscar(total, indice) de um endpoint paginado por deslocamento."""
    chamadas = []

    def buscar(total, indice):
        chamadas.append((total, indice))
        if falha_no_indice is not None and indice == falha_no_indice:
            raise SGRServerError('HTTP 502', status_code=502)
        lote_max = min(total, teto) if teto else total
        inicio = 0 if ignora_indice else indice
        if erro_404_apos_fim and inicio >= len(registros):
            raise SGRNotFoundError('fim', status_code=404)
        return registros[inicio:inicio + lote_max]
    buscar.chamadas = chamadas
    return buscar


REGISTROS = [{'cod': i} for i in range(201)]


class TestPaginacao:
    def test_200_mais_1_le_tudo_e_prova_o_fim(self):
        coleta = ColetaPaginada('/x', 'teste')
        buscar = _fonte(REGISTROS)
        assert paginar(buscar, 200, coleta) == REGISTROS
        assert buscar.chamadas == [(200, 0), (200, 200), (200, 201)]
        assert (coleta.completa, coleta.registros, coleta.paginas) == (True, 201, 3)

    def test_api_que_entrega_menos_que_o_pedido_nao_encerra_cedo(self):
        coleta = ColetaPaginada('/x', 'teste')
        assert paginar(_fonte(REGISTROS, teto=50), 200, coleta) == REGISTROS
        assert coleta.completa

    def test_50_mais_1(self):
        registros = REGISTROS[:51]
        assert paginar(_fonte(registros), 50, ColetaPaginada('/x', 't')) == registros

    def test_colecao_vazia(self):
        coleta = ColetaPaginada('/x', 't')
        assert paginar(_fonte([]), 200, coleta) == []
        assert coleta.completa and coleta.paginas == 1

    def test_pagina_repetida_depois_de_pagina_cheia_e_erro(self):
        coleta = ColetaPaginada('/x', 't')
        with pytest.raises(SGRPaginacaoError, match='repetida'):
            paginar(_fonte(REGISTROS, ignora_indice=True), 200, coleta)
        assert not coleta.completa

    def test_endpoint_que_ignora_indice_com_pagina_curta_ja_trouxe_tudo(self):
        coleta = ColetaPaginada('/x', 't')
        assert paginar(_fonte(REGISTROS[:5], ignora_indice=True), 200, coleta) == REGISTROS[:5]
        assert coleta.completa and 'ignora o índice' in coleta.observacao

    def test_registro_deslocado_entre_paginas_e_erro(self):
        paginas = [REGISTROS[0:2], REGISTROS[1:3]]

        def buscar(total, indice):
            return paginas.pop(0) if paginas else []
        with pytest.raises(SGRPaginacaoError, match='repetidos'):
            paginar(buscar, 2, ColetaPaginada('/x', 't'))

    def test_erro_no_meio_nao_certifica(self):
        coleta = ColetaPaginada('/x', 't')
        with pytest.raises(SGRServerError):
            paginar(_fonte(REGISTROS, falha_no_indice=200), 200, coleta)
        assert (coleta.completa, coleta.erro) == (False, 'SGRServerError')

    def test_404_depois_de_pagina_curta_e_fim(self):
        coleta = ColetaPaginada('/x', 't')
        assert paginar(_fonte(REGISTROS[:3], erro_404_apos_fim=True), 200, coleta) == REGISTROS[:3]
        assert coleta.completa

    def test_teto_de_paginas_nao_e_silencioso(self):
        with pytest.raises(SGRPaginacaoError, match='teto'):
            paginar(_fonte(REGISTROS, teto=1), 200, ColetaPaginada('/x', 't'), max_paginas=10)


class FakeSGR:
    """Dublê do SGRClient com frota grande e falhas configuráveis."""

    def __init__(self, *, veiculos=201, falha_veiculos_no_indice=None, ignora_indice_veiculos=False,
                 falha_rastreador=False, falha_abertos=False):
        self.veiculos = [
            {'cod_veiculo': str(i), 'cod_cliente': '1', 'placa_veiculo': f'AAA{i:04d}', 'situacao_veiculo': 'ATIVO'}
            for i in range(veiculos)
        ]
        self.falha_veiculos_no_indice = falha_veiculos_no_indice
        self.ignora_indice_veiculos = ignora_indice_veiculos
        self.falha_rastreador = falha_rastreador
        self.falha_abertos = falha_abertos
        self.request_count = 0
        self.request_log = []

    def buscar_clientes(self, total, indice=0):
        return [{'cod_cliente': '1', 'nome_cliente': 'A', 'cpf_cliente': '11144477735',
                 'situacao': {'descricao': 'ATIVO'}}][indice:indice + total]

    def buscar_rastreadores(self, total=200, indice=0):
        if self.falha_rastreador and indice > 0:
            raise SGRServerError('HTTP 500', status_code=500)
        return [{'placa': 'AAA0000', 'imei_equipamento': f'35548802090{i:04d}'} for i in range(250)][indice:indice + total]

    def get_grupo_mensalidade(self, total=200, indice=0):
        return []

    def get_grupo_adesao(self, total=200, indice=0):
        return []

    def get_vencimento(self, cod_cliente=None):
        return []

    def buscar_veiculos_por_cliente(self, cod_cliente, total=200, indice=0):
        if self.falha_veiculos_no_indice is not None and indice == self.falha_veiculos_no_indice:
            raise SGRServerError('HTTP 502', status_code=502)
        inicio = 0 if self.ignora_indice_veiculos else indice
        return self.veiculos[inicio:inicio + total]

    def buscar_vinculos_por_placa(self, placa, total=200, indice=0):
        return []

    def buscar_boletos_cliente(self, cpf_cnpj, total=200, indice=0):
        return []

    def buscar_boletos_abertos_cliente(self, cpf_cnpj, total=200, indice=0):
        if self.falha_abertos:
            raise SGRServerError('HTTP 500', status_code=500)
        return []


@pytest.fixture()
def sem_espera(monkeypatch):
    from app.services.sgr_migration import poc
    monkeypatch.setattr(poc, '_ESPERA_ENTRE_TENTATIVAS', 0)


class TestColetaDoCliente:
    def test_cliente_com_201_veiculos_tem_a_frota_inteira(self, sem_espera):
        resultado = run_poc(FakeSGR(), limit=1)
        [cliente] = resultado.clients
        assert len(cliente.vehicles) == 201
        assert cliente.coleta_incompleta == []
        [veiculos] = [c for c in resultado.coletas if c['endpoint'] == '/buscar_veiculo']
        assert (veiculos['registros'], veiculos['completa'], veiculos['escopo']) == (201, True, 'cliente:1')
        [rastreadores] = [c for c in resultado.coletas if c['endpoint'] == '/buscar_rastreador']
        assert (rastreadores['registros'], rastreadores['completa']) == (250, True)

    def test_falha_na_segunda_pagina_bloqueia_o_cliente_em_vez_de_importar_metade(self, db, sem_espera):
        resultado = run_poc(FakeSGR(falha_veiculos_no_indice=200), limit=1)
        [cliente] = resultado.clients
        assert cliente.coleta_incompleta and cliente.vehicles == []
        stats = import_poc_result(db, resultado, dry_run=False)
        from app.models.client import Client
        assert db.query(Client).count() == 0
        assert stats.status_execucao == 'incompleta'

    def test_endpoint_que_repete_pagina_cheia_bloqueia_o_cliente(self, sem_espera):
        resultado = run_poc(FakeSGR(ignora_indice_veiculos=True), limit=1)
        assert 'SGRPaginacaoError' in resultado.clients[0].coleta_incompleta[0]

    def test_indice_de_rastreadores_incompleto_interrompe_a_leitura(self, sem_espera):
        with pytest.raises(SGRIncompleteScan, match='rastreadores'):
            run_poc(FakeSGR(falha_rastreador=True), limit=1)

    def test_boletos_em_aberto_nao_confirmados_nao_viram_cancelados(self, sem_espera):
        resultado = run_poc(FakeSGR(veiculos=0, falha_abertos=True), limit=1, com_boletos=True)
        [cliente] = resultado.clients
        assert cliente.billings == []
        assert any('em aberto' in p for p in cliente.coleta_incompleta)


# ---------------------------------------------------------------------------
# SGR-06 — download
# ---------------------------------------------------------------------------

class Resposta:
    def __init__(self, status=200, corpo=b'%PDF-1.4 x', headers=None, bloco=None):
        self.status_code = status
        self.headers = headers or {}
        self._corpo = corpo
        self._bloco = bloco or max(len(corpo), 1)
        self.fechada = False

    def iter_content(self, _tamanho):
        for i in range(0, len(self._corpo), self._bloco):
            yield self._corpo[i:i + self._bloco]

    def close(self):
        self.fechada = True


class Sessao:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.urls: list[str] = []

    def get(self, url, **kw):
        assert kw.get('allow_redirects') is False and kw.get('stream') is True
        self.urls.append(url)
        return self.respostas.pop(0)


def _dns(tabela):
    def resolver(host, porta, proto=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (tabela[host], porta))]
    return resolver


PUBLICO = _dns({'boletos.exemplo': '93.184.216.34', 'cdn.gateway.exemplo': '93.184.216.35',
                'interno.gateway.exemplo': '10.0.0.5', 'meta.gateway.exemplo': '169.254.169.254',
                'local.gateway.exemplo': '127.0.0.1'})
HOSTS = {'boletos.exemplo', '.gateway.exemplo'}


def _baixar(respostas, url='https://boletos.exemplo/b.pdf', **kw):
    sessao = Sessao(respostas)
    return download.baixar(url, session=sessao, resolver=PUBLICO, permitidos=HOSTS, **kw), sessao


class TestDownload:
    @pytest.mark.parametrize('url,motivo', [
        ('http://boletos.exemplo/b.pdf', 'esquema_nao_https'),
        ('https://outro.exemplo/b.pdf', 'host_nao_permitido'),
        ('https://gateway.exemplo.evil.com/b.pdf', 'host_nao_permitido'),
        ('https://user:senha@boletos.exemplo/b.pdf', 'credencial_na_url'),
        ('https://boletos.exemplo:8443/b.pdf', 'porta_nao_padrao'),
        ('https://127.0.0.1/b.pdf', 'host_nao_permitido'),
        ('file:///etc/passwd', 'esquema_nao_https'),
    ])
    def test_destino_recusado_sem_nenhuma_requisicao(self, url, motivo):
        sessao = Sessao([])
        with pytest.raises(download.DownloadRecusado) as exc:
            download.baixar(url, session=sessao, resolver=PUBLICO, permitidos=HOSTS)
        assert exc.value.motivo == motivo
        assert sessao.urls == []

    @pytest.mark.parametrize('host', ['interno.gateway.exemplo', 'meta.gateway.exemplo', 'local.gateway.exemplo'])
    def test_redirect_para_ip_interno_e_recusado_antes_de_conectar(self, host):
        with pytest.raises(download.DownloadRecusado) as exc:
            _baixar([Resposta(302, headers={'Location': f'https://{host}/latest/meta-data'})])
        assert exc.value.motivo == 'ip_nao_publico'

    def test_redirect_para_http_e_recusado(self):
        with pytest.raises(download.DownloadRecusado, match='esquema_nao_https'):
            _baixar([Resposta(301, headers={'Location': 'http://boletos.exemplo/b.pdf'})])

    def test_redirect_permitido_e_seguido_com_as_mesmas_regras(self):
        arquivo, sessao = _baixar([
            Resposta(302, headers={'Location': 'https://cdn.gateway.exemplo/b.pdf'}),
            Resposta(200, b'%PDF-1.7 ok'),
        ])
        assert arquivo.conteudo == b'%PDF-1.7 ok' and arquivo.host == 'cdn.gateway.exemplo'
        assert sessao.urls == ['https://boletos.exemplo/b.pdf', 'https://cdn.gateway.exemplo/b.pdf']

    def test_redirects_demais(self):
        voltas = [Resposta(302, headers={'Location': 'https://boletos.exemplo/b.pdf'}) for _ in range(5)]
        with pytest.raises(download.DownloadRecusado, match='redirects_demais'):
            _baixar(voltas, max_redirects=3)

    def test_corpo_acima_do_teto_e_cortado_no_streaming(self):
        grande = Resposta(200, b'%PDF-' + b'x' * 5000, bloco=1000)
        with pytest.raises(download.DownloadRecusado, match='arquivo_grande_demais'):
            _baixar([grande], max_bytes=2048)

    def test_content_length_acima_do_teto_nem_le_o_corpo(self):
        with pytest.raises(download.DownloadRecusado, match='arquivo_grande_demais'):
            _baixar([Resposta(200, b'%PDF-x', headers={'Content-Length': '999999999'})], max_bytes=2048)

    def test_5xx_e_falha_transitoria(self):
        with pytest.raises(download.DownloadFalhou):
            _baixar([Resposta(503)])

    def test_host_da_base_do_sgr_e_sempre_permitido(self, monkeypatch):
        monkeypatch.setattr(settings, 'sgr_download_hosts', '')
        monkeypatch.setattr(settings, 'sgr_base_url', 'https://sgr.hinova.com.br/sgr/api')
        assert download.hosts_permitidos() == {'sgr.hinova.com.br'}

    def test_ipv6_mapeado_para_loopback_e_recusado(self):
        resolver = lambda host, porta, proto=None: [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:127.0.0.1', porta, 0, 0))]  # noqa: E731
        with pytest.raises(download.DownloadRecusado, match='ip_nao_publico'):
            download.baixar('https://boletos.exemplo/b.pdf', session=Sessao([]), resolver=resolver, permitidos=HOSTS)


class TestConteudo:
    def test_html_disfarcado_de_pdf(self):
        with pytest.raises(download.DownloadRecusado, match='conteudo_nao_pdf'):
            download.validar_pdf(b'<html><body>%PDF-</body></html>')

    @pytest.mark.parametrize('xml', [
        b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>',
        b'<?xml version="1.0"?><!DOCTYPE a SYSTEM "file:///etc/passwd"><a/>',
        b'<!doctype a><a/>',
    ])
    def test_xml_com_dtd_ou_entidade_e_recusado(self, xml):
        with pytest.raises(download.DownloadRecusado, match='xml_com_dtd'):
            download.validar_xml(xml)

    def test_xml_malformado(self):
        with pytest.raises(download.DownloadRecusado, match='xml_malformado'):
            download.validar_xml(b'<nfse><numero>1</nfse>')

    def test_xml_valido(self):
        download.validar_xml(b'<?xml version="1.0" encoding="UTF-8"?><CompNfse><Numero>2210</Numero></CompNfse>')
