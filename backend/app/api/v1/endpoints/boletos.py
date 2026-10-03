"""
Endpoints de geração de boletos e arquivos CNAB (400 e 240).

GET  /boletos/{billing_id}          → Dados do boleto (JSON)
GET  /boletos/{billing_id}/pdf      → PDF do boleto
POST /boletos/cnab400               → Arquivo remessa CNAB400
POST /boletos/cnab240               → Arquivo remessa CNAB240
"""
from __future__ import annotations

import hashlib
import hmac
import re
import smtplib
import unicodedata
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.endpoints.common import (
    get_billing_or_404 as _get_billing_or_404,
    get_client_or_404 as _get_client_or_404,
)
from app.api.v1.endpoints.settings import carregar_mensagens, render_template
from app.core.config import settings
from app.db.session import get_db
from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.billing_charge_item import BillingChargeItem
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.cnab_remessa import CnabRemessa
from app.models.enums import BillingStatus, UserRole
from app.models.vehicle import Vehicle
from app.models.uninstall_event import UninstallEvent
from app.services import ailos_boletos, cnab_remessa
from app.services.ailos_boletos import aplicar_dados_oficiais_ailos
from app.services.boleto_ailos import gerar_dados_boleto, DadosBoleto
from app.services.boleto_pdf import gerar_boleto_pdf, gerar_carne_pdf
from app.services.billing_closure.uninstall_fees import uninstall_fee_for_event

router = APIRouter()


class RemessaDescarteIn(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)

# Rotas públicas (sem JWT) — boleto por link tokenizado, para envio ao cliente
# por WhatsApp/e-mail. Montado em /public no api.py.
public_router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)


def _public_token(billing_id: int) -> str:
    """Token HMAC do link público do boleto (não adivinhável, sem estado)."""
    return hmac.new(
        settings.secret_key.encode(), f'boleto-pdf:{billing_id}'.encode(), hashlib.sha256
    ).hexdigest()[:20]


def public_boleto_url(billing_id: int) -> str:
    base = (settings.backend_public_url or '').rstrip('/')
    return f'{base}{settings.api_v1_prefix}/public/boleto/{billing_id}/{_public_token(billing_id)}'


def _pagador_do_billing(b: Billing, db: Session) -> Client:
    """Cliente que aparece como pagador/sacado no boleto — o interveniente
    financeiro do contrato, quando houver; senão, o cliente da cobrança.
    Mantém o PDF/link coerente com o pagador registrado na Ailos."""
    return ailos_boletos.resolver_pagador(db, b, _get_client_or_404(b.client_id, db))


_TIPO_SERVICO = {
    'recorrente': 'MENSALIDADE DE MONITORAMENTO VEICULAR',
    'prorata': 'MENSALIDADE PROPORCIONAL (PRÓ-RATA)',
    'instalacao': 'INSTALAÇÃO DE EQUIPAMENTO DE RASTREAMENTO',
    'desinstalacao': 'DESINSTALAÇÃO DE EQUIPAMENTO DE RASTREAMENTO',
    'manutencao': 'MANUTENÇÃO DE EQUIPAMENTO DE RASTREAMENTO',
    'adesao': 'TAXA DE ADESÃO',
    'avulsa': 'SERVIÇO AVULSO',
    'boleto_unico': 'BOLETO ÚNICO',
}


def descricao_servico(
    b: Billing, placa: str | None = None, period_label: str | None = None,
) -> str:
    """
    Descreve o que está sendo cobrado, para o boleto.

    Pedido do cliente (reunião de 07/08/2026): o pagador precisa entender o
    motivo da cobrança olhando o boleto. Antes saía só ``billing.title``, que
    em cobrança gerada pelo fechamento é apenas "Mensalidade".

    Monta: serviço · competência · parcela · placa — sem repetir o que o
    título já diz.
    """
    titulo = (b.title or '').strip()
    # O título de cobrança parcelada já vem com o sufixo "• parcela N/M"
    # (ver financial.py e billings.py) — removido aqui porque a parcela é
    # readicionada abaixo a partir de installment_number/installment_total,
    # o que duplicava "PARCELA N/M" na descrição.
    titulo = re.sub(r'\s*[•·-]\s*parcela\s+\d+\s*/\s*\d+\s*$', '', titulo, flags=re.IGNORECASE).strip()
    base = _TIPO_SERVICO.get(b.billing_type or '', '') or titulo or 'SERVIÇO DE RASTREAMENTO'
    partes = [base.upper()]

    # O título só entra quando acrescenta algo (ex.: "Instalação 2º veículo").
    if titulo and titulo.upper() not in base.upper():
        partes.append(titulo.upper())
    if period_label or b.period_label:
        partes.append(f'REF. {period_label or b.period_label}')
    if b.installment_total and b.installment_total > 1:
        partes.append(f'PARCELA {b.installment_number or 1}/{b.installment_total}')
    if placa:
        # Espaço não separável — no boleto/carnê, "PLACA" nunca deve quebrar
        # de linha isolado da placa em si.
        partes.append(f'PLACA: {placa}')
    return ' · '.join(partes)


