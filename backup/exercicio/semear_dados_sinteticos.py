"""
Popula um banco + MinIO DESCARTÁVEIS com dados sintéticos para o exercício de
recuperação. Roda dentro da imagem do backend (usa os modelos e o storage da
aplicação), com DATABASE_URL/MINIO_* apontando para os containers de ensaio.

Recusa rodar se o banco já tiver clientes: nunca escreve em base existente.

Tudo aqui é inventado: CPFs sequenciais, PFX autoassinado, tokens aleatórios.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, '/app')

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs12  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.models.registry_all  # noqa: E402,F401  (registra todos os modelos)
from app.core import crypto  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.models.ailos_client_token import AilosClientToken  # noqa: E402
from app.models.ailos_integration import AilosIntegration  # noqa: E402
from app.models.ailos_retorno_arquivo import AilosRetornoArquivo  # noqa: E402
from app.models.billing import Billing  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.enums import BillingStatus, ClientStatus  # noqa: E402
from app.models.nfse_certificado import NfseCertificado  # noqa: E402
from app.models.payable import Payable  # noqa: E402
from app.models.system_setting import SystemSetting  # noqa: E402
from app.services import storage  # noqa: E402

N_CLIENTES = int(os.environ.get('ENSAIO_CLIENTES', '500'))
COBRANCAS_POR_CLIENTE = int(os.environ.get('ENSAIO_COBRANCAS_POR_CLIENTE', '12'))
N_DOCUMENTOS = int(os.environ.get('ENSAIO_DOCUMENTOS', '200'))
SENHA_PFX = secrets.token_urlsafe(16)


def _pfx() -> bytes:
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agora = dt.datetime.now(dt.timezone.utc)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'EMPRESA SINTETICA DE ENSAIO:00000000000191')])
    cert = (
        x509.CertificateBuilder().subject_name(nome).issuer_name(nome)
        .public_key(chave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(days=1)).not_valid_after(agora + dt.timedelta(days=365))
        .sign(chave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b'ensaio', chave, cert, None, serialization.BestAvailableEncryption(SENHA_PFX.encode()),
    )


def main() -> None:
    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        if db.scalar(select(func.count()).select_from(Client)):
            raise SystemExit('banco já tem clientes — o semeador só roda em banco de ensaio vazio')

        storage.ensure_bucket()
        status_ciclo = [BillingStatus.PAID, BillingStatus.PAID, BillingStatus.PENDING,
                        BillingStatus.OVERDUE, BillingStatus.CANCELED]
        clientes = []
        for i in range(N_CLIENTES):
            clientes.append(Client(name=f'Cliente Sintético {i:05d}', cpf_cnpj=f'{i:011d}',
                                   type='pf', status=ClientStatus.ACTIVE))
        db.add_all(clientes)
        db.flush()

        hoje = dt.date(2026, 9, 30)
        for c in clientes:
            for m in range(COBRANCAS_POR_CLIENTE):
                st = status_ciclo[(c.id + m) % len(status_ciclo)]
                valor = Decimal(89 + (c.id % 7) * 10) + Decimal('0.90')
                db.add(Billing(
                    client_id=c.id, amount=valor, due_date=hoje - dt.timedelta(days=30 * m),
                    status=st, billing_type='recorrente',
                    paid_amount=valor if st == BillingStatus.PAID else None,
                    payment_date=hoje - dt.timedelta(days=30 * m) if st == BillingStatus.PAID else None,
                    is_deleted=(c.id + m) % 97 == 0,
                ))
        for i in range(300):
            db.add(Payable(description=f'Conta sintética {i}', amount=Decimal('150.35') + i,
                           due_date=hoje - dt.timedelta(days=i % 90)))

        enviados = excluidos = substituidos = 0
        for i in range(N_DOCUMENTOS):
            c = clientes[i % len(clientes)]
            chave = f'clients/{c.id}/documents/{uuid.uuid4()}-documento-{i}.pdf'
            conteudo = b'%PDF-1.4\n% documento sintetico de ensaio\n' + secrets.token_bytes(4096 + i * 37)
            ativo = True
            if i % 25 == 0:
                # Documento excluído: objeto removido do storage, linha inativa.
                ativo = False
                excluidos += 1
            else:
                storage.upload_bytes(chave, conteudo, 'application/pdf')
                enviados += 1
                if i % 40 == 1:
                    ativo = False  # versão substituída: inativa, mas o objeto fica
                    substituidos += 1
            db.add(Document(file_name=f'documento-{i}.pdf', object_key=chave, content_type='application/pdf',
                            size_bytes=len(conteudo), reference_type='client', reference_id=c.id,
                            category='contrato', active=ativo))

        retorno = b'RETORNO CNAB SINTETICO\n' * 50
        storage.upload_bytes('ailos/retorno/ensaio-20260929.ret', retorno, 'text/plain')
        db.add(AilosRetornoArquivo(numero_convenio='000000', data_movimento=dt.date(2026, 9, 29),
                                   status='downloaded', storage_object_key='ailos/retorno/ensaio-20260929.ret'))

        db.add(AilosClientToken(environment='producao',
                                access_token_encrypted=crypto.encrypt_token(secrets.token_urlsafe(32)),
                                expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)))
        db.add(AilosIntegration(numero_convenio='000000', status='authorized',
                                cooperado_token_encrypted=crypto.encrypt_token(secrets.token_urlsafe(32))))
        db.add(SystemSetting(key='smtp_password_enc', value=crypto.encrypt_token(secrets.token_urlsafe(12))))
        db.add(NfseCertificado(titular='EMPRESA SINTETICA DE ENSAIO', cnpj='00000000000191',
                               arquivo_cifrado=crypto.encrypt_bytes(_pfx()),
                               senha_cifrada=crypto.encrypt_token(SENHA_PFX), ativo=True,
                               nome_arquivo='ensaio.pfx'))
        db.commit()

        print(json.dumps({
            'clientes': N_CLIENTES,
            'cobrancas': db.scalar(select(func.count()).select_from(Billing)),
            'contas_a_pagar': 300,
            'documentos': N_DOCUMENTOS,
            'objetos_enviados': enviados + 1,
            'documentos_excluidos_sem_objeto': excluidos,
            'versoes_substituidas_com_objeto': substituidos,
            'segredos_cifrados': 4,
        }))


if __name__ == '__main__':
    main()
