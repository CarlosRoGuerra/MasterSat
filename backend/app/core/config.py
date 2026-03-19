from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env.example', extra='ignore')

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


settings = Settings()
