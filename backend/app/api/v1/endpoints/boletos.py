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
from sqlalchemy import func, select
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
from app.models.sicredi_boleto import SicrediBoleto
from app.models.sicredi_lote import SicrediLote
from app.models.billing import Billing
from app.models.billing_charge_item import BillingChargeItem
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.cnab_remessa import CnabRemessa
from app.models.enums import BillingStatus, UserRole
from app.models.vehicle import Vehicle
from app.models.uninstall_event import UninstallEvent
from app.services import ailos_boletos, cnab_remessa, sicredi_boletos, sicredi_client, sicredi_lotes
from app.services.email_smtp import emails_cadastrados
from app.services.ailos_boletos import aplicar_dados_oficiais_ailos
from app.services.boleto_ailos import CEDENTE_CNPJ, CEDENTE_NOME, gerar_dados_boleto, DadosBoleto
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
    client_id: int | None = Query(default=None, ge=1),
    payer_client_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Carnês gerados de um cliente (para reabrir/baixar depois da geração).

    client_id consulta o cliente atendido; payer_client_id consulta o pagador
    histórico das parcelas. A central financeira usa o segundo filtro.
    """
    if client_id is None and payer_client_id is None:
        raise HTTPException(status_code=422, detail='Informe o cliente ou o responsável financeiro.')
    lote_ids = (
        select(AilosBoleto.lote_id)
        .join(Billing, Billing.id == AilosBoleto.billing_id)
        .where(Billing.is_deleted.is_(False), AilosBoleto.lote_id.isnot(None))
        .distinct()
    )
    if client_id is not None:
        lote_ids = lote_ids.where(Billing.client_id == client_id)
    if payer_client_id is not None:
        lote_ids = lote_ids.where(func.coalesce(Billing.payer_client_id, Billing.client_id) == payer_client_id)
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
    # O ID é local a cada banco; clientes distinguem pelo campo banco.
    for item in resultado:
        item['banco'] = 'ailos'
    lotes_sicredi = db.query(SicrediLote).filter(SicrediLote.tipo == 'carne')
    if payer_client_id is not None:
        lotes_sicredi = lotes_sicredi.filter(SicrediLote.payer_client_id == payer_client_id)
    for lote in lotes_sicredi.order_by(SicrediLote.id.desc()).all():
        parcelas = sicredi_lotes.parcelas_do_lote(db, lote)
        cobrancas = [b for _, b in parcelas if b is not None]
        if not any(not b.is_deleted for b in cobrancas):
            continue
        if client_id is not None and not any(b.client_id == client_id and not b.is_deleted for b in cobrancas):
            continue
        registradas = sum(sicredi_boletos.registrado(db, b.id) is not None for b in cobrancas)
        resultado.append({
            'banco': 'sicredi', 'lote_id': lote.id, 'ticket': lote.ticket,
            'criado_em': lote.created_at.isoformat() if lote.created_at else None,
            'parcelas': len(lote.billing_ids or []), 'parcelas_registradas': registradas,
            'parcelas_pagas': sum(b.status == BillingStatus.PAID for b in cobrancas),
            'total': float(sum((b.amount or 0) for b in cobrancas)),
            'valor_pago': float(sum((b.amount or 0) for b in cobrancas if b.status == BillingStatus.PAID)),
            'status': 'completed' if registradas == len(lote.billing_ids or []) else lote.status,
            'parcelas_detalhe': [{'billing_id': b.id, 'numero_parcela': indice,
                                 'vencimento': b.due_date.isoformat() if b.due_date else None,
                                 'valor': float(b.amount or 0), 'status': b.status.value,
                                 'data_pagamento': b.payment_date.isoformat() if b.payment_date else None}
                                for indice, (_, b) in enumerate(parcelas, 1) if b is not None],
        })
    return sorted(resultado, key=lambda item: item['criado_em'] or '', reverse=True)


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
    motivo_sicredi = None
    try:
        sicredi_client.credenciais()
    except sicredi_client.SicrediError as exc:
        motivo_sicredi = str(exc)
    return {
        'ailos_api': {'habilitado': True, 'emissao': True, 'conciliacao': True, 'motivo': None},
        'sicredi_api': {'habilitado': motivo_sicredi is None, 'emissao': motivo_sicredi is None,
                       'conciliacao': True, 'motivo': motivo_sicredi},
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

    sicredi = sicredi_boletos.registrado(db, b.id)
    if sicredi is not None:
        return {
            "billing_id": b.id,
            "nosso_numero": sicredi.nosso_numero,
            "codigo_barras": sicredi.codigo_barras,
            "linha_digitavel": sicredi.linha_digitavel,
            "cedente": {
                "nome": settings.sicredi_beneficiario_nome,
                "cnpj": settings.sicredi_beneficiario_cnpj,
                "agencia": f"{sicredi.cooperativa}.{sicredi.posto}",
                "codigo": sicredi.codigo_beneficiario,
                "convenio": sicredi.codigo_beneficiario,
                "carteira": "1",
            },
            "sacado": {
                "nome": c.name,
                "cpf_cnpj": c.cpf_cnpj or "",
                "endereco": item["sacado_endereco"],
            },
            "vencimento": (sicredi.data_vencimento or b.due_date).isoformat() if (sicredi.data_vencimento or b.due_date) else None,
            "emissao": (sicredi.created_at.date() if sicredi.created_at else date.today()).isoformat(),
            "valor": float(sicredi.valor_nominal if sicredi.valor_nominal is not None else b.amount),
            "banco": {"codigo": "748", "nome": "Sicredi"},
            "boleto_registrado": True,
            "public_pdf_url": public_boleto_url(b.id),
            "pix_copia_cola": sicredi.qr_code,
        }

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


def _pdf_sicredi(b: Billing, c: Client, db: Session) -> tuple[bytes, str]:
    """Layout MasterSat, com os dados do título já registrado no Sicredi."""
    boleto = sicredi_boletos.registrado(db, b.id)
    if boleto is None:
        raise HTTPException(
            status_code=409, detail='O boleto precisa estar registrado no Sicredi antes da impressão.',
        )
    _recusar_titulo_baixado(boleto)
    dados = dados_boleto_sicredi(b, c, db, boleto)
    pdf_bytes = gerar_boleto_pdf(dados)
    data_ref = (dados.data_vencimento or date.today()).strftime("%d-%m-%Y")
    return pdf_bytes, f"{_slug_arquivo(c.name)} {data_ref}.pdf"


def _recusar_titulo_baixado(ailos_boleto) -> None:
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


def _conteudo_boleto(b: Billing, c: Client, db: Session) -> dict:
    """Recibo, serviços, placas e instruções comuns aos dois bancos."""
    item = _billing_to_boleto_item(b, c)
    components = list(db.scalars(
        select(Billing).where(
            Billing.substituted_by_id == b.id,
            Billing.is_deleted.is_(False),
        ).order_by(Billing.id)
    ).all())
    placa = _placa_do_billing(b, db) or None
    # Na linha "Referente a" a placa fica em instrução própria, na linha de
    # baixo — não misturada ao resto do texto (item["itens"] mantém o
    # descritivo completo, com placa, para a tabela do boleto).
    servico_sem_placa = descricao_servico(b, None)
    itens = _itens_detalhados(b, db)
    service_period_label = _periodo_servico_fechamento(b) or b.period_label
    # Boleto que reúne vários veículos (boleto único/carnê de várias placas).
    placas_componentes = list(dict.fromkeys(
        p for p in (_placa_do_billing(comp, db) for comp in components) if p
    ))
    if components and (b.title or '').startswith('Carnê ') and b.installment_total:
        referente = (f"Referente a: carnê {b.period_label}, parcela {b.installment_number}/"
                     f"{b.installment_total}, {len(itens)} itens.")
    elif components:
        referente = f"Referente a: fechamento {service_period_label} com {len(itens)} itens."
    else:
        referente = f"Referente a: {servico_sem_placa}."
    return dict(
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
            referente,
            *([f"Placa: {placa}"] if placa
              else [f"Placas: {', '.join(placas_componentes)}"] if len(placas_componentes) > 1 else []),
            "Não receber após o vencimento.",
            "Após vencimento entrar em contato: whats (47)98877-9273",
        ],
    )


def dados_boleto(b: Billing, c: Client, db: Session, ailos_boleto: AilosBoleto | None) -> DadosBoleto:
    """Dados oficiais Ailos com o conteúdo comum do recibo MasterSat."""
    dados = gerar_dados_boleto(**_conteudo_boleto(b, c, db))
    return aplicar_dados_oficiais_ailos(dados, ailos_boleto)


def dados_boleto_sicredi(b: Billing, c: Client, db: Session, boleto: SicrediBoleto) -> DadosBoleto:
    """A impressão não calcula nem registra códigos bancários: usa os recebidos."""
    conteudo = _conteudo_boleto(b, c, db)
    conteudo.pop('vencimento')
    conteudo.pop('data_emissao')
    numero = boleto.nosso_numero
    linha = re.sub(r'\D', '', boleto.linha_digitavel or '')
    if len(linha) != 47 or len(boleto.codigo_barras or '') != 44 or len(numero) != 9:
        raise HTTPException(status_code=409, detail='O registro Sicredi não contém os códigos completos para impressão.')
    linha_formatada = (
        f'{linha[:5]}.{linha[5:10]}  {linha[10:15]}.{linha[15:21]}  '
        f'{linha[21:26]}.{linha[26:32]}  {linha[32]}  {linha[33:]}'
    )
    pagador_registrado = (boleto.payload_request or {}).get('pagador') or {}
    # Nome e documento são os enviados ao banco; itens e placas vêm do mesmo
    # detalhamento da Ailos, inclusive quando o título reúne várias cobranças.
    conteudo['sacado_nome'] = pagador_registrado.get('nome') or c.name
    conteudo['sacado_cpf_cnpj'] = pagador_registrado.get('documento') or c.cpf_cnpj or ''
    conteudo['valor'] = Decimal(str(boleto.valor_nominal if boleto.valor_nominal is not None else b.amount))
    if boleto.ambiente == 'sandbox':
        conteudo['instrucoes'] = ['HOMOLOGAÇÃO - SEM VALIDADE FINANCEIRA. NÃO PAGAR.', *conteudo['instrucoes']]
    return DadosBoleto(
        **conteudo,
        numero_documento=boleto.seu_numero or str(b.id),
        nosso_numero=numero,
        nosso_numero_dv=numero[-1],
        nosso_numero_display=f'{numero[:2]}/{numero[2:8]}-{numero[8:]}',
        data_emissao=boleto.created_at.date() if boleto.created_at else date.today(),
        data_vencimento=boleto.data_vencimento or b.due_date,
        codigo_barras=boleto.codigo_barras,
        linha_digitavel=linha_formatada,
        cedente_nome=settings.sicredi_beneficiario_nome,
        cedente_cnpj=settings.sicredi_beneficiario_cnpj,
        cedente_agencia=f'{boleto.cooperativa}.{boleto.posto}',
        cedente_codigo=boleto.codigo_beneficiario,
        cedente_convenio=boleto.codigo_beneficiario,
        carteira='1', banco_codigo='748', banco_nome='Sicredi',
        pix_emv=boleto.qr_code,
    )


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
    PDF MasterSat — só para título registrado na Ailos ou no Sicredi.

    Antes o PDF saía para qualquer cobrança, com nosso número e código de
    barras calculados aqui. Parecia um boleto pronto, mas o banco não tinha
    registro dele: não era pagável e não conciliava.
    """
    b = _get_billing_or_404(billing_id, db)
    sicredi = sicredi_boletos.registrado(db, billing_id)
    if sicredi is not None:
        _recusar_titulo_baixado(sicredi)
        pdf_bytes, filename = _pdf_sicredi(b, _pagador_do_billing(b, db), db)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
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


