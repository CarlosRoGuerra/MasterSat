"""
Endpoints de geração de boletos e arquivos CNAB (400 e 240).

GET  /boletos/{billing_id}          → Dados do boleto (JSON)
GET  /boletos/{billing_id}/pdf      → PDF do boleto
POST /boletos/cnab400               → Arquivo remessa CNAB400
POST /boletos/cnab240               → Arquivo remessa CNAB240
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, UserRole
from app.models.vehicle import Vehicle
from app.services.ailos_boletos import aplicar_dados_oficiais_ailos
from app.services.boleto_ailos import gerar_dados_boleto, DadosBoleto
from app.services.boleto_pdf import gerar_boleto_pdf
from app.services.cnab400 import gerar_arquivo_cnab400
from app.services.cnab240 import gerar_arquivo_cnab240

router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)


def _get_billing_or_404(billing_id: int, db: Session) -> Billing:
    b = db.get(Billing, billing_id)
    if not b or b.is_deleted:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada")
    return b


def _get_client_or_404(client_id: int, db: Session) -> Client:
    c = db.get(Client, client_id)
    if not c or c.is_deleted:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return c


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
    c = _get_client_or_404(b.client_id, db)
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
    }


# ---------------------------------------------------------------------------
# GET /boletos/{billing_id}/pdf  — PDF do boleto
# ---------------------------------------------------------------------------

@router.get("/{billing_id}/pdf")
def get_boleto_pdf(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Gera e retorna o PDF do boleto para download/impressão."""
    b = _get_billing_or_404(billing_id, db)
    c = _get_client_or_404(b.client_id, db)
    item = _billing_to_boleto_item(b, c)

    dados = gerar_dados_boleto(
        billing_id=b.id,
        valor=b.amount,
        vencimento=b.due_date,
        sacado_nome=c.name,
        sacado_cpf_cnpj=c.cpf_cnpj or "",
        sacado_endereco=item["sacado_endereco"],
        data_emissao=item["data_emissao"],
        instrucoes=[
            "Não receber após o vencimento.",
            "Após vencimento entrar em contato: contato@mastersat.com.br",
            f"Referente ao contrato de rastreamento. Cobrança #{b.id}.",
        ],
    )
    ailos_boleto = db.query(AilosBoleto).filter_by(billing_id=b.id).first()
    dados = aplicar_dados_oficiais_ailos(dados, ailos_boleto)

    pdf_bytes = gerar_boleto_pdf(dados)

    # Nome do arquivo: placa_do_veiculo + data_emissao
    # Ex: boleto_PQPP666_04-06-2026.pdf
    placa = ""
    if b.vehicle_id:
        veiculo = db.get(Vehicle, b.vehicle_id)
        if veiculo and not veiculo.is_deleted:
            placa = veiculo.plate
    data_emissao_str = item["data_emissao"].strftime("%d-%m-%Y") if item.get("data_emissao") else date.today().strftime("%d-%m-%Y")

    if placa:
        filename = f"boleto_{placa}_{data_emissao_str}.pdf"
    else:
        filename = f"boleto_{b.id:06d}_{data_emissao_str}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# POST /boletos/cnab400  — arquivo remessa CNAB400
# ---------------------------------------------------------------------------

@router.post("/cnab400")
def gerar_cnab400(
    billing_ids: list[int] | None = None,
    status: str = Query(default="pendente", description="pending ou overdue"),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Gera arquivo de remessa CNAB400 para envio ao banco Ailos.

    Se billing_ids for fornecido, gera apenas para esses boletos.
    Caso contrário, gera para todos os boletos pendentes ou vencidos.
    """
    billings = _buscar_billings(billing_ids, status, db)
    if not billings:
        raise HTTPException(status_code=404, detail="Nenhuma cobrança encontrada para os critérios informados")

    items = _preparar_items(billings, db)
    arquivo = gerar_arquivo_cnab400(items)

    hoje = date.today().strftime("%Y%m%d")
    filename = f"remessa_cnab400_{hoje}.rem"
    return Response(
        content=arquivo,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# POST /boletos/cnab240  — arquivo remessa CNAB240
# ---------------------------------------------------------------------------

@router.post("/cnab240")
def gerar_cnab240(
    billing_ids: list[int] | None = None,
    status: str = Query(default="pendente"),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Gera arquivo de remessa CNAB240 para envio ao banco Ailos.
    """
    billings = _buscar_billings(billing_ids, status, db)
    if not billings:
        raise HTTPException(status_code=404, detail="Nenhuma cobrança encontrada para os critérios informados")

    items = _preparar_items(billings, db)
    arquivo = gerar_arquivo_cnab240(items)

    hoje = date.today().strftime("%Y%m%d")
    filename = f"remessa_cnab240_{hoje}.rem"
    return Response(
        content=arquivo,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _buscar_billings(
    billing_ids: list[int] | None,
    status: str,
    db: Session,
) -> list[Billing]:
    """Busca billings por IDs ou por status."""
    if billing_ids:
        return [
            b for b in [db.get(Billing, bid) for bid in billing_ids]
            if b and not b.is_deleted
        ]

    status_map = {
        "pendente": BillingStatus.PENDING,
        "pending":  BillingStatus.PENDING,
        "vencido":  BillingStatus.OVERDUE,
        "overdue":  BillingStatus.OVERDUE,
    }
    bs = status_map.get(status, BillingStatus.PENDING)
    return db.scalars(
        select(Billing)
        .where(Billing.status == bs, Billing.is_deleted.is_(False))
        .order_by(Billing.due_date.asc())
        .limit(500)
    ).all()


def _preparar_items(billings: list[Billing], db: Session) -> list[dict]:
    """Converte lista de Billing para lista de dicts para os geradores CNAB."""
    items = []
    for b in billings:
        c = db.get(Client, b.client_id)
        if not c or c.is_deleted:
            continue
        items.append(_billing_to_boleto_item(b, c))
    return items
