import logging
import warnings

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_WEAK_KEYS = {'change-me-super-secret', 'secret', 'changeme', ''}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_name: str = 'Sistema de Rastreamento'
    api_v1_prefix: str = '/api/v1'
    secret_key: str = 'change-me-super-secret'
    algorithm: str = 'HS256'
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    password_reset_expire_minutes: int = 30
    # Produção: sempre False — nunca retorna token de reset no response
    debug_return_reset_token: bool = False
    database_url: str = 'postgresql+psycopg://postgres:postgres@db:5432/rastreamento'
    frontend_url: str = 'http://localhost:3000'
    backend_public_url: str = 'http://localhost:8000'

    minio_endpoint: str = 'minio:9000'
    minio_root_user: str = 'minioadmin'
    minio_root_password: str = 'minioadmin'
    minio_secure: bool = False
    minio_bucket: str = 'rastreamento'
    minio_public_url: str = 'http://localhost:9000'

    multiportal_enabled: bool = False
    multiportal_wsdl_url: str = 'http://webmportal.dynalias.net:83/services/IntegracaoAdmService?wsdl'
    multiportal_id: str = ''
    multiportal_password: str = ''
    multiportal_group_codes: str = ''
    multiportal_send_welcome_email: bool = False
    multiportal_request_timeout: int = 30

    # Rate limiting
    rate_limit_default: str = '200/minute'
    rate_limit_login: str = '5/minute'
    rate_limit_exports: str = '10/minute'

    def warn_insecure(self) -> None:
        """Emite avisos se configurações inseguras forem detectadas."""
        if self.secret_key in _WEAK_KEYS or len(self.secret_key) < 32:
            warnings.warn(
                '⚠ SECRET_KEY fraca ou padrão detectada! '
                'Gere uma chave forte: python -c "import secrets; print(secrets.token_hex(32))"',
                UserWarning,
                stacklevel=2,
            )
        if self.debug_return_reset_token:
            warnings.warn(
                '⚠ DEBUG_RETURN_RESET_TOKEN=true — '
                'tokens de reset são expostos no response. Desative em produção.',
                UserWarning,
                stacklevel=2,
            )
        if self.minio_root_password in ('minioadmin', 'admin', ''):
            warnings.warn(
                '⚠ Senha do MinIO ainda é o valor padrão. Troque antes de produção.',
                UserWarning,
                stacklevel=2,
            )


settings = Settings()
settings.warn_insecure()
