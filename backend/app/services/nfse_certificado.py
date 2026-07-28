"""
Cadastro do certificado digital A1 (ICP-Brasil) da NFS-e.

O operador envia o .pfx e a senha pela tela; o serviço abre o arquivo para
validar a senha e **extrair** titular, emissor, CNPJ e validade (esses campos
não são digitados). Arquivo e senha são guardados criptografados.

Ordem de resolução na emissão: certificado ATIVO no banco → NFSE_CERT_PATH do
.env (compatibilidade com a instalação atual).
"""
from __future__ import annotations

import datetime as dt

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_bytes, decrypt_token, encrypt_bytes, encrypt_token
from app.models.nfse_certificado import NfseCertificado

# OID do CNPJ no e-CNPJ ICP-Brasil (OtherName dentro do SubjectAltName)
_OID_CNPJ = '2.16.76.1.3.3'


class CertificadoError(Exception):
    """Arquivo inválido, senha incorreta ou certificado fora do padrão."""


def _texto_do_subject(cert, oid) -> str | None:
    achado = cert.subject.get_attributes_for_oid(oid)
    return achado[0].value if achado else None


def _cnpj_do_certificado(cert) -> str | None:
    """Lê o CNPJ do OtherName 2.16.76.1.3.3; cai no CN se não achar."""
    from cryptography import x509
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for nome in san.get_values_for_type(x509.OtherName):
            if nome.type_id.dotted_string == _OID_CNPJ:
                # DER: os 14 dígitos do CNPJ vêm no fim do valor codificado
                digitos = ''.join(c for c in nome.value.decode('latin-1') if c.isdigit())
                if len(digitos) >= 14:
                    return digitos[:14]
    except Exception:  # noqa: BLE001 — extensão ausente/malformada não é fatal
        pass
    cn = _texto_do_subject(cert, NameOID.COMMON_NAME) or ''
    digitos = ''.join(c for c in cn.split(':')[-1] if c.isdigit())
    return digitos[:14] if len(digitos) >= 14 else None


def inspecionar(arquivo: bytes, senha: str) -> dict:
    """
    Abre o .pfx e devolve os dados do certificado. Levanta ``CertificadoError``
    com mensagem útil se a senha estiver errada ou o arquivo não for um PKCS#12.
    """
    try:
        chave, cert, _ = pkcs12.load_key_and_certificates(arquivo, senha.encode())
    except Exception as exc:  # noqa: BLE001 — a lib levanta tipos variados
        raise CertificadoError(
            'Não foi possível abrir o certificado. Confira se o arquivo é um '
            '.pfx/.p12 válido e se a senha está correta.'
        ) from exc

    if chave is None or cert is None:
        raise CertificadoError('O arquivo não contém chave privada e certificado.')

    return {
        'titular': _texto_do_subject(cert, NameOID.COMMON_NAME) or 'Certificado sem CN',
        'emissor': (cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME) or [None])[0]
                   and cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
        'cnpj': _cnpj_do_certificado(cert),
        'valido_de': cert.not_valid_before_utc,
        'valido_ate': cert.not_valid_after_utc,
    }


def salvar(
    db: Session,
    arquivo: bytes,
    senha: str,
    *,
    nome_arquivo: str | None = None,
    enviado_por: int | None = None,
) -> NfseCertificado:
    """Valida o .pfx, desativa o anterior e grava o novo como ativo."""
    dados = inspecionar(arquivo, senha)

    if dados['valido_ate'] and dados['valido_ate'] < dt.datetime.now(dt.timezone.utc):
        raise CertificadoError(
            f'Certificado vencido em {dados["valido_ate"].strftime("%d/%m/%Y")}. '
            'Emita um novo na Autoridade Certificadora.'
        )

    db.query(NfseCertificado).filter_by(ativo=True).update({'ativo': False})
    registro = NfseCertificado(
        titular=dados['titular'],
        cnpj=dados['cnpj'],
        emissor=dados['emissor'],
        valido_de=dados['valido_de'],
        valido_ate=dados['valido_ate'],
        nome_arquivo=nome_arquivo,
        arquivo_cifrado=encrypt_bytes(arquivo),
        senha_cifrada=encrypt_token(senha),
        ativo=True,
        enviado_por=enviado_por,
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)
    _invalidar_cache()
    return registro


def obter_ativo(db: Session) -> NfseCertificado | None:
    return (
        db.query(NfseCertificado)
        .filter_by(ativo=True)
        .order_by(NfseCertificado.id.desc())
        .first()
    )


def material_pem(db: Session) -> tuple[bytes, bytes] | None:
    """
    (chave_pem, cert_pem) do certificado ativo, ou None se não houver — nesse
    caso a emissão cai no NFSE_CERT_PATH do .env.
    """
    registro = obter_ativo(db)
    if registro is None:
        return None
    arquivo = decrypt_bytes(registro.arquivo_cifrado)
    senha = decrypt_token(registro.senha_cifrada)
    chave, cert, _ = pkcs12.load_key_and_certificates(arquivo, senha.encode())
    return (
        chave.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        cert.public_bytes(serialization.Encoding.PEM),
    )


def _invalidar_cache() -> None:
    """Limpa os caches do módulo de emissão para o novo certificado valer já."""
    from app.services import nfse_nacional
    nfse_nacional._material_certificado.cache_clear()
    nfse_nacional._par_pem_mtls.cache_clear()


def _com_fuso(valor: dt.datetime | None) -> dt.datetime | None:
    """Normaliza para UTC-aware. O SQLite devolve datetime ingênuo, o Postgres
    devolve com fuso — comparar os dois tipos levanta TypeError."""
    if valor is None:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=dt.timezone.utc)


def para_dict(registro: NfseCertificado | None) -> dict | None:
    """Resumo seguro para a API — nunca expõe arquivo nem senha."""
    if registro is None:
        return None
    agora = dt.datetime.now(dt.timezone.utc)
    vence = _com_fuso(registro.valido_ate)
    dias = (vence - agora).days if vence else None
    inicio = _com_fuso(registro.valido_de)
    return {
        'id': registro.id,
        'titular': registro.titular,
        'cnpj': registro.cnpj,
        'emissor': registro.emissor,
        'nome_arquivo': registro.nome_arquivo,
        'valido_de': inicio.isoformat() if inicio else None,
        'valido_ate': vence.isoformat() if vence else None,
        'dias_para_vencer': dias,
        'vencido': bool(vence and vence < agora),
        'ativo': registro.ativo,
        'enviado_em': registro.created_at.isoformat() if getattr(registro, 'created_at', None) else None,
    }