def _billing_to_boleto_item(b: Billing, c: Client) -> dict:
    """Converte Billing + Client para o dict usado pelos geradores CNAB."""
    endereco = " ".join(filter(None, [
        c.address_line,
        c.address_number,
        c.address_complement,
    ]))
    return {
        "billing_id": b.id,
        "valor": float(b.amount),
        "vencimento": b.due_date,
        "data_emissao": b.created_at.date() if hasattr(b, "created_at") and b.created_at else date.today(),
        "sacado_nome": c.name,
        "sacado_cpf_cnpj": c.cpf_cnpj or "",
        "sacado_endereco": endereco,
        "sacado_bairro": c.neighborhood or "",
        "sacado_cep": c.zip_code or "",
        "sacado_cidade": c.city or "",
        "sacado_estado": c.state or "",
    }


# ---------------------------------------------------------------------------
# GET /boletos/carne  — carnês gerados de um cliente
# Declarado ANTES de /{billing_id}, senão "carne" seria lido como billing_id.
# ---------------------------------------------------------------------------

@router.get("/carne")
def listar_carnes(
    client_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Carnês gerados de um cliente (para reabrir/baixar depois da geração).

    Encontra os lotes tipo 'carne' cujas parcelas pertencem ao cliente, via os
    boletos vinculados ao lote.
    """
    lote_ids = (
        select(AilosBoleto.lote_id)
        .join(Billing, Billing.id == AilosBoleto.billing_id)
        .where(Billing.client_id == client_id, AilosBoleto.lote_id.isnot(None))
        .distinct()
    )
    lotes = (
        db.query(AilosLote)
        .filter(AilosLote.tipo == 'carne', AilosLote.id.in_(lote_ids))
        .order_by(AilosLote.id.desc())
        .all()
    )

    resultado = []
    for lote in lotes:
        ids = lote.billing_ids or []
        cobrancas = db.query(Billing).filter(Billing.id.in_(ids)).all() if ids else []
        registradas = sum(1 for bid in ids if boleto_registrado(bid, db) is not None)
        pagas = sum(1 for b in cobrancas if b.status == BillingStatus.PAID)
        resultado.append({
            'lote_id': lote.id,
            'ticket': lote.ticket,
            'criado_em': lote.created_at.isoformat() if lote.created_at else None,
            'parcelas': len(ids),
            'parcelas_registradas': registradas,
            'parcelas_pagas': pagas,
            'total': float(sum((b.amount or 0) for b in cobrancas)),
            'valor_pago': float(sum((b.amount or 0) for b in cobrancas if b.status == BillingStatus.PAID)),
            'status': lote.status,
            # Detalhe por parcela — a tela usa isto para mostrar o que já foi
            # pago e o que ainda falta, sem precisar de outra chamada.
            'parcelas_detalhe': [
                {
                    'billing_id': b.id,
                    'numero_parcela': b.installment_number,
                    'vencimento': b.due_date.isoformat() if b.due_date else None,
                    'valor': float(b.amount or 0),
                    'status': b.status.value,
                    'data_pagamento': b.payment_date.isoformat() if b.payment_date else None,
                }
                for b in sorted(cobrancas, key=lambda x: (x.installment_number or 0, x.due_date or date.min))
            ],
        })
    return resultado


# ---------------------------------------------------------------------------
# GET /boletos/remessas e /boletos/canais (Fase 03, FIN-07)
# Também ANTES de /{billing_id}, pelo mesmo motivo de /carne.
# ---------------------------------------------------------------------------

@router.get("/remessas")
def listar_remessas(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    remessas = db.scalars(select(CnabRemessa).order_by(CnabRemessa.id.desc()).limit(200)).all()
    return [
        {
            'id': r.id, 'layout': r.layout, 'sequencial': r.sequencial, 'status': r.status,
            'arquivo_sha256': r.arquivo_sha256, 'total_titulos': r.total_titulos,
            'valor_total': float(r.valor_total), 'criada_em': r.created_at,
            'descartada_em': r.descartada_em, 'descarte_motivo': r.descarte_motivo,
        }
        for r in remessas
    ]


@router.get("/canais")
def canais_bancarios(_: object = Depends(require_roles(*ALLOWED_ROLES))):
    """Matriz de canais de emissão: o que está disponível e por quê."""
    cnab = cnab_remessa.canal_habilitado()
    motivo_cnab = None if cnab else (
        'Layout não homologado com o banco e sem leitura de retorno CNAB.'
    )
    return {
        'ailos_api': {'habilitado': True, 'emissao': True, 'conciliacao': True, 'motivo': None},
        'cnab240': {'habilitado': cnab, 'emissao': cnab, 'conciliacao': False, 'motivo': motivo_cnab},
        'cnab400': {'habilitado': cnab, 'emissao': cnab, 'conciliacao': False, 'motivo': motivo_cnab},
    }


# ---------------------------------------------------------------------------
# GET /boletos/{billing_id}  — dados do boleto (JSON)
# ---------------------------------------------------------------------------

@router.get("/{billing_id}")
def get_boleto(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Retorna os dados calculados do boleto (código de barras, linha digitável, etc.)
    sem gerar o PDF. Útil para exibir no frontend ou copiar a linha digitável.
    """
    b = _get_billing_or_404(billing_id, db)
    c = _pagador_do_billing(b, db)
    item = _billing_to_boleto_item(b, c)

    dados = gerar_dados_boleto(
        billing_id=b.id,
        valor=b.amount,
        vencimento=b.due_date,
        sacado_nome=c.name,
        sacado_cpf_cnpj=c.cpf_cnpj or "",
        sacado_endereco=item["sacado_endereco"],
        data_emissao=item["data_emissao"],
    )
    ailos_boleto = db.query(AilosBoleto).filter_by(billing_id=b.id).first()
    dados = aplicar_dados_oficiais_ailos(dados, ailos_boleto)

    return {
        "billing_id": dados.billing_id,
        "nosso_numero": dados.nosso_numero_display,
        "codigo_barras": dados.codigo_barras,
        "linha_digitavel": dados.linha_digitavel,
        "cedente": {
            "nome": dados.cedente_nome,
            "cnpj": dados.cedente_cnpj,
            "agencia": dados.cedente_agencia,
            "codigo": dados.cedente_codigo,
            "convenio": dados.cedente_convenio,
            "carteira": dados.carteira,
        },
        "sacado": {
            "nome": dados.sacado_nome,
            "cpf_cnpj": dados.sacado_cpf_cnpj,
            "endereco": dados.sacado_endereco,
        },
        "vencimento": dados.data_vencimento.isoformat() if dados.data_vencimento else None,
        "emissao": dados.data_emissao.isoformat(),
        "valor": float(dados.valor),
        "banco": {"codigo": dados.banco_codigo, "nome": dados.banco_nome},
        # Sem registro na Ailos o título não é pagável no banco — o frontend usa
        # esta flag para bloquear o envio ao cliente
        "boleto_registrado": bool(ailos_boleto and ailos_boleto.linha_digitavel),
        # Link do PDF sem login (token HMAC) — para enviar ao cliente por Whats/e-mail
        "public_pdf_url": public_boleto_url(b.id),
    }


# ---------------------------------------------------------------------------
# GET /boletos/{billing_id}/pdf  — PDF do boleto
# ---------------------------------------------------------------------------

def boleto_registrado(billing_id: int, db: Session) -> AilosBoleto | None:
    """
    O boleto registrado na Ailos, ou None.

    Só conta como registrado quando a Ailos devolveu linha digitável E código de
    barras — mesmo critério de ``aplicar_dados_oficiais_ailos``. Sem isso, o que
    existe é apenas o cálculo local: um papel com aparência de boleto que o
    banco não conhece, não aceita pagamento e não concilia.
    """
    ab = db.query(AilosBoleto).filter_by(billing_id=billing_id).first()
    return ab if (ab and ab.linha_digitavel and ab.codigo_barras) else None


def _recusar_titulo_baixado(ailos_boleto: AilosBoleto) -> None:
    """Baixa pendente/confirmada (Fase 03): a cobrança deixou de valer ou o
    título foi baixado — não sai PDF nem e-mail para pagamento."""
    if ailos_boleto.baixa_status is not None:
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'boleto_ailos_baixado',
                'message': 'Este boleto foi baixado (ou está com baixa pendente) no banco e não deve '
                           'ser enviado para pagamento.',
            },
        )


