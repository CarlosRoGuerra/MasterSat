"""Remessa Ailos CNAB 240, manual versão 14 (maio/2025), pp. 15–23.

Layout do arquivo 084 e lote 043 conforme o manual. O exemplo .rem antigo
usa 045; prevalece a especificação mais recente fornecida.
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

from app.core.timezone import hoje
from app.services.ailos_beneficiario import beneficiario_atual
from app.services.boleto_ailos import gerar_nosso_numero
from app.services.cnab_campos import dinheiro, numero, registro, texto

CRLF = '\r\n'
LINHA_LEN = 240


def _validar(linha: str, seq: int):
    if len(linha) != 240:
        raise ValueError(f'Linha {seq}: esperado 240 caracteres.')


def _data(d: date | None) -> str:
    return d.strftime('%d%m%Y') if d else '00000000'


def _controle(lote: int, seq: int, segmento: str) -> list:
    return [(1, 3, '085'), (4, 7, numero(lote, 4)), (8, 8, '3'),
            (9, 13, numero(seq, 5)), (14, 14, segmento), (16, 17, '01')]


def header_arquivo(data_geracao: date | None = None, seq_arquivo: int = 1) -> str:
    b = beneficiario_atual()
    return registro(240, [
        (1, 3, '085'), (4, 7, '0000'), (8, 8, '0'), (18, 18, '2'), (19, 32, b.cnpj),
        (33, 52, texto(b.convenio, 20)), (53, 57, numero(b.agencia, 5)), (58, 58, b.agencia_dv),
        (59, 70, numero(b.conta, 12)), (71, 71, b.conta_dv), (73, 102, texto(b.nome, 30)),
        (103, 132, texto(b.cooperativa_nome, 30)), (143, 143, '1'),
        (144, 151, _data(data_geracao or hoje())), (152, 157, '000000'),
        (158, 163, numero(seq_arquivo, 6)), (164, 166, '084'), (167, 171, '00000'),
    ])


def header_lote(lote: int = 1, data_geracao: date | None = None, seq_arquivo: int = 1) -> str:
    b = beneficiario_atual()
    return registro(240, [
        (1, 3, '085'), (4, 7, numero(lote, 4)), (8, 8, '1'), (9, 9, 'R'), (10, 11, '01'),
        (14, 16, '043'), (18, 18, '2'), (19, 33, numero(b.cnpj, 15)),
        (34, 53, texto(b.convenio, 20)), (54, 58, numero(b.agencia, 5)), (59, 59, b.agencia_dv),
        (60, 71, numero(b.conta, 12)), (72, 72, b.conta_dv), (74, 103, texto(b.nome, 30)),
        (184, 191, numero(seq_arquivo, 8)), (192, 199, _data(data_geracao or hoje())), (200, 207, '00000000'),
    ])


def segmento_p(lote: int, seq: int, billing_id: int, valor: float | Decimal,
               vencimento: date | None, data_emissao: date | None = None,
               mora_valor: float = 0.0, desconto_valor: float = 0.0) -> str:
    b = beneficiario_atual()
    nosso, _dv = gerar_nosso_numero(billing_id, b)
    juros = int(dinheiro(mora_valor, 15)) > 0
    desconto = int(dinheiro(desconto_valor, 15)) > 0
    return registro(240, _controle(lote, seq, 'P') + [
        (18, 22, numero(b.agencia, 5)), (23, 23, b.agencia_dv),
        (24, 35, numero(b.conta, 12)), (36, 36, b.conta_dv), (38, 57, texto(nosso, 20)),
        (58, 58, numero(int(b.carteira), 1)), (59, 59, '1'), (60, 60, '1'), (61, 61, '2'), (62, 62, '2'),
        (63, 77, texto(str(billing_id), 15)), (78, 85, _data(vencimento)),
        (86, 100, dinheiro(valor, 15, positivo=True)), (101, 105, '00000'),
        (107, 108, '02'), (109, 109, 'N'), (110, 117, _data(data_emissao or hoje())),
        (118, 118, '1' if juros else '3'),
        (119, 126, _data(vencimento + timedelta(days=1) if juros and vencimento else None)),
        (127, 141, dinheiro(mora_valor, 15)), (142, 142, '1' if desconto else '2'),
        (143, 150, _data(vencimento if desconto else None)), (151, 165, dinheiro(desconto_valor, 15)),
        (166, 180, '0' * 15), (181, 195, '0' * 15), (196, 220, texto(str(billing_id), 25)),
        (221, 221, '3'), (222, 223, '00'), (224, 224, '2'), (225, 227, '000'),
        (228, 229, '09'), (230, 239, '0000000000'),
    ])


def segmento_q(lote: int, seq: int, sacado_nome: str, sacado_cpf_cnpj: str,
               sacado_endereco: str = '', sacado_bairro: str = '', sacado_cep: str = '',
               sacado_cidade: str = '', sacado_estado: str = '') -> str:
    documento = ''.join(c for c in sacado_cpf_cnpj if c.isascii() and c.isdigit())
    if len(documento) not in (11, 14):
        raise ValueError('CPF/CNPJ do pagador deve conter 11 ou 14 dígitos.')
    if documento == beneficiario_atual().cnpj:
        raise ValueError('CNAB 240: o pagador não pode ser o próprio beneficiário (Dicas de homologação, p. 2).')
    cep = ''.join(c for c in sacado_cep if c.isascii() and c.isdigit())
    return registro(240, _controle(lote, seq, 'Q') + [
        (18, 18, '2' if len(documento) == 14 else '1'), (19, 33, numero(documento, 15)),
        (34, 73, texto(sacado_nome, 40)), (74, 113, texto(sacado_endereco, 40)),
        (114, 128, texto(sacado_bairro, 15)), (129, 136, numero(cep or '0', 8)),
        (137, 151, texto(sacado_cidade, 15)), (152, 153, texto(sacado_estado, 2)),
        (154, 154, '0'), (155, 169, '0' * 15), (210, 212, '000'),
    ])


def segmento_r(lote: int, seq: int, multa_pct: float = 0.0, data_multa: date | None = None) -> str:
    multa = int(dinheiro(multa_pct, 15)) > 0
    return registro(240, _controle(lote, seq, 'R') + [
        (18, 18, '0'), (19, 26, '00000000'), (27, 41, '0' * 15),
        (42, 42, '0'), (43, 50, '00000000'), (51, 65, '0' * 15),
        (66, 66, '2' if multa else '0'), (67, 74, _data(data_multa)), (75, 89, dinheiro(multa_pct, 15)),
        (200, 207, '00000000'), (208, 210, '000'), (211, 215, '00000'), (217, 228, '0' * 12), (231, 231, '0'),
    ])


def trailer_lote(lote: int, qtd_registros: int, qtd_titulos: int, valor_total: float | Decimal) -> str:
    return registro(240, [
        (1, 3, '085'), (4, 7, numero(lote, 4)), (8, 8, '5'), (18, 23, numero(qtd_registros, 6)),
        (24, 29, numero(qtd_titulos, 6)), (30, 46, dinheiro(valor_total, 17)),
        (47, 52, '000000'), (53, 69, '0' * 17), (70, 75, '000000'), (76, 92, '0' * 17),
        (93, 98, '000000'), (99, 115, '0' * 17),
    ])


def trailer_arquivo(qtd_lotes: int, qtd_registros: int) -> str:
    return registro(240, [(1, 3, '085'), (4, 7, '9999'), (8, 8, '9'),
                          (18, 23, numero(qtd_lotes, 6)), (24, 29, numero(qtd_registros, 6)), (30, 35, '000000')])


def gerar_arquivo_cnab240(boletos: Iterable[dict], seq_arquivo: int = 1,
                          data_geracao: date | None = None) -> bytes:
    lista = list(boletos)
    linhas = [header_arquivo(data_geracao, seq_arquivo), header_lote(1, data_geracao, seq_arquivo)]
    total = Decimal('0')
    seq = 1
    for item in lista:
        linhas.append(segmento_p(1, seq, item['billing_id'], item['valor'], item.get('vencimento'),
                                 item.get('data_emissao'), item.get('mora_valor', 0), item.get('desconto_valor', 0)))
        total += Decimal(str(item['valor']))
        seq += 1
        linhas.append(segmento_q(1, seq, item.get('sacado_nome', ''), item.get('sacado_cpf_cnpj', ''),
                                 item.get('sacado_endereco', ''), item.get('sacado_bairro', ''),
                                 item.get('sacado_cep', ''), item.get('sacado_cidade', ''), item.get('sacado_estado', '')))
        seq += 1
        if item.get('multa_pct', 0):
            venc = item.get('vencimento')
            linhas.append(segmento_r(1, seq, item['multa_pct'], venc + timedelta(days=1) if venc else None))
            seq += 1
    linhas.append(trailer_lote(1, seq + 1, len(lista), total))
    linhas.append(trailer_arquivo(1, len(linhas) + 1))
    return (CRLF.join(linhas) + CRLF).encode('ascii')
