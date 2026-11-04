"""Roteiro local: entrada válida, PDF obrigatório e limites do sandbox."""
from __future__ import annotations

import json
import sys

import pytest

from app.services import sicredi_boletos, sicredi_client
from scripts import sicredi_homologacao as roteiro
from tests.sicredi_apoio import Resp, resposta_cadastro, sicredi  # noqa: F401

NUMERO = '262000010'
PDF = b'%PDF-1.4\nconteudo de teste'


@pytest.fixture(autouse=True)
def _perfil_independente_do_env_pessoal(monkeypatch):
    monkeypatch.setattr(roteiro, '_configuracao_local', lambda: {})


def _rotas(sicredi):
    sicredi.quando('POST', sicredi_boletos.PATH_BOLETOS, resposta_cadastro(NUMERO))
    sicredi.quando('GET', sicredi_boletos.PATH_BOLETOS, Resp(200, {'situacao': 'LIQUIDADO'}))
    sicredi.quando('GET', sicredi_boletos.PATH_PDF, Resp(200, content=PDF))
    sicredi.quando('GET', sicredi_boletos.PATH_LIQUIDADOS, Resp(200, {'items': []}))
    sicredi.quando('PATCH', sicredi_boletos.PATH_BAIXA.format(nosso_numero=NUMERO), Resp(202))


def _rodar(monkeypatch, tmp_path, *args):
    monkeypatch.setattr(sys, 'argv', ['sicredi_homologacao.py', '--saida', str(tmp_path), *args])
    return roteiro.main()


def test_sandbox_gera_pdf_sem_chamar_endpoint_exclusivo_de_producao(sicredi, monkeypatch, tmp_path):
    _rotas(sicredi)
    log_original = sicredi_client._registrar_log
    assert _rodar(monkeypatch, tmp_path) == 0
    assert sicredi_client._registrar_log is log_original
    assert (tmp_path / 'boleto.pdf').read_bytes() == PDF
    relatorio = json.loads((tmp_path / 'relatorio.json').read_text(encoding='utf-8'))
    assert relatorio['sucesso'] is True
    assert relatorio['pdf_modelo_sandbox'] is True
    assert relatorio['resumo']['cadastrados']['executado'] is False
    assert not sicredi.chamadas_de('GET', f'{sicredi_boletos.PATH_BOLETOS}/cadastrados')
    cadastro = sicredi.chamadas_de('POST', sicredi_boletos.PATH_BOLETOS)[0]['json']
    assert roteiro.is_valid_cpf(cadastro['pagador']['documento'])
    assert len(cadastro['seuNumero']) == 10
    assert len(cadastro['idTituloEmpresa']) == 25
    assert sicredi_client.mask_payload(cadastro)['pagador']['documento'] == '*********09'


def test_cpf_invalido_do_manual_e_rejeitado_antes_da_rede(sicredi, monkeypatch, tmp_path):
    assert _rodar(monkeypatch, tmp_path, '--pagador-documento', '02738306006') == 2
    assert not sicredi.tokens and not sicredi.chamadas


@pytest.mark.parametrize('valor', ['abc', '0', '-1', 'NaN', 'Infinity', '10.001'])
def test_valor_invalido_nao_emite_boleto(sicredi, monkeypatch, tmp_path, valor):
    assert _rodar(monkeypatch, tmp_path, f'--valor={valor}') == 2
    assert not sicredi.tokens and not sicredi.chamadas


def test_resposta_json_no_lugar_de_pdf_nao_e_salva_como_boleto(sicredi, monkeypatch, tmp_path):
    _rotas(sicredi)
    sicredi.quando('GET', sicredi_boletos.PATH_PDF, Resp(200, {'erro': 'sem PDF'}))
    assert _rodar(monkeypatch, tmp_path) == 1
    assert not (tmp_path / 'boleto.pdf').exists()
    relatorio = json.loads((tmp_path / 'relatorio.json').read_text(encoding='utf-8'))
    assert relatorio['sucesso'] is False
    assert relatorio['falhas'][0]['passo'] == 4


def test_cadastro_sem_linha_digitavel_nao_informa_sucesso(sicredi, monkeypatch, tmp_path):
    sicredi.quando('POST', sicredi_boletos.PATH_BOLETOS, Resp(201, {'nossoNumero': NUMERO}))
    assert _rodar(monkeypatch, tmp_path) == 1
    assert not (tmp_path / 'boleto.pdf').exists()
    assert len(sicredi.chamadas) == 1


def test_falha_de_consulta_e_reportada_mas_nao_impede_download_do_pdf(sicredi, monkeypatch, tmp_path):
    _rotas(sicredi)
    sicredi.quando('GET', sicredi_boletos.PATH_BOLETOS, Resp(404, {'message': 'Não encontrado'}))
    assert _rodar(monkeypatch, tmp_path) == 1
    assert (tmp_path / 'boleto.pdf').read_bytes() == PDF
    relatorio = json.loads((tmp_path / 'relatorio.json').read_text(encoding='utf-8'))
    assert relatorio['falhas'][0]['passo'] == 3


def test_producao_sem_pagador_explicito_nao_usa_dados_ficticios(sicredi, monkeypatch, tmp_path):
    assert _rodar(monkeypatch, tmp_path, '--producao', '--confirmo-boleto-real') == 2
    assert not sicredi.tokens and not sicredi.chamadas


def test_producao_sem_confirmacao_nao_emite(sicredi, monkeypatch, tmp_path):
    assert _rodar(monkeypatch, tmp_path, '--producao') == 2
    assert not sicredi.tokens and not sicredi.chamadas


