"""Linha do Tempo do Cliente — GET /api/v1/clients/{id}/timeline.

Agrega, por categoria, os eventos relevantes de um cliente a partir das
tabelas de domínio já existentes (Client, Vehicle, TrackerHistory, Contract,
Document, Billing, ServiceOrderStatusLog, AuditLog). Não existe — e não foi
criada — uma tabela de eventos dedicada: os 3 logs append-only já usados por
outras telas (TrackerHistory, ServiceOrderStatusLog, AuditLog) cobrem
rastreador/OS/auditoria com timestamp real e sem ambiguidade; contrato,
documento, veículo e cobrança não têm log de transição dedicado, então usam
o timestamp mais próximo disponível (`updated_at`/data de negócio) quando o
estado atual difere do estado inicial — esses casos ficam marcados como
"(aprox.)" na descrição do evento, nunca apresentados como precisos demais.

Mesmo padrão de app/services/global_search.py: uma função por categoria, uma
query cada (nenhuma delas dentro de um loop), RBAC aplicado aqui dentro (não
só no endpoint) via listas de roles por categoria — pedir uma categoria sem
permissão devolve lista vazia, não 403 (mesmo comportamento de
`CONTRACT_ROLES` em global_search.py).
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.document import Document
from app.models.enums import BillingStatus, DocumentReviewStatus, UserRole
from app.models.service_order import ServiceOrder
from app.models.service_order_status_log import ServiceOrderStatusLog
from app.models.tracker import Tracker
from app.models.tracker_history import TrackerHistory
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.client_timeline import TimelineCategory, TimelineEventOut, TimelineLinkOut
from app.schemas.pagination import Page

CONTRACT_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)
FINANCIAL_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)
AUDIT_ROLES = (UserRole.ADMIN,)

# Teto de segurança por categoria — mesmo espírito do `.limit(50)` que
# `client_timeline_pdf` já usa e do comentário em schemas/pagination.py
# ("não é paginação, é um limite de segurança"). Não pagina a tabela, pagina
# de verdade o feed que o usuário efetivamente vê (ver run_client_timeline).
CATEGORY_CAP = 300

ALL_CATEGORIES: tuple[TimelineCategory, ...] = (
    'cliente', 'veiculo', 'rastreador', 'contrato', 'documento', 'financeiro', 'os', 'auditoria',
)


def _dt(value: date | datetime) -> datetime:
    """Normaliza Date/DateTime pra datetime timezone-aware — a timeline
    ordena fontes diferentes juntas: colunas `DateTime(timezone=True)`
    (TimestampMixin) chegam aware no Postgres real (mas naive no SQLite dos
    testes), e várias colunas de origem são só Date (sem hora, sempre
    naive). Misturar aware com naive no mesmo sort() derruba com
    TypeError — por isso tudo aqui sempre ganha tzinfo (UTC como convenção
    neutra pras datas puras, que não têm fuso próprio de qualquer forma)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.combine(value, time(12, 0), tzinfo=timezone.utc)


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, 'value') else str(value)


def _client_events(client: Client) -> list[TimelineEventOut]:
    return [
        TimelineEventOut(
            id=f'client:{client.id}:created',
            category='cliente',
            type='client_created',
            occurred_at=_dt(client.created_at),
            title='Cliente cadastrado',
            description=f'{client.name} foi cadastrado no sistema.',
            severity='info',
            link=TimelineLinkOut(entity='client', id=client.id),
        )
    ]


def _vehicle_events(db: Session, client_id: int) -> list[TimelineEventOut]:
    rows = db.scalars(
        select(Vehicle)
        .where(Vehicle.client_id == client_id)
        .order_by(Vehicle.id.desc())
        .limit(CATEGORY_CAP)
    ).all()

    events: list[TimelineEventOut] = []
    for v in rows:
        label = ' '.join(part for part in (v.brand, v.model) if part)
        desc = f'{v.plate} — {label}' if label else v.plate
        events.append(TimelineEventOut(
            id=f'vehicle:{v.id}:added',
            category='veiculo',
            type='vehicle_added',
            occurred_at=_dt(v.created_at),
            title='Veículo adicionado',
            description=desc,
            severity='info',
            link=TimelineLinkOut(entity='vehicle', id=v.id, client_id=client_id),
        ))
        if v.is_deleted and v.updated_at != v.created_at:
            events.append(TimelineEventOut(
                id=f'vehicle:{v.id}:removed',
                category='veiculo',
                type='vehicle_removed',
                occurred_at=_dt(v.updated_at),
                title='Veículo removido',
                description=f'{desc} (data aprox. — última atualização do registro)',
                severity='warning',
            ))
    return events


