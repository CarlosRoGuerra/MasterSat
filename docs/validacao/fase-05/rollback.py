"""Ensaio em banco exclusivo fase05_rollback; nunca usa DATABASE_URL real."""
import hashlib
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine, text
from tests.test_fase02_migrations_postgres import _alembic, _billing, _seed_base

SERVER = 'postgresql+psycopg://postgres:fase05-test-only@mastersat-f05-pg:5432/'
NAME = 'fase05_rollback'
admin = create_engine(SERVER + 'fase05', isolation_level='AUTOCOMMIT')
db = create_engine(SERVER + NAME)
evidence = Path('/evidence/rollback.json')


def documentos():
    with db.connect() as conn:
        rows = conn.execute(text('SELECT billing_id, numero_rps, serie_rps, numero_nfse, '
                                 'chave_acesso, protocolo, xml_envio, xml_retorno '
                                 'FROM nfse_notas ORDER BY id')).all()
        return hashlib.sha256(json.dumps([tuple(row) for row in rows]).encode()).hexdigest()


if sys.argv[1] == 'prepare':
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE {NAME}'))
    _alembic(db, 'upgrade', 'a4c7e2f9b1d6')
    with db.begin() as conn:
        ids = _seed_base(conn)
        for status in ('emitida', 'pending'):
            bid = _billing(conn, ids, billing_type='avulsa', amount=100)
            conn.execute(text("""INSERT INTO nfse_notas
                (billing_id,status,numero_rps,serie_rps,numero_nfse,chave_acesso,protocolo,xml_envio,xml_retorno)
                VALUES (:bid,:st,'10','40000','99','1234','PROTO','<DPS/>','<NFSe/>')"""),
                {'bid': bid, 'st': status})
    antes = documentos()
    _alembic(db, 'upgrade', 'head')
    _alembic(db, 'downgrade', 'a4c7e2f9b1d6')
    assert documentos() == antes
    evidence.write_text(json.dumps({'before': antes, 'after_downgrade': documentos()}), encoding='utf-8')
    print('Código novo: upgrade + downgrade preservam documentos; imagem antiga pode ler revisão Fase 04.')
elif sys.argv[1] == 'verify':
    data = json.loads(evidence.read_text())
    _alembic(db, 'upgrade', 'head')
    assert documentos() == data['before']
    with db.connect() as conn:
        assert conn.execute(text('SELECT status FROM nfse_notas ORDER BY id')).scalars().all() == ['emitida', 'desconhecido']
    data['after_reupgrade'] = documentos()
    data['result'] = 'preservado'
    evidence.write_text(json.dumps(data, indent=2), encoding='utf-8')
    db.dispose()
    assert NAME == 'fase05_rollback'
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE {NAME} WITH (FORCE)'))
    print('Reupgrade preserva identidade/XML/status; banco descartável removido.')
else:
    raise ValueError('use prepare ou verify')
