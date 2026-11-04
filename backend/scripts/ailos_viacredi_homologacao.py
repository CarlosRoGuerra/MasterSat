"""Gera remessas e PDFs para conferência da nova parametrização Viacredi.

Não autentica no banco, registra títulos, altera cobranças ou envia e-mail.
O registro bancário só será validado após importar a remessa na homologação.
"""
import argparse
from datetime import date, timedelta
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.timezone import hoje
from app.services.ailos_beneficiario import beneficiario_atual
from app.services.boleto_ailos import gerar_dados_boleto
from app.services.boleto_pdf import gerar_boleto_pdf
from app.services.cnab240 import gerar_arquivo_cnab240
from app.services.cnab400 import gerar_arquivo_cnab400
from app.services.sgr_migration.normalize import is_valid_cpf, is_valid_cnpj


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pagador-nome', required=True)
    parser.add_argument('--pagador-documento', required=True)
    parser.add_argument('--endereco', required=True)
    parser.add_argument('--bairro', required=True)
    parser.add_argument('--cidade', required=True)
    parser.add_argument('--uf', required=True)
    parser.add_argument('--cep', required=True)
    parser.add_argument('--primeiro-numero', type=int, default=380)
    parser.add_argument('--quantidade', type=int, default=3)
    parser.add_argument('--sequencia-remessa', type=int, default=1)
    parser.add_argument('--vencimento', type=date.fromisoformat, default=hoje() + timedelta(days=10))
    parser.add_argument('--saida', type=Path, default=Path('output/ailos-viacredi-homologacao'))
    args = parser.parse_args()
    b = beneficiario_atual()
    if (b.convenio, b.agencia, b.agencia_dv, b.conta_com_dv) != ('101004', '0101', '5', '00454702'):
        parser.error('Ajuste os parâmetros Ailos para o novo convênio Viacredi antes de gerar os arquivos.')
    documento = re.sub('[^0-9]', '', args.pagador_documento)
    if not (is_valid_cpf(documento) or is_valid_cnpj(documento)) or documento == b.cnpj:
        parser.error('Informe CPF/CNPJ válido de um pagador diferente do beneficiário.')
    if not 380 <= args.primeiro_numero <= args.primeiro_numero + args.quantidade - 1 <= 390:
        parser.error('Use a faixa de homologação 380 a 390, com pelo menos um título.')
    cep = re.sub('[^0-9]', '', args.cep)
    if len(cep) != 8 or not re.fullmatch('[A-Za-z]{2}', args.uf):
        parser.error('Informe CEP de oito dígitos e UF de duas letras.')
    itens = [dict(billing_id=n, valor=10, vencimento=args.vencimento, data_emissao=hoje(),
                  sacado_nome=args.pagador_nome, sacado_cpf_cnpj=documento,
                  sacado_endereco=args.endereco, sacado_bairro=args.bairro,
                  sacado_cidade=args.cidade, sacado_estado=args.uf, sacado_cep=cep)
             for n in range(args.primeiro_numero, args.primeiro_numero + args.quantidade)]
    args.saida.mkdir(parents=True, exist_ok=True)
    for layout, gerador in (('240', gerar_arquivo_cnab240), ('400', gerar_arquivo_cnab400)):
        destino = args.saida / f'VIACREDI_{layout}_{args.sequencia_remessa:06d}.REM'
        destino.write_bytes(gerador(itens, seq_arquivo=args.sequencia_remessa))
        print(destino.resolve())
    for item in itens:
        dados = gerar_dados_boleto(
            item['billing_id'], item['valor'], item['vencimento'], args.pagador_nome, documento,
            f'{args.endereco} - {args.bairro}', data_emissao=hoje(),
            sacado_cidade=args.cidade, sacado_cep=cep, sacado_uf=args.uf,
            instrucoes=['HOMOLOGACAO - SEM VALIDADE FINANCEIRA. NAO PAGAR.',
                        'CONFERENCIA DO LAYOUT VIACREDI. REGISTRO BANCARIO NAO REALIZADO.'],
        )
        destino = args.saida / f'boleto-viacredi-{item["billing_id"]}.pdf'
        destino.write_bytes(gerar_boleto_pdf(dados))
        print(destino.resolve())
    print('Arquivos de conferência gerados localmente; nenhum título registrado no banco.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
