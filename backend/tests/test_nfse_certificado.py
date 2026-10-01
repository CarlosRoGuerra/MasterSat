"""Testes do cadastro do certificado digital A1 da NFS-e."""
from __future__ import annotations

import datetime as dt
import hashlib
import multiprocessing
from pathlib import Path

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
    nfse_certificado._invalidar_cache()
    yield
    _fernet.cache_clear()
    nfse_certificado._invalidar_cache()


def _pfx(*, cn='MASTERSAT COMERCIO LTDA:14228344000167', dias_validade=365, senha=SENHA, dias_inicio=None) -> bytes:
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
        .not_valid_before(agora + dt.timedelta(days=min(-1, dias_validade - 1) if dias_inicio is None else dias_inicio))
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


def test_validade_funciona_na_cryptography_antiga():
    """
    O container roda cryptography 41 (presa por pyOpenSSL<24, exigida pelo
    signxml), que NÃO tem not_valid_before_utc — só not_valid_before, ingênuo.
    Este teste simula essa versão; sem o fallback, a leitura do .pfx quebrava
    com AttributeError só dentro do container.
    """
    class CertAntigo:
        not_valid_before = dt.datetime(2026, 7, 24, 14, 0, 0)   # sem tzinfo
        not_valid_after = dt.datetime(2027, 7, 24, 14, 0, 0)

        def __getattr__(self, nome):  # o _utc não existe nessa versão
            raise AttributeError(nome)

    inicio, fim = nfse_certificado._validade(CertAntigo())
    assert inicio.tzinfo is dt.timezone.utc and fim.tzinfo is dt.timezone.utc
    assert fim.year == 2027


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


def test_salvar_recusa_certificado_ainda_nao_valido(db):
    with pytest.raises(nfse_certificado.CertificadoError, match='ainda não'):
        nfse_certificado.salvar(db, _pfx(dias_inicio=2), SENHA)


def test_cache_revalida_vencimento_em_cada_utilizacao(db, monkeypatch):
    nfse_certificado.salvar(db, _pfx(dias_validade=1), SENHA)
    nfse_certificado.material_pem(db)  # aquece cache com certificado válido
    agora = dt.datetime.now(dt.timezone.utc)
    original = dt.datetime

    class RelogioFuturo(original):
        @classmethod
        def now(cls, tz=None):
            return agora + dt.timedelta(days=2)

    monkeypatch.setattr(nfse_certificado.dt, 'datetime', RelogioFuturo)
    with pytest.raises(nfse_certificado.CertificadoError, match='vencido'):
        nfse_certificado.material_pem(db)


def test_renovacao_nao_depende_de_invalidacao_local(db, monkeypatch):
    nfse_certificado.salvar(db, _pfx(), SENHA)
    anterior = nfse_certificado.material_pem(db)
    monkeypatch.setattr(nfse_certificado, '_invalidar_cache', lambda: None)
    nfse_certificado.salvar(db, _pfx(cn='NOVO:99999999000191'), SENHA)
    assert nfse_certificado.material_pem(db) != anterior


def test_falha_banco_nao_ressuscita_certificado_env(monkeypatch, tmp_path):
    from sqlalchemy.exc import OperationalError
    from app.db import session
    from app.services import nfse_nacional

    arquivo = tmp_path / 'anterior.pfx'
    arquivo.write_bytes(_pfx())
    monkeypatch.setattr(settings, 'nfse_cert_path', str(arquivo))
    monkeypatch.setattr(settings, 'nfse_cert_senha', SENHA)

    def indisponivel():
        raise OperationalError('offline', {}, Exception('offline'))

    monkeypatch.setattr(session, 'SessionLocal', indisponivel)
    with pytest.raises(nfse_nacional.NfseError, match='cadastrado'):
        nfse_nacional._material_certificado()


def test_arquivo_env_substituido_e_validade_conferida(monkeypatch, tmp_path):
    from app.services import nfse_nacional

    arquivo = tmp_path / 'teste.pfx'
    arquivo.write_bytes(_pfx())
    monkeypatch.setattr(nfse_nacional, '_material_do_banco', lambda: None)
    monkeypatch.setattr(settings, 'nfse_cert_path', str(arquivo))
    monkeypatch.setattr(settings, 'nfse_cert_senha', SENHA)
    anterior = nfse_nacional._material_certificado()
    arquivo.write_bytes(_pfx(cn='NOVO:99999999000191'))
    assert nfse_nacional._material_certificado() != anterior
    arquivo.write_bytes(_pfx(dias_validade=-2))
    with pytest.raises(nfse_nacional.NfseError, match='vencido'):
        nfse_nacional._material_certificado()


