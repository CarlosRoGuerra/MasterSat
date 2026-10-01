"""Interoperabilidade local: DPS real, XMLDSig e mTLS, sem serviços fiscais."""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from lxml import etree
from signxml import XMLVerifier, SignatureConfiguration, SignatureMethod, DigestAlgorithm
from signxml.exceptions import InvalidSignature

from app.core.config import settings
from app.services import nfse_nacional as nf
from tests.test_nfse_nacional import _billing, _client


@pytest.fixture(scope='module')
def cadeia_sintetica():
    agora = dt.datetime.now(dt.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'CA sintética Fase 05')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(agora - dt.timedelta(days=1)).not_valid_after(agora + dt.timedelta(days=3))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                      key_encipherment=False, data_encipherment=False,
                                      key_agreement=False, key_cert_sign=True, crl_sign=True,
                                      encipher_only=None, decipher_only=None), critical=True)
          .sign(ca_key, hashes.SHA256()))

    def emitir(nome, uso, servidor=False):
        chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        builder = (x509.CertificateBuilder()
                   .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nome)]))
                   .issuer_name(ca_name).public_key(chave.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(agora - dt.timedelta(days=1)).not_valid_after(agora + dt.timedelta(days=2))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.ExtendedKeyUsage([uso]), critical=False))
        if servidor:
            builder = builder.add_extension(x509.SubjectAlternativeName([
                x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
            ]), critical=False)
        cert = builder.sign(ca_key, hashes.SHA256())
        return (
            chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM),
        )

    return {
        'ca': ca.public_bytes(serialization.Encoding.PEM),
        'server': emitir('localhost', ExtendedKeyUsageOID.SERVER_AUTH, servidor=True),
        'client': emitir('Cliente sintético:00000000000191', ExtendedKeyUsageOID.CLIENT_AUTH),
        'client_renovado': emitir('Cliente renovado:00000000000191', ExtendedKeyUsageOID.CLIENT_AUTH),
    }


def _verificar(xml, certificado):
    return XMLVerifier().verify(
        xml, x509_cert=certificado, id_attribute='Id',
        expect_config=SignatureConfiguration(
            location='./', expect_references=1,
            signature_methods=frozenset({SignatureMethod.RSA_SHA256}),
            digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
        ),
    )


def test_dps_real_assina_verifica_referencia_namespace_c14n_utf8_xsd(monkeypatch, cadeia_sintetica):
    material = cadeia_sintetica['client']
    monkeypatch.setattr(nf, '_material_certificado', lambda: material)
    dps = nf.montar_dps(_billing(title='Monitoramento — manutenção, ação e conexão'), _client(), '983')
    assinada = nf.assinar_dps(dps)
    xml = nf._serializar_dps(assinada)
    assinada = etree.fromstring(xml)  # namespaces efetivos no documento transmitido
    assert xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")
    assert 'manutenção'.encode() in xml
    assert all(elemento.prefix is None for elemento in assinada.iter())
    ns = {'ds': nf.NS_DSIG, 'n': nf.NS_NFSE}
    referencia = assinada.find('./ds:Signature/ds:SignedInfo/ds:Reference', ns)
    assert referencia.get('URI') == '#' + assinada.find('n:infDPS', ns).get('Id')
    assert assinada.find('./ds:Signature/ds:SignedInfo/ds:CanonicalizationMethod', ns).get('Algorithm') == 'http://www.w3.org/2001/10/xml-exc-c14n#'
    assert [t.get('Algorithm') for t in referencia.findall('ds:Transforms/ds:Transform', ns)] == [
        'http://www.w3.org/2000/09/xmldsig#enveloped-signature',
        'http://www.w3.org/2001/10/xml-exc-c14n#',
    ]
    verificado = _verificar(xml, material[1]).signed_xml
    assert verificado.tag == f'{{{nf.NS_NFSE}}}infDPS'
    assert verificado.get('Id') == nf.id_dps('983')
    assert 'manutenção' in verificado.findtext('n:serv/n:cServ/n:xDescServ', namespaces=ns)
    # Exercita o XSD oficial com a única exceção TSSerieDPS já documentada;
    # atualização das bibliotecas não acrescenta nenhuma dispensa de validação.
    nf.validar_dps(etree.fromstring(xml))


