"""
Configuração da integração SGR (ETAPA 3): nenhuma credencial tem valor
default não-vazio, e os campos são carregados via variáveis de ambiente
(pydantic-settings), nunca hardcoded.
"""
from __future__ import annotations

from app.core.config import Settings


def _settings(**over) -> Settings:
    # _env_file=None isola o teste de um .env local.
    return Settings(_env_file=None, **over)


def test_sgr_credentials_default_to_empty():
    settings = _settings()
    assert settings.sgr_cod_mobile == ''
    assert settings.sgr_username == ''
    assert settings.sgr_password == ''
    assert settings.sgr_api_key == ''


def test_sgr_base_url_has_a_sane_default():
    settings = _settings()
    assert settings.sgr_base_url.startswith('https://')


def test_sgr_migration_limit_defaults_to_ten():
    assert _settings().sgr_migration_limit == 10


def test_sgr_settings_are_read_from_env(monkeypatch):
    monkeypatch.setenv('SGR_COD_MOBILE', '1234')
    monkeypatch.setenv('SGR_USERNAME', 'usuario-teste')
    monkeypatch.setenv('SGR_PASSWORD', 'senha-teste')
    monkeypatch.setenv('SGR_API_KEY', 'chave-teste')
    monkeypatch.setenv('SGR_MIGRATION_LIMIT', '5')

    settings = Settings(_env_file=None)

    assert settings.sgr_cod_mobile == '1234'
    assert settings.sgr_username == 'usuario-teste'
    assert settings.sgr_password == 'senha-teste'
    assert settings.sgr_api_key == 'chave-teste'
    assert settings.sgr_migration_limit == 5
