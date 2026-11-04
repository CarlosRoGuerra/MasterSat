"""Roteiro de homologação da API de Cobrança Sicredi (gera o boleto de homologação).

Percorre a API no ambiente configurado e mostra o resultado de cada passo:

  1. autenticação (OAuth2 password)
  2. cadastro do boleto (NORMAL, ou HIBRIDO com --hibrido)
  3. consulta pelo nosso número
  4. PDF oficial (salvo em arquivo)
  5. consulta de duplicidade por seuNumero (somente produção)
  6. liquidados do dia
  7. alteração de vencimento (+1 dia)   — com --alterar-vencimento
  8. baixa                              — sandbox sempre; produção só com --baixar-no-fim

Nada é gravado no banco do sistema: o log mascarado de cada chamada vai para
``relatorio.json`` na pasta de saída, junto com ``boleto.pdf``.

Impressão pelo beneficiário (manual API 4.0, pp. 218–219):

    python scripts/sicredi_homologacao.py --impressao-local \\
        --pagador-documento <CPF> --pagador-nome "NOME DO PAGADOR"

Esse modo usa os dados do beneficiário configurados no .env local e gera
um boleto de homologação SEM VALIDADE FINANCEIRA, sem chamadas à API.
Use --no-impressao-local para executar o roteiro da API quando o modo
local estiver habilitado no perfil de homologação.

Sandbox (dados fixos de teste do Sicredi; só a x-api-key de SANDBOX é sua):

    cd backend
    SICREDI_API_KEY=<x-api-key do sandbox> python scripts/sicredi_homologacao.py

Produção (boleto REAL; use um pagador seu e valor baixo):

    SICREDI_AMBIENTE=producao SICREDI_PRODUCAO_CONFIRMADA=true \\
    SICREDI_API_KEY=... SICREDI_CODIGO_ACESSO=... \\
    SICREDI_COOPERATIVA=2602 SICREDI_POSTO=13 SICREDI_CODIGO_BENEFICIARIO=11948 \\
    python scripts/sicredi_homologacao.py --producao --confirmo-boleto-real \\
        --valor 10.00 --pagador-documento <CPF/CNPJ> --pagador-nome "NOME DO PAGADOR"

Nas chamadas feitas ao sandbox, o PDF retornado foi um modelo MOCK.
O manual não descreve essa limitação na seção de impressão (pp. 51–54).
O capítulo 9 permite a impressão pelo beneficiário conforme seu layout.

Em produção o nosso número usa o byte 9 (o sistema usa o 2), separado
da sequência dos boletos emitidos pela tela.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from dotenv import dotenv_values

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')

from app.core.config import settings  # noqa: E402
from app.services import sicredi_boletos, sicredi_client, sicredi_nosso_numero  # noqa: E402
from app.services.sgr_migration.normalize import is_valid_cpf, is_valid_cnpj  # noqa: E402

REGISTROS: list[dict] = []
FALHAS: list[dict] = []


def _configuracao_local() -> dict[str, str]:
    arquivo = pathlib.Path(__file__).resolve().parents[1] / '.env'
    valores = {k: v or '' for k, v in dotenv_values(arquivo).items() if k.startswith('SICREDI_')}
    valores.update({k: v for k, v in os.environ.items() if k.startswith('SICREDI_')})
    return valores


def _log_em_memoria(ambiente, method, path, request_payload, response_payload, status_code,
                    success, error_message, billing_id) -> None:
    REGISTROS.append({
        'momento': datetime.now().isoformat(timespec='seconds'),
        'ambiente': ambiente,
        'metodo': method,
        'caminho': path,
        'status_http': status_code,
        'sucesso': success,
        'erro': error_message,
        'pedido': sicredi_client.mask_payload(request_payload) if request_payload is not None else None,
        'resposta': sicredi_client.mask_payload(response_payload) if response_payload is not None else None,
    })


def _passo(numero: int, titulo: str, func):
    print(f'\n[{numero}] {titulo}')
    try:
        resultado = func()
    except (sicredi_client.SicrediApiError,) as exc:
        print(f'    FALHOU: HTTP {exc.status_code} — {exc.mensagem or exc.friendly_message}')
        FALHAS.append({'passo': numero, 'titulo': titulo, 'erro': str(exc)})
        return None
    except (sicredi_client.SicrediError, sicredi_boletos.SicrediValidationError, OSError) as exc:
        print(f'    FALHOU: {exc}')
        FALHAS.append({'passo': numero, 'titulo': titulo, 'erro': str(exc)})
        return None
    print('    OK')
    return resultado


def main() -> int:
    perfil = _configuracao_local()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--impressao-local', action=argparse.BooleanOptionalAction,
                        default=perfil.get('SICREDI_HOMOLOGACAO_IMPRESSAO_LOCAL', '').lower() in ('true', '1'),
                        help='imprimir boleto de homologação pelo beneficiário, sem registro bancário')
    parser.add_argument('--producao', action='store_true', help='usar o ambiente de produção (boleto real)')
    parser.add_argument('--confirmo-boleto-real', action='store_true', help='obrigatório com --producao')
    parser.add_argument('--hibrido', action='store_true', help='cadastrar como HIBRIDO (QR Pix)')
    parser.add_argument('--pix-demonstrativo', action=argparse.BooleanOptionalAction, default=None,
                        help='somente impressão local: QR aleatório, sem cobrança vinculada')
    parser.add_argument('--valor', default='10.00')
    parser.add_argument('--dias-vencimento', type=int, default=10)
    parser.add_argument('--pagador-documento', default=perfil.get('SICREDI_HOMOLOGACAO_PAGADOR_DOCUMENTO') or None,
                        help='CPF/CNPJ do pagador; sandbox usa CPF de teste válido')
    parser.add_argument('--pagador-nome', default=perfil.get('SICREDI_HOMOLOGACAO_PAGADOR_NOME') or None,
                        help='nome do pagador; obrigatório em produção e na impressão local')
    parser.add_argument('--alterar-vencimento', action='store_true')
    parser.add_argument('--baixar-no-fim', action='store_true', help='em produção, dar baixa no boleto criado')
    parser.add_argument('--saida', default=None, help='pasta para relatorio.json e boleto.pdf')
    args = parser.parse_args()
    args.perfil_local = perfil
    if args.pix_demonstrativo is None:
        args.pix_demonstrativo = args.impressao_local and perfil.get(
            'SICREDI_HOMOLOGACAO_PIX_DEMONSTRATIVO', '').lower() in ('true', '1')
    if args.pix_demonstrativo and not args.impressao_local:
        print('Pix demonstrativo é exclusivo da impressão local, sem chamadas à API.')
        return 2

    if args.impressao_local:
        if args.producao or args.alterar_vencimento or args.baixar_no_fim:
            print('Impressão local é exclusiva para homologação, sem operações bancárias.')
            return 2
        if args.hibrido and not args.pix_demonstrativo:
            print('Híbrido local exige --pix-demonstrativo. Pix bancário deve vir da API ou do retorno CNAB.')
            return 2
        if not args.pagador_documento or not args.pagador_nome:
            print('Impressão local exige nome e CPF/CNPJ do pagador nos parâmetros ou no .env local.')
            return 2

    if args.producao:
        if not args.confirmo_boleto_real:
            print('Produção registra um boleto REAL: rode de novo com --confirmo-boleto-real.')
            return 2
        if not args.pagador_documento or not args.pagador_nome:
            print('Produção exige --pagador-documento e --pagador-nome de um pagador real.')
            return 2
    args.pagador_documento = args.pagador_documento or '12345678909'
    args.pagador_nome = args.pagador_nome or 'PAGADOR TESTE HOMOLOGACAO'
    args.pagador_documento = re.sub(r'[^0-9]', '', args.pagador_documento)
    if not (is_valid_cpf(args.pagador_documento) or is_valid_cnpj(args.pagador_documento)):
        print('CPF/CNPJ do pagador inválido: confira os dígitos verificadores.')
        return 2
    args.pagador_nome = sicredi_boletos.texto_sicredi(args.pagador_nome)
    if not args.pagador_nome:
        print('Nome do pagador vazio.')
        return 2
    try:
        valor = Decimal(args.valor)
        if not valor.is_finite() or valor <= 0 or valor != valor.quantize(Decimal('0.01')):
            raise ValueError
        args.valor = float(valor)
    except (InvalidOperation, ValueError, OverflowError):
        print('Valor inválido: informe um número positivo com até duas casas decimais (ex.: 10.00).')
        return 2
    if args.dias_vencimento < 0:
        print('--dias-vencimento não pode ser negativo.')
        return 2

    ambiente_anterior = settings.sicredi_ambiente
    log_anterior = sicredi_client._registrar_log
    settings.sicredi_ambiente = 'producao' if args.producao else 'sandbox'
    REGISTROS.clear()
    FALHAS.clear()
    sicredi_client._registrar_log = _log_em_memoria  # relatório em arquivo, sem banco
    try:
        return _executar(args)
    finally:
        sicredi_client._registrar_log = log_anterior
        settings.sicredi_ambiente = ambiente_anterior


def _executar(args) -> int:

    saida = pathlib.Path(args.saida or f'sicredi_homologacao_{datetime.now():%Y%m%d_%H%M%S}')
    saida.mkdir(parents=True, exist_ok=True)

    if args.impressao_local:
        return _imprimir_homologacao_local(args, saida)

    try:
        cred = sicredi_client.credenciais()
    except sicredi_client.SicrediError as exc:
        print(f'Configuração inválida: {exc}')
        return 2
    print(f'Ambiente: {cred.ambiente} | convênio {cred.cooperativa}/{cred.posto}/{cred.codigo_beneficiario}')
    if cred.ambiente == 'sandbox':
        print('ATENÇÃO: consultas e PDF do sandbox usam dados fixos de exemplo (MOCK), não os dados da MasterSat.')

    if _passo(1, 'Autenticação', lambda: sicredi_client.obter_token(cred, forcar=True)) is None:
        _salvar(saida, cred, {})
        return 1

    byte = 9 if cred.ambiente == 'producao' else 2
    sequencial = random.randint(1, sicredi_nosso_numero.SEQUENCIAL_MAXIMO)
    nosso_numero = sicredi_nosso_numero.montar(
        cred.cooperativa, cred.posto, cred.codigo_beneficiario, date.today().year, byte, sequencial,
    )
    documento = args.pagador_documento
    identificador = uuid4().hex.upper()
    seu_numero = f'H{identificador[:9]}'
    payload = {
        'codigoBeneficiario': cred.codigo_beneficiario,
        'tipoCobranca': 'HIBRIDO' if args.hibrido else 'NORMAL',
        'especieDocumento': settings.sicredi_especie_documento,
        'nossoNumero': nosso_numero,
        'seuNumero': seu_numero,
        'idTituloEmpresa': f'MST-HOMOLOG-{identificador[:13]}',
        'dataVencimento': (date.today() + timedelta(days=args.dias_vencimento)).isoformat(),
        'valor': float(args.valor),
        'pagador': {
            'tipoPessoa': 'PESSOA_JURIDICA' if len(documento) == 14 else 'PESSOA_FISICA',
            'documento': documento,
            'nome': sicredi_boletos.texto_sicredi(args.pagador_nome),
        },
        'mensagens': ['BOLETO DE HOMOLOGACAO - MASTERSAT'],
    }
    resumo: dict = {'nosso_numero_enviado': nosso_numero, 'seu_numero': seu_numero, 'payload': payload}

    def _cadastrar():
        dados = sicredi_client.request('POST', sicredi_boletos.PATH_BOLETOS,
                                       json_body=payload, cred=cred).json
        for campo, tamanho in (('nossoNumero', 9), ('linhaDigitavel', 47), ('codigoBarras', 44)):
            texto = str(dados.get(campo, '')) if isinstance(dados, dict) else ''
            if not re.fullmatch(rf'[0-9]{{{tamanho}}}', texto):
                raise sicredi_client.SicrediError(f'Cadastro retornou {campo} inválido ou ausente.')
        if args.hibrido and (not dados.get('qrCode') or not dados.get('txid')):
            raise sicredi_client.SicrediError('Boleto híbrido retornou sem QR Pix/txid; confira a habilitação da modalidade.')
        return dados

    cadastro = _passo(2, f'Cadastro do boleto {payload["tipoCobranca"]} (nosso número {nosso_numero})', _cadastrar)
    if not isinstance(cadastro, dict):
        _salvar(saida, cred, resumo)
        return 1
    resumo['cadastro'] = cadastro
    numero = str(cadastro['nossoNumero'])
    linha = str(cadastro['linhaDigitavel'])
    print(f'    nosso número: {numero} | linha digitável: {linha}')
    if args.hibrido:
        print(f'    QR Pix: {"presente" if cadastro.get("qrCode") else "AUSENTE (modalidade híbrida habilitada?)"}')

    def _consultar():
        dados = sicredi_boletos.consultar_titulo(cred, numero)
        if dados is None:
            raise sicredi_client.SicrediError('Consulta não retornou o título.')
        return dados

    consulta = _passo(3, 'Consulta pelo nosso número', _consultar)
    if isinstance(consulta, dict):
        resumo['consulta'] = consulta
        print(f'    situação: {consulta.get("situacao")}')

    if linha:
        def _pdf():
            resp = sicredi_client.request('GET', sicredi_boletos.PATH_PDF, params={'linhaDigitavel': linha},
                                          binario=True, cred=cred)
            if not resp.content.startswith(b'%PDF-'):
                raise sicredi_client.SicrediError('Sicredi retornou conteúdo inválido em vez do PDF do boleto.')
            arquivo = saida / 'boleto.pdf'
            arquivo.write_bytes(resp.content)
            return arquivo
        arquivo = _passo(4, 'PDF oficial', _pdf)
        if arquivo:
            print(f'    salvo em {arquivo} ({arquivo.stat().st_size} bytes)')
            resumo['pdf'] = str(arquivo.resolve())

    if cred.ambiente == 'sandbox':
        print('\n[5] Consulta de duplicidade: não disponível no sandbox (manual 4.0, pp. 173–177).')
        resumo['cadastrados'] = {'executado': False, 'motivo': 'Endpoint disponível somente em produção.'}
    else:
        cadastrados = _passo(5, 'Consulta de duplicidade por seuNumero', lambda: sicredi_client.request(
            'GET', f'{sicredi_boletos.PATH_BOLETOS}/cadastrados',
            params={'codigoBeneficiario': cred.codigo_beneficiario, 'seuNumero': seu_numero}, cred=cred).json)
        if cadastrados is not None:
            resumo['cadastrados'] = cadastrados

    liquidados = _passo(6, 'Liquidados do dia', lambda: sicredi_client.request(
        'GET', sicredi_boletos.PATH_LIQUIDADOS,
        params={'codigoBeneficiario': cred.codigo_beneficiario, 'dia': date.today().strftime('%d/%m/%Y'),
                'pagina': 0}, cred=cred).json)
    if liquidados is not None:
        resumo['liquidados'] = liquidados

    comando = {'codigoBeneficiario': cred.codigo_beneficiario}
    if args.alterar_vencimento:
        nova = (date.today() + timedelta(days=args.dias_vencimento + 1)).isoformat()
        resumo['alteracao_vencimento'] = _passo(7, f'Alteração de vencimento para {nova}', lambda: sicredi_client.request(
            'PATCH', f'{sicredi_boletos.PATH_BOLETOS}/{numero}/data-vencimento',
            json_body={'dataVencimento': nova}, headers_extra=comando, cred=cred).json)

    if cred.ambiente == 'sandbox' or args.baixar_no_fim:
        resumo['baixa'] = _passo(8, 'Baixa do boleto', lambda: sicredi_client.request(
            'PATCH', sicredi_boletos.PATH_BAIXA.format(nosso_numero=numero), json_body={},
            headers_extra=comando, cred=cred).json)
    else:
        print('\n[8] Baixa: não executada (produção sem --baixar-no-fim). O boleto continua ativo.')

    _salvar(saida, cred, resumo)
    return 1 if FALHAS else 0


def _imprimir_homologacao_local(args, saida: pathlib.Path) -> int:
    from scripts.sicredi_homologacao_pdf import DadosHomologacao, gerar_pdf

    perfil = args.perfil_local
    obrigatorios = ('SICREDI_BENEFICIARIO_NOME', 'SICREDI_BENEFICIARIO_CNPJ', 'SICREDI_CONTA_CORRENTE',
                    'SICREDI_COOPERATIVA', 'SICREDI_POSTO', 'SICREDI_CODIGO_BENEFICIARIO')
    faltantes = [campo for campo in obrigatorios if not perfil.get(campo)]
    if faltantes:
        print('Configure no .env local: ' + ', '.join(faltantes))
        return 2
    cnpj = re.sub(r'[^0-9]', '', perfil['SICREDI_BENEFICIARIO_CNPJ'])
    if not is_valid_cnpj(cnpj):
        print('CNPJ do beneficiário inválido no .env local.')
        return 2
    try:
        numero = sicredi_nosso_numero.montar(
            perfil['SICREDI_COOPERATIVA'], perfil['SICREDI_POSTO'], perfil['SICREDI_CODIGO_BENEFICIARIO'],
            date.today().year, 9, random.randint(1, sicredi_nosso_numero.SEQUENCIAL_MAXIMO))
        dados = DadosHomologacao(
            beneficiario_nome=perfil['SICREDI_BENEFICIARIO_NOME'], beneficiario_cnpj=cnpj,
            cooperativa=perfil['SICREDI_COOPERATIVA'], posto=perfil['SICREDI_POSTO'],
            codigo_beneficiario=perfil['SICREDI_CODIGO_BENEFICIARIO'], conta=perfil['SICREDI_CONTA_CORRENTE'],
            pagador_nome=args.pagador_nome, pagador_documento=args.pagador_documento,
            nosso_numero=numero, seu_numero=f'H{uuid4().hex[:9].upper()}', emissao=date.today(),
            vencimento=date.today() + timedelta(days=args.dias_vencimento), valor=Decimal(str(args.valor)),
            pagador_endereco=sicredi_boletos.texto_sicredi(perfil.get('SICREDI_HOMOLOGACAO_PAGADOR_ENDERECO', '')),
            pagador_cidade=sicredi_boletos.texto_sicredi(perfil.get('SICREDI_HOMOLOGACAO_PAGADOR_CIDADE', '')),
            pagador_uf=perfil.get('SICREDI_HOMOLOGACAO_PAGADOR_UF', '').upper(),
            pagador_cep=re.sub(r'[^0-9]', '', perfil.get('SICREDI_HOMOLOGACAO_PAGADOR_CEP', '')),
            endereco_ficticio=perfil.get('SICREDI_HOMOLOGACAO_ENDERECO_FICTICIO', '').lower() in ('true', '1'),
            pix_demonstrativo=args.pix_demonstrativo,
        )
        pdf, codigos = gerar_pdf(dados)
        arquivo = saida / 'boleto.pdf'
        arquivo.write_bytes(pdf)
        relatorio = {
            'gerado_em': datetime.now().isoformat(timespec='seconds'), 'ambiente': 'homologacao_local',
            'sucesso': True, 'origem_pdf': 'impressao_pelo_beneficiario', 'registrado_no_sicredi': False,
            'pdf_modelo_sandbox': False, 'chamadas': [],
            'fontes': ['Manual API Cobrança 4.0, pp. 216–219', 'Manual CNAB 400 2.9, pp. 39–49',
                       'Manual QR Code Cobrança'],
            'beneficiario': {'nome': dados.beneficiario_nome, 'cnpj': cnpj, 'cooperativa': dados.cooperativa,
                             'posto': dados.posto, 'codigo': dados.codigo_beneficiario, 'conta': dados.conta},
            'pagador': sicredi_client.mask_payload({'nome': dados.pagador_nome, 'documento': dados.pagador_documento}),
            'nosso_numero': numero, 'valor': str(dados.valor), 'vencimento': dados.vencimento.isoformat(),
            'seu_numero': dados.seu_numero, 'emissao': dados.emissao.isoformat(),
            'dados_ficticios': (['pix'] if args.pix_demonstrativo else [])
                               + (['endereco_pagador'] if dados.endereco_ficticio else []),
            'pdf': str(arquivo.resolve()), **codigos,
        }
        (saida / 'relatorio.json').write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding='utf-8')
    except (ValueError, OSError, sicredi_nosso_numero.NossoNumeroError) as exc:
        print(f'Falha na impressão de homologação: {exc}')
        return 1
    print('Modo: impressão pelo beneficiário, conforme manual API 4.0, pp. 218–219.')
    print(f'Convênio: {dados.cooperativa}/{dados.posto}/{dados.codigo_beneficiario} | conta {dados.conta}')
    print('HOMOLOGAÇÃO — SEM VALIDADE FINANCEIRA. Não houve chamadas à API nem registro bancário.')
    if args.pix_demonstrativo:
        print('Pix demonstrativo: location aleatória NÃO registrada. Teste de layout e leitura, sem pagamento.')
    print(f'PDF: {arquivo.resolve()}')
    print(f'Relatório: {(saida / "relatorio.json").resolve()}')
    return 0


def _salvar(saida: pathlib.Path, cred: sicredi_client.Credenciais, resumo: dict) -> None:
    relatorio = {
        'gerado_em': datetime.now().isoformat(timespec='seconds'),
        'ambiente': cred.ambiente,
        'sucesso': not FALHAS,
        'falhas': FALHAS,
        'pdf_modelo_sandbox': cred.ambiente == 'sandbox',
        'convenio': {'cooperativa': cred.cooperativa, 'posto': cred.posto,
                     'codigo_beneficiario': cred.codigo_beneficiario},
        'resumo': sicredi_client.mask_payload(resumo),
        'chamadas': REGISTROS,
    }
    arquivo = saida / 'relatorio.json'
    arquivo.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(f'\nRelatório: {arquivo}')


if __name__ == '__main__':
    raise SystemExit(main())
