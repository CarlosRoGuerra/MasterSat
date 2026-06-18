"""
Script de homologação da integração Ailos — Cobrança Bancária API.

Cria/atualiza 3 registros de teste (Client + Billing, marcados com
notes='[HOMOLOGACAO AILOS]'):
  1. Pessoa física com endereço completo
  2. Pessoa jurídica com endereço completo
  3. Pessoa física com endereço mínimo (sem complemento/email/telefone)

Para cada um:
  - Gera o boleto via API Ailos (app.services.ailos_boletos.gerar_boleto)
  - Monta os dados do boleto (app.services.boleto_ailos.gerar_dados_boleto)
  - Aplica os dados oficiais retornados pela Ailos
    (app.services.ailos_boletos.aplicar_dados_oficiais_ailos)
  - Gera o PDF (app.services.boleto_pdf.gerar_boleto_pdf) e salva em
    --output-dir

Pré-requisitos:
  1. .env preenchido com as credenciais SANDBOX da Ailos (variáveis AILOS_*)
  2. Cooperado já autorizado: POST /api/v1/ailos/connect (papel ADMIN) ->
     abrir a `login_url` retornada no navegador -> a Ailos chama
     POST /api/v1/ailos/callback automaticamente
  3. Confirmar GET /api/v1/ailos/status -> "cooperado_status": "authorized"

Uso:
  cd backend
  python scripts/ailos_homologacao.py [--output-dir DIR]

Seguro para re-executar: faz UPSERT dos registros de teste pelo CPF/CNPJ.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

# Garante que 'app' é encontrado quando executado de dentro de backend/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres:postgres@localhost:5432/rastreamento')

from app.db.session import SessionLocal  # noqa: E402
from app.models.ailos_boleto import AilosBoleto  # noqa: E402
from app.models.billing import Billing  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.enums import BillingStatus  # noqa: E402
from app.services import ailos_boletos  # noqa: E402
from app.services.boleto_ailos import gerar_dados_boleto  # noqa: E402
from app.services.boleto_pdf import gerar_boleto_pdf  # noqa: E402

NOTES_MARKER = '[HOMOLOGACAO AILOS]'

CLIENTES_HOMOLOGACAO: list[dict] = [
    dict(
        name='HOMOLOGACAO AILOS - PESSOA FISICA',
        cpf_cnpj='52998224725',  # CPF de teste com DV válido
        type='pf',
        email='homologacao.pf@mastersat.com.br',
        phone='(22) 99999-0001',
        zip_code='28970-000',
        address_line='Rua de Homologacao',
        address_number='100',
        address_complement='Sala 1',
        neighborhood='Centro',
        city='Araruama',
        state='RJ',
    ),
    dict(
        name='HOMOLOGACAO AILOS - PESSOA JURIDICA LTDA',
        cpf_cnpj='11222333000181',  # CNPJ de teste com DV válido
        type='pj',
        email='homologacao.pj@mastersat.com.br',
        phone='(22) 99999-0002',
        zip_code='28970-000',
        address_line='Avenida de Homologacao',
        address_number='200',
        address_complement='Galpao 2',
        neighborhood='Centro',
        city='Araruama',
        state='RJ',
    ),
    dict(
        name='HOMOLOGACAO AILOS - ENDERECO MINIMO',
        cpf_cnpj='11144477735',  # CPF de teste com DV válido
        type='pf',
        email=None,
        phone=None,
        zip_code='28970-000',
        address_line='Rua Minima',
        address_number='1',
        address_complement=None,
        neighborhood='Centro',
        city='Araruama',
        state='RJ',
    ),
]


def _upsert_cliente(db, dados: dict) -> Client:
    cliente = db.query(Client).filter_by(cpf_cnpj=dados['cpf_cnpj']).first()
    if cliente is None:
        cliente = Client(cpf_cnpj=dados['cpf_cnpj'])
        db.add(cliente)

    for campo, valor in dados.items():
        setattr(cliente, campo, valor)
    cliente.notes = NOTES_MARKER

    db.commit()
    db.refresh(cliente)
    return cliente


def _upsert_billing(db, cliente: Client, vencimento: date) -> Billing:
    billing = db.query(Billing).filter_by(client_id=cliente.id, notes=NOTES_MARKER).first()
    if billing is None:
        billing = Billing(
            client_id=cliente.id,
            title='HOMOLOGACAO AILOS',
            billing_type='avulsa',
            amount=Decimal('99.90'),
            due_date=vencimento,
            status=BillingStatus.PENDING,
            period_label=vencimento.strftime('%m/%Y'),
            notes=NOTES_MARKER,
        )
        db.add(billing)
    else:
        billing.due_date = vencimento
        billing.amount = Decimal('99.90')
        billing.status = BillingStatus.PENDING

    db.commit()
    db.refresh(billing)
    return billing


def _gerar_pdf(db, billing: Billing, cliente: Client) -> bytes:
    endereco = ' '.join(filter(None, [
        cliente.address_line, cliente.address_number, cliente.address_complement,
    ]))
    dados = gerar_dados_boleto(
        billing_id=billing.id,
        valor=billing.amount,
        vencimento=billing.due_date,
        sacado_nome=cliente.name,
        sacado_cpf_cnpj=cliente.cpf_cnpj or '',
        sacado_endereco=endereco,
        data_emissao=date.today(),
        instrucoes=[
            'HOMOLOGACAO AILOS - NAO RECEBER, BOLETO DE TESTE.',
            f'Referente ao contrato de rastreamento. Cobranca #{billing.id}.',
        ],
    )
    ailos_boleto = db.query(AilosBoleto).filter_by(billing_id=billing.id).first()
    dados = ailos_boletos.aplicar_dados_oficiais_ailos(dados, ailos_boleto)
    return gerar_boleto_pdf(dados)


def main() -> None:
    parser = argparse.ArgumentParser(description='Gera boletos de homologação via API Ailos')
    parser.add_argument(
        '--output-dir',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'homologacao_pdfs'),
        help='Diretório onde os PDFs gerados serão salvos (default: backend/homologacao_pdfs/)',
    )
    args = parser.parse_args()

    print('=== Homologação Ailos — Cobrança Bancária API ===\n')
    print('Pré-requisitos:')
    print('  1. .env preenchido com credenciais SANDBOX da Ailos (variáveis AILOS_*)')
    print('  2. Cooperado autorizado: POST /api/v1/ailos/connect (ADMIN) -> abrir')
    print('     login_url -> a Ailos chama POST /api/v1/ailos/callback')
    print('  3. Confirmar GET /api/v1/ailos/status -> "cooperado_status": "authorized"')
    print()

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    vencimento = date.today() + timedelta(days=15)

    db = SessionLocal()
    sucesso = 0
    erros: list[tuple[str, str]] = []
    billing_ids: list[int] = []
    client_ids: list[int] = []

    try:
        for idx, dados_cliente in enumerate(CLIENTES_HOMOLOGACAO, start=1):
            print(f'--- Registro {idx}: {dados_cliente["name"]} ---')
            cliente = _upsert_cliente(db, dados_cliente)
            billing = _upsert_billing(db, cliente, vencimento)
            client_ids.append(cliente.id)
            billing_ids.append(billing.id)

            try:
                ailos_boletos.gerar_boleto(db, billing, cliente)
                pdf_bytes = _gerar_pdf(db, billing, cliente)
            except Exception as exc:
                print(f'  [ERRO] {exc}')
                erros.append((dados_cliente['name'], str(exc)))
                continue

            nome_arquivo = f'boleto_homologacao_{idx}.pdf'
            caminho = os.path.join(output_dir, nome_arquivo)
            with open(caminho, 'wb') as f:
                f.write(pdf_bytes)

            print(f'  [OK] PDF salvo em {caminho}')
            sucesso += 1
    finally:
        db.close()

    print(f'\n=== {sucesso}/{len(CLIENTES_HOMOLOGACAO)} boletos gerados com sucesso ===')
    if erros:
        print('\nFalhas:')
        for nome, msg in erros:
            print(f'  - {nome}: {msg}')

    print('\nPróximos passos:')
    print(f'  1. Envie os PDFs gerados em "{output_dir}"')
    print('     para homologacaocobranca@ailos.coop.br')
    print('  2. Suporte Ailos: (047) 3231-4196')
    print('  3. Após aprovação, troque AILOS_ENV=production e use as credenciais reais.')

    print('\nSQL para limpar os registros de teste criados por este script:')
    print(f"  DELETE FROM ailos_boletos WHERE billing_id IN ({', '.join(str(b) for b in billing_ids)});")
    print(f"  DELETE FROM billings WHERE id IN ({', '.join(str(b) for b in billing_ids)});")
    print(f"  DELETE FROM clients WHERE id IN ({', '.join(str(c) for c in client_ids)});")


if __name__ == '__main__':
    main()
