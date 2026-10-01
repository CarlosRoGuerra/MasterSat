"""
Download controlado dos arquivos que a origem aponta (SGR-06).

O SGR devolve URLs — PDF do boleto em aberto, XML da NFS-e num gateway de
terceiros — e o importador as baixava com ``requests.get(url)``: redirect
automático para qualquer lugar, corpo inteiro na memória, nenhuma checagem
de esquema, host ou IP. Uma origem comprometida (ou um dado adulterado)
poderia apontar para a rede interna (metadados de nuvem, MinIO, PostgreSQL)
ou mandar um arquivo gigante.

Regras (todas no servidor, nenhuma depende de tela):
  * só ``https`` na porta padrão, sem usuário/senha na URL;
  * host na allowlist (``SGR_DOWNLOAD_HOSTS`` + host de ``SGR_BASE_URL``);
  * todo IP resolvido tem de ser público — loopback, privado, link-local,
    multicast, reservado e não especificado são recusados;
  * redirect não é seguido pelo ``requests``: cada salto passa pelas mesmas
    checagens, até ``SGR_DOWNLOAD_MAX_REDIRECTS``;
  * corpo lido em streaming com teto (``SGR_DOWNLOAD_MAX_BYTES``), também
    contra ``Content-Length`` mentiroso;
  * conteúdo validado pelo que é, não pelo MIME declarado: PDF precisa do
    cabeçalho ``%PDF-``; XML não pode ter DOCTYPE/ENTITY e precisa ser bem
    formado (parser sem rede, sem DTD, sem expansão de entidade).

Limitação conhecida: a resolução DNS é conferida antes da conexão, mas o
``requests`` resolve de novo ao conectar (janela de DNS rebinding). A
allowlist de hosts é o controle principal; ver docs/migracao-sgr/hosts-permitidos.md.
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from app.core.config import settings

_REDIRECTS = (301, 302, 303, 307, 308)
_BLOCO = 64 * 1024


class DownloadRecusado(Exception):
    """O destino ou o conteúdo não passou numa regra. ``motivo`` é um código
    curto (vai para o outbox e para o relatório — nunca a URL, que pode ter
    token)."""

    def __init__(self, motivo: str, detalhe: str = ''):
        self.motivo = motivo
        super().__init__(f'{motivo}{": " + detalhe if detalhe else ""}')


class DownloadFalhou(Exception):
    """Falha transitória (rede, timeout, 5xx): vale tentar de novo depois."""


@dataclass(frozen=True)
class ArquivoBaixado:
    conteudo: bytes
    sha256: str
    host: str


def hosts_permitidos() -> set[str]:
    hosts = {h.strip().lower() for h in (settings.sgr_download_hosts or '').split(',') if h.strip()}
    base = urlsplit(settings.sgr_base_url or '').hostname
    if base:
        hosts.add(base.lower())
    return hosts


def _host_permitido(host: str, permitidos: set[str]) -> bool:
    for regra in permitidos:
        if regra.startswith('.'):
            if host.endswith(regra) and len(host) > len(regra):
                return True
        elif host == regra:
            return True
    return False


def host_da_url(url: str | None) -> str | None:
    """Só o host (para relatório/decisão). Nunca devolve caminho ou query."""
    try:
        return (urlsplit(url or '').hostname or None) if url else None
    except ValueError:
        return None


def validar_url(url: str, permitidos: set[str] | None = None) -> str:
    """Checagens que não dependem de rede. Devolve o host (minúsculo)."""
    permitidos = hosts_permitidos() if permitidos is None else permitidos
    try:
        partes = urlsplit(url)
        porta = partes.port
    except ValueError as exc:
        raise DownloadRecusado('url_invalida') from exc
    if partes.scheme.lower() != 'https':
        raise DownloadRecusado('esquema_nao_https', partes.scheme or '(vazio)')
    if partes.username or partes.password:
        raise DownloadRecusado('credencial_na_url')
    host = (partes.hostname or '').lower()
    if not host:
        raise DownloadRecusado('url_sem_host')
    if porta not in (None, 443):
        raise DownloadRecusado('porta_nao_padrao', str(porta))
    if not _host_permitido(host, permitidos):
        raise DownloadRecusado('host_nao_permitido', host)
    return host


def _ip_publico(ip: str) -> bool:
    endereco = ipaddress.ip_address(ip.split('%', 1)[0])
    if isinstance(endereco, ipaddress.IPv6Address) and endereco.ipv4_mapped:
        endereco = endereco.ipv4_mapped
    return endereco.is_global and not (
        endereco.is_private or endereco.is_loopback or endereco.is_link_local
        or endereco.is_multicast or endereco.is_reserved or endereco.is_unspecified
    )


def conferir_ips(host: str, resolver=socket.getaddrinfo) -> None:
    try:
        infos = resolver(host, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise DownloadFalhou(f'DNS não resolveu {host}') from exc
    ips = {info[4][0] for info in infos}
    if not ips:
        raise DownloadFalhou(f'DNS sem endereço para {host}')
    for ip in ips:
        if not _ip_publico(ip):
            raise DownloadRecusado('ip_nao_publico', host)


def baixar(
    url: str,
    *,
    session=None,
    resolver=socket.getaddrinfo,
    permitidos: set[str] | None = None,
    max_bytes: int | None = None,
    max_redirects: int | None = None,
    timeout: int | None = None,
) -> ArquivoBaixado:
    """Baixa `url` aplicando todas as regras do módulo."""
    import requests

    session = session or requests.Session()
    permitidos = hosts_permitidos() if permitidos is None else permitidos
    limite = max_bytes or settings.sgr_download_max_bytes
    saltos = settings.sgr_download_max_redirects if max_redirects is None else max_redirects
    timeout = timeout or settings.sgr_timeout_seconds

    atual = url
    for _ in range(saltos + 1):
        host = validar_url(atual, permitidos)
        conferir_ips(host, resolver)
        try:
            resposta = session.get(atual, stream=True, allow_redirects=False, timeout=timeout)
        except requests.RequestException as exc:
            raise DownloadFalhou(type(exc).__name__) from exc
        try:
            if resposta.status_code in _REDIRECTS:
                destino = resposta.headers.get('Location')
                if not destino:
                    raise DownloadRecusado('redirect_sem_destino')
                atual = urljoin(atual, destino)
                continue
            if resposta.status_code >= 500 or resposta.status_code == 429:
                raise DownloadFalhou(f'HTTP {resposta.status_code}')
            if resposta.status_code >= 400:
                raise DownloadRecusado('http_erro', str(resposta.status_code))
            declarado = resposta.headers.get('Content-Length')
            if declarado and declarado.isdigit() and int(declarado) > limite:
                raise DownloadRecusado('arquivo_grande_demais', declarado)
            partes: list[bytes] = []
            total = 0
            try:
                for bloco in resposta.iter_content(_BLOCO):
                    if not bloco:
                        continue
                    total += len(bloco)
                    if total > limite:
                        raise DownloadRecusado('arquivo_grande_demais', f'>{limite}')
                    partes.append(bloco)
            except requests.RequestException as exc:
                raise DownloadFalhou(type(exc).__name__) from exc
            conteudo = b''.join(partes)
            return ArquivoBaixado(conteudo, hashlib.sha256(conteudo).hexdigest(), host)
        finally:
            resposta.close()
    raise DownloadRecusado('redirects_demais', str(saltos))


def validar_pdf(conteudo: bytes) -> None:
    if not conteudo.startswith(b'%PDF-'):
        raise DownloadRecusado('conteudo_nao_pdf')


def validar_xml(conteudo: bytes) -> None:
    if not conteudo.strip():
        raise DownloadRecusado('xml_vazio')
    cabeca = conteudo.upper()
    if b'<!DOCTYPE' in cabeca or b'<!ENTITY' in cabeca:
        raise DownloadRecusado('xml_com_dtd')
    from lxml import etree

    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False, huge_tree=False,
    )
    try:
        etree.fromstring(conteudo, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise DownloadRecusado('xml_malformado') from exc
