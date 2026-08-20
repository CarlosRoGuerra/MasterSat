"""Etapa 7: a app deve RECUSAR subir em produção com configuração insegura."""
from __future__ import annotations

import pytest

from app.core.config import Settings

# Base segura de produção (nenhum problema esperado).
_SEGURO = dict(
    environment='production',
    secret_key='a' * 64,
    database_url='postgresql+psycopg://user:senha-forte-123@db:5432/x',
    minio_root_password='minio-senha-forte-123',
)


def _settings(**over) -> Settings:
    # _env_file=None isola o teste de um .env local.
    return Settings(_env_file=None, **{**_SEGURO, **over})


def test_producao_segura_nao_levanta():
    _settings().enforce_security()  # sem problemas → não levanta


def test_producao_recusa_secret_fraca():
    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        _settings(secret_key='change-me-super-secret').enforce_security()


def test_producao_recusa_postgres_padrao():
    with pytest.raises(RuntimeError, match='postgres'):
        _settings(database_url='postgresql+psycopg://postgres:postgres@db:5432/x').enforce_security()


def test_producao_recusa_minio_padrao():
    with pytest.raises(RuntimeError, match='MinIO'):
        _settings(minio_root_password='minioadmin').enforce_security()


def test_producao_recusa_multiportal_http():
    with pytest.raises(RuntimeError, match='Multiportal'):
        _settings(multiportal_enabled=True,
                  multiportal_wsdl_url='http://webmportal.dynalias.net:83/x?wsdl').enforce_security()


def test_producao_multiportal_http_com_override_ok():
    _settings(multiportal_enabled=True,
              multiportal_wsdl_url='http://x/wsdl',
              multiportal_allow_insecure_http=True).enforce_security()


def test_desenvolvimento_apenas_avisa():
    # Em dev, config fraca só avisa (não impede a subida).
    s = _settings(environment='development', secret_key='change-me-super-secret')
    with pytest.warns(UserWarning, match='SECRET_KEY'):
        s.enforce_security()
