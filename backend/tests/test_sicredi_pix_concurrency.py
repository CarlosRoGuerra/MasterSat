"""Disputas reais de sessões PostgreSQL; o banco Sicredi é sempre substituído.

Executável com unittest no container local, somente em banco test_pix_*.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import os
from threading import Barrier, Lock
import unittest
from unittest.mock import patch
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.timezone import hoje
from app.db.session import Base
from app.models import registry_all  # noqa: F401
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus
from app.models.sicredi_pix import SicrediPixCobranca, SicrediPixRecorrencia
from app.services import sicredi_pix as svc
from app.services.sicredi_pix_client import ambiente


class Bank:
    def __init__(self):
        self.lock = Lock()
        self.calls = []
        self.status = 'ATIVA'
        self.rec_id = 'RN1234567820261105' + uuid4().hex[:11]

    def __call__(self, **kwargs): return self
    def __enter__(self): return self
    def __exit__(self, *_): pass

    def request(self, method, path, **kwargs):
        with self.lock:
            self.calls.append((method, path))
            if method == 'POST' and path.endswith('/locrec'): return {'id': 10}
            if method == 'POST' and path.endswith('/rec'): return {'idRec': self.rec_id}
            if '/rec/' in path: return {'idRec': path.rsplit('/', 1)[1], 'status': 'APROVADA'}
            if method == 'PATCH': self.status = 'CANCELADA'
            return {'txid': path.rsplit('/', 1)[1], 'idRec': self.rec_id, 'status': self.status}


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'PostgreSQL descartável não configurado')
class TestPixConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = make_url(os.environ['TEST_DATABASE_URL'])
        cls.name = 'test_pix_' + uuid4().hex[:12]
        cls.admin = sa.create_engine(base, isolation_level='AUTOCOMMIT')
        with cls.admin.connect() as conn: conn.execute(sa.text(f'CREATE DATABASE "{cls.name}"'))
        cls.engine = sa.create_engine(base.set(database=cls.name))
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        if not cls.name.startswith('test_pix_'): raise RuntimeError('Banco de teste inválido')
        with cls.admin.connect() as conn: conn.execute(sa.text(f'DROP DATABASE "{cls.name}" WITH (FORCE)'))
        cls.admin.dispose()

    def setUp(self):
        self.bank = Bank()
        self.api_patch = patch.object(svc, 'PixClient', self.bank)
        self.write_patch = patch.object(svc, 'exigir_escrita')
        self.api_patch.start(); self.write_patch.start()
        self.addCleanup(self.api_patch.stop); self.addCleanup(self.write_patch.stop)
        with self.Session() as db:
            client = Client(name='Teste concorrência', cpf_cnpj=uuid4().hex[:18], forma_cobranca='pix_automatico')
            db.add(client); db.commit()
            self.cid = client.id

    def create_charge(self):
        with self.Session() as db:
            rec = SicrediPixRecorrencia(client_id=self.cid, ambiente=ambiente(), referencia=uuid4().hex,
                id_rec=self.bank.rec_id, status='APROVADA', ativa=True, data_inicial=hoje() + timedelta(days=10),
                objeto='Mensalidades', valor_minimo=Decimal('0.01'))
            billing = Billing(client_id=self.cid, amount=Decimal('64.99'), due_date=hoje() + timedelta(days=10),
                              status=BillingStatus.PENDING, billing_type='avulsa')
            db.add_all([rec, billing]); db.flush()
            svc.enfileirar_fechamento(db, [billing.id]); db.commit()
            self.charge_id = db.scalar(sa.select(SicrediPixCobranca.id).where(SicrediPixCobranca.billing_id == billing.id))

    def test_duas_sessoes_so_enviam_um_agendamento(self):
        self.create_charge()
        barrier = Barrier(2)
        def run():
            with self.Session() as db:
                charge = db.get(SicrediPixCobranca, self.charge_id)
                barrier.wait(timeout=10)
                svc.processar_cobranca(db, charge)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run) for _ in range(2)]
            for future in futures: future.result(timeout=30)
        self.assertEqual(len([c for c in self.bank.calls if c[0] == 'PUT']), 1)

    def test_cancelamento_recarrega_estado_apos_envio_concorrente(self):
        self.create_charge()
        with self.Session() as stale:
            charge = stale.get(SicrediPixCobranca, self.charge_id)
            self.assertIsNone(charge.enviada_em)
            with self.Session() as sender:
                svc.processar_cobranca(sender, sender.get(SicrediPixCobranca, self.charge_id))
            svc.cancelar_cobranca(stale, charge)
        self.assertEqual(len([c for c in self.bank.calls if c[0] == 'PATCH']), 1)
        with self.Session() as db:
            self.assertEqual(db.get(SicrediPixCobranca, self.charge_id).status, 'CANCELADA')

    def test_criacao_concorrente_preserva_uma_autorizacao(self):
        barrier = Barrier(2)
        def run():
            with self.Session() as db:
                barrier.wait(timeout=10)
                svc.criar_recorrencia(db, self.cid, data_inicial=hoje() + timedelta(days=10),
                                     valor_minimo=Decimal('0.01'), objeto='Mensalidades')
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run) for _ in range(2)]
            for future in futures: future.result(timeout=30)
        self.assertEqual(len([c for c in self.bank.calls if c[0] == 'POST']), 2)  # Location + recorrência.
        with self.Session() as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(SicrediPixRecorrencia)
                                       .where(SicrediPixRecorrencia.client_id == self.cid)), 1)
