"""Migration do convite Pix: só colunas novas; autorizações existentes preservadas.

SQLite sempre; PostgreSQL apenas com TEST_DATABASE_URL (banco test_pix_conv_* descartável).
No container: python -m unittest tests.test_sicredi_pix_convite_migration -v
"""
import importlib.util
import os
from pathlib import Path
import unittest
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).parents[1]
REVISION = 'e6b2d4f8a1c3'
ANTERIOR = 'd2a7c9e41b60'
NOVAS = {'convite_canal', 'convite_status', 'convite_destino', 'convite_tentativas',
         'convite_iniciado_em', 'convite_enviado_em', 'convite_erro'}


def migration(connection):
    spec = importlib.util.spec_from_file_location('pix_convite_migration',
                                                  BACKEND / f'alembic/versions/{REVISION}_sicredi_pix_convite.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def colunas(conn):
    return {c['name'] for c in sa.inspect(conn).get_columns('sicredi_pix_recorrencias')}


class TestConviteMigration(unittest.TestCase):
    def test_sqlite_aditiva_e_reversivel(self):
        engine = sa.create_engine('sqlite://')
        with engine.begin() as conn:
            conn.execute(sa.text('CREATE TABLE sicredi_pix_recorrencias (id INTEGER PRIMARY KEY, client_id INTEGER, '
                                 "status VARCHAR(30), id_rec VARCHAR(29), pix_copia_cola TEXT)"))
            conn.execute(sa.text("INSERT INTO sicredi_pix_recorrencias VALUES (1, 7, 'CRIADA', 'RN1', '000201')"))
            module = migration(conn)
            module.upgrade()
            self.assertTrue(NOVAS <= colunas(conn))
            row = conn.execute(sa.text('SELECT client_id, status, id_rec, pix_copia_cola, convite_tentativas, '
                                       'convite_status FROM sicredi_pix_recorrencias WHERE id=1')).one()
            self.assertEqual(tuple(row), (7, 'CRIADA', 'RN1', '000201', 0, None))
            module.downgrade()
            self.assertFalse(NOVAS & colunas(conn))
            self.assertEqual(conn.execute(sa.text('SELECT status FROM sicredi_pix_recorrencias WHERE id=1')).scalar(), 'CRIADA')


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'PostgreSQL descartável não configurado')
class TestConviteMigrationPostgres(unittest.TestCase):
    def test_upgrade_e_downgrade_preservam_autorizacao(self):
        base = make_url(os.environ['TEST_DATABASE_URL'])
        name = 'test_pix_conv_' + uuid4().hex[:10]
        admin = sa.create_engine(base, isolation_level='AUTOCOMMIT')
        engine = None
        try:
            with admin.connect() as conn:
                conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
            engine = sa.create_engine(base.set(database=name))
            cfg = Config(str(BACKEND / 'alembic.ini'))
            cfg.set_main_option('script_location', str(BACKEND / 'alembic'))
            with engine.begin() as conn:
                cfg.attributes['connection'] = conn
                command.upgrade(cfg, ANTERIOR)
                cid = conn.execute(sa.text("INSERT INTO clients (name,cpf_cnpj,type,status,is_deleted,forma_cobranca) "
                    "VALUES ('Teste Pix','52998224725','pf','ACTIVE',false,'pix_automatico') RETURNING id")).scalar_one()
                rid = conn.execute(sa.text("""INSERT INTO sicredi_pix_recorrencias
                    (client_id,ambiente,referencia,id_rec,status,ativa,data_inicial,objeto,valor_minimo,
                     politica_retentativa,processar_evento,pix_copia_cola)
                    VALUES (:cid,'homologacao','contrato-teste','RN1234567820261105abcdefghijk','CRIADA',true,
                            '2026-11-20','Mensalidades',0.01,'NAO_PERMITE',false,'000201')
                    RETURNING id"""), {'cid': cid}).scalar_one()
                command.upgrade(cfg, REVISION)
                self.assertEqual(MigrationContext.configure(conn).get_current_revision(), REVISION)
                self.assertTrue(NOVAS <= colunas(conn))
                row = conn.execute(sa.text('SELECT status, id_rec, pix_copia_cola, ativa, convite_tentativas, convite_status '
                                           'FROM sicredi_pix_recorrencias WHERE id=:rid'), {'rid': rid}).one()
                self.assertEqual(tuple(row), ('CRIADA', 'RN1234567820261105abcdefghijk', '000201', True, 0, None))
                conn.execute(sa.text("UPDATE sicredi_pix_recorrencias SET convite_status='enviado', convite_tentativas=1 "
                                     'WHERE id=:rid'), {'rid': rid})
                command.downgrade(cfg, ANTERIOR)
                self.assertFalse(NOVAS & colunas(conn))
                self.assertEqual(conn.execute(sa.text('SELECT status FROM sicredi_pix_recorrencias WHERE id=:rid'),
                                              {'rid': rid}).scalar(), 'CRIADA')
                command.upgrade(cfg, 'head')
                self.assertEqual(MigrationContext.configure(conn).get_current_revision(), REVISION)
        finally:
            if engine is not None:
                engine.dispose()
            if not name.startswith('test_pix_conv_'):
                raise RuntimeError('Nome de teste inválido')
            with admin.connect() as conn:
                conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            admin.dispose()