def preparar_envio_boleto_email(db: Session, billing_id: int, incluir_nfse: bool):
    """Valida e monta o e-mail antes de reservar um envio de fechamento."""
    b = _get_billing_or_404(billing_id, db)
    if b.payment_method == 'pix_automatico':
        from app.models.sicredi_pix import SicrediPixCobranca
        agenda = db.scalar(select(SicrediPixCobranca).where(SicrediPixCobranca.billing_id == billing_id))
        if (b.status not in (BillingStatus.PENDING, BillingStatus.OVERDUE)
            or not agenda or agenda.status != 'ATIVA' or agenda.pendencia):
            raise HTTPException(409, detail='Pix Automático não agendado ou com pendência de conferência.')
        payer = _pagador_do_billing(b, db)
        recipients = emails_cadastrados(payer)
        if not recipients:
            raise HTTPException(400, detail='Cliente sem e-mail cadastrado.')
        attachments = []
        if incluir_nfse:
            from app.api.v1.endpoints.nfse import anexo_nfse_emitida
            attachments.append(anexo_nfse_emitida(db, billing_id))
        subject = f'MasterSat - Pix Automático - {b.period_label or b.due_date.strftime("%m/%Y")}'
        body = (f'Olá, {payer.name}.\n\nSua cobrança MasterSat de R$ {_valor_brl(b.amount)}, '
                f'com vencimento em {b.due_date.strftime("%d/%m/%Y")}, está agendada por Pix Automático. '
                'Você pode acompanhar o agendamento no aplicativo do seu banco.\n\n'
                + ('A NFS-e está anexada a este e-mail.\n\n' if incluir_nfse else '') + 'MasterSat')
        return ', '.join(recipients), subject, body, attachments
    sicredi = sicredi_boletos.registrado(db, billing_id)
    ailos_boleto = None if sicredi is not None else boleto_registrado(billing_id, db)
    if sicredi is None and ailos_boleto is None:
        raise HTTPException(
            status_code=409,
            detail='Esta cobrança ainda não tem boleto emitido. Gere o boleto pela Ailos ou pelo Sicredi no Financeiro.',
        )
    _recusar_titulo_baixado(sicredi or ailos_boleto)
    if b.status not in (BillingStatus.PENDING, BillingStatus.OVERDUE):
        raise HTTPException(status_code=409, detail='Somente cobranças em aberto podem ser enviadas por e-mail.')
    c = _pagador_do_billing(b, db)
    destinatarios = emails_cadastrados(c)
    if not destinatarios:
        raise HTTPException(status_code=400, detail='Cliente sem e-mail cadastrado.')

    if sicredi is not None:
        linha_digitavel = sicredi.linha_digitavel
        pdf_bytes, filename = _pdf_sicredi(b, c, db)
    else:
        linha_digitavel = dados_boleto(b, c, db, ailos_boleto).linha_digitavel
        pdf_bytes, filename = _montar_pdf_boleto(b, c, db, ailos_boleto)
    if not pdf_bytes or not pdf_bytes.startswith(b'%PDF-'):
        raise HTTPException(status_code=502, detail='Não foi possível gerar o PDF do boleto. O e-mail não foi enviado.')
    nfse_anexo = None
    if incluir_nfse:
        from app.api.v1.endpoints.nfse import anexo_nfse_emitida
        nfse_anexo = anexo_nfse_emitida(db, billing_id)

    variaveis = {
        'NOME': (c.name or '').upper(),
        'VALOR': _valor_brl(b.amount),
        'VENCIMENTO': b.due_date.strftime('%d/%m/%Y') if b.due_date else '',
        'REFERENTE': b.period_label or (b.due_date.strftime('%m/%Y') if b.due_date else ''),
        'CODIGO_BARRAS': re.sub(r'\D', '', linha_digitavel or ''),
        'LINK_BOLETO': public_boleto_url(b.id),
    }
    tpl = carregar_mensagens(db)
    assunto = render_template(tpl['msg_boleto_assunto'], variaveis)
    corpo = render_template(tpl['msg_boleto_email'], variaveis)
    if nfse_anexo:
        corpo += '\n\nA NFS-e desta cobrança também está anexada a este e-mail.'

    anexos = [(filename, pdf_bytes, 'application/pdf')]
    if nfse_anexo:
        anexos.append(nfse_anexo)
    return ', '.join(destinatarios), assunto, corpo, anexos


