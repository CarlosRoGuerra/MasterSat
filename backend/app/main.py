from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import Base, SessionLocal, engine
from app.models import audit_log, billing, billing_change_log, client, client_charge_item, contract, document, integration_log, password_reset_token, plan, service_order, service_order_status_log, service_product, tracker, tracker_history, user, vehicle  # noqa: F401 — side-effect imports that register models with SQLAlchemy Base
from app.core.audit import AuditMiddleware
from app.models.enums import UserRole
from app.models.user import User
from app.services.storage import ensure_bucket

app = FastAPI(title=settings.app_name)

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
app.add_middleware(AuditMiddleware)

app.include_router(api_router, prefix=settings.api_v1_prefix)


def ensure_schema_updates():
    with engine.begin() as conn:
        inspector = inspect(conn)

        if inspector.has_table('clients'):
            client_columns = {column['name'] for column in inspector.get_columns('clients')}
            if 'extra_emails' not in client_columns:
                conn.execute(text('ALTER TABLE clients ADD COLUMN extra_emails JSON'))
            if 'contacts' not in client_columns:
                conn.execute(text('ALTER TABLE clients ADD COLUMN contacts JSON'))

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
            if 'billing_interval_months' not in plan_columns:
                conn.execute(text('ALTER TABLE plans ADD COLUMN billing_interval_months INTEGER DEFAULT 1'))

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


@app.on_event('startup')
def on_startup():
    Base.metadata.create_all(bind=engine)
    ensure_schema_updates()
    ensure_bucket()
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == 'admin@rastreamento.local').first()
        admin_hash = get_password_hash('Admin@123')

        if not admin:
            db.add(
                User(
                    name='Administrador',
                    email='admin@rastreamento.local',
                    password_hash=admin_hash,
                    role=UserRole.ADMIN,
                    active=True,
                )
            )
        else:
            admin.name = 'Administrador'
            admin.password_hash = admin_hash
            admin.role = UserRole.ADMIN
            admin.active = True
            admin.is_deleted = False

        db.commit()
    finally:
        db.close()


@app.get('/')
def healthcheck():
    return {'status': 'ok', 'app': settings.app_name}
