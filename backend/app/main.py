from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import Base, SessionLocal, engine
from app.models import billing, client, contract, password_reset_token, plan, service_order, tracker, user, vehicle
from app.models.enums import UserRole
from app.models.user import User

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


@app.on_event('startup')
def on_startup():
    Base.metadata.create_all(bind=engine)
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