# action -> (type, título, severidade). Os 8 valores reais gravados por
# _register_history() em api/v1/endpoints/trackers.py. Ação desconhecida
# (futura) cai num fallback genérico em vez de sumir da timeline.
_TRACKER_ACTION_META: dict[str, tuple[str, str, str]] = {
    'created': ('tracker_created', 'Rastreador cadastrado', 'info'),
    'linked': ('tracker_linked', 'Rastreador vinculado', 'success'),
    'unlinked': ('tracker_unlinked', 'Rastreador desvinculado', 'warning'),
    'transferred': ('tracker_transferred', 'Rastreador transferido', 'info'),
    'swapped_out': ('tracker_swapped_out', 'Rastreador removido (troca)', 'warning'),
    'swapped_in': ('tracker_swapped_in', 'Rastreador instalado (troca)', 'success'),
    'status_changed': ('tracker_status_changed', 'Status do rastreador alterado', 'info'),
    'deleted': ('tracker_deleted', 'Rastreador excluído', 'danger'),
}


def _tracker_events(db: Session, client_id: int) -> list[TimelineEventOut]:
    rows = db.scalars(
        select(TrackerHistory)
        .where(or_(TrackerHistory.previous_client_id == client_id, TrackerHistory.new_client_id == client_id))
        .order_by(TrackerHistory.id.desc())
        .limit(CATEGORY_CAP)
    ).all()
    if not rows:
        return []

    tracker_ids = {h.tracker_id for h in rows}
    vehicle_ids = {v for h in rows for v in (h.previous_vehicle_id, h.new_vehicle_id) if v}
    user_ids = {h.created_by_user_id for h in rows if h.created_by_user_id}

    imei_by_tracker = dict(db.execute(select(Tracker.id, Tracker.imei).where(Tracker.id.in_(tracker_ids))).all())
    plate_by_vehicle = (
        dict(db.execute(select(Vehicle.id, Vehicle.plate).where(Vehicle.id.in_(vehicle_ids))).all())
        if vehicle_ids else {}
    )
    name_by_user = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all())
        if user_ids else {}
    )

    events: list[TimelineEventOut] = []
    for h in rows:
        type_, title, severity = _TRACKER_ACTION_META.get(
            h.action, (f'tracker_{h.action}', h.action.replace('_', ' ').capitalize(), 'info'),
        )
        imei = imei_by_tracker.get(h.tracker_id, f'#{h.tracker_id}')
        vehicle_plate = plate_by_vehicle.get(h.new_vehicle_id) or plate_by_vehicle.get(h.previous_vehicle_id)
        desc = f'IMEI {imei}' + (f' — veículo {vehicle_plate}' if vehicle_plate else '')

        metadata: dict[str, str] = {}
        if h.previous_status or h.new_status:
            metadata['status'] = f'{h.previous_status or "-"} → {h.new_status or "-"}'
        if h.notes:
            metadata['notas'] = h.notes

        events.append(TimelineEventOut(
            id=f'tracker_history:{h.id}',
            category='rastreador',
            type=type_,
            occurred_at=_dt(h.event_date or h.created_at),
            title=title,
            description=desc,
            severity=severity,
            actor_name=name_by_user.get(h.created_by_user_id),
            link=TimelineLinkOut(
                entity='tracker', id=h.tracker_id, client_id=client_id,
                vehicle_id=h.new_vehicle_id or h.previous_vehicle_id,
            ),
            metadata=metadata or None,
        ))
    return events


