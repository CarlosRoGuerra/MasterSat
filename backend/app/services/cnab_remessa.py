"""Remessa CNAB 240/400 como registro controlado (Fase 03, FIN-07).

Antes, ``POST /boletos/cnab240`` era um download direto: aceitava cobrança paga,
cancelada, ID repetido e cobrança já registrada pela API Ailos; toda remessa
saía com sequência 1 e nada ficava gravado. Agora:

* o canal fica DESLIGADO até homologação com o banco
  (``CNAB_REMESSA_HABILITADA``) — não há leitura de retorno CNAB e o layout
  nunca foi validado pelo banco, então a tela não o apresenta como emissão;
* ligado, só entra cobrança em aberto, sem repetição e sem título em nenhum
  canal (mesma política da API: ``titulo_bancario.EMITIR_CNAB``);
* cada remessa grava sequência própria por layout, hash e conteúdo; os títulos
  ficam ``reservado`` e a emissão pela API Ailos passa a recusá-los;
* remessa não enviada ao banco pode ser descartada (administrador, com
  motivo), liberando os títulos. Nada é apagado.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.billing import Billing
from app.models.cnab_remessa import CnabRemessa, CnabRemessaItem
from app.models.enums import BillingStatus
from app.services import titulo_bancario
from app.services.boleto_ailos import gerar_nosso_numero
from app.services.cnab240 import gerar_arquivo_cnab240
from app.services.cnab400 import gerar_arquivo_cnab400
from app.services.financial import lock_billings_for_update

LAYOUTS = {'240': gerar_arquivo_cnab240, '400': gerar_arquivo_cnab400}
_ABERTAS = (BillingStatus.PENDING, BillingStatus.OVERDUE)


class RemessaError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 409, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}

    def detail(self) -> dict:
        return {'code': self.code, 'message': self.message, **self.extra}


def canal_habilitado() -> bool:
    return bool(settings.cnab_remessa_habilitada)


def exigir_canal_habilitado() -> None:
    if not canal_habilitado():
        raise RemessaError(
            'canal_cnab_indisponivel',
            'Remessa CNAB indisponível: o layout não foi homologado com o banco e o sistema '
            'não lê o arquivo de retorno CNAB, então títulos enviados por remessa não seriam '
            'conciliados. Emita pela API Ailos (aba Ailos). O canal é liberado só depois da '
            'homologação (CNAB_REMESSA_HABILITADA).',
        )


def validar_selecao(db: Session, billing_ids: list[int]) -> list[Billing]:
    """Trava e valida uma seleção explícita. Recusa tudo de uma vez, listando
    cada motivo — nunca gera remessa parcial em silêncio."""
    repetidos = sorted({bid for bid in billing_ids if billing_ids.count(bid) > 1})
    if repetidos:
        raise RemessaError(
            'selecao_invalida', 'A seleção repete cobranças.', status_code=422,
            extra={'repetidos': repetidos},
        )
    locked = {b.id: b for b in lock_billings_for_update(db, billing_ids)}
    motivos: dict[int, str] = {}
    for bid in billing_ids:
        billing = locked.get(bid)
        if billing is None or billing.is_deleted:
            motivos[bid] = 'nao_encontrada'
        elif billing.status not in _ABERTAS:
            motivos[bid] = f'status_{billing.status.value}'
    validos = [bid for bid in billing_ids if bid not in motivos]
    for bid, titulo in titulo_bancario.titulos_bancarios(db, validos).items():
        if titulo.estado != titulo_bancario.SEM_TITULO:
            motivos[bid] = f'titulo_{titulo.estado}'
    if motivos:
        raise RemessaError(
            'selecao_invalida',
            'Há cobranças que não podem entrar na remessa: só cobrança em aberto e sem título '
            'no banco (nem pela API Ailos nem em outra remessa).',
            extra={'motivos': {str(k): v for k, v in sorted(motivos.items())}},
        )
    return [locked[bid] for bid in billing_ids]


def selecionar_por_status(db: Session, status: BillingStatus, limite: int = 500) -> list[Billing]:
    """Seleção automática: em aberto, sem título em nenhum canal."""
    candidatas = db.scalars(
        select(Billing.id)
        .where(Billing.status == status, Billing.is_deleted.is_(False))
        .order_by(Billing.due_date.asc(), Billing.id.asc())
        .limit(limite * 2)
    ).all()
    titulos = titulo_bancario.titulos_bancarios(db, candidatas)
    livres = [bid for bid in candidatas if titulos[bid].estado == titulo_bancario.SEM_TITULO][:limite]
    if not livres:
        return []
    return validar_selecao(db, livres)


@dataclass
class RemessaGerada:
    remessa: CnabRemessa
    arquivo: bytes


def gerar_remessa(
    db: Session, layout: str, billings: list[Billing], itens: list[dict], *, user_id: int | None,
) -> RemessaGerada:
    """Grava a remessa (sequência, hash, conteúdo, itens reservados) e devolve
    os bytes. Corrida com outra remessa vira 409, não arquivo duplicado."""
    sequencial = (db.scalar(
        select(func.max(CnabRemessa.sequencial)).where(CnabRemessa.layout == layout)
    ) or 0) + 1
    arquivo = LAYOUTS[layout](itens, seq_arquivo=sequencial)
    remessa = CnabRemessa(
        layout=layout,
        sequencial=sequencial,
        arquivo_sha256=hashlib.sha256(arquivo).hexdigest(),
        arquivo=arquivo,
        total_titulos=len(itens),
        valor_total=sum((Decimal(str(item['valor'])) for item in itens), Decimal('0.00')),
        status='gerada',
        generated_by_user_id=user_id,
    )
    db.add(remessa)
    try:
        db.flush()
        por_id = {b.id: b for b in billings}
        for item in itens:
            billing = por_id[item['billing_id']]
            nosso_numero, _dv = gerar_nosso_numero(billing.id)
            db.add(CnabRemessaItem(
                remessa_id=remessa.id,
                billing_id=billing.id,
                valor=billing.amount,
                vencimento=billing.due_date,
                nosso_numero=nosso_numero,
                status='reservado',
            ))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RemessaError(
            'remessa_concorrente',
            'Outra remessa foi gerada ao mesmo tempo com a mesma sequência ou os mesmos títulos. '
            'Atualize a lista de remessas e gere de novo se ainda for necessário.',
        ) from exc
    db.refresh(remessa)
    return RemessaGerada(remessa=remessa, arquivo=arquivo)


def descartar_remessa(db: Session, remessa_id: int, *, motivo: str) -> CnabRemessa:
    remessa = db.scalar(select(CnabRemessa).where(CnabRemessa.id == remessa_id).with_for_update())
    if remessa is None:
        raise RemessaError('remessa_nao_encontrada', 'Remessa não encontrada.', status_code=404)
    if remessa.status == 'descartada':
        return remessa
    remessa.status = 'descartada'
    remessa.descartada_em = titulo_bancario.agora()
    remessa.descarte_motivo = motivo
    for item in db.scalars(select(CnabRemessaItem).where(CnabRemessaItem.remessa_id == remessa.id)).all():
        item.status = 'liberado'
    db.commit()
    db.refresh(remessa)
    return remessa