def _placa_do_billing(b: Billing, db: Session) -> str:
    if b.vehicle_id:
        veiculo = db.get(Vehicle, b.vehicle_id)
        if veiculo and not veiculo.is_deleted:
            return veiculo.plate or ""
    return ""


def _periodo_servico_fechamento(b: Billing) -> str | None:
    """Competência comercial do fechamento, inclusive dos títulos antigos."""
    if not (b.title or '').startswith('Fechamento '):
        return None
    marker = re.search(r'Período de serviço: (\d{2}/\d{4})', b.notes or '')
    if marker:
        return marker.group(1)
    if b.period_label == b.due_date.strftime('%m/%Y') and (b.notes or '').startswith('Itens do boleto único'):
        # Fechamentos criados antes da correção gravavam o mês do vencimento.
        previous = date(b.due_date.year, b.due_date.month, 1)
        previous = date(previous.year - 1, 12, 1) if previous.month == 1 else date(previous.year, previous.month - 1, 1)
        return previous.strftime('%m/%Y')
    return b.period_label


def _itens_detalhados(
    b: Billing, db: Session, visited: set[int] | None = None,
    service_period_label: str | None = None,
) -> list[tuple[str, float]]:
    """Expande mensalidades combinadas e taxas agrupadas sem perder o total."""
    visited = set(visited or ())
    if b.id in visited:
        return [(descricao_servico(b, _placa_do_billing(b, db) or None, service_period_label), float(b.amount))]
    visited.add(b.id)
    children = list(db.scalars(
        select(Billing).where(
            Billing.substituted_by_id == b.id,
            Billing.is_deleted.is_(False),
        ).order_by(Billing.id)
    ).all())
    if children:
        service_period_label = service_period_label or _periodo_servico_fechamento(b)
        return [item for child in children for item in _itens_detalhados(child, db, visited, service_period_label)]

    plate = _placa_do_billing(b, db) or None
    if b.billing_type == 'primeira_mensalidade':
        links = list(db.scalars(
            select(BillingChargeItem).where(BillingChargeItem.billing_id == b.id)
            .order_by(BillingChargeItem.id)
        ).all())
        service_amount = sum((Decimal(str(link.amount)) for link in links), Decimal('0.00'))
        monthly_amount = Decimal(str(b.amount)) - service_amount
        if links and monthly_amount >= 0:
            monthly_label = 'MENSALIDADE PRÓ-RATA' if 'pró-rata' in (b.title or '').lower() else 'MENSALIDADE'
            items = [(f'{monthly_label} - REF. {service_period_label or b.period_label}' + (f' - PLACA {plate}' if plate else ''), float(monthly_amount))]
            for link in links:
                service = db.get(ClientChargeItem, link.item_id)
                description = service.title if service and not service.is_deleted else f'Serviço #{link.item_id}'
                items.append((description + (f' - PLACA {plate}' if plate else ''), float(link.amount)))
            return items

    if b.billing_type == 'taxa_desinstalacao':
        events = list(db.scalars(
            select(UninstallEvent).where(UninstallEvent.billing_id == b.id)
            .order_by(UninstallEvent.id)
        ).all())
        if events:
            event_items = []
            for event in events:
                amount, title = uninstall_fee_for_event(db, event)
                vehicle = db.get(Vehicle, event.vehicle_id)
                event_plate = vehicle.plate if vehicle and not vehicle.is_deleted else None
                description = f'{title} - {event.uninstall_date.strftime("%d/%m/%Y")}'.upper()
                if event_plate:
                    description += f' - PLACA {event_plate}'
                event_items.append((description, float(amount)))
            if sum(Decimal(str(value)) for _, value in event_items) == Decimal(str(b.amount)):
                return event_items

    return [(descricao_servico(b, plate, service_period_label), float(b.amount))]


