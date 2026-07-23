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
    # ANEXO I. NÃO é o ItemListaServico de 4 dígitos do padrão antigo.
    # 110501 = "Serviços relacionados ao monitoramento e rastreamento a
    #           distância [...] de veículos, cargas, pessoas [...] por meio de
    #           telefonia móvel, transmissão de satélites, rádio [...]"
    #           → é a descrição literal do serviço da MasterSat (LC116 11.05).
    # ⚠ DECISÃO FISCAL PENDENTE: no cadastro de Joinville a empresa está sob o
    # item 11.02, cujo código nacional é 110201 ("Vigilância, segurança ou
    # monitoramento de bens, pessoas e semoventes"). Confirmar com o contador
    # qual usar — código inexistente na lista nacional é rejeitado com E0310.
    nfse_nac_cod_trib_nacional: str = '110501'
    nfse_nac_cod_nbs: str = ''              # opcional
    nfse_nac_trib_issqn: str = '1'          # 1 = Operação tributável
    nfse_nac_tipo_ret_issqn: str = '1'      # 1 = Não retido
    # Percentual do Simples p/ o grupo totTrib. Vazio → envia indTotTrib=0.
    nfse_nac_perc_trib_simples: str = ''
    # Algoritmos da assinatura XMLDSig — validar no 1º teste em produção restrita
    nfse_nac_alg_assinatura: str = 'rsa-sha256'
    nfse_nac_alg_digest: str = 'sha256'

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
