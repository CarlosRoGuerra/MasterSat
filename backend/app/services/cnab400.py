"""Remessa Ailos CNAB 400, manual fornecido, pp. 16–18.

Posições de base 1; identidade compartilhada com a API e o PDF.
"""
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.core.timezone import hoje
from app.services.ailos_beneficiario import beneficiario_atual
from app.services.boleto_ailos import gerar_nosso_numero, modulo11_nosso_numero  # noqa: F401
from app.services.cnab_campos import dinheiro, numero, registro, texto

CRLF = '\r\n'
LINHA_LEN = 400


def _validar(linha: str, num: int):
    if len(linha) != LINHA_LEN:
        raise ValueError(f'Linha {num}: esperado {LINHA_LEN} caracteres.')


def header(data_geracao: date | None = None, seq_arquivo: int = 1) -> str:
    b = beneficiario_atual()
    return registro(400, [
        (1, 1, '0'), (2, 2, '1'), (3, 9, 'REMESSA'), (10, 11, '01'),
        (12, 19, 'COBRANCA'), (27, 30, b.agencia), (31, 31, b.agencia_dv),
        (32, 39, numero(b.conta, 8)), (40, 40, b.conta_dv), (41, 46, '000000'),
        (47, 76, texto(b.nome, 30)), (77, 94, texto('085AILOS', 18)),
        (95, 100, (data_geracao or hoje()).strftime('%d%m%y')),
        (101, 107, numero(seq_arquivo, 7)), (130, 136, numero(b.convenio, 7)), (395, 400, '000001'),
    ])


def detalhe(billing_id: int, valor: float | Decimal, vencimento: date | None,
            sacado_nome: str, sacado_cpf_cnpj: str, sacado_endereco: str = '',
            sacado_bairro: str = '', sacado_cep: str = '', sacado_cidade: str = '',
            sacado_estado: str = '', data_emissao: date | None = None,
            instrucao1: str = '00', instrucao2: str = '00', mora_diaria: float = 0.0,
            desconto: float = 0.0, seq_registro: int = 2) -> str:
    b = beneficiario_atual()
    nosso, _dv = gerar_nosso_numero(billing_id, b)
    documento = ''.join(c for c in sacado_cpf_cnpj if c.isascii() and c.isdigit())
    if len(documento) not in (11, 14):
        raise ValueError('CPF/CNPJ do pagador deve conter 11 ou 14 dígitos.')
    if instrucao2 != '00':
        raise ValueError('CNAB 400 Ailos, posições 159–160: informar zeros.')
    cep = ''.join(c for c in sacado_cep if c.isascii() and c.isdigit())
    return registro(400, [
        (1, 1, '7'), (2, 3, '02'), (4, 17, b.cnpj), (18, 21, b.agencia), (22, 22, b.agencia_dv),
        (23, 30, numero(b.conta, 8)), (31, 31, b.conta_dv), (32, 38, numero(b.convenio, 7)),
        (39, 63, texto(str(billing_id), 25)), (64, 80, nosso), (81, 84, '0000'),
        (92, 94, b.variacao), (95, 101, '0000000'), (107, 108, b.carteira), (109, 110, '01'),
        (111, 120, texto(str(billing_id), 10)),
        (121, 126, vencimento.strftime('%d%m%y') if vencimento else '000000'),
        (127, 139, dinheiro(valor, 13, positivo=True)), (140, 142, '085'),
        (143, 146, '0000'), (148, 149, '01'), (150, 150, 'N'),
        (151, 156, (data_emissao or hoje()).strftime('%d%m%y')),
        (157, 158, numero(instrucao1, 2)), (159, 160, '00'),
        (161, 173, dinheiro(mora_diaria, 13)), (174, 179, '000000'),
        (180, 192, dinheiro(desconto, 13)), (193, 205, '0' * 13), (206, 218, '0' * 13),
        (219, 220, '02' if len(documento) == 14 else '01'), (221, 234, numero(documento, 14)),
        (235, 271, texto(sacado_nome, 37)), (275, 314, texto(sacado_endereco, 40)),
        (315, 326, texto(sacado_bairro, 12)), (327, 334, numero(cep or '0', 8)),
        (335, 349, texto(sacado_cidade, 15)), (350, 351, texto(sacado_estado, 2)),
        (395, 400, numero(seq_registro, 6)),
    ])


def trailer(qtd_registros: int, seq_registro: int) -> str:
    # P. 18: não há quantidade em 2–6; são brancos até 394.
    return registro(400, [(1, 1, '9'), (395, 400, numero(seq_registro, 6))])


def gerar_arquivo_cnab400(boletos: Iterable[dict], seq_arquivo: int = 1,
                          data_geracao: date | None = None) -> bytes:
    linhas = [header(data_geracao, seq_arquivo)]
    for item in boletos:
        linhas.append(detalhe(
            billing_id=item['billing_id'], valor=item['valor'], vencimento=item.get('vencimento'),
            sacado_nome=item.get('sacado_nome', ''), sacado_cpf_cnpj=item.get('sacado_cpf_cnpj', ''),
            sacado_endereco=item.get('sacado_endereco', ''), sacado_bairro=item.get('sacado_bairro', ''),
            sacado_cep=item.get('sacado_cep', ''), sacado_cidade=item.get('sacado_cidade', ''),
            sacado_estado=item.get('sacado_estado', ''), data_emissao=item.get('data_emissao'),
            mora_diaria=item.get('mora_diaria', 0), desconto=item.get('desconto', 0), seq_registro=len(linhas) + 1,
        ))
    linhas.append(trailer(len(linhas) - 1, len(linhas) + 1))
    return (CRLF.join(linhas) + CRLF).encode('ascii')