def preparar_envio_carne_email(db: Session, lote_id: int, billing_id: int, incluir_nfse: bool):
    """E-mail único do carnê Sicredi: o PDF com todas as parcelas em aberto.

    Recusa (409) enquanto houver parcela em aberto sem registro — nunca envia
    carnê incompleto. A NFS-e anexada é a da 1ª parcela (``billing_id``).
    """
    lote = db.get(SicrediLote, lote_id)
    if lote is None or lote.tipo != 'carne' or billing_id not in (lote.billing_ids or []):
        raise HTTPException(status_code=404, detail='Carnê Sicredi não encontrado para esta cobrança.')
    pdf_bytes = _carne_pdf_sicredi(db, lote_id).body
    if not pdf_bytes or not pdf_bytes.startswith(b'%PDF-'):
        raise HTTPException(status_code=502, detail='Não foi possível gerar o PDF do carnê. O e-mail não foi enviado.')
    abertas = [b for _, b in sicredi_lotes.parcelas_do_lote(db, lote)
               if b is not None and not b.is_deleted and b.status in (BillingStatus.PENDING, BillingStatus.OVERDUE)]
    c = _pagador_do_billing(_get_billing_or_404(billing_id, db), db)
    destinatarios = emails_cadastrados(c)
    if not destinatarios:
        raise HTTPException(status_code=400, detail='Cliente sem e-mail cadastrado.')
    nfse_anexo = None
    if incluir_nfse:
        from app.api.v1.endpoints.nfse import anexo_nfse_emitida
        nfse_anexo = anexo_nfse_emitida(db, billing_id)
    primeiro, ultimo = abertas[0].due_date, abertas[-1].due_date
    total = sum((b.amount for b in abertas), Decimal('0'))
    assunto = f'Carnê MasterSat — {primeiro:%m/%Y} a {ultimo:%m/%Y}'
    corpo = (
        f'Olá, {(c.name or "").upper()}.\n\n'
        f'Segue o seu carnê MasterSat com {len(abertas)} parcela(s) mensais, de {primeiro:%d/%m/%Y} '
        f'a {ultimo:%d/%m/%Y}, no total de R$ {_valor_brl(total)}.\n\n'
        'Cada parcela tem o próprio código de barras e o próprio vencimento: pague uma por mês, '
        'até a data indicada em cada boleto.'
        + ('\n\nA NFS-e da primeira parcela também está anexada a este e-mail.' if nfse_anexo else '')
        + '\n\nMasterSat'
    )
    anexos = [(f'carne-mastersat-{primeiro:%m-%Y}-a-{ultimo:%m-%Y}.pdf', pdf_bytes, 'application/pdf')]
    if nfse_anexo:
        anexos.append(nfse_anexo)
    return ', '.join(destinatarios), assunto, corpo, anexos