@pytest.mark.parametrize('tag,novo', [('xDescServ', 'conteúdo alterado'), ('vServ', '999.00')])
def test_mutacao_da_dps_invalida_assinatura(monkeypatch, cadeia_sintetica, tag, novo):
    material = cadeia_sintetica['client']
    monkeypatch.setattr(nf, '_material_certificado', lambda: material)
    assinada = nf.assinar_dps(nf.montar_dps(_billing(), _client(), '984'))
    assinada.find(f'.//{{{nf.NS_NFSE}}}{tag}').text = novo
    with pytest.raises(InvalidSignature):
        _verificar(nf._serializar_dps(assinada), material[1])


def test_perfil_municipal_sha1_nao_desativa_validacao(monkeypatch, cadeia_sintetica):
    from app.services import nfse_joinville
    monkeypatch.setattr(nf, '_material_certificado', lambda: cadeia_sintetica['client'])
    envio, rps, inf, rps_id, lote_id = nfse_joinville.montar_envio_lote(_billing(), _client(), '1', '1')
    with pytest.raises(nfse_joinville.NfseError, match='Perfil de assinatura municipal'):
        nfse_joinville._assinar_lote(envio, inf, rps, rps_id, lote_id)


def test_mtls_real_local_valida_ca_e_adota_certificado_renovado(monkeypatch, tmp_path, cadeia_sintetica):
    """Handshake real SSL + requests, exclusivamente 127.0.0.1 e PKI efêmera."""
    ca_path, server_key, server_cert = (tmp_path / nome for nome in ('ca.pem', 'server.key', 'server.pem'))
    ca_path.write_bytes(cadeia_sintetica['ca'])
    server_key.write_bytes(cadeia_sintetica['server'][0])
    server_cert.write_bytes(cadeia_sintetica['server'][1])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            fingerprint = hashlib.sha256(self.connection.getpeercert(binary_form=True)).hexdigest()
            data = json.dumps({'certificado': fingerprint}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            return

    servidor = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.verify_mode = ssl.CERT_REQUIRED
    contexto.load_verify_locations(cafile=str(ca_path))
    contexto.load_cert_chain(certfile=str(server_cert), keyfile=str(server_key))
    servidor.socket = contexto.wrap_socket(servidor.socket, server_side=True)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    url = f'https://127.0.0.1:{servidor.server_port}'
    session = requests.Session()
    session.trust_env = False
    arquivos_usados = []

    def post_local(url_request, **kwargs):
        arquivos_usados.extend(kwargs['cert'])
        return session.post(url_request, verify=str(ca_path), **kwargs)

    monkeypatch.setattr(nf, '_ambiente', lambda *args: ('2', url))
    monkeypatch.setattr(nf.requests, 'post', post_local)
    try:
        for nome in ('client', 'client_renovado'):
            material = cadeia_sintetica[nome]
            monkeypatch.setattr(nf, '_material_certificado', lambda: material)
            resposta = nf._post('/nfse', {'sintetico': True})
            esperado = x509.load_pem_x509_certificate(material[1]).fingerprint(hashes.SHA256()).hex()
            assert resposta.json()['certificado'] == esperado
            assert all(not Path(p).exists() for p in arquivos_usados)
        # O servidor exige certificado: ausência é falha TLS, não sucesso HTTP.
        with pytest.raises(requests.exceptions.SSLError):
            session.post(url, json={}, verify=str(ca_path), timeout=5)
        # O cliente valida a CA do servidor; nenhuma chamada usa verify=False.
        with nf._par_pem_mtls() as par:
            with pytest.raises(requests.exceptions.SSLError):
                session.post(url, json={}, cert=par, timeout=5)
    finally:
        session.close()
        servidor.shutdown()
        servidor.server_close()
        thread.join(5)