def _contract_events(db: Session, client_id: int) -> list[TimelineEventOut]:
    rows = db.scalars(
        select(Contract)
        .where(or_(Contract.client_id == client_id, Contract.interveniente_client_id == client_id))
        .order_by(Contract.id.desc())
        .limit(CATEGORY_CAP)
    ).all()

    events: list[TimelineEventOut] = []
    for c in rows:
        link = TimelineLinkOut(entity='contract', id=c.id, client_id=c.client_id)
        events.append(TimelineEventOut(
            id=f'contract:{c.id}:created',
            category='contrato',
            type='contract_created',
            occurred_at=_dt(c.created_at),
            title='Contrato criado',
            description=f'Contrato #{c.id} — início em {c.start_date.strftime("%d/%m/%Y")}',
            severity='info',
            link=link,
        ))
        if c.signed and c.signed_at:
            events.append(TimelineEventOut(
                id=f'contract:{c.id}:signed',
                category='contrato',
                type='contract_signed',
                occurred_at=_dt(c.signed_at),
                title='Contrato assinado',
                description=f'Contrato #{c.id}',
                severity='success',
                link=link,
            ))
        if c.status != 'ativo' and c.updated_at != c.created_at:
            danger = c.status == 'cancelado'
            title = 'Contrato cancelado' if danger else f'Contrato — status: {c.status}'
            events.append(TimelineEventOut(
                id=f'contract:{c.id}:status',
                category='contrato',
                type='contract_status_changed',
                occurred_at=_dt(c.updated_at),
                title=title,
                description=f'Contrato #{c.id} (data aprox. — última atualização do registro)',
                severity='danger' if danger else 'warning',
                link=link,
            ))
    return events


_DOCUMENT_REVIEW_META: dict[DocumentReviewStatus, tuple[str, str]] = {
    DocumentReviewStatus.APPROVED: ('Documento aprovado', 'success'),
    DocumentReviewStatus.REJECTED: ('Documento rejeitado', 'danger'),
    DocumentReviewStatus.RESUBMISSION_REQUESTED: ('Reenvio de documento solicitado', 'warning'),
    DocumentReviewStatus.UNDER_REVIEW: ('Documento em análise', 'info'),
}


def _document_events(db: Session, client_id: int) -> list[TimelineEventOut]:
    vehicle_rows = db.execute(select(Vehicle.id, Vehicle.is_deleted).where(Vehicle.client_id == client_id)).all()
    order_rows = db.execute(select(ServiceOrder.id, ServiceOrder.is_deleted).where(ServiceOrder.client_id == client_id)).all()
    vehicle_ids = [vid for vid, _ in vehicle_rows]
    order_ids = [oid for oid, _ in order_rows]
    # Só o dono ATIVO vira id de navegação — um veículo/OS já excluído (soft
    # delete) não pode virar link morto no clique do evento (mesmo cuidado
    # de _search_documents em global_search.py). O documento continua
    # aparecendo na timeline, só sem o botão "Ver registro".
    active_vehicle_ids = {vid for vid, deleted in vehicle_rows if not deleted}
    active_order_ids = {oid for oid, deleted in order_rows if not deleted}

    conditions = [and_(Document.reference_type == 'client', Document.reference_id == client_id)]
    if vehicle_ids:
        conditions.append(and_(Document.reference_type == 'vehicle', Document.reference_id.in_(vehicle_ids)))
    if order_ids:
        conditions.append(and_(Document.reference_type == 'service_order', Document.reference_id.in_(order_ids)))

    rows = db.scalars(
        select(Document).where(or_(*conditions)).order_by(Document.id.desc()).limit(CATEGORY_CAP)
    ).all()

    events: list[TimelineEventOut] = []
    for d in rows:
        link: TimelineLinkOut | None = None
        if d.reference_type == 'client':
            link = TimelineLinkOut(entity='document', id=d.id, client_id=client_id)
        elif d.reference_type == 'vehicle' and d.reference_id in active_vehicle_ids:
            link = TimelineLinkOut(entity='document', id=d.id, vehicle_id=d.reference_id)
        elif d.reference_type == 'service_order' and d.reference_id in active_order_ids:
            link = TimelineLinkOut(entity='document', id=d.id, service_order_id=d.reference_id)
        events.append(TimelineEventOut(
            id=f'document:{d.id}:uploaded',
            category='documento',
            type='document_uploaded',
            occurred_at=_dt(d.created_at),
            title='Documento enviado',
            description=f'{d.file_name} ({d.category})',
            severity='info',
            link=link,
        ))
        if d.review_status != DocumentReviewStatus.SUBMITTED and d.updated_at != d.created_at:
            title, severity = _DOCUMENT_REVIEW_META.get(d.review_status, (f'Documento — {_enum_value(d.review_status)}', 'info'))
            desc = d.file_name + (f' — {d.review_notes}' if d.review_notes else '') + ' (data aprox.)'
            events.append(TimelineEventOut(
                id=f'document:{d.id}:review',
                category='documento',
                type='document_review_status_changed',
                occurred_at=_dt(d.updated_at),
                title=title,
                description=desc,
                severity=severity,
                link=link,
            ))
    return events


