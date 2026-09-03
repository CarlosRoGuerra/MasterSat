import unicodedata

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings
class Base(DeclarativeBase): pass

# Pooling só faz sentido em bancos de servidor (Postgres). O SQLite usado nos
# testes usa SingletonThreadPool e NÃO aceita pool_size/max_overflow.
_is_sqlite = settings.database_url.startswith('sqlite')
_pool_kwargs = (
    {}
    if _is_sqlite
    else dict(
        pool_pre_ping=True,   # descarta conexão morta antes de usar (sobrevive a restart do Postgres)
        pool_recycle=1800,    # recicla conexões a cada 30min (evita conexões zumbis)
        pool_size=5,
        max_overflow=10,
    )
)
engine = create_engine(settings.database_url, future=True, **_pool_kwargs)


def _sqlite_unaccent(value: str | None) -> str | None:
    if value is None:
        return None
    return ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))


# A busca global usa unaccent() pra ignorar acento (ver app/services/
# global_search.py). No Postgres é a extensão real (migration); aqui
# registramos a mesma função em Python pra rodar a MESMA query SQL nos dois
# bancos, sem `if dialeto` na lógica de busca. Escuta na classe Engine (não
# na instância `engine` acima) porque os testes montam seu próprio engine
# SQLite :memory: por teste (ver tests/conftest.py `_make_engine`) — só
# psycopg (Postgres) não tem `create_function`, então o guard abaixo já cobre
# os dois bancos sem checar dialeto explicitamente.
@event.listens_for(Engine, 'connect')
def _register_sqlite_functions(dbapi_connection, _):
    create_function = getattr(dbapi_connection, 'create_function', None)
    if create_function is not None:
        create_function('unaccent', 1, _sqlite_unaccent)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
