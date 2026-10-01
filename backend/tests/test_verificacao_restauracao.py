"""Verificação pós-restauração (OPS-01/OPS-02): chave Fernet, PFX, objetos e saldo."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from app.core import crypto
from app.core.config import settings
from app.models.ailos_client_token import AilosClientToken
from app.models.ailos_integration import AilosIntegration
from app.models.ailos_retorno_arquivo import AilosRetornoArquivo
from app.models.billing import Billing
from app.models.document import Document
from app.models.enums import BillingStatus
from app.models.nfse_certificado import NfseCertificado
from app.models.system_setting import SystemSetting
from app.services.verificacao_restauracao import tamanho_em_diretorio, verificar
from scripts.verificar_restauracao import main as cli_main

CHAVE = Fernet.generate_key().decode()
SENHA_PFX = 'senha-pfx-sintetica'


def _usar_chave(monkeypatch, chave: str) -> None:
    monkeypatch.setattr(settings, 'ailos_token_encryption_key', chave)
    crypto._fernet.cache_clear()


def _pfx(senha: str = SENHA_PFX, dias: int = 365) -> bytes:
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agora = dt.datetime.now(dt.timezone.utc)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'EMPRESA SINTETICA:00000000000191')])
    cert = (
        x509.CertificateBuilder().subject_name(nome).issuer_name(nome)
        .public_key(chave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(agora + dt.timedelta(days=dias) - dt.timedelta(days=1))
        .not_valid_after(agora + dt.timedelta(days=dias))
        .sign(chave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b'teste', chave, cert, None, serialization.BestAvailableEncryption(senha.encode()),
    )


@pytest.fixture()
def objetos(tmp_path):
    raiz = tmp_path / 'objetos'
    (raiz / 'clients' / '1' / 'documents').mkdir(parents=True)
    (raiz / 'ailos' / 'retorno').mkdir(parents=True)
    (raiz / 'clients' / '1' / 'documents' / 'contrato.pdf').write_bytes(b'%PDF-1.4 sintetico')
    (raiz / 'ailos' / 'retorno' / 'r1.ret').write_bytes(b'retorno')
    return raiz


@pytest.fixture()
def banco_restaurado(db, cliente, monkeypatch):
    """Banco com um segredo de cada tipo, documentos e cobranças — tudo sintético."""
    _usar_chave(monkeypatch, CHAVE)
    db.add(AilosClientToken(environment='producao', access_token_encrypted=crypto.encrypt_token('token-app'),
                            expires_at=dt.datetime.now(dt.timezone.utc)))
    db.add(AilosIntegration(numero_convenio='1', cooperado_token_encrypted=crypto.encrypt_token('token-coop')))
    db.add(SystemSetting(key='smtp_password_enc', value=crypto.encrypt_token('senha-smtp')))
    db.add(NfseCertificado(titular='EMPRESA SINTETICA', arquivo_cifrado=crypto.encrypt_bytes(_pfx()),
                           senha_cifrada=crypto.encrypt_token(SENHA_PFX), ativo=True))
    db.add(Document(file_name='contrato.pdf', object_key='clients/1/documents/contrato.pdf',
                    content_type='application/pdf', size_bytes=len(b'%PDF-1.4 sintetico'),
                    reference_type='client', reference_id=cliente.id, active=True))
    # Excluído: o objeto foi removido do storage e a linha fica inativa.
    db.add(Document(file_name='velho.pdf', object_key='clients/1/documents/velho.pdf',
                    content_type='application/pdf', size_bytes=10,
                    reference_type='client', reference_id=cliente.id, active=False))
    db.add(AilosRetornoArquivo(numero_convenio='1', data_movimento=dt.date(2026, 9, 1),
                               storage_object_key='ailos/retorno/r1.ret'))
    db.add_all([
        Billing(client_id=cliente.id, amount=Decimal('129.90'), due_date=dt.date(2026, 9, 10),
                status=BillingStatus.PENDING),
        Billing(client_id=cliente.id, amount=Decimal('0.10'), due_date=dt.date(2026, 9, 10),
                status=BillingStatus.PENDING),
        Billing(client_id=cliente.id, amount=Decimal('50.00'), paid_amount=Decimal('50.00'),
                due_date=dt.date(2026, 8, 10), status=BillingStatus.PAID),
    ])
    db.commit()
    return db.get_bind()


def test_aprova_quando_chave_pfx_e_objetos_conferem(banco_restaurado, objetos):
    res = verificar(banco_restaurado, tamanho_em_diretorio(str(objetos)))
    assert res.erros == []
    assert res.aprovado
    assert res.segredos['ailos_client_tokens'] == {'total': 1, 'decifrados': 1, 'falhas': 0}
    assert res.segredos['ailos_integrations']['decifrados'] == 1
    assert res.segredos['smtp_password']['decifrados'] == 1
    assert res.segredos['nfse_certificados']['decifrados'] == 1
    assert res.certificados[0]['abre'] is True and res.certificados[0]['vencido'] is False
    assert res.objetos == {'referencias_obrigatorias': 2, 'ausentes': 0,
                           'tamanho_divergente': 0, 'inativos_sem_objeto': 1}


def test_saldo_em_centavos_sem_perda_de_precisao(banco_restaurado, objetos):
    res = verificar(banco_restaurado, tamanho_em_diretorio(str(objetos)))
    pendente = next(v for k, v in res.financeiro.items() if 'PENDING' in k or 'pendente' in k)
    assert pendente == {'quantidade': 2, 'valor_centavos': 13000, 'pago_centavos': 0}


def test_falta_de_chave_fernet_reprova(banco_restaurado, objetos, monkeypatch):
    _usar_chave(monkeypatch, '')
    res = verificar(banco_restaurado, tamanho_em_diretorio(str(objetos)))
    assert not res.aprovado
    assert any('AILOS_TOKEN_ENCRYPTION_KEY não foi informada' in e for e in res.erros)


def test_chave_fernet_errada_reprova_todos_os_segredos(banco_restaurado, objetos, monkeypatch):
    _usar_chave(monkeypatch, Fernet.generate_key().decode())
    res = verificar(banco_restaurado, tamanho_em_diretorio(str(objetos)))
    assert not res.aprovado
    assert res.segredos['ailos_client_tokens']['falhas'] == 1
    assert res.segredos['smtp_password']['falhas'] == 1
    assert res.segredos['nfse_certificados']['falhas'] == 1


def test_pfx_com_senha_divergente_reprova(db, cliente, objetos, monkeypatch):
    _usar_chave(monkeypatch, CHAVE)
    db.add(NfseCertificado(titular='X', arquivo_cifrado=crypto.encrypt_bytes(_pfx()),
                           senha_cifrada=crypto.encrypt_token('outra-senha'), ativo=True))
    db.commit()
    res = verificar(db.get_bind(), tamanho_em_diretorio(str(objetos)))
    assert not res.aprovado
    assert res.certificados[0]['abre'] is False


def test_certificado_ativo_vencido_so_avisa(db, objetos, monkeypatch):
    _usar_chave(monkeypatch, CHAVE)
    db.add(NfseCertificado(titular='X', arquivo_cifrado=crypto.encrypt_bytes(_pfx(dias=-5)),
                           senha_cifrada=crypto.encrypt_token(SENHA_PFX), ativo=True))
    db.commit()
    res = verificar(db.get_bind(), tamanho_em_diretorio(str(objetos)))
    assert res.aprovado
    assert any('vencido' in a for a in res.avisos)


def test_objeto_ausente_reprova(banco_restaurado, objetos):
    (objetos / 'clients' / '1' / 'documents' / 'contrato.pdf').unlink()
    res = verificar(banco_restaurado, tamanho_em_diretorio(str(objetos)))
    assert not res.aprovado
    assert res.objetos['ausentes'] == 1
    assert any('contrato.pdf' in e for e in res.erros)


def test_objeto_com_tamanho_divergente_reprova(banco_restaurado, objetos):
    (objetos / 'clients' / '1' / 'documents' / 'contrato.pdf').write_bytes(b'truncado')
    res = verificar(banco_restaurado, tamanho_em_diretorio(str(objetos)))
    assert not res.aprovado
    assert res.objetos['tamanho_divergente'] == 1


def test_chave_de_objeto_nao_escapa_do_diretorio(tmp_path):
    (tmp_path / 'fora.txt').write_text('x')
    raiz = tmp_path / 'objetos'
    raiz.mkdir()
    assert tamanho_em_diretorio(str(raiz))('../fora.txt') is None


def test_sem_segredos_nem_armazenamento_avisa_sem_reprovar(db):
    res = verificar(db.get_bind(), None)
    assert res.aprovado
    assert any('decifragem não foi exercitada' in a for a in res.avisos)
    assert any('documentos não verificados' in a for a in res.avisos)


def test_impressao_digital_igual_a_do_backup_sh(banco_restaurado, objetos):
    import hashlib
    esperado = 'sha256:' + hashlib.sha256(CHAVE.encode()).hexdigest()[:16]
    assert verificar(banco_restaurado, tamanho_em_diretorio(str(objetos))).fernet_key_fingerprint == esperado


def test_resultado_nao_contem_segredos_decifrados(banco_restaurado, objetos):
    texto = repr(verificar(banco_restaurado, tamanho_em_diretorio(str(objetos))).as_dict())
    for segredo in ('token-app', 'token-coop', 'senha-smtp', SENHA_PFX, CHAVE):
        assert segredo not in texto


def test_cli_retorna_codigo_de_saida(tmp_path, monkeypatch, capsys):
    from sqlalchemy import create_engine

    from app.db.session import Base

    url = f'sqlite:///{tmp_path / "restaurado.db"}'
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    _usar_chave(monkeypatch, '')
    saida = tmp_path / 'r.json'
    assert cli_main(['--database-url', url, '--objetos-dir', str(tmp_path), '--json', str(saida)]) == 0
    assert '"aprovado": true' in saida.read_text(encoding='utf-8')
    assert 'APROVADO' in capsys.readouterr().out