def _financial_events(db: Session, client_id: int) -> list[TimelineEventOut]:
    rows = db.scalars(
        select(Billing)
        .where(
            or_(Billing.client_id == client_id, Billing.payer_client_id == client_id),
            Billing.is_deleted.is_(False),
        )
        .order_by(Billing.id.desc())
        .limit(CATEGORY_CAP)
    ).all()

    events: list[TimelineEventOut] = []
    for b in rows:
        title = b.title or 'Cobrança'
        amount = f'R$ {float(b.amount):.2f}'
        due = b.due_date.strftime('%d/%m/%Y')
        events.append(TimelineEventOut(
            id=f'billing:{b.id}:created',
            category='financeiro',
            type='billing_created',
            occurred_at=_dt(b.created_at),
            title='Cobrança gerada',
            description=f'{title} — {amount} — venc. {due}',
            severity='info',
        ))
        if b.status == BillingStatus.PAID and b.payment_date:
            events.append(TimelineEventOut(
                id=f'billing:{b.id}:paid',
                category='financeiro',
                type='billing_paid',
                occurred_at=_dt(b.payment_date),
                title='Pagamento registrado',
                description=f'{title} — {amount}',
                severity='success',
            ))
        elif b.status == BillingStatus.OVERDUE:
            events.append(TimelineEventOut(
                id=f'billing:{b.id}:overdue',
                category='financeiro',
                type='billing_overdue',
                occurred_at=_dt(b.due_date),
                title='Boleto vencido',
                description=f'{title} — {amount} (venceu em {due})',
                severity='danger',
            ))
        elif b.status == BillingStatus.CANCELED and b.updated_at != b.created_at:
            events.append(TimelineEventOut(
                id=f'billing:{b.id}:canceled',
                category='financeiro',
                type='billing_canceled',
                occurred_at=_dt(b.updated_at),
                title='Cobrança cancelada',
                description=f'{title} — {amount} (data aprox.)',
                severity='warning',
            ))
    return events


def _service_order_events(db: Session, client_id: int) -> list[TimelineEventOut]:
    rows = db.execute(
        select(ServiceOrderStatusLog, ServiceOrder.number, ServiceOrder.id, ServiceOrder.vehicle_id)
        .join(ServiceOrder, ServiceOrder.id == ServiceOrderStatusLog.service_order_id)
        .where(ServiceOrder.client_id == client_id)
        .order_by(ServiceOrderStatusLog.id.desc())
        .limit(CATEGORY_CAP)
    ).all()
    if not rows:
        return []

    user_ids = {log.changed_by_id for log, *_ in rows if log.changed_by_id}
    name_by_user = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all())
        if user_ids else {}
    )

    events: list[TimelineEventOut] = []
    for log, number, so_id, vehicle_id in rows:
        prev = _enum_value(log.previous_status)
        new = _enum_value(log.new_status)
        if prev is None:
            type_, title, severity = 'service_order_created', 'OS criada', 'info'
        elif new == 'concluida':
            type_, title, severity = 'service_order_completed', 'OS concluída', 'success'
        elif new == 'cancelada':
            type_, title, severity = 'service_order_canceled', 'OS cancelada', 'danger'
        elif new == 'em_andamento':
            type_, title, severity = 'service_order_started', 'OS iniciada', 'info'
        else:
            type_, title, severity = 'service_order_status_changed', 'OS — status alterado', 'info'

        events.append(TimelineEventOut(
            id=f'service_order_status_log:{log.id}',
            category='os',
            type=type_,
            occurred_at=_dt(log.created_at),
            title=title,
            description=f'OS #{number}' + (f' — {log.notes}' if log.notes else ''),
            severity=severity,
            actor_name=name_by_user.get(log.changed_by_id),
            link=TimelineLinkOut(entity='service_order', id=so_id, client_id=client_id, vehicle_id=vehicle_id),
        ))
    return events


