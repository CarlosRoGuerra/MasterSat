from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_name: str = 'Sistema de Rastreamento'
    api_v1_prefix: str = '/api/v1'
    secret_key: str = 'change-me-super-secret'
    algorithm: str = 'HS256'
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    password_reset_expire_minutes: int = 30
    debug_return_reset_token: bool = True
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


settings = Settings()
