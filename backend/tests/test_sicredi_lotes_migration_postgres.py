"""Reproduz a revisão marcada com coluna ausente no PostgreSQL local."""
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.test_fase02_migrations_postgres import (  # noqa: F401
    _alembic, _billing, _colunas, _revisao, _seed_base, banco,
)

pytestmark = pytest.mark.postgres
ANTERIOR = 'a6e2d9c51b74'
CORRECAO = 'b8e4c6d2a907'


@pytest.mark.parametrize('schema_incompleto', [True, False])
def test_upgrade_completa_vinculo_sem_perder_boleto_existente(banco, schema_incompleto):
    _alembic(banco, 'upgrade', ANTERIOR)
    with banco.begin() as conn:
        ids = _seed_base(conn)
        bid = _billing(conn, ids, billing_type='avulsa')
        conn.execute(text("""
            INSERT INTO sicredi_boletos
                (billing_id, ambiente, cooperativa, posto, codigo_beneficiario,
                 nosso_numero, tipo_cobranca, status)
            VALUES (:bid, 'sandbox', '2602', '13', '11948', '262000010', 'NORMAL', 'REGISTRADO')
        """), {'bid': bid})
        if schema_incompleto:
            conn.execute(text('ALTER TABLE sicredi_boletos DROP COLUMN lote_id'))
            conn.execute(text('ALTER TABLE sicredi_boletos DROP CONSTRAINT ck_sicredi_boletos_status'))
            conn.execute(text("""ALTER TABLE sicredi_boletos ADD CONSTRAINT ck_sicredi_boletos_status
                CHECK (status IN ('REGISTRANDO', 'DESFECHO_DESCONHECIDO', 'ERRO_REGISTRO', 'REGISTRADO'))"""))
    assert _revisao(banco) == ANTERIOR
    _alembic(banco, 'upgrade', 'head')
    assert _revisao(banco) == CORRECAO
    assert 'lote_id' in _colunas(banco, 'sicredi_boletos')
    inspector = inspect(banco)
    assert any(fk['referred_table'] == 'sicredi_lotes' and fk['constrained_columns'] == ['lote_id']
               for fk in inspector.get_foreign_keys('sicredi_boletos'))
    assert 'ix_sicredi_boletos_lote_id' in {i['name'] for i in inspector.get_indexes('sicredi_boletos')}
    with banco.begin() as conn:
        assert conn.execute(text('SELECT nosso_numero FROM sicredi_boletos WHERE billing_id=:bid'), {'bid': bid}).scalar_one() == '262000010'
        assert conn.execute(text('SELECT amount FROM billings WHERE id=:bid'), {'bid': bid}).scalar_one() == 50
        conn.execute(text("UPDATE sicredi_boletos SET status='NA_FILA' WHERE billing_id=:bid"), {'bid': bid})
    with pytest.raises(IntegrityError), banco.begin() as conn:
        conn.execute(text('UPDATE sicredi_boletos SET lote_id=999999 WHERE billing_id=:bid'), {'bid': bid})
    _alembic(banco, 'downgrade', ANTERIOR)
    assert 'lote_id' in _colunas(banco, 'sicredi_boletos')
    _alembic(banco, 'upgrade', 'head')
