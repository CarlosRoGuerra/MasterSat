"""
Gera um boleto de EXEMPLO em PDF para conferir o design (logo da MasterSat).
Não toca na Ailos nem no banco — é só layout.

Uso (dentro do backend / container):
    python scripts/gerar_boleto_teste.py            # gera boleto_teste.pdf
    python scripts/gerar_boleto_teste.py /tmp/x.pdf # caminho de saída custom

Para baixar da VPS:
    docker compose ... exec backend python scripts/gerar_boleto_teste.py /tmp/boleto_teste.pdf
    docker compose ... cp backend:/tmp/boleto_teste.pdf ./boleto_teste.pdf
"""
import os
import sys
from datetime import date
from decimal import Decimal

# Permite rodar como "python scripts/gerar_boleto_teste.py" (adiciona backend/ ao path)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.boleto_ailos import gerar_dados_boleto
from app.services.boleto_pdf import gerar_boleto_pdf


def main() -> None:
    dados = gerar_dados_boleto(
        billing_id=999,
        valor=Decimal('149.90'),
        vencimento=date(2026, 7, 15),
        sacado_nome='CARLOS ROBERTO GUERRA CLEMENTE HONORIO DA SILVA',
        sacado_cpf_cnpj='15964802702',
        sacado_endereco='RUA NATIVIDADE 552 RECREIO DOS BANDEIRANTES',
        instrucoes=[
            'Não receber após o vencimento.',
            'Após vencimento entrar em contato: contato@mastersat.com.br',
            'Referente ao contrato de rastreamento.',
        ],
    )
    saida = sys.argv[1] if len(sys.argv) > 1 else 'boleto_teste.pdf'
    with open(saida, 'wb') as f:
        f.write(gerar_boleto_pdf(dados))
    print(f'Boleto de teste gerado: {saida}')


if __name__ == '__main__':
    main()
