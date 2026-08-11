import logging
import secrets
import threading
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import get_password_hash
from app.db.session import Base, SessionLocal, engine
from app.models import ailos_api_log, ailos_boleto, ailos_client_token, ailos_integration, ailos_lote, ailos_retorno_arquivo, audit_log, billing, billing_change_log, client, client_charge_item, closure_job, contract, document, integration_log, nfse_certificado, nfse_lote, nfse_nota, password_reset_token, payable, plan, service_order, service_order_status_log, service_product, system_setting, tracker, tracker_history, uninstall_event, user, vehicle  # noqa: F401 — side-effect imports that register models with SQLAlchemy Base
from app.core.audit import AuditMiddleware
from app.models.enums import UserRole
from app.models.user import User
from app.services.storage import ensure_bucket

# /docs, /redoc e /openapi.json só ficam expostos se ENABLE_DOCS=true (dev).
# Em produção ficam desativados para não publicar a superfície da API.
app = FastAPI(
    title=settings.app_name,
    docs_url='/docs' if settings.enable_docs else None,
    redoc_url='/redoc' if settings.enable_docs else None,
    openapi_url='/openapi.json' if settings.enable_docs else None,
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        'http://localhost:3000',
        'http://127.0.0.1:3000',
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# ── Auditoria ─────────────────────────────────────────────────────────────────
app.add_middleware(AuditMiddleware)

app.include_router(api_router, prefix=settings.api_v1_prefix)


def ensure_schema_updates():
    with engine.begin() as conn:
        inspector = inspect(conn)

        if inspector.has_table('clients'):
            client_columns = {column['name'] for column in inspector.get_columns('clients')}
            client_alter_statements = {
                'extra_emails': 'ALTER TABLE clients ADD COLUMN extra_emails JSON',
                'contacts': 'ALTER TABLE clients ADD COLUMN contacts JSON',
                'rg_ie': 'ALTER TABLE clients ADD COLUMN rg_ie VARCHAR(30)',
                'birth_date': 'ALTER TABLE clients ADD COLUMN birth_date DATE',
                'emergency_contacts': 'ALTER TABLE clients ADD COLUMN emergency_contacts JSON',
                'boleto_format': 'ALTER TABLE clients ADD COLUMN boleto_format VARCHAR(20)',
                'boleto_fee': 'ALTER TABLE clients ADD COLUMN boleto_fee VARCHAR(3)',
                'issue_invoice': 'ALTER TABLE clients ADD COLUMN issue_invoice VARCHAR(3)',
                'tributacao': 'ALTER TABLE clients ADD COLUMN tributacao VARCHAR(30)',
                'iss_retido': 'ALTER TABLE clients ADD COLUMN iss_retido VARCHAR(3)',
                'optante_simples': 'ALTER TABLE clients ADD COLUMN optante_simples VARCHAR(3)',
                'delivery_method': 'ALTER TABLE clients ADD COLUMN delivery_method VARCHAR(20)',
                'send_boleto_whatsapp': 'ALTER TABLE clients ADD COLUMN send_boleto_whatsapp BOOLEAN DEFAULT FALSE',
                'trade_name': 'ALTER TABLE clients ADD COLUMN trade_name VARCHAR(180)',
            }
            for column_name, sql in client_alter_statements.items():
                if column_name not in client_columns:
                    conn.execute(text(sql))

        if inspector.has_table('contracts'):
            contract_columns = {column['name'] for column in inspector.get_columns('contracts')}
            contract_alter_statements = {
                'installation_fee': 'ALTER TABLE contracts ADD COLUMN installation_fee NUMERIC(10,2)',
                'uninstall_fee': 'ALTER TABLE contracts ADD COLUMN uninstall_fee NUMERIC(10,2)',
                'interveniente_client_id': 'ALTER TABLE contracts ADD COLUMN interveniente_client_id INTEGER REFERENCES clients(id)',
                'bank': "ALTER TABLE contracts ADD COLUMN bank VARCHAR(40) DEFAULT 'ailos'",
                'signed': 'ALTER TABLE contracts ADD COLUMN signed BOOLEAN DEFAULT FALSE',
                'signed_at': 'ALTER TABLE contracts ADD COLUMN signed_at DATE',
            }
            for column_name, sql in contract_alter_statements.items():
                if column_name not in contract_columns:
                    conn.execute(text(sql))

        if inspector.has_table('ailos_boletos'):
            ailos_boleto_columns = {column['name'] for column in inspector.get_columns('ailos_boletos')}
            if 'pix_emv' not in ailos_boleto_columns:
                conn.execute(text('ALTER TABLE ailos_boletos ADD COLUMN pix_emv TEXT'))
            if 'pix_qr_base64' not in ailos_boleto_columns:
                conn.execute(text('ALTER TABLE ailos_boletos ADD COLUMN pix_qr_base64 TEXT'))

        if inspector.has_table('ailos_integrations'):
            ailos_integ_columns = {column['name'] for column in inspector.get_columns('ailos_integrations')}
            if 'auto_relogin_failures' not in ailos_integ_columns:
                conn.execute(text('ALTER TABLE ailos_integrations ADD COLUMN auto_relogin_failures INTEGER DEFAULT 0'))

        if inspector.has_table('nfse_notas'):
            nfse_nota_columns = {column['name'] for column in inspector.get_columns('nfse_notas')}
            if 'lote_id' not in nfse_nota_columns:
                conn.execute(text('ALTER TABLE nfse_notas ADD COLUMN lote_id INTEGER REFERENCES nfse_lotes(id)'))

        if inspector.has_table('documents'):
            document_columns = {column['name'] for column in inspector.get_columns('documents')}
            document_alter_statements = {
                'review_status': "ALTER TABLE documents ADD COLUMN review_status VARCHAR(30) DEFAULT 'enviado'",
                'review_notes': 'ALTER TABLE documents ADD COLUMN review_notes TEXT',
                'active': 'ALTER TABLE documents ADD COLUMN active BOOLEAN DEFAULT TRUE',
            }
            for column_name, sql in document_alter_statements.items():
                if column_name not in document_columns:
                    conn.execute(text(sql))

        if inspector.has_table('vehicles'):
            vehicle_columns = {column['name'] for column in inspector.get_columns('vehicles')}
            alter_statements = {
                'sales_point': 'ALTER TABLE vehicles ADD COLUMN sales_point VARCHAR(120)',
                'seller_consultant': 'ALTER TABLE vehicles ADD COLUMN seller_consultant VARCHAR(120)',
                'vehicle_classification': 'ALTER TABLE vehicles ADD COLUMN vehicle_classification VARCHAR(80)',
                'user_alert': 'ALTER TABLE vehicles ADD COLUMN user_alert TEXT',
                'contract_number': 'ALTER TABLE vehicles ADD COLUMN contract_number VARCHAR(60)',
                'contract_date': 'ALTER TABLE vehicles ADD COLUMN contract_date DATE',
                'contract_end_date': 'ALTER TABLE vehicles ADD COLUMN contract_end_date DATE',
                'address_zip_code': 'ALTER TABLE vehicles ADD COLUMN address_zip_code VARCHAR(8)',
                'address_line': 'ALTER TABLE vehicles ADD COLUMN address_line VARCHAR(255)',
                'address_number': 'ALTER TABLE vehicles ADD COLUMN address_number VARCHAR(30)',
                'address_complement': 'ALTER TABLE vehicles ADD COLUMN address_complement VARCHAR(120)',
                'neighborhood': 'ALTER TABLE vehicles ADD COLUMN neighborhood VARCHAR(120)',
                'city': 'ALTER TABLE vehicles ADD COLUMN city VARCHAR(120)',
                'state': 'ALTER TABLE vehicles ADD COLUMN state VARCHAR(2)',
                'manufacture_year': 'ALTER TABLE vehicles ADD COLUMN manufacture_year INTEGER',
                'model_year': 'ALTER TABLE vehicles ADD COLUMN model_year INTEGER',
                'fuel_type': 'ALTER TABLE vehicles ADD COLUMN fuel_type VARCHAR(40)',
                'fipe_code': 'ALTER TABLE vehicles ADD COLUMN fipe_code VARCHAR(30)',
                'fipe_value': 'ALTER TABLE vehicles ADD COLUMN fipe_value NUMERIC(12,2)',
            }
            for column_name, sql in alter_statements.items():
                if column_name not in vehicle_columns:
                    conn.execute(text(sql))

        if inspector.has_table('trackers'):
            tracker_columns = {column['name'] for column in inspector.get_columns('trackers')}
            tracker_alter_statements = {
                'serial_number': 'ALTER TABLE trackers ADD COLUMN serial_number VARCHAR(60)',
                'sim_iccid': 'ALTER TABLE trackers ADD COLUMN sim_iccid VARCHAR(40)',
                'sim_status': 'ALTER TABLE trackers ADD COLUMN sim_status VARCHAR(30)',
                'acquisition_date': 'ALTER TABLE trackers ADD COLUMN acquisition_date DATE',
                'install_date': 'ALTER TABLE trackers ADD COLUMN install_date DATE',
                'notes': 'ALTER TABLE trackers ADD COLUMN notes TEXT',
                'firmware': 'ALTER TABLE trackers ADD COLUMN firmware VARCHAR(60)',
                'external_manufacturer_id': 'ALTER TABLE trackers ADD COLUMN external_manufacturer_id INTEGER',
                'external_manufacturer_label': 'ALTER TABLE trackers ADD COLUMN external_manufacturer_label VARCHAR(120)',
                'ip_address': 'ALTER TABLE trackers ADD COLUMN ip_address VARCHAR(60)',
                'port': 'ALTER TABLE trackers ADD COLUMN port INTEGER',
                'install_location': 'ALTER TABLE trackers ADD COLUMN install_location VARCHAR(120)',
                'chip_type': 'ALTER TABLE trackers ADD COLUMN chip_type INTEGER',
                'equipment_type': 'ALTER TABLE trackers ADD COLUMN equipment_type INTEGER',
                'communication_type': 'ALTER TABLE trackers ADD COLUMN communication_type INTEGER',
                'service_plan_name': 'ALTER TABLE trackers ADD COLUMN service_plan_name VARCHAR(120)',
                'installation_fee': 'ALTER TABLE trackers ADD COLUMN installation_fee NUMERIC(12,2)',
                'uninstall_date': 'ALTER TABLE trackers ADD COLUMN uninstall_date DATE',
                'integration_status': 'ALTER TABLE trackers ADD COLUMN integration_status VARCHAR(30)',
                'integration_last_code': 'ALTER TABLE trackers ADD COLUMN integration_last_code VARCHAR(20)',
                'integration_last_description': 'ALTER TABLE trackers ADD COLUMN integration_last_description TEXT',
                'integration_last_transaction_id': 'ALTER TABLE trackers ADD COLUMN integration_last_transaction_id VARCHAR(40)',
            }
            for column_name, sql in tracker_alter_statements.items():
                if column_name not in tracker_columns:
                    conn.execute(text(sql))
            conn.execute(text("UPDATE trackers SET serial_number = imei WHERE serial_number IS NULL OR serial_number = ''"))

        if inspector.has_table('contracts'):
            contract_columns = {column['name'] for column in inspector.get_columns('contracts')}
            contract_alter_statements = {
                'billing_day': 'ALTER TABLE contracts ADD COLUMN billing_day INTEGER',
                'payment_method': 'ALTER TABLE contracts ADD COLUMN payment_method VARCHAR(40)',
                'notes': 'ALTER TABLE contracts ADD COLUMN notes TEXT',
                'vehicle_id': 'ALTER TABLE contracts ADD COLUMN vehicle_id INTEGER',
                'tracker_id': 'ALTER TABLE contracts ADD COLUMN tracker_id INTEGER',
            }
            for column_name, sql in contract_alter_statements.items():
                if column_name not in contract_columns:
                    conn.execute(text(sql))

        if inspector.has_table('billings'):
            billing_columns = {column['name'] for column in inspector.get_columns('billings')}
            billing_alter_statements = {
                'paid_amount': 'ALTER TABLE billings ADD COLUMN paid_amount NUMERIC(10,2)',
                'receipt_number': 'ALTER TABLE billings ADD COLUMN receipt_number VARCHAR(40)',
                'period_label': 'ALTER TABLE billings ADD COLUMN period_label VARCHAR(20)',
            }
            for column_name, sql in billing_alter_statements.items():
                if column_name not in billing_columns:
                    conn.execute(text(sql))

        if inspector.has_table('plans'):
            plan_columns = {column['name'] for column in inspector.get_columns('plans')}
            plan_alter_statements = {
                'billing_interval_months': 'ALTER TABLE plans ADD COLUMN billing_interval_months INTEGER DEFAULT 1',
                'default_installation_fee': 'ALTER TABLE plans ADD COLUMN default_installation_fee NUMERIC(10,2)',
                'default_uninstall_fee': 'ALTER TABLE plans ADD COLUMN default_uninstall_fee NUMERIC(10,2)',
                'default_billing_day': 'ALTER TABLE plans ADD COLUMN default_billing_day INTEGER',
                'default_duration_months': 'ALTER TABLE plans ADD COLUMN default_duration_months INTEGER',
            }
            for column_name, sql in plan_alter_statements.items():
                if column_name not in plan_columns:
                    conn.execute(text(sql))

        if inspector.has_table('billings'):
            billing_columns = {column['name'] for column in inspector.get_columns('billings')}
            more_billing = {
                'item_id': 'ALTER TABLE billings ADD COLUMN item_id INTEGER',
                'vehicle_id': 'ALTER TABLE billings ADD COLUMN vehicle_id INTEGER',
                'tracker_id': 'ALTER TABLE billings ADD COLUMN tracker_id INTEGER',
                'title': 'ALTER TABLE billings ADD COLUMN title VARCHAR(160)',
                'billing_type': "ALTER TABLE billings ADD COLUMN billing_type VARCHAR(30) DEFAULT 'recorrente'",
                'installment_number': 'ALTER TABLE billings ADD COLUMN installment_number INTEGER',
                'installment_total': 'ALTER TABLE billings ADD COLUMN installment_total INTEGER',
            }
            for column_name, sql in more_billing.items():
                if column_name not in billing_columns:
                    conn.execute(text(sql))

        if inspector.has_table('client_charge_items'):
            charge_columns = {column['name'] for column in inspector.get_columns('client_charge_items')}
            if 'tracker_id' not in charge_columns:
                conn.execute(text('ALTER TABLE client_charge_items ADD COLUMN tracker_id INTEGER'))

        if inspector.has_table('vehicles'):
            vehicle_columns = {column['name'] for column in inspector.get_columns('vehicles')}
            if 'uninstalled_at' not in vehicle_columns:
                conn.execute(text('ALTER TABLE vehicles ADD COLUMN uninstalled_at DATE'))

        if inspector.has_table('clients'):
            client_columns = {column['name'] for column in inspector.get_columns('clients')}
            if 'billing_day' not in client_columns:
                conn.execute(text('ALTER TABLE clients ADD COLUMN billing_day INTEGER'))

        if inspector.has_table('contracts'):
            contract_columns = {column['name'] for column in inspector.get_columns('contracts')}
            if 'billing_modality' not in contract_columns:
                conn.execute(text("ALTER TABLE contracts ADD COLUMN billing_modality VARCHAR(20) DEFAULT 'boleto'"))

        # ── Uninstall events (taxa de desinstalação pendente para fechamento) ──
        if not inspector.has_table('uninstall_events'):
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS uninstall_events (
                    id SERIAL PRIMARY KEY,
                    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
                    tracker_id INTEGER REFERENCES trackers(id),
                    contract_id INTEGER REFERENCES contracts(id),
                    client_id INTEGER NOT NULL REFERENCES clients(id),
                    uninstall_date DATE NOT NULL,
                    fee_amount NUMERIC(10,2),
                    service_product_id INTEGER REFERENCES service_products(id),
                    status VARCHAR(20) NOT NULL DEFAULT \'pending\',
                    billing_id INTEGER REFERENCES billings(id),
                    processed_at TIMESTAMP WITH TIME ZONE,
                    notes TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                )
            '''))

        # ── Closure jobs (rastreamento de geração assíncrona de fechamento) ──
        if not inspector.has_table('closure_jobs'):
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS closure_jobs (
                    id SERIAL PRIMARY KEY,
                    reference_month VARCHAR(7) NOT NULL,
                    filter_type VARCHAR(20) NOT NULL DEFAULT \'all\',
                    client_id INTEGER,
                    status VARCHAR(20) NOT NULL DEFAULT \'queued\',
                    result JSON,
                    error TEXT,
                    started_at TIMESTAMP WITH TIME ZONE,
                    completed_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                )
            '''))

        # ── Integração Ailos — Cobrança Bancária API ──────────────────────────
        # Ordem de criação respeita as dependências de FK abaixo.
        if not inspector.has_table('ailos_lotes'):
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS ailos_lotes (
                    id SERIAL PRIMARY KEY,
                    tipo VARCHAR(10) NOT NULL,
                    ticket VARCHAR(60) NOT NULL UNIQUE,
                    numero_convenio VARCHAR(20) NOT NULL,
                    billing_ids JSON,
                    status VARCHAR(20) NOT NULL DEFAULT \'processing\',
                    payload_response JSON,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                )
            '''))

        if not inspector.has_table('ailos_boletos'):
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS ailos_boletos (
                    id SERIAL PRIMARY KEY,
                    billing_id INTEGER NOT NULL UNIQUE REFERENCES billings(id),
                    lote_id INTEGER REFERENCES ailos_lotes(id),
                    numero_convenio VARCHAR(20) NOT NULL,
                    numero_documento VARCHAR(40),
                    nosso_numero VARCHAR(40),
                    identificador_unico_titulo VARCHAR(60),
                    linha_digitavel VARCHAR(60),
                    codigo_barras VARCHAR(60),
                    valor_nominal NUMERIC(10,2),
                    data_vencimento DATE,
                    status_ailos VARCHAR(40),
                    payload_request JSON,
                    payload_response JSON,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                )
            '''))

        if not inspector.has_table('ailos_api_logs'):
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS ailos_api_logs (
                    id SERIAL PRIMARY KEY,
                    endpoint VARCHAR(255) NOT NULL,
                    method VARCHAR(10) NOT NULL,
                    request_payload JSON,
                    response_payload JSON,
                    status_code INTEGER,
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    error_message TEXT,
                    correlation_id VARCHAR(40),
                    billing_id INTEGER REFERENCES billings(id),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                )
            '''))

        if not inspector.has_table('ailos_integrations'):
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS ailos_integrations (
                    id SERIAL PRIMARY KEY,
                    numero_convenio VARCHAR(20) NOT NULL,
                    codigo_carteira INTEGER NOT NULL DEFAULT 1,
                    cooperativa_codigo VARCHAR(20),
                    conta_numero VARCHAR(20),
                    conta_digito VARCHAR(4),
                    status VARCHAR(20) NOT NULL DEFAULT \'pending\',
                    state VARCHAR(80),
                    cooperado_token_encrypted TEXT,
                    cooperado_token_expires_at TIMESTAMP WITH TIME ZONE,
                    authorized_at TIMESTAMP WITH TIME ZONE,
                    last_refresh_at TIMESTAMP WITH TIME ZONE,
                    last_error TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                )
            '''))

        if not inspector.has_table('ailos_client_tokens'):
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS ailos_client_tokens (
                    id SERIAL PRIMARY KEY,
                    environment VARCHAR(20) NOT NULL UNIQUE,
                    access_token_encrypted TEXT NOT NULL,
                    token_type VARCHAR(40),
                    scope VARCHAR(255),
                    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                )
            '''))

        if not inspector.has_table('ailos_retorno_arquivos'):
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS ailos_retorno_arquivos (
                    id SERIAL PRIMARY KEY,
                    numero_convenio VARCHAR(20) NOT NULL,
                    data_movimento DATE NOT NULL,
                    ticket VARCHAR(60) UNIQUE,
                    status VARCHAR(20) NOT NULL DEFAULT \'requested\',
                    storage_object_key VARCHAR(255),
                    requested_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    downloaded_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                )
            '''))


