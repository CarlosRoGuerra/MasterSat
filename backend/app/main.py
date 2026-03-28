from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import Base, SessionLocal, engine
from app.models import billing, client, contract, document, password_reset_token, plan, service_order, tracker, user, vehicle
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

app.include_router(api_router, prefix=settings.api_v1_prefix)


def ensure_schema_updates():
    with engine.begin() as conn:
        inspector = inspect(conn)

        if inspector.has_table('clients'):
            client_columns = {column['name'] for column in inspector.get_columns('clients')}
            if 'extra_emails' not in client_columns:
                conn.execute(text('ALTER TABLE clients ADD COLUMN extra_emails JSON'))

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
