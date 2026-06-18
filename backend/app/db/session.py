from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings
class Base(DeclarativeBase): pass
engine = create_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,   # descarta conexão morta antes de usar (sobrevive a restart do Postgres)
    pool_recycle=1800,    # recicla conexões a cada 30min (evita conexões zumbis)
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