def _seed_admin() -> None:
    """
    Cria o admin inicial apenas se ainda não existir (banco vazio).

    Sem senha pública: usa INITIAL_ADMIN_PASSWORD do .env ou gera uma aleatória,
    logada UMA vez para troca no primeiro acesso. Tolerante a corrida entre
    workers (IntegrityError no e-mail único).
    """
    db = SessionLocal()
    try:
        email = settings.initial_admin_email
        if db.query(User).filter(User.email == email).first():
            return
        senha = settings.initial_admin_password or secrets.token_urlsafe(16)
        db.add(User(
            name='Administrador',
            email=email,
            password_hash=get_password_hash(senha),
            role=UserRole.ADMIN,
            active=True,
        ))
        db.commit()
        if not settings.initial_admin_password:
            logging.getLogger('uvicorn.error').warning(
                'ADMIN INICIAL criado: %s — senha gerada: %s — TROQUE no primeiro acesso.',
                email, senha,
            )
    except IntegrityError:
        db.rollback()  # outro worker criou primeiro
    finally:
        db.close()


def _run_locked(lock_key: int, fn):
    """Executa ``fn(db)`` com um advisory lock dedicado (1 worker por vez).

    A conexão do lock é SEPARADA da sessão de trabalho (uma SessionLocal
    própria, que faz COMMIT de verdade). Antes, ``Session(bind=conn)`` na mesma
    conexão do lock fazia o commit ser descartado no fim do ``with`` — o
    refresh/baixa "rodava" mas não persistia. Retorna o que ``fn`` retornar, ou
    None se outro worker já tem o lock.
    """
    lock_conn = engine.connect()
    try:
        if not lock_conn.exec_driver_sql(f'SELECT pg_try_advisory_lock({int(lock_key)})').scalar():
            return None  # outro worker tem o lock
        db = SessionLocal()
        try:
            return fn(db)
        finally:
            db.close()
    finally:
        try:
            lock_conn.exec_driver_sql(f'SELECT pg_advisory_unlock({int(lock_key)})')
        except Exception:  # noqa: BLE001
            pass
        lock_conn.close()