def dados_boleto(b: Billing, c: Client, db: Session, ailos_boleto: AilosBoleto) -> DadosBoleto:
    """
    Monta o DadosBoleto de uma cobrança, com os dados oficiais da Ailos
    aplicados. Compartilhado pelo boleto avulso e por cada parcela do carnê.
    """
    item = _billing_to_boleto_item(b, c)
    components = list(db.scalars(
        select(Billing).where(
            Billing.substituted_by_id == b.id,
            Billing.is_deleted.is_(False),
        ).order_by(Billing.id)
    ).all())
    placa = _placa_do_billing(b, db) or None
    servico = descricao_servico(b, placa)
    # Na linha "Referente a" a placa fica em instrução própria, na linha de
    # baixo — não misturada ao resto do texto (item["itens"] mantém o
    # descritivo completo, com placa, para a tabela do boleto).
    servico_sem_placa = descricao_servico(b, None)
    itens = _itens_detalhados(b, db)
    service_period_label = _periodo_servico_fechamento(b) or b.period_label
    dados = gerar_dados_boleto(
        billing_id=b.id,
        valor=b.amount,
        vencimento=b.due_date,
        sacado_nome=c.name,
        sacado_cpf_cnpj=c.cpf_cnpj or "",
        sacado_endereco=item["sacado_endereco"],
        data_emissao=item["data_emissao"],
        sacado_cidade=c.city or "",
        sacado_cep=c.zip_code or "",
        sacado_uf=c.state or "",
        sacado_ie=c.rg_ie or "",
        itens=itens,
        instrucoes=[
            (f"Referente a: fechamento {service_period_label} com {len(itens)} itens."
             if components else f"Referente a: {servico_sem_placa}."),
            *([f"Placa: {placa}"] if placa else []),
            "Não receber após o vencimento.",
            "Após vencimento entrar em contato: whats (47)98877-9273",
        ],
    )
    return aplicar_dados_oficiais_ailos(dados, ailos_boleto)


