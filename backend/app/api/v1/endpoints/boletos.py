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
import unicodedata
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import settings
from app.db.session import get_db
from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, UserRole
from app.models.vehicle import Vehicle
from app.services import ailos_boletos
from app.services.ailos_boletos import aplicar_dados_oficiais_ailos
from app.services.boleto_ailos import gerar_dados_boleto, DadosBoleto
from app.services.boleto_pdf import gerar_boleto_pdf, gerar_carne_pdf
from app.services.cnab400 import gerar_arquivo_cnab400
from app.services.cnab240 import gerar_arquivo_cnab240

router = APIRouter()

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
}


def descricao_servico(b: Billing, placa: str | None = None) -> str:
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
    if b.period_label:
        partes.append(f'REF. {b.period_label}')
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


def _placa_do_billing(b: Billing, db: Session) -> str:
    if b.vehicle_id:
        veiculo = db.get(Vehicle, b.vehicle_id)
        if veiculo and not veiculo.is_deleted:
            return veiculo.plate or ""
    return ""


def dados_boleto(b: Billing, c: Client, db: Session, ailos_boleto: AilosBoleto) -> DadosBoleto:
    """
    Monta o DadosBoleto de uma cobrança, com os dados oficiais da Ailos
    aplicados. Compartilhado pelo boleto avulso e por cada parcela do carnê.
    """
    item = _billing_to_boleto_item(b, c)
    placa = _placa_do_billing(b, db) or None
    servico = descricao_servico(b, placa)
    # Na linha "Referente a" a placa fica em instrução própria, na linha de
    # baixo — não misturada ao resto do texto (item["itens"] mantém o
    # descritivo completo, com placa, para a tabela do boleto).
    servico_sem_placa = descricao_servico(b, None)
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
        itens=[(servico, float(b.amount))],
        instrucoes=[
            f"Referente a: {servico_sem_placa}.",
            *([f"Placa: {placa}"] if placa else []),
            "Não receber após o vencimento.",
            "Após vencimento entrar em contato: contato@mastersat.com.br",
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
    c = _pagador_do_billing(b, db)
    pdf_bytes, filename = _montar_pdf_boleto(b, c, db, ailos_boleto)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


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
        if ab is None:
            continue
        b = db.get(Billing, billing_id)
        if not b or b.is_deleted:
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
        dono = db.get(Client, b.client_id)
        if not dono or dono.is_deleted:
            continue
        # Remessa CNAB registra o título — o pagador é o interveniente, se houver.
        c = ailos_boletos.resolver_pagador(db, b, dono)
        items.append(_billing_to_boleto_item(b, c))
    return items
