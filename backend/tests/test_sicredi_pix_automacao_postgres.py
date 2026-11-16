"""Disputas reais em PostgreSQL para a automação do Pix (convite e associação).

Banco descartável test_pix_auto_*, criado e removido aqui; o Sicredi e o SMTP
são sempre substituídos. Executável com unittest no container de testes.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
import os
from threading import Barrier, Event, Lock
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.timezone import hoje
from app.db.session import Base
from app.models import registry_all  # noqa: F401
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus
from app.models.sicredi_pix import SicrediPixCobranca, SicrediPixRecorrencia
from app.services import email_smtp, sicredi_pix as svc, sicredi_pix_convite as convite
from app.services.sicredi_pix_client import ambiente


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'PostgreSQL descartável não configurado')
class TestPixAutomacaoPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = make_url(os.environ['TEST_DATABASE_URL'])
        cls.name = 'test_pix_auto_' + uuid4().hex[:10]
        cls.admin = sa.create_engine(base, isolation_level='AUTOCOMMIT')
        with cls.admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{cls.name}"'))
        cls.engine = sa.create_engine(base.set(database=cls.name), pool_size=10)
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        if not cls.name.startswith('test_pix_auto_'):
            raise RuntimeError('Banco de teste inválido')
        with cls.admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE "{cls.name}" WITH (FORCE)'))
        cls.admin.dispose()

    def setUp(self):
        self.sent = []
        self.lock = Lock()

        def send(db, destinatario, assunto, corpo, html=None, anexo=None, config=None, anexos=None):
            time.sleep(0.3)  # janela para a segunda sessão tentar a mesma reserva
            with self.lock:
                self.sent.append(destinatario)

        cfg = {'host': 'smtp.teste', 'port': 587, 'username': '', 'password': '', 'password_set': False,
               'from_email': 'cobranca@teste.local', 'from_name': '', 'security': 'tls', 'enabled': True}
        patches = [patch.object(email_smtp, 'enviar_email', send), patch.object(email_smtp, 'load_config', lambda db: cfg)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with self.Session() as db:
            client = Client(name='Pagador Pix', cpf_cnpj=uuid4().hex[:14], email='pagador@test.local',
                            forma_cobranca='pix_automatico')
            db.add(client); db.commit()
            self.cid = client.id

    def _rec(self, db, status='APROVADA', **extra):
        rec = SicrediPixRecorrencia(client_id=self.cid, ambiente=ambiente(), referencia=uuid4().hex[:30],
            id_rec='RN' + uuid4().hex[:27], status=status, ativa=True, data_inicial=hoje() + timedelta(days=5),
            objeto='Mensalidades', valor_minimo=Decimal('0.01'), **extra)
        db.add(rec); db.flush()
        return rec

    def _billing(self, db, due):
        billing = Billing(client_id=self.cid, amount=Decimal('64.99'), due_date=due, status=BillingStatus.PENDING,
                          billing_type='consolidada', payment_method='pix_automatico')
        db.add(billing); db.flush()
        return billing

    def test_duas_sessoes_enviam_um_unico_convite(self):
        with self.Session() as db:
            rec = self._rec(db, status='CRIADA', pix_copia_cola='000201autorizacao', convite_canal='email',
                            convite_status='pendente')
            db.commit()
            rec_id = rec.id
        barrier = Barrier(2)

        def run():
            with self.Session() as db:
                barrier.wait(timeout=10)
                try:
                    convite.enviar_convite(db, rec_id)
                except convite.ConviteError:
                    db.rollback()
        with ThreadPoolExecutor(max_workers=2) as pool:
            for future in [pool.submit(run) for _ in range(2)]:
                future.result(timeout=30)
        self.assertEqual(self.sent, ['pagador@test.local'])
        with self.Session() as db:
            rec = db.get(SicrediPixRecorrencia, rec_id)
            self.assertEqual((rec.convite_status, rec.convite_tentativas), ('enviado', 1))

    def test_dois_workers_associam_cada_cobranca_uma_vez(self):
        with self.Session() as db:
            self._rec(db)
            ids = [self._billing(db, hoje() + timedelta(days=5 + 31 * i)).id for i in range(3)]
            db.commit()
        barrier = Barrier(2)
        errors = []

        def run():
            with self.Session() as db:
                barrier.wait(timeout=10)
                try:
                    svc.associar_cobrancas_do_fechamento(db)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            for future in [pool.submit(run) for _ in range(2)]:
                future.result(timeout=30)
        self.assertEqual(errors, [])
        with self.Session() as db:
            got = db.scalars(sa.select(SicrediPixCobranca.billing_id).where(SicrediPixCobranca.billing_id.in_(ids))).all()
            self.assertEqual(sorted(got), sorted(ids))

    def test_fechamento_nao_e_desfeito_por_reserva_concorrente_no_mesmo_ciclo(self):
        """Worker reserva B1 (ciclo C) sem confirmar; o "fechamento" cria B2 no mesmo
        ciclo e tenta reservar. O INSERT espera o worker; quando ele confirma, só a
        reserva de B2 é descartada (savepoint) e o fechamento confirma o resto."""
        due = hoje() + timedelta(days=5)
        with self.Session() as db:
            self._rec(db)
            b1 = self._billing(db, due).id
            db.commit()
        worker = self.Session()
        svc.enfileirar_pendentes(worker, self.cid)
        worker.flush()  # reserva de B1 feita, transação ainda aberta
        started, finished = Event(), Event()
        result = {}

        def closure():
            with self.Session() as db:
                b2 = self._billing(db, due + timedelta(days=1))
                outro = self._billing(db, due + timedelta(days=40))
                started.set()
                result['queued'] = svc.enfileirar_fechamento(db, [b2.id, outro.id])
                db.commit()  # o fechamento inteiro precisa sobreviver
                result['ids'] = (b2.id, outro.id)
            finished.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(closure)
            started.wait(timeout=10)
            time.sleep(0.5)  # o INSERT do fechamento fica aguardando o índice único
            self.assertFalse(finished.is_set())
            worker.commit()
            worker.close()
            future.result(timeout=30)
        b2, outro = result['ids']
        self.assertEqual(len(result['queued']), 1)
        with self.Session() as db:
            self.assertIsNotNone(db.get(Billing, b2))
            charges = dict(db.execute(sa.select(SicrediPixCobranca.billing_id, SicrediPixCobranca.status)).all())
            self.assertIn(b1, charges)
            self.assertIn(outro, charges)
            self.assertNotIn(b2, charges)