def _slug_arquivo(texto: str) -> str:
    """Nome de arquivo seguro para o header HTTP: sem acentos nem caracteres proibidos."""
    t = unicodedata.normalize('NFKD', texto or '')
    t = ''.join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r'[\\/:*?"<>|]+', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t or 'documento'


def _montar_pdf_boleto(b: Billing, c: Client, db: Session,
                       ailos_boleto: AilosBoleto) -> tuple[bytes, str]:
    """Gera o PDF do boleto e o nome do arquivo (compartilhado entre a rota
    autenticada e o link público)."""
    pdf_bytes = gerar_boleto_pdf(dados_boleto(b, c, db, ailos_boleto))

    # Nome do arquivo: nome do cliente + data de vencimento (ex.: "EUNICE SOUSA SIMAS 28-08-2026.pdf")
    data_ref = (b.due_date or date.today()).strftime("%d-%m-%Y")
    filename = f"{_slug_arquivo(c.name)} {data_ref}.pdf"
    return pdf_bytes, filename


@router.get("/{billing_id}/pdf")
def get_boleto_pdf(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    PDF do boleto — só para título registrado na Ailos.

    Antes o PDF saía para qualquer cobrança, com nosso número e código de
    barras calculados aqui. Parecia um boleto pronto, mas o banco não tinha
    registro dele: não era pagável e não conciliava.
    """
    b = _get_billing_or_404(billing_id, db)
    ailos_boleto = boleto_registrado(billing_id, db)
    if ailos_boleto is None:
        raise HTTPException(
            status_code=409,
            detail='Esta cobrança ainda não tem boleto emitido na Ailos, então não há '
                   'PDF para baixar. Gere o boleto na aba Ailos do Financeiro.',
        )
    _recusar_titulo_baixado(ailos_boleto)
    c = _pagador_do_billing(b, db)
    pdf_bytes, filename = _montar_pdf_boleto(b, c, db, ailos_boleto)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# POST /boletos/{billing_id}/enviar-email — envia o boleto por e-mail
# ---------------------------------------------------------------------------

def _valor_brl(v) -> str:
    return f'{float(v or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


@router.post("/{billing_id}/enviar-email")
def enviar_boleto_email(
    billing_id: int,
    incluir_nfse: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Envia o boleto por e-mail direto do painel, via SMTP configurado em
    Configurações → E-mail, com o PDF anexado — sem depender de cliente de
    e-mail externo na máquina do operador.
    """
    b = _get_billing_or_404(billing_id, db)
    if b.status not in (BillingStatus.PENDING, BillingStatus.OVERDUE):
        raise HTTPException(status_code=409, detail='Somente cobranças em aberto podem ser enviadas por e-mail.')
    ailos_boleto = boleto_registrado(billing_id, db)
    if ailos_boleto is None:
        raise HTTPException(
            status_code=409,
            detail='Esta cobrança ainda não tem boleto emitido na Ailos, então não pode ser enviada. '
                   'Gere o boleto na aba Ailos do Financeiro.',
        )
    _recusar_titulo_baixado(ailos_boleto)
    c = _pagador_do_billing(b, db)
    if not c.email:
        raise HTTPException(status_code=400, detail='Cliente sem e-mail cadastrado.')

    dados = dados_boleto(b, c, db, ailos_boleto)
    pdf_bytes, filename = _montar_pdf_boleto(b, c, db, ailos_boleto)
    nfse_anexo = None
    if incluir_nfse:
        from app.api.v1.endpoints.nfse import anexo_nfse_emitida
        nfse_anexo = anexo_nfse_emitida(db, billing_id)

    variaveis = {
        'NOME': (c.name or '').upper(),
        'VALOR': _valor_brl(b.amount),
        'VENCIMENTO': b.due_date.strftime('%d/%m/%Y') if b.due_date else '',
        'REFERENTE': b.period_label or (b.due_date.strftime('%m/%Y') if b.due_date else ''),
        'CODIGO_BARRAS': re.sub(r'\D', '', dados.linha_digitavel or ''),
        'LINK_BOLETO': public_boleto_url(b.id),
    }
    tpl = carregar_mensagens(db)
    assunto = render_template(tpl['msg_boleto_assunto'], variaveis)
    corpo = render_template(tpl['msg_boleto'], variaveis)
    if nfse_anexo:
        corpo += '\n\nA NFS-e desta cobrança também está anexada a este e-mail.'

    from app.services.email_smtp import EmailConfigError, enviar_email
    try:
        anexos = [(filename, pdf_bytes, 'application/pdf')]
        enviar_email(
            db, destinatario=c.email, assunto=assunto, corpo=corpo,
            **({'anexos': [*anexos, nfse_anexo]} if nfse_anexo else {'anexo': anexos[0]}),
        )
    except EmailConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except smtplib.SMTPException as exc:
        raise HTTPException(status_code=400, detail=f'Erro do servidor de e-mail: {exc}') from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f'Não foi possível conectar ao servidor de e-mail: {exc}') from exc

    return {'message': f'E-mail enviado para {c.email}.'}


