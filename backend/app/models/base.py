from datetime import datetime
from sqlalchemy import Boolean, DateTime, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base
class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
def _trgm_visivel(_ddl, _target, bind, **_kw) -> bool:
    return bool(bind.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_opclass "
        "WHERE opcname = 'gin_trgm_ops' AND pg_opclass_is_visible(oid))"
    )).scalar())


def trigram_index(name: str, column: str) -> Index:
    """Índice GIN pg_trgm da Busca Global (migration c3f9a1b2d4e6).

    Declarado no modelo para o autogenerate não propor removê-lo (DB-01). Em
    produção quem cria é a migration (que instala a extensão). O ``ddl_if``
    vale só para ``create_all`` dos testes: no SQLite e em schema de teste sem
    pg_trgm visível o índice é pulado — ele só acelera ILIKE, não é regra.
    O Alembic não lê ``ddl_if``, então o autogenerate continua enxergando-o.
    """
    return Index(
        name, column, postgresql_using='gin', postgresql_ops={column: 'gin_trgm_ops'},
    ).ddl_if(dialect='postgresql', callable_=_trgm_visivel)


class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