def _perfil_homologacao():
    return {'SICREDI_HOMOLOGACAO_IMPRESSAO_LOCAL': 'true',
            'SICREDI_HOMOLOGACAO_PAGADOR_NOME': 'PAGADOR DE TESTE',
            'SICREDI_HOMOLOGACAO_PAGADOR_DOCUMENTO': '12345678909',
            'SICREDI_BENEFICIARIO_NOME': 'EMPRESA DE TESTE LTDA',
            'SICREDI_BENEFICIARIO_CNPJ': '11444777000161', 'SICREDI_CONTA_CORRENTE': '12345-6',
            'SICREDI_COOPERATIVA': '2602', 'SICREDI_POSTO': '13', 'SICREDI_CODIGO_BENEFICIARIO': '11948'}


def test_impressao_local_usa_convenio_informado_sem_chamadas_a_api(sicredi, monkeypatch, tmp_path):
    monkeypatch.setattr(roteiro, '_configuracao_local', _perfil_homologacao)
    assert _rodar(monkeypatch, tmp_path) == 0
    assert not sicredi.tokens and not sicredi.chamadas
    pdf = (tmp_path / 'boleto.pdf').read_bytes()
    assert pdf.startswith(b'%PDF-')
    assert b'HOMOLOGACAO - SEM VALIDADE FINANCEIRA' in pdf
    assert b'EMPRESA DE TESTE LTDA' in pdf and b'123.456.789-09' in pdf
    assert b'2602.13.11948' in pdf and b'12345-6' in pdf
    relatorio = json.loads((tmp_path / 'relatorio.json').read_text(encoding='utf-8'))
    assert relatorio['registrado_no_sicredi'] is False
    assert relatorio['pdf_modelo_sandbox'] is False
    assert relatorio['beneficiario']['codigo'] == '11948'
    assert relatorio['pagador']['documento'] == '*********09'
    assert not relatorio['chamadas']


@pytest.mark.parametrize('argumento', ['--producao', '--hibrido', '--baixar-no-fim', '--alterar-vencimento'])
def test_impressao_local_rejeita_operacoes_bancarias(sicredi, monkeypatch, tmp_path, argumento):
    monkeypatch.setattr(roteiro, '_configuracao_local', _perfil_homologacao)
    assert _rodar(monkeypatch, tmp_path, argumento) == 2
    assert not sicredi.tokens and not sicredi.chamadas
    assert not (tmp_path / 'boleto.pdf').exists()


def test_perfil_local_nao_impede_roteiro_da_api_quando_desativado(sicredi, monkeypatch, tmp_path):
    _rotas(sicredi)
    monkeypatch.setattr(roteiro, '_configuracao_local', _perfil_homologacao)
    assert _rodar(monkeypatch, tmp_path, '--no-impressao-local') == 0
    assert sicredi.tokens and sicredi.chamadas
    assert (tmp_path / 'boleto.pdf').read_bytes() == PDF


def test_hibrido_demonstrativo_gera_pdf_com_endereco_e_pix_sem_rede(sicredi, monkeypatch, tmp_path):
    perfil = _perfil_homologacao()
    perfil.update(SICREDI_HOMOLOGACAO_PIX_DEMONSTRATIVO='true',
                  SICREDI_HOMOLOGACAO_PAGADOR_ENDERECO='RUA DE HOMOLOGACAO, 100 - BAIRRO TESTE',
                  SICREDI_HOMOLOGACAO_PAGADOR_CIDADE='PORTO ALEGRE',
                  SICREDI_HOMOLOGACAO_PAGADOR_UF='RS', SICREDI_HOMOLOGACAO_PAGADOR_CEP='90220220',
                  SICREDI_HOMOLOGACAO_ENDERECO_FICTICIO='true')
    monkeypatch.setattr(roteiro, '_configuracao_local', lambda: perfil)
    assert _rodar(monkeypatch, tmp_path, '--hibrido') == 0
    assert not sicredi.tokens and not sicredi.chamadas
    pdf = (tmp_path / 'boleto.pdf').read_bytes()
    assert b'PIX DEMONSTRATIVO - SEM COBRANCA VINCULADA' in pdf
    assert b'RUA DE HOMOLOGACAO, 100 - BAIRRO TESTE' in pdf
    assert b'PORTO ALEGRE - RS - CEP 90220-220' in pdf
    relatorio = json.loads((tmp_path / 'relatorio.json').read_text(encoding='utf-8'))
    assert relatorio['pix_copia_cola'].startswith('000201010212')
    assert relatorio['dados_ficticios'] == ['pix', 'endereco_pagador']
    assert relatorio['pix_demonstrativo'] is True
    assert not relatorio['registrado_no_sicredi'] and not relatorio['chamadas']


def test_pix_demonstrativo_nao_e_enviado_para_api(sicredi, monkeypatch, tmp_path):
    assert _rodar(monkeypatch, tmp_path, '--pix-demonstrativo') == 2
    assert not sicredi.tokens and not sicredi.chamadas


def test_perfil_demonstrativo_nao_contamina_hibrido_real_do_sandbox(sicredi, monkeypatch, tmp_path):
    _rotas(sicredi)
    perfil = _perfil_homologacao()
    perfil['SICREDI_HOMOLOGACAO_PIX_DEMONSTRATIVO'] = 'true'
    monkeypatch.setattr(roteiro, '_configuracao_local', lambda: perfil)
    assert _rodar(monkeypatch, tmp_path, '--no-impressao-local') == 0
    cadastro = sicredi.chamadas_de('POST', sicredi_boletos.PATH_BOLETOS)[0]['json']
    assert cadastro['tipoCobranca'] == 'NORMAL'
    assert 'qrCode' not in cadastro