def _alerta_admin(mensagem: str, logger) -> None:
    """Loga em nível crítico e, se ALERT_WEBHOOK estiver no .env (Discord/Slack —
    o mesmo canal dos alertas de backup), envia a mensagem para lá."""
    logger.critical(mensagem)
    webhook = settings.alert_webhook
    if not webhook:
        return
    try:
        import requests
        # 'content' = Discord; 'text' = Slack — cada um ignora a chave do outro
        requests.post(webhook, json={'content': mensagem, 'text': mensagem}, timeout=10)
    except Exception:  # noqa: BLE001
        logger.warning('Falha ao enviar alerta para o ALERT_WEBHOOK.')


def _cooperado_token_keepalive():
    """Renova o token do cooperado Ailos (vive 30 min e NÃO pode ser renovado
    após expirar). Advisory lock: com vários workers, só um renova por ciclo.

    Endurecimentos contra a morte silenciosa da sessão:
    - 1º refresh logo no boot (5s): um deploy pode ter consumido boa parte da
      janela de 30 min — esperar 10 min já matou o token uma vez (04/07).
    - Refresh falhou com token vivo → nova tentativa em 60s (não em 10 min).
    - Sessão expirou de vez → alerta no log (CRITICAL) e no ALERT_WEBHOOK,
      uma única vez por queda; avisa também quando a sessão volta.
    """
    from app.services.ailos_client import manter_sessao_cooperado
    logger = logging.getLogger('uvicorn.error')

    CICLO = 600        # 10 min — folga de 20 min antes dos 30
    RETRY_CURTO = 60   # refresh falhou mas o token ainda vive → insiste rápido
    ja_alertado = False

    espera = 5  # primeiro refresh imediatamente após o boot
    while True:
        time.sleep(espera)
        espera = CICLO
        try:
            resultado = _run_locked(918273646, manter_sessao_cooperado)
            if resultado in ('renovado', 'relogin_disparado'):
                if ja_alertado:
                    _alerta_admin('✅ Sessão do cooperado Ailos RECUPERADA — emissão de boletos normalizada.', logger)
                    ja_alertado = False
                logger.info('Sessão Ailos (keepalive): %s.', resultado)
            elif resultado == 'refresh_falhou_token_vivo':
                espera = RETRY_CURTO
                logger.warning('Sessão Ailos: refresh falhou com token vivo — nova tentativa em %ss.', RETRY_CURTO)
            elif resultado in ('expirado_sem_relogin', 'relogin_travado'):
                if not ja_alertado:
                    _alerta_admin(
                        '🚨 Sessão do cooperado Ailos EXPIROU — emissão de boletos PARADA. '
                        'Reconecte pelo painel Financeiro (botão "Reconectar Ailos") ou via '
                        'POST /api/v1/ailos/connect + login no navegador.',
                        logger,
                    )
                    ja_alertado = True
            elif resultado not in (None, 'sem_integracao'):
                logger.warning('Sessão Ailos (keepalive): %s.', resultado)
        except Exception as exc:  # noqa: BLE001 — keepalive nunca pode derrubar o worker
            logger.warning('Keepalive do token Ailos falhou (renovará no próximo ciclo): %s', exc)


