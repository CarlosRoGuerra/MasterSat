"""
Gera um contrato de EXEMPLO em PDF para conferir o novo layout da ficha de
adesão (PIX, boleto via WhatsApp, renovação anual, credenciais, observação na
última página). Não toca no banco — é só layout, com dados fictícios.

Uso (dentro do backend / container):
    python scripts/gerar_contrato_teste.py              # gera contrato_teste.pdf
    python scripts/gerar_contrato_teste.py /tmp/x.pdf   # caminho de saída custom

Para baixar da VPS:
    docker compose ... exec backend python scripts/gerar_contrato_teste.py /tmp/contrato_teste.pdf
    docker compose ... cp backend:/tmp/contrato_teste.pdf ./contrato_teste.pdf
"""
import os
import sys
from datetime import date
from types import SimpleNamespace

# Permite rodar como "python scripts/gerar_contrato_teste.py" (adiciona backend/ ao path)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.contract_pdf import gerar_contrato_pdf


def main() -> None:
    client = SimpleNamespace(
        name='CARLOS ROBERTO GUERRA CLEMENTE HONORIO DA SILVA',
        cpf_cnpj='159.648.027-02',
        rg_ie='1234567 SSP/SC',
        birth_date=date(1990, 5, 20),
        address_line='RUA NATIVIDADE',
        address_number='552',
        neighborhood='RECREIO DOS BANDEIRANTES',
        zip_code='22790-725',
        city='RIO DE JANEIRO',
        state='RJ',
        phone='(21) 99204-3278',
        email='crrobg@gmail.com',
        emergency_contacts=[
            {'name': 'EUNICE', 'phone': '', 'mobile': '(47) 99965-2845'},
            {'name': '', 'phone': '', 'mobile': ''},
        ],
    )
    contract = SimpleNamespace(
        id=9999,
        start_date=date.today(),
        billing_day=5,
        installation_fee=10,
        uninstall_fee=10,
        payment_method='boleto',
    )
    plan = SimpleNamespace(price=64.99, billing_interval_months=1)
    vehicle = SimpleNamespace(plate='ABC1D23')

    saida = sys.argv[1] if len(sys.argv) > 1 else 'contrato_teste.pdf'
    with open(saida, 'wb') as f:
        f.write(gerar_contrato_pdf(contract, client, plan, vehicle))
    print(f'Contrato de teste gerado: {saida}')


if __name__ == '__main__':
    main()
