"""Boletos pela API de Cobrança Sicredi: emissão, consulta, PDF, baixa e conciliação.

Segue o desenho da Ailos (``app/services/ailos_boletos.py``), que já resolveu
os casos difíceis do registro bancário:

* a cobrança é travada e a política única (``titulo_bancario.exigir``) decide
  se pode emitir — inclusive olhando o título da Ailos, então uma cobrança
  nunca tem boleto ativo nos dois bancos;
* a reserva (com o nosso número já alocado) é COMITADA antes do POST: se o
  processo cair no meio, a reserva vira desfecho desconhecido e nada é
  liberado sem consulta;
* timeout/5xx depois do envio = desfecho desconhecido; só rejeição 4xx (ou
  pedido que nem saiu daqui) libera nova tentativa — com o MESMO nosso número;
* conciliação nunca quita pagamento com valor diferente nem cobrança que não
  está em aberto: vira pendência para o financeiro (mesmos códigos da Ailos).

Regras dos manuais do Sicredi aplicadas aqui (pasta ``sicred`` e portal):
nosso número ``AABnnnnnD`` com DV módulo 11 (``sicredi_nosso_numero``); só
caracteres da planilha "Caracteres válidos" nos textos; nome, endereço e
cidade com no máximo 40; ``seuNumero`` até 10; sem ``dataInicioJuros`` /
``dataInicioMulta`` (convertem o híbrido em boleto comum sem aviso).
"""
from __future__ import annotations

import base64
import logging
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timezone import BRASILIA_TZ, hoje
from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client import Client
from app.models.enums import BillingStatus
from app.models.sicredi_boleto import (
    STATUS_DESFECHO_DESCONHECIDO,
    STATUS_ERRO_REGISTRO,
    STATUS_REGISTRADO,
    STATUS_REGISTRANDO,
    SicrediBoleto,
)
from app.services import sicredi_client, sicredi_nosso_numero, titulo_bancario
from app.services.sicredi_codigos import codigo_barras as calcular_codigo_barras, linha_digitavel as calcular_linha
from app.services.ailos_boletos import resolver_pagador
from app.services.financial import lock_billings_for_update, marcar_billing_pago

logger = logging.getLogger(__name__)

PATH_BOLETOS = '/cobranca/boleto/v1/boletos'
PATH_PDF = f'{PATH_BOLETOS}/pdf'
PATH_LIQUIDADOS = f'{PATH_BOLETOS}/liquidados/dia'
PATH_BAIXA = PATH_BOLETOS + '/{nosso_numero}/baixa'

TAMANHO_TEXTO = 40


