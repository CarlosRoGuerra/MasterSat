import logging
import warnings

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_WEAK_KEYS = {'change-me-super-secret', 'secret', 'changeme', ''}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_name: str = 'Sistema de Rastreamento'
    # 'development' | 'production'. Em produção a app RECUSA subir com segredos
    # fracos (não só avisa). Deixe 'development' em dev/testes.
    environment: str = 'development'
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
    # Chave de API para integrações máquina-a-máquina (ex.: o CobraZap puxa os boletos
    # via header X-API-Key). Vazio = endpoints de /integrations/cobrancas ficam off (503).
    integration_api_key: str = ''
    # Webhook de alertas (Discord/Slack) — mesmo canal usado pelo backup.
    # Usado p/ avisar quando a sessão do cooperado Ailos morre (emissão parada).
    alert_webhook: str = ''
    # Admin inicial (criado só se o banco não tiver esse e-mail). Se a senha não
    # for definida, é gerada uma aleatória e logada uma vez — NUNCA usar senha pública.
    initial_admin_email: str = 'admin@rastreamento.local'
    initial_admin_password: str = ''
    database_url: str = 'postgresql+psycopg://postgres:postgres@db:5432/rastreamento'
    frontend_url: str = 'http://localhost:3000'
    backend_public_url: str = 'http://localhost:8000'
    # Backend de storage do rate limiter (slowapi/limits) — compartilhado entre
    # workers uvicorn. Sem isto cada worker mantém seu próprio contador em
    # memória e o limite efetivo vira (limite x workers).
    redis_url: str = 'redis://redis:6379/0'
    # Teto de tamanho de requisição/upload (bytes). Protege contra upload gigante
    # que estouraria a memória (o arquivo é lido inteiro para o MinIO). 25 MB.
    max_upload_bytes: int = 25 * 1024 * 1024

    minio_endpoint: str = 'minio:9000'
    minio_root_user: str = 'minioadmin'
    minio_root_password: str = 'minioadmin'
    minio_secure: bool = False
    minio_bucket: str = 'rastreamento'
    minio_public_url: str = 'http://localhost:9000'

    multiportal_enabled: bool = False
    multiportal_wsdl_url: str = 'http://webmportal.dynalias.net:83/services/IntegracaoAdmService?wsdl'
    # O WSDL do parceiro é HTTP (sem TLS). Em produção a app recusa subir com o
    # Multiportal ligado sobre HTTP a menos que isto seja True (aceite explícito
    # do risco de tráfego em texto claro).
    multiportal_allow_insecure_http: bool = False
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
    # 1 = registra o título na Nuclea (boleto REGISTRADO) — valor do Postman
    # oficial da Ailos. 2 deixava o boleto sair como "não registrado".
    ailos_default_indicador_registro_nuclea: int = 1
    # BolePix (boleto híbrido com QR Code Pix). Requer que a conta tenha uma
    # chave Pix aleatória cadastrada, ativada e vinculada à funcionalidade na
    # Ailos. Quando True, envia "bolePix": true no payload V2 de geração.
    ailos_bole_pix: bool = False
    # Re-login automático headless (mantém a sessão do cooperado viva sem
    # reautorizar no navegador). Requer as credenciais abaixo. ⚠ 3 senhas
    # erradas BLOQUEIAM a conta — só ligue com a senha confirmada (trava em 2).
    ailos_auto_relogin: bool = False
    ailos_cooperado_cooperativa: str = ''
    ailos_cooperado_conta: str = ''
    ailos_cooperado_senha: str = ''
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
    # ── PIS/COFINS — grupo TributosFederais, OBRIGATÓRIO no padrão Nota
    # Nacional ("Obriga informar Pis/Cofins"). As tags antigas ValorPis/
    # ValorCofins foram descontinuadas (erro E923).
    # Defaults de optante do Simples Nacional (PIS/COFINS recolhidos no DAS):
    # CST 08 = operação sem incidência, base e alíquotas zeradas, sem retenção.
    # ⚠ CONFIRME COM O CONTADOR antes de emitir em produção.
    nfse_pis_cofins_cst: str = '08'
    nfse_pis_cofins_base: str = '0.00'
    nfse_aliquota_pis: str = '0.00'
    nfse_aliquota_cofins: str = '0.00'
    # 0 = PIS/COFINS/CSLL não retidos
    nfse_tipo_retencao_pis_cofins: str = '0'
    nfse_cert_path: str = ''               # .pfx/.p12 — OBRIGATÓRIO no Emissor Nacional
    nfse_cert_senha: str = ''
    nfse_timeout_seconds: int = 60

    # ── NFS-e Emissor Nacional (SNNFSe / Sefin Nacional, REST) ───────────────
    # Joinville encerrou a emissão municipal em 20/07/2026 (erro E930 no
    # webservice antigo). 'nacional' usa este módulo; 'joinville' mantém o
    # legado só para consultar notas já emitidas.
    nfse_provedor: str = 'nacional'         # 'nacional' | 'joinville'
    # 'producao_restrita' = ambiente de testes; 'producao' emite nota REAL
    nfse_nac_ambiente: str = 'producao_restrita'
    # Trava anti-acidente: emitir em 'producao' (nota fiscal REAL, irreversível)
    # exige este flag explícito True, além de nfse_nac_ambiente='producao'.
    nfse_nac_producao_confirmada: bool = False
    # Série da DPS. Joinville padronizou as faixas no portal da NF-em:
    # 40000 = aplicativo próprio com integração via API à Sefin Nacional (nosso
    # caso), 60000 = emissor móvel (MEI), 70000 = emissor web (padrão do portal),
    # 80000 = transcrição manual. Não confundir com a série 3000 do webservice
    # municipal antigo.
    nfse_nac_serie: str = '40000'
    nfse_nac_ver_aplic: str = 'MasterSat-1.0'
    # opSimpNac: 1=Não optante, 2=Optante MEI, 3=Optante ME/EPP
    nfse_nac_op_simples_nacional: str = '3'
    # regApTribSN (só quando opSimpNac=3): 1=Federais e municipal pelo SN,
    # 2=Federais pelo SN e ISSQN por fora, 3=Federais e municipal por fora
    nfse_nac_reg_apur_simples: str = '1'
    nfse_nac_regime_especial: str = '0'     # 0 = Nenhum
    # Código de tributação nacional (6 dígitos), da aba MUN.INCID_INFO.SERV. do
    # ANEXO I. SELECIONÁVEL por emissão conforme o serviço prestado. Os três que
    # a MasterSat usa (confirmados pelo cliente):
    #   110201 (11.02.01) — Vigilância/segurança/MONITORAMENTO (mensalidade)
    #   140101 (14.01.01) — Manutenção/conservação de veículos (instalação)
    #   150307 (15.03.07) — LOCAÇÃO de bens e equipamentos (aluguel do rastreador)
    # Este é só o DEFAULT quando a emissão não informa o código.
    nfse_nac_cod_trib_nacional: str = '110201'
    nfse_nac_cod_nbs: str = ''              # opcional
    nfse_nac_trib_issqn: str = '1'          # 1 = Operação tributável
    nfse_nac_tipo_ret_issqn: str = '1'      # 1 = Não retido
    # IM do prestador: Joinville rejeita (E0120). Só ligar se um município exigir.
    nfse_nac_enviar_im: bool = False
    # Percentual aproximado dos tributos do Simples (pTotTribSN, Lei 12.741).
    # OBRIGATÓRIO p/ optante do Simples (indTotTrib dá E0712). ⚠ Valor real vem
    # do contador; '6.00' é um placeholder que a produção restrita aceitou.
    nfse_nac_perc_trib_simples: str = '6.00'
    # Algoritmos da assinatura XMLDSig — COMPROVADOS na produção restrita
    # (emissão HTTP 201): SHA-256 + canonicalização EXCLUSIVA (xml-exc-c14n#).
    nfse_nac_alg_assinatura: str = 'rsa-sha256'
    nfse_nac_alg_digest: str = 'sha256'
    nfse_nac_c14n: str = 'http://www.w3.org/2001/10/xml-exc-c14n#'

    # Rate limiting
    rate_limit_default: str = '200/minute'
    rate_limit_login: str = '5/minute'
    rate_limit_exports: str = '10/minute'

    # Retenção de logs de integração Ailos (request/response mascarados só nas
    # chaves sensíveis — CPF/CNPJ, endereço e valores ficam em texto claro).
    # Purgados automaticamente após esse período (ver main.py, worker de retenção).
    ailos_log_retention_months: int = 12

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in ('production', 'producao', 'prod')

    def _security_problems(self) -> list[str]:
        """Lista de problemas de configuração insegura (vazia = tudo ok)."""
        problems: list[str] = []
        if self.secret_key in _WEAK_KEYS or len(self.secret_key) < 32:
            problems.append(
                'SECRET_KEY fraca ou padrão — gere: '
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if self.debug_return_reset_token:
            problems.append('DEBUG_RETURN_RESET_TOKEN=true expõe tokens de reset no response')
        if self.minio_root_password in ('minioadmin', 'admin', ''):
            problems.append('Senha do MinIO ainda é o valor padrão')
        if 'postgres:postgres@' in self.database_url:
            problems.append('DATABASE_URL ainda usa a senha padrão "postgres"')
        if self.ailos_client_id and self.ailos_client_secret and not self.ailos_token_encryption_key:
            problems.append(
                'Credenciais Ailos sem AILOS_TOKEN_ENCRYPTION_KEY — gere: '
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        if (
            self.multiportal_enabled
            and self.multiportal_wsdl_url.strip().lower().startswith('http://')
            and not self.multiportal_allow_insecure_http
        ):
            problems.append(
                'Multiportal habilitado com WSDL em HTTP (sem TLS) — dados e credenciais '
                'trafegam em texto claro. Use HTTPS ou defina MULTIPORTAL_ALLOW_INSECURE_HTTP=true '
                'para aceitar o risco explicitamente'
            )
        return problems

    def enforce_security(self) -> None:
        """Em produção, RECUSA subir com configuração insegura; fora, só avisa."""
        problems = self._security_problems()
        if not problems:
            return
        if self.is_production:
            raise RuntimeError(
                'Inicialização recusada (ENVIRONMENT=production) por configuração insegura:\n  - '
                + '\n  - '.join(problems)
                + '\nCorrija o .env antes de subir a aplicação.'
            )
        for problem in problems:
            warnings.warn(f'⚠ {problem}', UserWarning, stacklevel=2)

    # Compat: nome antigo mantido como alias.
    warn_insecure = enforce_security


settings = Settings()
settings.enforce_security()