_AUDIT_SEVERITY = {'DELETE': 'danger', 'POST': 'success'}


def _audit_events(db: Session, client_id: int) -> list[TimelineEventOut]:
    # Correlaciona pelos ids atuais do cliente — um rastreador/veículo já
    # desvinculado deste cliente não entra aqui (a ação em si continua
    # visível nas categorias rastreador/veiculo, que não dependem do vínculo
    # atual). Suficiente pro objetivo desta categoria: "outras alterações
    # relevantes" que não têm log de domínio próprio.
    vehicle_ids = list(db.scalars(select(Vehicle.id).where(Vehicle.client_id == client_id)))
    tracker_ids = list(db.scalars(select(Tracker.id).where(Tracker.client_id == client_id)))
    contract_ids = list(db.scalars(
        select(Contract.id).where(or_(Contract.client_id == client_id, Contract.interveniente_client_id == client_id))
    ))
    order_ids = list(db.scalars(select(ServiceOrder.id).where(ServiceOrder.client_id == client_id)))

    conditions = [and_(AuditLog.entity_type == 'cliente', AuditLog.entity_id == client_id)]
    if vehicle_ids:
        conditions.append(and_(AuditLog.entity_type == 'veiculo', AuditLog.entity_id.in_(vehicle_ids)))
    if tracker_ids:
        conditions.append(and_(AuditLog.entity_type == 'rastreador', AuditLog.entity_id.in_(tracker_ids)))
    if contract_ids:
        conditions.append(and_(AuditLog.entity_type == 'contrato', AuditLog.entity_id.in_(contract_ids)))
    if order_ids:
        conditions.append(and_(AuditLog.entity_type == 'ordem_servico', AuditLog.entity_id.in_(order_ids)))

    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.method != 'GET', or_(*conditions))
        .order_by(AuditLog.id.desc())
        .limit(CATEGORY_CAP)
    ).all()

    return [
        TimelineEventOut(
            id=f'audit_log:{a.id}',
            category='auditoria',
            type='audit_action',
            occurred_at=_dt(a.created_at),
            title=a.description or f'{a.method} {a.path}',
            severity=_AUDIT_SEVERITY.get(a.method, 'info'),
            actor_name=a.user_name,
        )
        for a in rows
    ]


def run_client_timeline(
    db: Session,
    role: UserRole,
    client: Client,
    category: TimelineCategory | None,
    skip: int,
    limit: int,
) -> Page[TimelineEventOut]:
    wanted = (category,) if category else ALL_CATEGORIES

    events: list[TimelineEventOut] = []
    if 'cliente' in wanted:
        events += _client_events(client)
    if 'veiculo' in wanted:
        events += _vehicle_events(db, client.id)
    if 'rastreador' in wanted:
        events += _tracker_events(db, client.id)
    if 'contrato' in wanted and role in CONTRACT_ROLES:
        events += _contract_events(db, client.id)
    if 'documento' in wanted:
        events += _document_events(db, client.id)
    if 'financeiro' in wanted and role in FINANCIAL_ROLES:
        events += _financial_events(db, client.id)
    if 'os' in wanted:
        events += _service_order_events(db, client.id)
    if 'auditoria' in wanted and role in AUDIT_ROLES:
        events += _audit_events(db, client.id)

    events.sort(key=lambda e: e.occurred_at, reverse=True)
    total = len(events)
    return Page[TimelineEventOut](items=events[skip: skip + limit], total=total)
