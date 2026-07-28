"""Testes do cadastro do certificado digital A1 da NFS-e."""
from __future__ import annotations

import datetime as dt

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from app.core.config import settings
from app.models.nfse_certificado import NfseCertificado
from app.services import nfse_certificado

VALID_KEY = Fernet.generate_key().decode()
SENHA = 'senha-de-teste'


@pytest.fixture(autouse=True)
def _chave_de_criptografia(monkeypatch):
    monkeypatch.setattr(settings, 'ailos_token_encryption_key', VALID_KEY)
    from app.core.crypto import _fernet
    _fernet.cache_clear()
    yield
    _fernet.cache_clear()


def _pfx(*, cn='MASTERSAT COMERCIO LTDA:14228344000167', dias_validade=365, senha=SENHA) -> bytes:
    """Gera um .pfx autoassinado para os testes."""
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    agora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'AC DE TESTE')]))
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        # p/ gerar um certificado vencido (dias_validade negativo), o início
        # precisa recuar junto — senão a própria lib recusa montar o cert.
        .not_valid_before(agora + dt.timedelta(days=dias_validade) - dt.timedelta(days=1))
        .not_valid_after(agora + dt.timedelta(days=dias_validade))
        .sign(chave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b'teste', chave, cert, None,
        serialization.BestAvailableEncryption(senha.encode()),
    )


# ---------------------------------------------------------------------------
# Leitura do arquivo
# ---------------------------------------------------------------------------

def test_inspecionar_extrai_titular_cnpj_e_validade():
    dados = nfse_certificado.inspecionar(_pfx(), SENHA)
    assert dados['titular'] == 'MASTERSAT COMERCIO LTDA:14228344000167'
    assert dados['cnpj'] == '14228344000167'
    assert dados['emissor'] == 'AC DE TESTE'
    assert dados['valido_ate'] > dt.datetime.now(dt.timezone.utc)


def test_inspecionar_recusa_senha_errada():
    with pytest.raises(nfse_certificado.CertificadoError, match='senha'):
        nfse_certificado.inspecionar(_pfx(), 'senha-errada')


def test_inspecionar_recusa_arquivo_que_nao_e_pfx():
    with pytest.raises(nfse_certificado.CertificadoError):
        nfse_certificado.inspecionar(b'isto nao e um certificado', SENHA)


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------

def test_salvar_guarda_arquivo_e_senha_criptografados(db):
    conteudo = _pfx()
    registro = nfse_certificado.salvar(db, conteudo, SENHA, nome_arquivo='ecnpj.pfx')

    assert registro.ativo is True
    assert registro.cnpj == '14228344000167'
    # nada em claro no banco
    assert registro.arquivo_cifrado != conteudo
    assert SENHA not in registro.senha_cifrada


def test_salvar_desativa_o_certificado_anterior(db):
    antigo = nfse_certificado.salvar(db, _pfx(), SENHA)
    novo = nfse_certificado.salvar(db, _pfx(cn='OUTRO TITULAR:99999999000191'), SENHA)

    db.refresh(antigo)
    assert antigo.ativo is False
    assert novo.ativo is True
    assert nfse_certificado.obter_ativo(db).id == novo.id
    # o anterior fica no histórico
    assert db.query(NfseCertificado).count() == 2


def test_salvar_recusa_certificado_vencido(db):
    vencido = _pfx(dias_validade=-10)
    with pytest.raises(nfse_certificado.CertificadoError, match='vencido'):
        nfse_certificado.salvar(db, vencido, SENHA)


def test_material_pem_devolve_chave_e_certificado(db):
    nfse_certificado.salvar(db, _pfx(), SENHA)
    chave_pem, cert_pem = nfse_certificado.material_pem(db)
    assert b'PRIVATE KEY' in chave_pem
    assert b'BEGIN CERTIFICATE' in cert_pem


def test_material_pem_sem_certificado_cadastrado(db):
    assert nfse_certificado.material_pem(db) is None


def test_para_dict_nao_expoe_arquivo_nem_senha(db):
    registro = nfse_certificado.salvar(db, _pfx(), SENHA)
    d = nfse_certificado.para_dict(registro)
    assert 'arquivo_cifrado' not in d and 'senha_cifrada' not in d
    assert d['titular'] and d['dias_para_vencer'] > 0
    assert d['vencido'] is False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_endpoint_get_sem_certificado_devolve_nulo(db, http):
    r = http.get('/api/v1/nfse/certificado')
    assert r.status_code == 200
    assert r.json() is None


def test_endpoint_upload_e_consulta(db, http):
    r = http.post(
        '/api/v1/nfse/certificado',
        files={'arquivo': ('ecnpj.pfx', _pfx(), 'application/x-pkcs12')},
        data={'senha': SENHA},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body['cnpj'] == '14228344000167'
    assert body['ativo'] is True

    atual = http.get('/api/v1/nfse/certificado').json()
    assert atual['id'] == body['id']


def test_endpoint_upload_com_senha_errada_retorna_422(db, http):
    r = http.post(
        '/api/v1/nfse/certificado',
        files={'arquivo': ('ecnpj.pfx', _pfx(), 'application/x-pkcs12')},
        data={'senha': 'errada'},
    )
    assert r.status_code == 422
    assert 'senha' in r.json()['detail']


def test_endpoint_upload_exige_admin(db, http_fin):
    """FINANCEIRO consulta, mas não troca o certificado."""
    assert http_fin.get('/api/v1/nfse/certificado').status_code == 200
    r = http_fin.post(
        '/api/v1/nfse/certificado',
        files={'arquivo': ('ecnpj.pfx', _pfx(), 'application/x-pkcs12')},
        data={'senha': SENHA},
    )
    assert r.status_code == 403
