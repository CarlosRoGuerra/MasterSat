"""
Verificação de um banco RESTAURADO (exercício de recuperação, OPS-01/OPS-02).

Responde, só com leituras, às perguntas que o pg_restore não responde:

- a chave Fernet guardada no cofre decifra TODOS os segredos do dump
  (tokens Ailos, senha SMTP, certificado A1 da NFS-e + senha)?
- o .pfx decifrado abre com a senha decifrada (e até quando vale)?
- todo documento ativo e todo arquivo de retorno Ailos referenciado pelo
  banco existe no armazenamento restaurado, com o tamanho registrado?
- qual o saldo financeiro (por situação, em centavos) e a revisão Alembic,
  para comparar com a origem?

Nunca imprime segredo decifrado: o resultado só traz contagens, datas de
validade e tamanhos. Usa as mesmas funções de cripto da aplicação
(app.core.crypto), então "decifrou aqui" significa "a aplicação decifra".
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.core import crypto

# Tamanho de um objeto no armazenamento; None = objeto não existe.
ObjectSizeLookup = Callable[[str], 'int | None']


@dataclass
class Resultado:
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    segredos: dict[str, dict[str, int]] = field(default_factory=dict)
    certificados: list[dict[str, object]] = field(default_factory=list)
    objetos: dict[str, int] = field(default_factory=dict)
    financeiro: dict[str, dict[str, object]] = field(default_factory=dict)
    alembic_revision: str | None = None
    fernet_key_fingerprint: str | None = None

    @property
    def aprovado(self) -> bool:
        return not self.erros

    def as_dict(self) -> dict[str, object]:
        return {
            'aprovado': self.aprovado,
            'erros': self.erros,
            'avisos': self.avisos,
            'alembic_revision': self.alembic_revision,
            'fernet_key_fingerprint': self.fernet_key_fingerprint,
            'segredos': self.segredos,
            'certificados': self.certificados,
            'objetos': self.objetos,
            'financeiro': self.financeiro,
        }


def _centavos(valor: object) -> int:
    return int((Decimal(str(valor or 0)) * 100).quantize(Decimal('1')))


def _chave_configurada() -> bool:
    from app.core.config import settings
    return bool(settings.ailos_token_encryption_key)


def impressao_digital_chave() -> str | None:
    """Mesma impressão digital que backup.sh grava no manifest (sha256, 16 hex)."""
    from app.core.config import settings
    chave = settings.ailos_token_encryption_key
    if not chave:
        return None
    return 'sha256:' + hashlib.sha256(chave.encode('utf-8')).hexdigest()[:16]


def _verificar_textos(conn, res: Resultado, rotulo: str, sql: str) -> None:
    valores = [row[0] for row in conn.execute(text(sql)) if row[0]]
    ok = falhas = 0
    for valor in valores:
        try:
            crypto.decrypt_token(valor)
            ok += 1
        except crypto.CryptoError:
            falhas += 1
    res.segredos[rotulo] = {'total': len(valores), 'decifrados': ok, 'falhas': falhas}
    if falhas:
        res.erros.append(f'{rotulo}: {falhas} de {len(valores)} segredo(s) não decifram com a chave Fernet informada')


def _verificar_certificados(conn, res: Resultado, agora: datetime) -> None:
    from cryptography.hazmat.primitives.serialization import pkcs12

    linhas = conn.execute(text(
        'SELECT id, ativo, arquivo_cifrado, senha_cifrada FROM nfse_certificados ORDER BY id'
    )).all()
    ok = falhas = 0
    for cert_id, ativo, arquivo_cifrado, senha_cifrada in linhas:
        info: dict[str, object] = {'id': cert_id, 'ativo': bool(ativo)}
        try:
            pfx = crypto.decrypt_bytes(bytes(arquivo_cifrado))
            senha = crypto.decrypt_token(senha_cifrada)
            _, certificado, _ = pkcs12.load_key_and_certificates(pfx, senha.encode('utf-8'))
            if certificado is None:
                raise ValueError('PFX sem certificado')
            validade = certificado.not_valid_after.replace(tzinfo=timezone.utc)
            info.update(abre=True, valido_ate=validade.isoformat(), vencido=validade < agora)
            if ativo and validade < agora:
                res.avisos.append(f'certificado NFS-e ativo #{cert_id} está vencido desde {validade.date()}')
            ok += 1
        except (crypto.CryptoError, ValueError, TypeError) as exc:
            # ValueError cobre senha errada e PFX corrompido no pkcs12.
            info.update(abre=False, motivo=type(exc).__name__)
            res.erros.append(f'certificado NFS-e #{cert_id} não abre após decifrar ({type(exc).__name__})')
            falhas += 1
        res.certificados.append(info)
    res.segredos['nfse_certificados'] = {'total': len(linhas), 'decifrados': ok, 'falhas': falhas}


def _verificar_objetos(conn, tabelas: set[str], res: Resultado, tamanho_de: ObjectSizeLookup) -> None:
    obrigatorios = ausentes = tamanho_divergente = opcionais_ausentes = 0
    if 'documents' in tabelas:
        for chave, tamanho, ativo in conn.execute(text(
            'SELECT object_key, size_bytes, active FROM documents ORDER BY id'
        )):
            real = tamanho_de(chave)
            if not ativo:
                # Documento excluído remove o objeto; versão substituída o mantém.
                if real is None:
                    opcionais_ausentes += 1
                continue
            obrigatorios += 1
            if real is None:
                ausentes += 1
                if ausentes <= 20:
                    res.erros.append(f'documento ativo sem objeto: {chave}')
            elif tamanho is not None and real != int(tamanho):
                tamanho_divergente += 1
                res.erros.append(f'documento {chave}: tamanho {real} difere do registrado ({tamanho})')
    if 'ailos_retorno_arquivos' in tabelas:
        for (chave,) in conn.execute(text(
            'SELECT storage_object_key FROM ailos_retorno_arquivos WHERE storage_object_key IS NOT NULL'
        )):
            obrigatorios += 1
            if tamanho_de(chave) is None:
                ausentes += 1
                if ausentes <= 20:
                    res.erros.append(f'arquivo de retorno Ailos sem objeto: {chave}')
    if ausentes > 20:
        res.erros.append(f'... {ausentes - 20} outra(s) referência(s) sem objeto')
    res.objetos = {
        'referencias_obrigatorias': obrigatorios,
        'ausentes': ausentes,
        'tamanho_divergente': tamanho_divergente,
        'inativos_sem_objeto': opcionais_ausentes,
    }


def _resumo_financeiro(conn, tabelas: set[str], res: Resultado) -> None:
    if 'billings' in tabelas:
        for status, excluida, qtd, valor, pago in conn.execute(text(
            'SELECT status, is_deleted, count(*), sum(amount), sum(paid_amount) '
            'FROM billings GROUP BY status, is_deleted ORDER BY status, is_deleted'
        )):
            chave = f'billings[{status}{"|excluida" if excluida else ""}]'
            res.financeiro[chave] = {
                'quantidade': int(qtd), 'valor_centavos': _centavos(valor), 'pago_centavos': _centavos(pago),
            }
    if 'payables' in tabelas:
        qtd, valor = conn.execute(text('SELECT count(*), sum(amount) FROM payables')).one()
        res.financeiro['payables'] = {'quantidade': int(qtd), 'valor_centavos': _centavos(valor)}


def verificar(
    engine: Engine,
    tamanho_objeto: ObjectSizeLookup | None = None,
    agora: datetime | None = None,
) -> Resultado:
    """Executa todas as verificações (só leitura) e devolve o resultado."""
    res = Resultado(fernet_key_fingerprint=impressao_digital_chave())
    agora = agora or datetime.now(timezone.utc)
    tabelas = set(inspect(engine).get_table_names())

    with engine.connect() as conn:
        if 'alembic_version' in tabelas:
            res.alembic_revision = ','.join(
                sorted(r[0] for r in conn.execute(text('SELECT version_num FROM alembic_version')))
            ) or None

        fontes = {
            'ailos_client_tokens': ('ailos_client_tokens',
                                    'SELECT access_token_encrypted FROM ailos_client_tokens'),
            'ailos_integrations': ('ailos_integrations',
                                   'SELECT cooperado_token_encrypted FROM ailos_integrations'),
            'smtp_password': ('system_settings',
                              "SELECT value FROM system_settings WHERE key = 'smtp_password_enc'"),
        }
        ha_segredos = False
        for tabela, sql in fontes.values():
            if tabela in tabelas and any(row[0] for row in conn.execute(text(sql))):
                ha_segredos = True
        if 'nfse_certificados' in tabelas and conn.execute(text('SELECT count(*) FROM nfse_certificados')).scalar():
            ha_segredos = True

        if ha_segredos and not _chave_configurada():
            res.erros.append(
                'o banco tem segredos cifrados mas AILOS_TOKEN_ENCRYPTION_KEY não foi informada — '
                'sem a chave do cofre, tokens Ailos, senha SMTP e certificado NFS-e são irrecuperáveis'
            )
        elif ha_segredos:
            for rotulo, (tabela, sql) in fontes.items():
                if tabela in tabelas:
                    _verificar_textos(conn, res, rotulo, sql)
            if 'nfse_certificados' in tabelas:
                _verificar_certificados(conn, res, agora)
        else:
            res.avisos.append('nenhum segredo cifrado no banco — decifragem não foi exercitada')

        if tamanho_objeto is None:
            res.avisos.append('armazenamento de objetos não informado — documentos não verificados')
        else:
            _verificar_objetos(conn, tabelas, res, tamanho_objeto)

        _resumo_financeiro(conn, tabelas, res)
    return res


def tamanho_em_diretorio(raiz: str) -> ObjectSizeLookup:
    """Consulta objetos numa cópia em disco (saída de `restore.sh objetos`)."""
    raiz_abs = os.path.realpath(raiz)

    def _tamanho(chave: str) -> int | None:
        caminho = os.path.realpath(os.path.join(raiz_abs, chave))
        # Chave vinda do banco nunca pode apontar para fora da cópia.
        if os.path.commonpath([raiz_abs, caminho]) != raiz_abs or not os.path.isfile(caminho):
            return None
        return os.path.getsize(caminho)

    return _tamanho


def tamanho_no_minio() -> ObjectSizeLookup:
    """Consulta objetos no MinIO configurado (MINIO_* do ambiente), como a aplicação faz."""
    from minio.error import S3Error

    from app.core.config import settings
    from app.services.storage import client

    def _tamanho(chave: str) -> int | None:
        try:
            return int(client.stat_object(settings.minio_bucket, chave).size)
        except S3Error as exc:
            if exc.code in ('NoSuchKey', 'NoSuchObject'):
                return None
            raise

    return _tamanho