@router.post("/{billing_id}/enviar-email")
def enviar_boleto_email(
    billing_id: int,
    incluir_nfse: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Envia boleto por SMTP; anexa a NFS-e quando solicitada."""
    destinatario, assunto, corpo, anexos = preparar_envio_boleto_email(
        db, billing_id, incluir_nfse,
    )

    from app.services.email_smtp import EmailConfigError, enviar_email
    try:
        enviar_email(
            db, destinatario=destinatario, assunto=assunto, corpo=corpo,
            **({'anexos': anexos} if incluir_nfse or not anexos else {'anexo': anexos[0]}),
        )
    except EmailConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except smtplib.SMTPException as exc:
        raise HTTPException(status_code=400, detail=f'Erro do servidor de e-mail: {exc}') from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f'Não foi possível conectar ao servidor de e-mail: {exc}') from exc

    return {'message': f'E-mail enviado para {destinatario}.'}


def _carne_pdf_sicredi(db: Session, lote_id: int, *, parcial: bool = False) -> Response:
    lote = db.get(SicrediLote, lote_id)
    if lote is None or lote.tipo != 'carne':
        raise HTTPException(status_code=404, detail='Carnê Sicredi não encontrado.')
    parcelas = []
    abertas = 0
    for bid, b in sicredi_lotes.parcelas_do_lote(db, lote):
        if b is None or b.is_deleted or b.status not in (BillingStatus.PENDING, BillingStatus.OVERDUE):
            continue
        abertas += 1
        registro = sicredi_boletos.registrado(db, bid)
        if registro is None or registro.baixa_status is not None:
            continue
        parcelas.append(dados_boleto_sicredi(b, _pagador_do_billing(b, db), db, registro))
    if not parcelas:
        raise HTTPException(status_code=409, detail='Nenhuma parcela deste carnê está registrada e disponível para pagamento.')
    if not parcial and len(parcelas) < abertas:
        raise HTTPException(status_code=409, detail=f'{len(parcelas)} de {abertas} parcelas em aberto disponíveis. Aguarde ou baixe explicitamente o carnê parcial.')
    return Response(content=gerar_carne_pdf(parcelas), media_type='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="carne-sicredi-{lote_id}.pdf"'})


@router.get("/carne/{lote_id}/pdf")
def get_carne_pdf(
    lote_id: int,
    banco: str = Query(default='ailos', pattern='^(ailos|sicredi)$'),
    parcial: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    PDF do carnê: todas as parcelas registradas de um lote tipo 'carne', uma
    por bloco pagável (3 por página).

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
    if banco == 'sicredi':
        return _carne_pdf_sicredi(db, lote_id, parcial=parcial)
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
    sicredi = sicredi_boletos.registrado(db, billing_id)
    if sicredi is not None:
        if sicredi.baixa_status is not None:
            raise HTTPException(status_code=404, detail="Boleto não encontrado")
        pdf_bytes, filename = _pdf_sicredi(b, _pagador_do_billing(b, db), db)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
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
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