def test_pem_de_chamada_ativa_sobrevive_renovacao_e_limpa_no_final(db, monkeypatch):
    from app.services import nfse_nacional

    nfse_certificado.salvar(db, _pfx(), SENHA)
    monkeypatch.setattr(nfse_nacional, '_material_do_banco', lambda: nfse_certificado.material_pem(db))
    with nfse_nacional._par_pem_mtls() as anterior:
        certificado_anterior = Path(anterior[0]).read_bytes()
        nfse_certificado.salvar(db, _pfx(cn='NOVO:99999999000191'), SENHA)
        with nfse_nacional._par_pem_mtls() as novo:
            assert Path(novo[0]).read_bytes() != certificado_anterior
            assert Path(anterior[0]).read_bytes() == certificado_anterior
        assert not Path(novo[0]).parent.exists()
        assert Path(anterior[1]).exists()
    assert not Path(anterior[0]).parent.exists()


def test_pem_removido_quando_transporte_falha(db, monkeypatch):
    import requests
    from app.services import nfse_nacional

    nfse_certificado.salvar(db, _pfx(), SENHA)
    monkeypatch.setattr(nfse_nacional, '_material_do_banco', lambda: nfse_certificado.material_pem(db))
    usados = []

    def timeout(*args, **kwargs):
        usados.extend(kwargs['cert'])
        assert all(Path(p).exists() for p in usados)
        raise requests.Timeout('sintético')

    monkeypatch.setattr(nfse_nacional.requests, 'post', timeout)
    with pytest.raises(nfse_nacional.NfseApiError):
        nfse_nacional._post('/nfse', {})
    assert usados and not any(Path(p).exists() for p in usados)


def _worker_certificado_versionado(database_url, chave_fernet, conexao):
    """Processo independente: preserva o cache entre as duas leituras."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.core.crypto import _fernet

    settings.ailos_token_encryption_key = chave_fernet
    _fernet.cache_clear()
    engine = create_engine(database_url)
    try:
        for _ in range(2):
            conexao.recv()
            with Session(engine) as sessao:
                _, certificado = nfse_certificado.material_pem(sessao)
                conexao.send(hashlib.sha256(certificado).hexdigest())
    finally:
        engine.dispose()
        conexao.close()


def test_renovacao_adotada_por_dois_processos_sem_restart(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.core.crypto import decrypt_bytes, decrypt_token

    database_url = f'sqlite:///{tmp_path / "certificados.sqlite"}'
    engine = create_engine(database_url)
    NfseCertificado.__table__.create(engine)
    contexto = multiprocessing.get_context('spawn')
    canais, processos = [], []
    try:
        with Session(engine) as sessao:
            conteudo = _pfx()
            antigo = nfse_certificado.salvar(sessao, conteudo, SENHA)
            for _ in range(2):
                pai, filho = contexto.Pipe()
                processo = contexto.Process(target=_worker_certificado_versionado, args=(database_url, VALID_KEY, filho))
                processo.start()
                filho.close()
                canais.append(pai)
                processos.append(processo)
            for canal in canais:
                canal.send('ler')
            for canal in canais:
                assert canal.poll(30), 'worker não terminou a primeira leitura'
            antigos = [canal.recv() for canal in canais]
            novo = nfse_certificado.salvar(sessao, _pfx(cn='RENOVADO:99999999000191'), SENHA)
            esperado = hashlib.sha256(nfse_certificado.material_pem(sessao)[1]).hexdigest()
            for canal in canais:
                canal.send('ler')
            for canal in canais:
                assert canal.poll(30), 'worker não percebeu a renovação'
            assert [canal.recv() for canal in canais] == [esperado, esperado]
            assert antigos[0] == antigos[1] != esperado
            sessao.refresh(antigo)
            assert not antigo.ativo and novo.ativo
            assert decrypt_bytes(antigo.arquivo_cifrado) == conteudo
            assert decrypt_token(antigo.senha_cifrada) == SENHA
    finally:
        for processo in processos:
            processo.join(10)
            if processo.is_alive():
                processo.terminate()
                processo.join(10)
        for canal in canais:
            canal.close()
        engine.dispose()


def _registro_com_validade(dias: int) -> NfseCertificado:
    """Registro em memória com vencimento relativo — para testar o alerta."""
    return NfseCertificado(
        titular='TESTE', arquivo_cifrado=b'x', senha_cifrada='y',
        valido_ate=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=dias, hours=1),
    )


@pytest.mark.parametrize('dias,esperado_vencido', [(365, False), (10, False), (-5, True)])
def test_para_dict_calcula_tempo_restante(dias, esperado_vencido):
    d = nfse_certificado.para_dict(_registro_com_validade(dias))
    assert d['vencido'] is esperado_vencido
    assert d['dias_para_vencer'] == dias


def test_para_dict_aceita_data_sem_fuso(db):
    """O SQLite devolve datetime ingênuo; comparar com aware levantava TypeError."""
    registro = NfseCertificado(
        titular='TESTE', arquivo_cifrado=b'x', senha_cifrada='y',
        valido_ate=dt.datetime.now() + dt.timedelta(days=30),  # sem tzinfo
    )
    d = nfse_certificado.para_dict(registro)
    assert d['vencido'] is False
    assert d['dias_para_vencer'] is not None


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