class SicrediValidationError(Exception):
    """Dado da cobrança ou do pagador recusado antes de qualquer chamada."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__('; '.join(errors))


# ---------------------------------------------------------------------------
# Texto e pagador
# ---------------------------------------------------------------------------

# Planilha "Caracteres válidos/inválidos" do Sicredi: só ASCII 32 a 125, sem
# aspas, apóstrofo, acento circunflexo e crase. O til e todo caractere
# acentuado ficam de fora — o acento é removido (á → a, ç → c) e o resto
# vira espaço.
_VALIDOS = frozenset(chr(c) for c in range(32, 126)) - set('"\'^`')


def texto_sicredi(valor: str | None, tamanho: int = TAMANHO_TEXTO, *, maiusculo: bool = True) -> str:
    if not valor:
        return ''
    decomposto = unicodedata.normalize('NFKD', str(valor))
    sem_acento = ''.join(ch for ch in decomposto if not unicodedata.combining(ch))
    limpo = ''.join(ch if ch in _VALIDOS else ' ' for ch in sem_acento)
    limpo = re.sub(r'\s+', ' ', limpo).strip()
    if maiusculo:
        limpo = limpo.upper()
    return limpo[:tamanho].rstrip()


def _digitos(valor: str | None) -> str:
    return re.sub(r'\D', '', valor or '')


def montar_pagador(cliente: Client) -> dict:
    """Bloco ``pagador`` no formato da API (limites do contrato OpenAPI).

    Endereço é opcional para o Sicredi, mas obrigatório quando a cooperativa
    ativa a validação de CEP: vai sempre que o cadastro tiver o dado válido.
    """
    erros: list[str] = []
    documento = _digitos(cliente.cpf_cnpj)
    if len(documento) not in (11, 14):
        erros.append(f'CPF/CNPJ do pagador inválido ({len(documento)} dígitos).')
    nome = texto_sicredi(cliente.name)
    if not nome:
        erros.append('Nome do pagador vazio.')
    if erros:
        raise SicrediValidationError(erros)

    pagador: dict = {
        'tipoPessoa': 'PESSOA_JURIDICA' if len(documento) == 14 else 'PESSOA_FISICA',
        'documento': documento,
        'nome': nome,
    }
    partes = [cliente.address_line, cliente.address_number, cliente.address_complement]
    endereco = texto_sicredi(' '.join(p for p in partes if p)) or texto_sicredi(cliente.address)
    if endereco:
        pagador['endereco'] = endereco
    cidade = texto_sicredi(cliente.city)
    if cidade:
        pagador['cidade'] = cidade
    uf = (cliente.state or '').strip().upper()
    if re.fullmatch(r'[A-Z]{2}', uf):
        pagador['uf'] = uf
    cep = _digitos(cliente.zip_code)
    if len(cep) == 8:
        pagador['cep'] = cep
    telefone = _digitos(cliente.phone)
    if telefone.startswith('55') and len(telefone) > 11:
        telefone = telefone[2:]
    if 10 <= len(telefone) <= 11:
        pagador['telefone'] = telefone
    email = (cliente.email or '').strip()
    if email and len(email) <= 40 and re.fullmatch(r'[^@\s"\'^`]+@[^@\s"\'^`]+\.[^@\s"\'^`]+', email):
        pagador['email'] = email
    return pagador


def seu_numero(billing: Billing) -> str:
    return str(billing.id)[-10:]


def id_titulo_empresa(billing: Billing) -> str:
    return f'MST-{billing.id}'[:25]


def montar_payload(billing: Billing, pagador: dict, *, codigo_beneficiario: str, nosso_numero: str) -> dict:
    erros: list[str] = []
    try:
        valor = Decimal(str(billing.amount)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError):
        valor = Decimal('0')
    if valor <= 0:
        erros.append('O valor da cobrança precisa ser maior que zero.')
    if billing.due_date is None:
        erros.append('Cobrança sem vencimento.')
    elif billing.due_date < hoje():
        erros.append(
            f'O vencimento {billing.due_date:%d/%m/%Y} já passou: corrija o vencimento antes de '
            'emitir no Sicredi.'
        )
    if erros:
        raise SicrediValidationError(erros)

    hibrido = bool(settings.sicredi_boleto_hibrido)
    payload = {
        'codigoBeneficiario': codigo_beneficiario,
        'tipoCobranca': 'HIBRIDO' if hibrido else 'NORMAL',
        'especieDocumento': settings.sicredi_especie_documento,
        'nossoNumero': nosso_numero,
        'seuNumero': seu_numero(billing),
        'idTituloEmpresa': id_titulo_empresa(billing),
        'dataVencimento': billing.due_date.isoformat(),
        'valor': float(valor),
        'pagador': pagador,
    }
    # Sem juros, multa, desconto, protesto ou negativação: decisão de
    # 30/10/2026, igual aos boletos da Ailos. dataInicioJuros/dataInicioMulta
    # nunca vão — o Sicredi converteria o híbrido em boleto comum sem avisar.
    if hibrido and settings.sicredi_validade_apos_vencimento > 0:
        payload['validadeAposVencimento'] = int(settings.sicredi_validade_apos_vencimento)
    referencia = texto_sicredi(
        ' '.join(p for p in ('REF.', billing.title, billing.period_label) if p), 80,
    )
    if referencia and referencia != 'REF.':
        payload['mensagens'] = [referencia]
    return payload


# ---------------------------------------------------------------------------
# Acesso ao registro local
# ---------------------------------------------------------------------------

def boleto_da_cobranca(db: Session, billing_id: int, *, recarregar: bool = False) -> SicrediBoleto | None:
    query = db.query(SicrediBoleto).filter_by(billing_id=billing_id)
    if recarregar:
        query = query.populate_existing()
    return query.first()


def registrado(db: Session, billing_id: int) -> SicrediBoleto | None:
    """O título ativo no Sicredi, ou None — mesmo critério de ``boleto_registrado`` da Ailos."""
    boleto = boleto_da_cobranca(db, billing_id)
    if boleto and boleto.status == STATUS_REGISTRADO and boleto.linha_digitavel and boleto.codigo_barras:
        return boleto
    return None


def _credenciais_do_boleto(boleto: SicrediBoleto) -> sicredi_client.Credenciais:
    """Consulta/baixa usam o convênio em que o título foi registrado."""
    cred = sicredi_client.credenciais()
    if (cred.ambiente, cred.cooperativa, cred.posto, cred.codigo_beneficiario) != (
        boleto.ambiente, boleto.cooperativa, boleto.posto, boleto.codigo_beneficiario,
    ):
        raise sicredi_client.SicrediError(
            f'O boleto foi registrado no ambiente {boleto.ambiente} (convênio '
            f'{boleto.cooperativa}/{boleto.codigo_beneficiario}), diferente da configuração atual.'
        )
    return cred


def _mesmo_convenio(boleto: SicrediBoleto, cred: sicredi_client.Credenciais) -> bool:
    return (boleto.ambiente, boleto.cooperativa, boleto.posto, boleto.codigo_beneficiario) == (
        cred.ambiente, cred.cooperativa, cred.posto, cred.codigo_beneficiario,
    )


# ---------------------------------------------------------------------------
# Emissão
# ---------------------------------------------------------------------------

def _travar_cobranca_aberta(db: Session, billing_id: int) -> Billing:
    travadas = lock_billings_for_update(db, [billing_id])
    titulo_bancario.recusar_somente_sistema(db, [billing_id])
    if (
        not travadas
        or travadas[0].is_deleted
        or travadas[0].status not in (BillingStatus.PENDING, BillingStatus.OVERDUE)
    ):
        raise SicrediValidationError([f'A cobrança #{billing_id} não está em aberto para emissão no Sicredi.'])
    return travadas[0]


def _desfecho_da_falha(exc: BaseException) -> str:
    if isinstance(exc, sicredi_client.SicrediDesfechoDesconhecido):
        return STATUS_DESFECHO_DESCONHECIDO
    if isinstance(exc, sicredi_client.SicrediApiError):
        if exc.status_code is None or exc.status_code >= 500 or exc.status_code == 408:
            return STATUS_DESFECHO_DESCONHECIDO
        return STATUS_ERRO_REGISTRO
    if isinstance(exc, (sicredi_client.SicrediError, SicrediValidationError,
                        sicredi_nosso_numero.NossoNumeroError)):
        return STATUS_ERRO_REGISTRO
    return STATUS_DESFECHO_DESCONHECIDO


def _falha_no_registro(db: Session, billing_id: int, exc: BaseException) -> BaseException:
    """Grava o desfecho da reserva e devolve a exceção que a API deve ver."""
    status = _desfecho_da_falha(exc)
    try:
        db.rollback()
        lock_billings_for_update(db, [billing_id])
        boleto = boleto_da_cobranca(db, billing_id, recarregar=True)
        if boleto is not None and boleto.status != STATUS_REGISTRADO:
            boleto.status = status
            boleto.erro = (getattr(exc, 'mensagem', None) or str(exc))[:2000]
        db.commit()
    except Exception:  # noqa: BLE001 — o erro original é o que sobe
        db.rollback()
        logger.exception('Não foi possível gravar %s na reserva Sicredi da cobrança %s.', status, billing_id)
    if status == STATUS_DESFECHO_DESCONHECIDO:
        incerto = sicredi_client.SicrediDesfechoDesconhecido(
            'O Sicredi não confirmou o registro e o boleto pode ter sido criado no banco. A cobrança '
            'fica bloqueada até a consulta confirmar o resultado ("Consultar no Sicredi" ou a '
            'conciliação automática).'
        )
        incerto.billing_ids = [billing_id]
        return incerto
    return exc


def _ja_cadastrado(exc: sicredi_client.SicrediApiError) -> bool:
    texto = (exc.mensagem or '').lower()
    return exc.status_code in (400, 409, 422) and 'nosso' in texto and any(
        marca in texto for marca in ('cadastr', 'exist', 'duplic', 'utiliz')
    )


class NossoNumeroEmUso(Exception):
    """O título encontrado pelo nosso número é de outra cobrança."""


# Prefixo em ``erro``: o número reservado pertence a outro título no banco e
# não pode ser reaproveitado na próxima tentativa.
PREFIXO_NOSSO_NUMERO_EM_USO = '[nosso número em uso] '


def _gravar_registro(db: Session, billing_id: int, dados: dict, *, conferir_seu_numero: bool = False) -> SicrediBoleto:
    # A confirmação do POST e a resolução por GET usam a mesma exclusão.
    lock_billings_for_update(db, [billing_id])
    boleto = boleto_da_cobranca(db, billing_id, recarregar=True)
    if conferir_seu_numero:
        # Recuperação pela consulta: só vale se o título é DESTA cobrança. Um
        # sequencial que voltou atrás (restauração de backup) faria o nosso
        # número de outro título ser anexado aqui.
        seu = str(dados.get('seuNumero') or '').strip()
        if seu and boleto.seu_numero and seu != boleto.seu_numero:
            raise NossoNumeroEmUso(
                f'O nosso número {boleto.nosso_numero} já pertence a outro título no Sicredi '
                f'(seu número {seu}).'
            )
    linha = _digitos(dados.get('linhaDigitavel'))
    barras = _digitos(dados.get('codigoBarras'))
    if len(linha) != 47 or len(barras) != 44:
        raise sicredi_client.SicrediError('Resposta do Sicredi sem linha digitável ou código de barras.')
    numero = _digitos(dados.get('nossoNumero'))
    if boleto.ambiente == 'producao':
        # O sandbox devolve vetores fixos. Em produção os códigos devem corresponder
        # exatamente à reserva e aos dados financeiros enviados ao banco.
        esperado = calcular_codigo_barras(boleto.cooperativa, boleto.posto, boleto.codigo_beneficiario,
                                           boleto.nosso_numero, boleto.data_vencimento, boleto.valor_nominal)
        if (numero != boleto.nosso_numero or barras != esperado
                or linha != _digitos(calcular_linha(esperado))
                or str(dados.get('cooperativa') or boleto.cooperativa) != boleto.cooperativa
                or str(dados.get('posto') or boleto.posto) != boleto.posto):
            raise sicredi_client.SicrediError('Dados oficiais do Sicredi divergentes da cobrança. Consultar o título antes de reenviar.')
        if conferir_seu_numero and not str(dados.get('seuNumero') or '').strip():
            raise sicredi_client.SicrediError('Consulta sem identificação da cobrança; não é possível vincular esse título automaticamente.')
    if numero and numero != boleto.nosso_numero:
        logger.warning('Sicredi devolveu nosso número %s para a reserva %s (cobrança %s).',
                       numero, boleto.nosso_numero, billing_id)
        boleto.nosso_numero = numero
    boleto.linha_digitavel = linha
    boleto.codigo_barras = barras
    boleto.txid = dados.get('txid') or dados.get('txId') or boleto.txid
    boleto.qr_code = dados.get('qrCode') or dados.get('codigoQrCode') or boleto.qr_code
    boleto.payload_response = dados
    boleto.status = STATUS_REGISTRADO
    boleto.erro = None
    if boleto.tipo_cobranca == 'HIBRIDO' and not boleto.qr_code:
        # O Sicredi converte híbrido em comum sem avisar (modalidade não
        # habilitada, ou data de início de juros/multa).
        boleto.erro = 'Registrado sem QR Pix: confira se a modalidade híbrida está habilitada na cooperativa.'
    db.commit()
    return boleto


def gerar_boleto(db: Session, billing_id: int, *, nova_tentativa: bool = False) -> SicrediBoleto:
    """Registra no Sicredi o boleto da cobrança.

    Idempotente: se já há título registrado e ativo, devolve o existente sem
    chamar o banco. ``nova_tentativa`` resolve antes um desfecho desconhecido
    pela consulta (nunca reenvia às cegas).
    """
    cred = sicredi_client.credenciais()  # configuração e trava de produção antes de tudo
    billing = _travar_cobranca_aberta(db, billing_id)
    existente = boleto_da_cobranca(db, billing_id)
    if registrado(db, billing_id) is not None:
        if existente.baixa_status is not None:
            raise titulo_bancario.PoliticaBancariaError(
                'boleto_ailos_baixado',
                'O boleto desta cobrança foi baixado (ou está com baixa pendente) no Sicredi e não '
                'pode ser reenviado. Lance uma nova cobrança para cobrar o valor.',
                [billing_id], estado=titulo_bancario.estado_do_sicredi(existente),
            )
        db.commit()
        return existente

    if nova_tentativa and titulo_bancario.estado_do_sicredi(existente) == titulo_bancario.DESFECHO_DESCONHECIDO:
        db.commit()  # solta a trava durante a consulta HTTP
        resultado = resolver_desfecho(db, billing_id)
        if resultado['resultado'] == 'registrado':
            return boleto_da_cobranca(db, billing_id)
        if resultado['resultado'] != 'nao_registrado':
            raise titulo_bancario.PoliticaBancariaError(
                'boleto_ailos_desfecho_desconhecido', resultado['mensagem'], [billing_id],
                estado=titulo_bancario.DESFECHO_DESCONHECIDO,
            )
        billing = _travar_cobranca_aberta(db, billing_id)
        existente = boleto_da_cobranca(db, billing_id)

    # Retomada só depois de a consulta confirmar ausência. Uma reserva recente
    # (neste banco ou na Ailos) pode ter um POST em andamento: nunca a sobrepõe.
    titulo_bancario.exigir(db, titulo_bancario.EMITIR_SICREDI, [billing_id])
    pagador = montar_pagador(resolver_pagador(db, billing))
    if (
        existente is not None
        and _mesmo_convenio(existente, cred)
        and not (existente.erro or '').startswith(PREFIXO_NOSSO_NUMERO_EM_USO)
    ):
        # A tentativa anterior não registrou (ERRO_REGISTRO ou desfecho
        # resolvido como "não existe"): o número reservado não foi usado.
        numero = existente.nosso_numero
    else:
        numero = sicredi_nosso_numero.proximo(
            db, ambiente=cred.ambiente, cooperativa=cred.cooperativa, posto=cred.posto,
            codigo_beneficiario=cred.codigo_beneficiario, ano=hoje().year,
            byte=settings.sicredi_nosso_numero_byte,
        )
    payload = montar_payload(billing, pagador, codigo_beneficiario=cred.codigo_beneficiario, nosso_numero=numero)

    if existente is None:
        existente = SicrediBoleto(billing_id=billing_id)
        db.add(existente)
    existente.ambiente = cred.ambiente
    existente.cooperativa = cred.cooperativa
    existente.posto = cred.posto
    existente.codigo_beneficiario = cred.codigo_beneficiario
    existente.nosso_numero = numero
    existente.seu_numero = payload['seuNumero']
    existente.id_titulo_empresa = payload['idTituloEmpresa']
    existente.tipo_cobranca = payload['tipoCobranca']
    existente.valor_nominal = Decimal(str(payload['valor'])).quantize(Decimal('0.01'))
    existente.data_vencimento = billing.due_date
    existente.payload_request = payload
    existente.payload_response = None
    existente.status = STATUS_REGISTRANDO
    existente.erro = None
    existente.registro_iniciado_em = titulo_bancario.agora()
    db.commit()

    try:
        resp = sicredi_client.request('POST', PATH_BOLETOS, json_body=payload, billing_id=billing_id, cred=cred)
    except sicredi_client.SicrediApiError as exc:
        if _ja_cadastrado(exc):
            # Uma tentativa anterior registrou e não gravou aqui: o mesmo
            # nosso número já existe no banco. Recupera os dados oficiais.
            resultado = resolver_desfecho(db, billing_id, forcar=True)
            if resultado['resultado'] == 'registrado':
                return boleto_da_cobranca(db, billing_id)
            if resultado['resultado'] == 'nao_registrado':
                # Número de outro título: já ficou ERRO_REGISTRO com o número
                # descartado; a próxima tentativa aloca outro.
                raise sicredi_client.SicrediError(resultado['mensagem']) from exc
            raise _falha_no_registro(
                db, billing_id, sicredi_client.SicrediDesfechoDesconhecido(str(exc)),
            ) from exc
        raise _falha_no_registro(db, billing_id, exc) from exc
    except Exception as exc:  # noqa: BLE001 — classificado e relançado
        raise _falha_no_registro(db, billing_id, exc) from exc

    dados = resp.json if isinstance(resp.json, dict) else {}
    try:
        return _gravar_registro(db, billing_id, dados)
    except Exception as exc:  # noqa: BLE001 — o Sicredi aceitou; falhou só o registro local
        raise _falha_no_registro(
            db, billing_id, sicredi_client.SicrediDesfechoDesconhecido(str(exc)),
        ) from exc


# ---------------------------------------------------------------------------
# Consulta e desfecho
# ---------------------------------------------------------------------------

def _nao_encontrado(exc: sicredi_client.SicrediApiError) -> bool:
    # GET individual: somente 404 significa título ausente (manual API 4.0).
    # Erro de validação/convênio e respostas incompletas não liberam emissão.
    return exc.status_code == 404


def consultar_titulo(
    cred: sicredi_client.Credenciais, nosso_numero: str, *, billing_id: int | None = None,
) -> dict | None:
    """Consulta pelo nosso número. None = o Sicredi não tem este título."""
    try:
        resp = sicredi_client.request(
            'GET', PATH_BOLETOS,
            params={'codigoBeneficiario': cred.codigo_beneficiario, 'nossoNumero': nosso_numero},
            billing_id=billing_id, cred=cred,
        )
    except sicredi_client.SicrediApiError as exc:
        if _nao_encontrado(exc):
            return None
        raise
    dados = resp.json
    if not isinstance(dados, dict) or not dados:
        raise sicredi_client.SicrediError('Consulta ao Sicredi devolveu uma resposta inválida; a ausência do título não foi confirmada.')
    return dados


def _prazo_ausencia_cumprido(boleto: SicrediBoleto) -> bool:
    iniciado = titulo_bancario._aware(boleto.registro_iniciado_em) or titulo_bancario._aware(boleto.updated_at)
    if iniciado is None:
        return True
    prazo = timedelta(minutes=settings.sicredi_ausencia_confirmada_minutos)
    return titulo_bancario.agora() - iniciado >= prazo


def _geracao_registro(boleto: SicrediBoleto) -> tuple:
    """Identidade da reserva consultada, independente do objeto da sessão."""
    return (boleto.id, boleto.ambiente, boleto.cooperativa, boleto.posto,
            boleto.codigo_beneficiario, boleto.nosso_numero,
            titulo_bancario._aware(boleto.registro_iniciado_em))


def resolver_desfecho(db: Session, billing_id: int, *, forcar: bool = False) -> dict:
    """Consulta pelo nosso número reservado se um registro incerto existe no banco.

    ``resultado``: ``registrado``, ``nao_registrado`` (não existe depois do
    prazo de processamento — a cobrança volta a aceitar emissão), ``aguardar``,
    ``indisponivel`` ou ``sem_pendencia``.
    """
    boleto = boleto_da_cobranca(db, billing_id)
    estado = titulo_bancario.estado_do_sicredi(boleto)
    if boleto is None or (not forcar and estado not in (
        titulo_bancario.DESFECHO_DESCONHECIDO, titulo_bancario.EM_REGISTRO,
    )):
        return {'resultado': 'sem_pendencia', 'estado': estado,
                'mensagem': 'Não há registro no Sicredi com desfecho pendente para esta cobrança.'}
    geracao = _geracao_registro(boleto)
    status_consultado = boleto.status
    try:
        cred = _credenciais_do_boleto(boleto)
        dados = consultar_titulo(cred, boleto.nosso_numero, billing_id=billing_id)
    except (sicredi_client.SicrediApiError, sicredi_client.SicrediError) as exc:
        _carimbar_falha(db, billing_id, str(exc))
        return {'resultado': 'indisponivel', 'estado': estado,
                'mensagem': f'Consulta ao Sicredi falhou ({exc}). O bloqueio continua.'}

    # A resposta pode chegar depois de outra consulta/POST já confirmar o
    # registro ou depois de uma nova reserva. Só aplica à geração consultada,
    # sob a mesma trava usada por emissão e mutações da cobrança.
    lock_billings_for_update(db, [billing_id])
    boleto = boleto_da_cobranca(db, billing_id, recarregar=True)
    estado_atual = titulo_bancario.estado_do_sicredi(boleto)
    if estado_atual in (titulo_bancario.REGISTRADO, titulo_bancario.BAIXADO):
        db.commit()
        return {'resultado': 'registrado', 'estado': estado_atual,
                'mensagem': 'O registro do boleto já foi confirmado; a resposta anterior não o altera.'}
    if boleto is None or _geracao_registro(boleto) != geracao or boleto.status != status_consultado:
        db.commit()
        return {'resultado': 'sem_pendencia', 'estado': estado_atual,
                'mensagem': 'O registro mudou durante a consulta. Consulte a situação atual antes de tentar novamente.'}
    boleto.ultima_consulta_em = titulo_bancario.agora()
    boleto.ultima_consulta_erro = None
    boleto.falhas_consulta = 0
    if dados is None:
        if _prazo_ausencia_cumprido(boleto):
            anterior = boleto.status
            boleto.status = STATUS_ERRO_REGISTRO
            db.add(BillingChangeLog(
                billing_id=billing_id,
                changed_by_user_id=None,
                field_name='registro_sicredi',
                previous_value=anterior,
                new_value=STATUS_ERRO_REGISTRO,
                justification=f'Consulta pelo nosso número {boleto.nosso_numero} não localizou o título no Sicredi.',
            ))
            db.commit()
            return {'resultado': 'nao_registrado', 'estado': titulo_bancario.SEM_TITULO,
                    'mensagem': 'O Sicredi não localizou o título: a cobrança pode ser emitida de novo.'}
        db.commit()
        return {'resultado': 'aguardar', 'estado': estado_atual,
                'mensagem': 'O Sicredi ainda não localizou o título; ele pode estar em processamento. '
                            'Consulte novamente em alguns minutos.'}

    if _digitos(dados.get('linhaDigitavel')) and _digitos(dados.get('codigoBarras')):
        try:
            boleto = _gravar_registro(db, billing_id, dados, conferir_seu_numero=True)
        except NossoNumeroEmUso as exc:
            # A validação não executou escritas; mantém a trava até registrar
            # a rejeição, sem abrir uma janela para outro confirmador.
            anterior = boleto.status
            boleto.status = STATUS_ERRO_REGISTRO
            boleto.erro = PREFIXO_NOSSO_NUMERO_EM_USO + str(exc)
            db.add(BillingChangeLog(
                billing_id=billing_id, changed_by_user_id=None, field_name='registro_sicredi',
                previous_value=anterior, new_value=STATUS_ERRO_REGISTRO, justification=str(exc),
            ))
            db.commit()
            return {'resultado': 'nao_registrado', 'estado': titulo_bancario.SEM_TITULO,
                    'mensagem': f'{exc} A próxima emissão usa um nosso número novo.'}
        return {'resultado': 'registrado', 'estado': titulo_bancario.REGISTRADO,
                'mensagem': f'Título localizado no Sicredi (nosso número {boleto.nosso_numero}).'}
    db.commit()
    return {'resultado': 'aguardar', 'estado': titulo_bancario.DESFECHO_DESCONHECIDO,
            'mensagem': 'A consulta não trouxe os dados do título. Consulte novamente em alguns minutos.'}


def _decimal(valor) -> Decimal | None:
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError):
        return None


def _data(valor) -> date | None:
    if not valor:
        return None
    texto = str(valor).strip()
    try:
        momento = datetime.fromisoformat(texto.replace('Z', '+00:00'))
    except ValueError:
        try:
            return datetime.strptime(texto[:10], '%d/%m/%Y').date()
        except ValueError:
            return None
    if momento.tzinfo is not None:
        momento = momento.astimezone(BRASILIA_TZ)
    return momento.date()


def extrair_situacao(dados: dict) -> dict:
    situacao = str(dados.get('situacao') or '').strip().upper()
    liquidacao = dados.get('dadosLiquidacao') or {}
    pago = situacao.startswith('LIQUIDADO')
    return {
        'situacao': situacao or None,
        'pago': pago,
        'valor_pago': _decimal(liquidacao.get('valor')) if pago else None,
        'data_pagamento': _data(liquidacao.get('data')) if pago else None,
        'baixado': situacao == 'BAIXADO POR SOLICITACAO',
        'rejeitado': situacao == 'REJEITADO',
    }


def _resposta(info: dict, *, pago: bool, mensagem: str, pendencia: str | None = None,
              quitada: bool = False) -> dict:
    return {
        'consultado': True,
        'situacao': info.get('situacao'),
        'pago': pago,
        'data_pagamento': info.get('data_pagamento') if pago else None,
        'valor_pago': float(info['valor_pago']) if pago and info.get('valor_pago') is not None else None,
        'mensagem': mensagem,
        'pendencia': pendencia,
        'quitada': quitada,
    }


def _aplicar_consulta(db: Session, billing_id: int, info: dict) -> dict:
    """Aplica a consulta sob trava da cobrança (mesmas regras da Ailos, FIN-06)."""
    travadas = lock_billings_for_update(db, [billing_id])
    billing = travadas[0] if travadas else None
    boleto = boleto_da_cobranca(db, billing_id, recarregar=True)
    if billing is None or boleto is None:
        db.rollback()
        return _resposta(info, pago=False, mensagem='Cobrança não encontrada.')

    boleto.situacao = info['situacao'] or boleto.situacao
    boleto.ultima_consulta_em = titulo_bancario.agora()
    boleto.ultima_consulta_erro = None
    boleto.falhas_consulta = 0
    if info['pago']:
        boleto.data_liquidacao = info['data_pagamento']
        boleto.valor_liquidado = info['valor_pago']

    if info['rejeitado']:
        anterior = boleto.status
        boleto.status = STATUS_ERRO_REGISTRO
        boleto.erro = 'O Sicredi informou a situação REJEITADO (título não confirmado pelo banco).'
        db.add(BillingChangeLog(
            billing_id=billing_id, changed_by_user_id=None, field_name='registro_sicredi',
            previous_value=anterior, new_value=STATUS_ERRO_REGISTRO,
            justification='Consulta ao Sicredi devolveu a situação REJEITADO.',
        ))
        db.commit()
        return _resposta(info, pago=False,
                         mensagem='O Sicredi rejeitou o título. Corrija o cadastro e emita de novo.')

    detalhe = {
        'valor_titulo': str(billing.amount),
        'valor_pago': str(info['valor_pago']) if info['valor_pago'] is not None else None,
        'data_pagamento': info['data_pagamento'].isoformat() if info['data_pagamento'] else None,
        'situacao_sicredi': info['situacao'],
        'status_local': billing.status.value,
        'nosso_numero': boleto.nosso_numero,
    }
    aberta = not billing.is_deleted and billing.status in (BillingStatus.PENDING, BillingStatus.OVERDUE)

    if aberta and info['pago']:
        valor = info['valor_pago']
        titulo = Decimal(str(billing.amount)).quantize(Decimal('0.01'))
        if valor is not None and valor != titulo:
            titulo_bancario.registrar_pendencia(boleto, 'pagamento_divergente', detalhe)
            db.commit()
            return _resposta(
                info, pago=False, pendencia='pagamento_divergente',
                mensagem=(f'O Sicredi informa pagamento de R$ {valor} para título de R$ {titulo}. '
                          'A cobrança não foi quitada: registre o recebimento classificando a '
                          'diferença (desconto, saldo, encargos ou crédito).'),
            )
        titulo_bancario.confirmar_baixa(boleto, origem=f'consulta Sicredi: {info["situacao"]}')
        marcar_billing_pago(
            db, billing,
            payment_date=info['data_pagamento'] or hoje(),
            paid_amount=valor if valor is not None else billing.amount,
            payment_method='boleto',
            notes='Baixa automática via Sicredi.',
            lock=False,
        )
        return _resposta(info, pago=True, quitada=True, mensagem='Pagamento confirmado no Sicredi — baixa realizada.')

    if aberta:
        if info['baixado']:
            titulo_bancario.confirmar_baixa(boleto, origem='consulta Sicredi: BAIXADO POR SOLICITACAO')
            titulo_bancario.registrar_pendencia(boleto, 'baixado_com_cobranca_aberta', detalhe)
            db.commit()
            return _resposta(info, pago=False, pendencia='baixado_com_cobranca_aberta',
                             mensagem='O boleto consta como BAIXADO no Sicredi, mas a cobrança está em aberto aqui.')
        db.commit()
        return _resposta(info, pago=False,
                         mensagem=f'Boleto ainda não consta como pago no Sicredi ({info["situacao"] or "sem situação"}).')

    pendencia = None
    if info['pago']:
        if billing.is_deleted:
            pendencia = 'pago_em_cobranca_removida'
        elif billing.status == BillingStatus.CANCELED:
            pendencia = 'pago_em_cobranca_cancelada'
        elif billing.status == BillingStatus.PAID and (billing.payment_method or '') != 'boleto':
            pendencia = 'possivel_pagamento_duplicado'
    if pendencia:
        titulo_bancario.registrar_pendencia(boleto, pendencia, detalhe)
    if info['pago'] or info['baixado']:
        titulo_bancario.confirmar_baixa(boleto, origem=f'consulta Sicredi: {info["situacao"]}')
    db.commit()
    return _resposta(
        info, pago=info['pago'], pendencia=pendencia,
        mensagem=('Pagamento no banco de cobrança que não está em aberto — verifique a pendência.'
                  if pendencia else 'Situação do título atualizada.'),
    )


def _carimbar_falha(db: Session, billing_id: int, erro: str) -> None:
    """Falha de consulta também conta como "consultado" para não travar a fila."""
    try:
        db.rollback()
        boleto = boleto_da_cobranca(db, billing_id, recarregar=True)
        if boleto is not None:
            boleto.ultima_consulta_em = titulo_bancario.agora()
            boleto.ultima_consulta_erro = erro[:2000]
            boleto.falhas_consulta = (boleto.falhas_consulta or 0) + 1
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception('Não foi possível registrar a falha de consulta Sicredi da cobrança %s.', billing_id)


def consultar(db: Session, billing_id: int) -> dict:
    """Consulta o título e aplica o resultado (quitação, baixa, pendência).

    Com registro incerto, resolve o desfecho em vez de consultar situação.
    """
    boleto = boleto_da_cobranca(db, billing_id)
    estado = titulo_bancario.estado_do_sicredi(boleto)
    if estado in (titulo_bancario.DESFECHO_DESCONHECIDO, titulo_bancario.EM_REGISTRO):
        resultado = resolver_desfecho(db, billing_id)
        return {'consultado': True, 'situacao': None, 'pago': False, 'data_pagamento': None,
                'valor_pago': None, 'mensagem': resultado['mensagem'], 'pendencia': None,
                'quitada': False, 'desfecho': resultado['resultado']}
    if boleto is None or boleto.status != STATUS_REGISTRADO:
        raise sicredi_client.SicrediError('Não há boleto registrado no Sicredi para esta cobrança.')
    cred = _credenciais_do_boleto(boleto)
    try:
        dados = consultar_titulo(cred, boleto.nosso_numero, billing_id=billing_id)
    except (sicredi_client.SicrediApiError, sicredi_client.SicrediError) as exc:
        _carimbar_falha(db, billing_id, str(exc))
        raise
    if dados is None:
        _carimbar_falha(db, billing_id, 'Título não localizado na consulta.')
        return {'consultado': True, 'situacao': None, 'pago': False, 'data_pagamento': None,
                'valor_pago': None, 'pendencia': None, 'quitada': False,
                'mensagem': f'O Sicredi não localizou o nosso número {boleto.nosso_numero}.'}
    return _aplicar_consulta(db, billing_id, extrair_situacao(dados))


# ---------------------------------------------------------------------------
# PDF e baixa
# ---------------------------------------------------------------------------

def pdf(db: Session, billing_id: int) -> bytes:
    """PDF oficial do boleto, gerado pelo Sicredi (2ª via a cada chamada)."""
    boleto = registrado(db, billing_id)
    if boleto is None:
        raise sicredi_client.SicrediError('Não há boleto registrado no Sicredi para esta cobrança.')
    cred = _credenciais_do_boleto(boleto)
    # No teste real de 03/11/2026, o cadastro retornou 201 antes de o PDF
    # estar disponível (422 inicial). Repetir somente a leitura, nunca o cadastro.
    for tentativa in range(3):
        try:
            resp = sicredi_client.request(
                'GET', PATH_PDF, params={'linhaDigitavel': boleto.linha_digitavel},
                binario=True, billing_id=billing_id, cred=cred,
            )
            break
        except sicredi_client.SicrediApiError as exc:
            if exc.status_code != 422 or tentativa == 2:
                raise
            time.sleep(2 * (tentativa + 1))
    conteudo = resp.content or b''
    if conteudo.startswith(b'%PDF'):
        return conteudo
    # Algumas versões do gateway devolvem o PDF em base64 (texto ou JSON).
    candidato = conteudo.strip().strip(b'"')
    try:
        decodificado = base64.b64decode(candidato, validate=True)
        if decodificado.startswith(b'%PDF'):
            return decodificado
    except (ValueError, TypeError):
        pass
    raise sicredi_client.SicrediError('O Sicredi não devolveu um PDF válido para este boleto.')


def solicitar_baixa(db: Session, billing_id: int, *, user_id: int | None, motivo: str) -> dict:
    """Instrução de baixa pela API (202 = recebida; o processamento é assíncrono).

    A confirmação vem da consulta (BAIXADO POR SOLICITACAO), pela conciliação
    automática ou por "Consultar no Sicredi".
    """
    motivo = (motivo or '').strip()
    if len(motivo) < 3:
        raise SicrediValidationError(['Informe o motivo da baixa.'])
    boleto = registrado(db, billing_id)
    if boleto is None:
        raise sicredi_client.SicrediError('Não há boleto registrado no Sicredi para esta cobrança.')
    if boleto.baixa_status == 'confirmada':
        raise sicredi_client.SicrediError('A baixa deste boleto já está confirmada no Sicredi.')
    if boleto.baixa_status == 'solicitada':
        return {'baixa_status': 'solicitada', 'transaction_id': boleto.baixa_transaction_id,
                'mensagem': 'A baixa já foi enviada; aguardando a confirmação do Sicredi.'}
    cred = _credenciais_do_boleto(boleto)
    try:
        resp = sicredi_client.request(
            'PATCH', PATH_BAIXA.format(nosso_numero=boleto.nosso_numero), json_body={},
            headers_extra={'codigoBeneficiario': cred.codigo_beneficiario},
            billing_id=billing_id, cred=cred,
        )
    except sicredi_client.SicrediApiError as exc:
        if exc.status_code == 422:
            # Liquidado/baixado/rejeitado no banco: a consulta põe o estado em dia.
            atualizado = consultar(db, billing_id)
            raise sicredi_client.SicrediError(
                f'O Sicredi recusou a baixa ({exc.mensagem or "regra de negócio"}). {atualizado["mensagem"]}'
            ) from exc
        raise
    corpo = resp.json if isinstance(resp.json, dict) else {}
    boleto = boleto_da_cobranca(db, billing_id, recarregar=True)
    anterior = boleto.baixa_status
    boleto.baixa_status = 'solicitada'
    boleto.baixa_enviada_em = titulo_bancario.agora()
    boleto.baixa_transaction_id = str(corpo.get('transactionId') or '')[:60] or None
    texto = f'[baixa enviada ao Sicredi] {motivo}'
    boleto.baixa_observacao = f'{boleto.baixa_observacao} | {texto}' if boleto.baixa_observacao else texto
    db.add(BillingChangeLog(
        billing_id=billing_id, changed_by_user_id=user_id, field_name='baixa_sicredi',
        previous_value=anterior, new_value='solicitada', justification=motivo,
    ))
    db.commit()
    return {'baixa_status': 'solicitada', 'transaction_id': boleto.baixa_transaction_id,
            'mensagem': 'Baixa enviada ao Sicredi. A confirmação chega pela consulta '
                        '(automática ou "Consultar no Sicredi").'}


def resolver_pendencia(db: Session, billing_id: int, *, user_id: int, justificativa: str) -> SicrediBoleto:
    """Encerra uma pendência de conciliação depois que o financeiro tratou."""
    boleto = boleto_da_cobranca(db, billing_id)
    if boleto is None or not boleto.pendencia:
        raise titulo_bancario.PoliticaBancariaError(
            'sem_pendencia', 'Esta cobrança não tem pendência de conciliação no Sicredi.', [billing_id],
        )
    db.add(BillingChangeLog(
        billing_id=billing_id, changed_by_user_id=user_id, field_name='pendencia_conciliacao',
        previous_value=boleto.pendencia, new_value=None, justification=justificativa,
    ))
    boleto.pendencia = None
    boleto.pendencia_detalhe = None
    boleto.pendencia_desde = None
    db.commit()
    return boleto


# ---------------------------------------------------------------------------
# Conciliação
# ---------------------------------------------------------------------------

def _liquidados_do_dia(cred: sicredi_client.Credenciais, dia: date) -> set[str]:
    numeros: set[str] = set()
    pagina = 0
    while True:
        resp = sicredi_client.request(
            'GET', PATH_LIQUIDADOS,
            params={'codigoBeneficiario': cred.codigo_beneficiario, 'dia': dia.strftime('%d/%m/%Y'),
                    'pagina': pagina},
            cred=cred,
        )
        corpo = resp.json if isinstance(resp.json, dict) else {}
        for item in corpo.get('items') or []:
            numero = _digitos(str(item.get('nossoNumero') or ''))
            if numero:
                numeros.add(numero)
        if not corpo.get('hasNext') or pagina >= 100:
            return numeros
        pagina += 1


def conciliar(db: Session, *, orcamento: int | None = None, dias: list[date] | None = None) -> dict:
    """Uma rodada de conciliação (o worker roda de hora em hora).

    1. resolve registros com desfecho desconhecido;
    2. consulta, pela lista de liquidados do dia (hoje e ontem — Pix de fim de
       semana cai no dia útil seguinte), os títulos pagos;
    3. com o orçamento que sobra, consulta em rodízio os títulos ativos (pega
       baixa confirmada, rejeição e pagamento antigo).

    A liquidação só é aplicada depois da consulta individual do título, como o
    manual recomenda.
    """
    cred = sicredi_client.credenciais()
    orcamento = orcamento if orcamento is not None else settings.sicredi_conciliacao_orcamento
    metricas = {'desfechos_resolvidos': 0, 'liquidados_no_banco': 0, 'consultados': 0,
                'quitados': 0, 'pendencias': 0, 'falhas': 0, 'erro_liquidados': None}
    do_convenio = (
        SicrediBoleto.ambiente == cred.ambiente,
        SicrediBoleto.cooperativa == cred.cooperativa,
        SicrediBoleto.posto == cred.posto,
        SicrediBoleto.codigo_beneficiario == cred.codigo_beneficiario,
    )

    incertos = db.scalars(select(SicrediBoleto).where(
        *do_convenio, SicrediBoleto.status.in_((STATUS_REGISTRANDO, STATUS_DESFECHO_DESCONHECIDO)),
    ).order_by(SicrediBoleto.ultima_consulta_em.is_(None).desc(),
               SicrediBoleto.ultima_consulta_em.asc(), SicrediBoleto.id.asc()).limit(50)).all()
    for boleto in incertos:
        if titulo_bancario.estado_do_sicredi(boleto) != titulo_bancario.DESFECHO_DESCONHECIDO:
            continue
        if resolver_desfecho(db, boleto.billing_id)['resultado'] in ('registrado', 'nao_registrado'):
            metricas['desfechos_resolvidos'] += 1

    liquidados: set[str] = set()
    for dia in dias or [hoje() - timedelta(days=1), hoje()]:
        try:
            liquidados |= _liquidados_do_dia(cred, dia)
        except (sicredi_client.SicrediApiError, sicredi_client.SicrediError) as exc:
            metricas['erro_liquidados'] = str(exc)[:500]
    ativos = (
        SicrediBoleto.status == STATUS_REGISTRADO,
        (SicrediBoleto.baixa_status.is_(None)) | (SicrediBoleto.baixa_status.in_(('pendente', 'solicitada'))),
    )
    alvo: list[int] = []
    if liquidados:
        alvo = list(db.scalars(select(SicrediBoleto.billing_id).where(
            *do_convenio, *ativos, SicrediBoleto.nosso_numero.in_(sorted(liquidados)),
        )).all())
        metricas['liquidados_no_banco'] = len(alvo)
    restante = max(orcamento - len(alvo), 0)
    if restante:
        rodizio = db.scalars(
            select(SicrediBoleto.billing_id)
            .where(*do_convenio, *ativos, SicrediBoleto.billing_id.not_in(alvo or [0]))
            .order_by(SicrediBoleto.ultima_consulta_em.is_(None).desc(), SicrediBoleto.ultima_consulta_em.asc())
            .limit(restante)
        ).all()
        alvo.extend(rodizio)

    for billing_id in alvo:
        try:
            resultado = consultar(db, billing_id)
        except Exception as exc:  # noqa: BLE001 — um título com erro não para a rodada
            metricas['falhas'] += 1
            logger.warning('Conciliação Sicredi: consulta da cobrança %s falhou: %s', billing_id, exc)
            continue
        metricas['consultados'] += 1
        if resultado.get('quitada'):
            metricas['quitados'] += 1
        if resultado.get('pendencia'):
            metricas['pendencias'] += 1
    return metricas
