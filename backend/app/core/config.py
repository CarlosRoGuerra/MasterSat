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
    # Swagger/OpenAPI: desabilitado por padrão (não expor a superfície da API em produção)
    enable_docs: bool = False
    # Admin inicial (criado só se o banco não tiver esse e-mail). Se a senha não
    # for definida, é gerada uma aleatória e logada uma vez — NUNCA usar senha pública.
    initial_admin_email: str = 'admin@rastreamento.local'
    initial_admin_password: str = ''
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

    # ── Integração Ailos (Cobrança Bancária API) ─────────────────────────────
    ailos_env: str = 'sandbox'
    ailos_apim_base_url: str = ''
    ailos_gateway_base_url: str = ''
    ailos_client_id: str = ''
    ailos_client_secret: str = ''
    ailos_developer_key: str = ''
    ailos_callback_url: str = ''
    ailos_timeout_seconds: int = 30
    ailos_numero_convenio: str = '102004'
    ailos_default_carteira: int = 1
    ailos_default_forma_emissao: int = 2
    ailos_default_indicador_registro_nuclea: int = 2
    ailos_token_encryption_key: str = ''

    # ── Integração NFS-e Joinville (Pública / Nota Nacional, SOAP) ────────────
    # Emissão comprovada SEM certificado (homologação aceita RPS sem assinatura);
    # o certificado A1 é opcional — se nfse_cert_path estiver definido, assina.
    nfse_enabled: bool = True
    nfse_env: str = 'homologacao'          # 'homologacao' | 'producao'
    nfse_cnpj: str = '14228344000167'
    nfse_inscricao_municipal: str = '109545'
    nfse_natureza_operacao: str = '107'    # natureza autorizada da MasterSat
    nfse_item_lista_servico: str = '1102'  # LC116 11.02 (monitoramento)
    nfse_aliquota_iss: str = '2.00'
    nfse_codigo_municipio: str = '4209102'  # IBGE Joinville/SC
    nfse_serie_rps: str = '3000'           # REGRA CRÍTICA: WebService usa série 3000
    nfse_optante_simples: int = 1          # 1=Sim, 2=Não
    nfse_incentivador_cultural: int = 2    # 1=Sim, 2=Não
    nfse_iss_retido: int = 2               # 1=Sim, 2=Não
    nfse_discriminacao_padrao: str = 'MONITORAMENTO E RASTREAMENTO DE VEICULOS'
    nfse_cert_path: str = ''               # opcional — .pfx/.p12 para assinar
    nfse_cert_senha: str = ''
    nfse_timeout_seconds: int = 60

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
        if 'postgres:postgres@' in self.database_url:
            warnings.warn(
                '⚠ DATABASE_URL ainda usa a senha padrão "postgres". Troque antes de produção.',
                UserWarning,
                stacklevel=2,
            )
        if self.ailos_client_id and self.ailos_client_secret and not self.ailos_token_encryption_key:
            warnings.warn(
                '⚠ Credenciais Ailos configuradas sem AILOS_TOKEN_ENCRYPTION_KEY — '
                'tokens da integração não poderão ser criptografados. Gere uma chave: '
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
                UserWarning,
                stacklevel=2,
            )


settings = Settings()
settings.warn_insecure()
