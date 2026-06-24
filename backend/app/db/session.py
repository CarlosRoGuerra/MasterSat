from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings
class Base(DeclarativeBase): pass

# Pooling só faz sentido em bancos de servidor (Postgres). O SQLite usado nos
# testes usa SingletonThreadPool e NÃO aceita pool_size/max_overflow.
_pool_kwargs = (
    {}
    if settings.database_url.startswith('sqlite')
    else dict(
        pool_pre_ping=True,   # descarta conexão morta antes de usar (sobrevive a restart do Postgres)
        pool_recycle=1800,    # recicla conexões a cada 30min (evita conexões zumbis)
        pool_size=5,
        max_overflow=10,
    )
)
engine = create_engine(settings.database_url, future=True, **_pool_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
