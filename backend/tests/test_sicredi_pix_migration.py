"""Migration Pix: preservação de dados e restrições, também executável sem pytest.

No container local: python -m unittest tests.test_sicredi_pix_migration -v
O teste PostgreSQL usa somente um banco descartável test_pix_* quando
TEST_DATABASE_URL está definido; nunca altera o banco informado na URL.
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
REVISION = 'c1f6a8d3e902'


def migration(connection):
    spec = importlib.util.spec_from_file_location('pix_migration', BACKEND / f'alembic/versions/{REVISION}_sicredi_pix_automatico.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


class TestPixMigration(unittest.TestCase):
    def test_preserva_clientes_e_nao_converte_cobrancas(self):
        engine = sa.create_engine('sqlite://')
        with engine.begin() as conn:
            old = "forma_cobranca IS NULL OR forma_cobranca IN ('boleto_mensal','carne_ailos','carne_sicredi','carne_simples','cartao_credito')"
            conn.execute(sa.text('CREATE TABLE clients (id INTEGER PRIMARY KEY, forma_cobranca VARCHAR(20), '
                                 'CONSTRAINT ck_clients_forma_cobranca CHECK (' + old + '))'))
            conn.execute(sa.text('CREATE TABLE billings (id INTEGER PRIMARY KEY, amount NUMERIC(10,2))'))
            conn.execute(sa.text("INSERT INTO clients VALUES (1,'boleto_mensal')"))
            conn.execute(sa.text('INSERT INTO billings VALUES (7,64.99)'))
            module = migration(conn)
            module.upgrade()
            self.assertEqual(conn.execute(sa.text('SELECT forma_cobranca FROM clients WHERE id=1')).scalar(), 'boleto_mensal')
            self.assertEqual(conn.execute(sa.text('SELECT amount FROM billings WHERE id=7')).scalar(), 64.99)
            self.assertEqual(conn.execute(sa.text('SELECT count(*) FROM sicredi_pix_recorrencias')).scalar(), 0)
            conn.execute(sa.text("UPDATE clients SET forma_cobranca='pix_automatico' WHERE id=1"))
            with self.assertRaises(RuntimeError): module.downgrade()
            conn.execute(sa.text("UPDATE clients SET forma_cobranca='boleto_mensal' WHERE id=1"))
            module.downgrade()
            self.assertNotIn('sicredi_pix_cobrancas', sa.inspect(conn).get_table_names())


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'PostgreSQL descartável não configurado')
class TestPixPostgres(unittest.TestCase):
    def test_upgrade_dados_indices_e_uma_cobranca_por_ciclo(self):
        base = make_url(os.environ['TEST_DATABASE_URL'])
        name = 'test_pix_' + uuid4().hex[:12]
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
                command.upgrade(cfg, 'b8e4c6d2a907')
                cid = conn.execute(sa.text("INSERT INTO clients (name,cpf_cnpj,type,status,is_deleted,forma_cobranca) "
                    "VALUES ('Teste Pix','52998224725','pf','ACTIVE',false,'boleto_mensal') RETURNING id")).scalar_one()
                bid = conn.execute(sa.text("INSERT INTO billings (client_id,billing_type,amount,due_date,status,is_deleted,"
                    "competencia_liberada,somente_sistema) VALUES (:cid,'avulsa',64.99,'2026-11-15','PENDING',false,false,false) RETURNING id"),
                    {'cid': cid}).scalar_one()
                command.upgrade(cfg, REVISION)
                self.assertEqual(conn.execute(sa.text('SELECT forma_cobranca FROM clients WHERE id=:cid'), {'cid': cid}).scalar(), 'boleto_mensal')
                self.assertEqual(str(conn.execute(sa.text('SELECT amount FROM billings WHERE id=:bid'), {'bid': bid}).scalar()), '64.99')
                conn.execute(sa.text("UPDATE clients SET forma_cobranca='pix_automatico' WHERE id=:cid"), {'cid': cid})
                rid = conn.execute(sa.text("""INSERT INTO sicredi_pix_recorrencias
                    (client_id,ambiente,referencia,status,ativa,data_inicial,objeto,valor_minimo,politica_retentativa,processar_evento)
                    VALUES (:cid,'homologacao','contrato-teste','APROVADA',true,'2026-11-15','Mensalidades',0.01,'NAO_PERMITE',false)
                    RETURNING id"""), {'cid': cid}).scalar_one()
                conn.execute(sa.text("""INSERT INTO sicredi_pix_cobrancas
                    (billing_id,recorrencia_id,txid,ciclo,vencimento,valor,status,tentativas,processar_evento)
                    VALUES (:bid,:rid,:txid,'2026-11-15','2026-11-15',64.99,'NA_FILA',0,false)"""),
                    {'bid': bid, 'rid': rid, 'txid': 'a' * 32})
                with self.assertRaises(sa.exc.IntegrityError), conn.begin_nested():
                    conn.execute(sa.text("""INSERT INTO sicredi_pix_recorrencias
                        (client_id,ambiente,referencia,status,ativa,data_inicial,objeto,valor_minimo,politica_retentativa,processar_evento)
                        VALUES (:cid,'homologacao','outro-contrato','CRIADA',true,'2026-11-15','Mensalidades',0.01,'NAO_PERMITE',false)"""), {'cid': cid})
                self.assertEqual(MigrationContext.configure(conn).get_current_revision(), REVISION)
                indexes = {i['name'] for i in sa.inspect(conn).get_indexes('sicredi_pix_cobrancas')}
                self.assertIn('uq_sicredi_pix_cobranca_ciclo', indexes)
                self.assertEqual(conn.execute(sa.text('SELECT count(*) FROM sicredi_pix_recebimentos')).scalar(), 0)
        finally:
            if engine is not None: engine.dispose()
            if not name.startswith('test_pix_'): raise RuntimeError('Nome de teste inválido')
            with admin.connect() as conn:
                conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            admin.dispose()