@router.get("/carne/{lote_id}/pdf")
def get_carne_pdf(
    lote_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    PDF do carnê: todas as parcelas registradas de um lote tipo 'carne', uma
    por bloco pagável (2 por página).

    Se alguma parcela ainda não tem os dados oficiais (linha digitável), tenta
    recuperá-la na Ailos antes de montar — o registro pode ter sido feito, mas
    a consulta de status ter falhado na geração.

    Enquanto houver parcela pendente e o prazo de espera não tiver esgotado
    (mesmos 10 min de `_consultar_carne_por_boleto`), recusa o download em vez
    de servir um carnê incompleto sem avisar — o carnê "gerava 1 boleto só"
    quando baixado cedo demais, com o resto pulado em silêncio. Depois do
    prazo, serve o que conseguiu (senão o download ficaria bloqueado para
    sempre por uma parcela que nunca resolve do lado do banco).
    """
    lote = db.get(AilosLote, lote_id)
    if lote is None or lote.tipo != 'carne':
        raise HTTPException(status_code=404, detail='Carnê não encontrado')

    # Auto-recuperação: se falta linha digitável em alguma parcela, consulta a
    # Ailos (parcela por parcela). Best-effort — se a Ailos estiver fora, segue
    # com o que já houver salvo.
    faltando_ids = [bid for bid in (lote.billing_ids or []) if boleto_registrado(bid, db) is None]
    if faltando_ids:
        try:
            ailos_boletos.consultar_lote(db, lote)
        except Exception:  # noqa: BLE001 — download não pode depender da Ailos
            pass
        faltando_ids = [bid for bid in (lote.billing_ids or []) if boleto_registrado(bid, db) is None]

    parcelas: list[DadosBoleto] = []
    # A ordem das parcelas é a ordem em que os billing_ids foram enviados.
    for billing_id in (lote.billing_ids or []):
        ab = boleto_registrado(billing_id, db)
        if ab is None or ab.baixa_status is not None:
            # Sem registro, ou parcela cancelada/recebida por fora/baixada:
            # não vai para o carnê que o cliente paga (Fase 03).
            continue
        b = db.get(Billing, billing_id)
        if not b or b.is_deleted or b.status == BillingStatus.CANCELED:
            continue
        c = ailos_boletos.resolver_pagador(db, b, db.get(Client, b.client_id))
        if not c:
            continue
        parcelas.append(dados_boleto(b, c, db, ab))

    if not parcelas:
        raise HTTPException(
            status_code=409,
            detail='Nenhuma parcela deste carnê está registrada na Ailos ainda. '
                   'Aguarde o processamento e tente novamente em instantes.',
        )

    if faltando_ids and not ailos_boletos.carne_prazo_esgotado(lote):
        raise HTTPException(
            status_code=409,
            detail=f'{len(parcelas)} de {len(lote.billing_ids or [])} parcelas prontas — '
                   'as demais ainda estão sendo processadas na Ailos. '
                   'Aguarde e tente novamente em instantes.',
        )

    return Response(
        content=gerar_carne_pdf(parcelas),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="carne-{lote_id}.pdf"'},
    )


# ---------------------------------------------------------------------------
# GET /public/boleto/{billing_id}/{token}  — PDF por link público (sem login)
# ---------------------------------------------------------------------------

@public_router.get("/boleto/{billing_id}/{token}")
def get_boleto_publico(
    billing_id: int,
    token: str,
    db: Session = Depends(get_db),
):
    """PDF do boleto acessível pelo cliente final via link enviado por
    WhatsApp/e-mail. Protegido por token HMAC — sem token válido, 404."""
    if not hmac.compare_digest(token, _public_token(billing_id)):
        raise HTTPException(status_code=404, detail="Boleto não encontrado")
    b = _get_billing_or_404(billing_id, db)
    # Cobrança cancelada não pode continuar pagável pelo link público — mesmo que
    # o titulo ainda esteja registrado na Ailos, não apresentamos o boleto.
    if b.status == BillingStatus.CANCELED:
        raise HTTPException(status_code=404, detail="Boleto não encontrado")
    # Link já enviado ao cliente + boleto ainda não registrado = cliente com um
    # papel impagável na mão. 404 (e não 409) para não expor a cobrança a quem
    # tenha o link de um título que não existe no banco.
    ailos_boleto = boleto_registrado(billing_id, db)
    if ailos_boleto is None:
        raise HTTPException(status_code=404, detail="Boleto não encontrado")
    # Baixa pendente/confirmada = a cobrança deixou de valer (recebida por
    # fora, cancelada) ou o título foi baixado: não entregar para pagamento.
    if ailos_boleto.baixa_status is not None:
        raise HTTPException(status_code=404, detail="Boleto não encontrado")
    c = _pagador_do_billing(b, db)
    pdf_bytes, filename = _montar_pdf_boleto(b, c, db, ailos_boleto)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# POST /boletos/cnab400  — arquivo remessa CNAB400
# ---------------------------------------------------------------------------

def _remessa_response(gerada: cnab_remessa.RemessaGerada) -> Response:
    remessa = gerada.remessa
    hoje = date.today().strftime("%Y%m%d")
    filename = f"remessa_cnab{remessa.layout}_{hoje}_{remessa.sequencial:06d}.rem"
    return Response(
        content=gerada.arquivo,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Remessa-Id": str(remessa.id),
            "X-Remessa-Sequencial": str(remessa.sequencial),
            "X-Remessa-SHA256": remessa.arquivo_sha256,
        },
    )


def _gerar_remessa_endpoint(
    layout: str, billing_ids: list[int] | None, status: str, db: Session, user_id: int | None,
) -> Response:
    try:
        cnab_remessa.exigir_canal_habilitado()
        billings = _buscar_billings(billing_ids, status, db)
        if not billings:
            raise HTTPException(status_code=404, detail="Nenhuma cobrança encontrada para os critérios informados")
        items = _preparar_items(billings, db)
        sem_pagador = sorted({b.id for b in billings} - {item["billing_id"] for item in items})
        if sem_pagador:
            raise cnab_remessa.RemessaError(
                'selecao_invalida', 'Há cobranças sem cliente ativo para o sacado.',
                extra={'motivos': {str(bid): 'cliente_removido' for bid in sem_pagador}},
            )
        gerada = cnab_remessa.gerar_remessa(db, layout, billings, items, user_id=user_id)
    except cnab_remessa.RemessaError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
    return _remessa_response(gerada)


@router.post("/cnab400")
def gerar_cnab400(
    billing_ids: list[int] | None = None,
    status: str = Query(default="pendente", description="pending ou overdue"),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Gera arquivo de remessa CNAB400 para envio ao banco Ailos.

    Canal desligado até homologação (409 ``canal_cnab_indisponivel``). Ligado:
    com billing_ids, só cobranças em aberto, sem repetição e sem título em
    nenhum canal (senão 409/422 com o motivo de cada uma); sem billing_ids,
    as pendentes ou vencidas livres. A remessa fica registrada (sequência,
    hash, títulos reservados) — ver GET /boletos/remessas.
    """
    return _gerar_remessa_endpoint('400', billing_ids, status, db, current_user.id)


# ---------------------------------------------------------------------------
# POST /boletos/cnab240  — arquivo remessa CNAB240
# ---------------------------------------------------------------------------

@router.post("/cnab240")
def gerar_cnab240(
    billing_ids: list[int] | None = None,
    status: str = Query(default="pendente"),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Gera arquivo de remessa CNAB240 para envio ao banco Ailos (mesmas regras
    do CNAB400).
    """
    return _gerar_remessa_endpoint('240', billing_ids, status, db, current_user.id)


# ---------------------------------------------------------------------------
# Remessas registradas e matriz de canais (Fase 03, FIN-07)
# ---------------------------------------------------------------------------

@router.get("/remessas/{remessa_id}/arquivo")
def baixar_remessa(
    remessa_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Os mesmos bytes (mesmo hash) da geração — baixar de novo não cria remessa."""
    remessa = db.get(CnabRemessa, remessa_id)
    if remessa is None:
        raise HTTPException(status_code=404, detail='Remessa não encontrada')
    return _remessa_response(cnab_remessa.RemessaGerada(remessa=remessa, arquivo=remessa.arquivo))


@router.post("/remessas/{remessa_id}/descartar")
def descartar_remessa(
    remessa_id: int,
    payload: RemessaDescarteIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    """Só para remessa que NÃO foi enviada ao banco: libera os títulos para
    outro canal. Remessa enviada não se descarta — o título existe no banco."""
    try:
        remessa = cnab_remessa.descartar_remessa(db, remessa_id, motivo=payload.motivo.strip())
    except cnab_remessa.RemessaError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
    return {'id': remessa.id, 'status': remessa.status, 'descartada_em': remessa.descartada_em}


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _buscar_billings(
    billing_ids: list[int] | None,
    status: str,
    db: Session,
) -> list[Billing]:
    """Seleção validada (explícita) ou automática (por status). Pagas,
    canceladas, repetidas ou com título em qualquer canal não entram."""
    if billing_ids:
        return cnab_remessa.validar_selecao(db, list(billing_ids))

    status_map = {
        "pendente": BillingStatus.PENDING,
        "pending":  BillingStatus.PENDING,
        "vencido":  BillingStatus.OVERDUE,
        "overdue":  BillingStatus.OVERDUE,
    }
    bs = status_map.get(status, BillingStatus.PENDING)
    return cnab_remessa.selecionar_por_status(db, bs, limite=500)


def _preparar_items(billings: list[Billing], db: Session) -> list[dict]:
    """Converte lista de Billing para lista de dicts para os geradores CNAB."""
    items = []
    for b in billings:
        dono = db.get(Client, b.client_id)
        if not dono or dono.is_deleted:
            continue
        # Remessa CNAB registra o título — o pagador é o interveniente, se houver.
        c = ailos_boletos.resolver_pagador(db, b, dono)
        items.append(_billing_to_boleto_item(b, c))
    return items