def _ailos_baixa_automatica():
    """Concilia pagamentos: consulta na Ailos os boletos de cobranças em aberto
    e dá baixa nas pagas. Roda a cada 1h. Advisory lock (só 1 worker concilia).
    Só age com o cooperado autorizado.
    """
    from app.models.ailos_integration import AilosIntegration
    from app.services.ailos_boletos import conciliar_boletos_abertos
    logger = logging.getLogger('uvicorn.error')

    def _job(db):
        integ = db.query(AilosIntegration).order_by(AilosIntegration.id.asc()).first()
        if integ and integ.status == 'authorized':
            return conciliar_boletos_abertos(db)
        return None

    while True:
        time.sleep(3600)  # 1h
        try:
            res = _run_locked(918273647, _job)
            if res and res.get('baixados'):
                logger.info(
                    'Conciliação Ailos: %s baixado(s) de %s consultado(s).',
                    res['baixados'], res['consultados'],
                )
        except Exception as exc:  # noqa: BLE001 — conciliação nunca pode derrubar o worker
            logger.warning('Conciliação automática Ailos falhou (tentará no próximo ciclo): %s', exc)


@app.on_event('startup')
def on_startup():
    # Com múltiplos workers (uvicorn --workers), o startup roda em cada processo.
    # Um advisory lock do Postgres serializa schema/seed entre os workers para
    # evitar corrida (ALTER/INSERT simultâneos).
    lock_conn = engine.connect()
    try:
        lock_conn.exec_driver_sql('SELECT pg_advisory_lock(918273645)')
        Base.metadata.create_all(bind=engine)
        ensure_schema_updates()
        _seed_admin()
    finally:
        try:
            lock_conn.exec_driver_sql('SELECT pg_advisory_unlock(918273645)')
        finally:
            lock_conn.close()

    ensure_bucket()

    # Renovador automático do token do cooperado Ailos (mantém a sessão viva
    # sem reautorização manual) + conciliação automática de pagamentos (baixa
    # dos boletos pagos). Só sobem se a integração estiver configurada.
    if settings.ailos_client_id and settings.ailos_token_encryption_key:
        threading.Thread(target=_cooperado_token_keepalive, daemon=True).start()
        threading.Thread(target=_ailos_baixa_automatica, daemon=True).start()

    # Verificação inicial de inadimplência (idempotente; não bloqueia o startup)
    try:
        from app.services.financial import mark_delinquent_clients
        startup_db = SessionLocal()
        try:
            mark_delinquent_clients(startup_db)
        finally:
            startup_db.close()
    except Exception:  # noqa: BLE001
        pass


@app.get('/')
def healthcheck():
    return {'status': 'ok', 'app': settings.app_name}
