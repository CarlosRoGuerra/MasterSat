from datetime import date
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus, UserRole
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.schemas.contract import ContractCreate, ContractOut, ContractUpdate
from app.services.financial import cancel_open_billings_for_contract, charge_item_effective_billing_count, refresh_overdue_statuses

router = APIRouter()


def serialize_contract(
    db: Session,
    contract: Contract,
    *,
    client_map: dict[int, Client] | None = None,
    plan_map: dict[int, Plan] | None = None,
    vehicle_map: dict[int, Vehicle] | None = None,
    tracker_map: dict[int, Tracker] | None = None,
    billing_agg_map: dict[int, tuple[int, date | None]] | None = None,
) -> ContractOut:
    # Mapas opcionais: quem lista vários contratos de uma vez (list_items) monta
    # tudo antes com queries em lote (IN/GROUP BY) e passa aqui — evita N+1.
    # Chamadas com um único contrato (create/update/get) seguem sem mapa e
    # fazem as queries avulsas de sempre.
    vehicle_id = getattr(contract, 'vehicle_id', None)
    tracker_id = getattr(contract, 'tracker_id', None)
    client = client_map.get(contract.client_id) if client_map is not None else db.get(Client, contract.client_id)
    plan = plan_map.get(contract.plan_id) if plan_map is not None else db.get(Plan, contract.plan_id)
    if vehicle_id:
        vehicle = vehicle_map.get(vehicle_id) if vehicle_map is not None else db.get(Vehicle, vehicle_id)
    else:
        vehicle = None
    if tracker_id:
        tracker = tracker_map.get(tracker_id) if tracker_map is not None else db.get(Tracker, tracker_id)
    else:
        tracker = None
    if billing_agg_map is not None:
        open_billings, next_due_date = billing_agg_map.get(contract.id, (0, None))
    else:
        open_billings = (
            db.query(func.count(Billing.id))
            .filter(
                Billing.is_deleted == False,
                Billing.contract_id == contract.id,
                Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            )
            .scalar()
            or 0
        )
        next_due = (
            db.query(Billing.due_date)
            .filter(
                Billing.is_deleted == False,
                Billing.contract_id == contract.id,
                Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            )
            .order_by(Billing.due_date.asc())
            .first()
        )
        next_due_date = next_due[0] if next_due else None
    return ContractOut(
        id=contract.id,
        client_id=contract.client_id,
        interveniente_client_id=getattr(contract, 'interveniente_client_id', None),
        plan_id=contract.plan_id,
        vehicle_id=getattr(contract, 'vehicle_id', None),
        tracker_id=getattr(contract, 'tracker_id', None),
        start_date=contract.start_date,
        end_date=contract.end_date,
        status=contract.status,
        billing_day=contract.billing_day,
        payment_method=contract.payment_method,
        bank=getattr(contract, 'bank', None),
        notes=contract.notes,
        installation_fee=float(contract.installation_fee) if contract.installation_fee is not None else None,
        uninstall_fee=float(contract.uninstall_fee) if contract.uninstall_fee is not None else None,
        signed=bool(getattr(contract, 'signed', False)),
        signed_at=getattr(contract, 'signed_at', None),
        client_name=client.name if (client and not client.is_deleted) else None,
        plan_name=plan.name if (plan and not plan.is_deleted) else None,
        vehicle_plate=vehicle.plate if (vehicle and not vehicle.is_deleted) else None,
        tracker_identifier=(tracker.imei if (tracker and not tracker.is_deleted) else None),
        monthly_value=float(plan.price) if (plan and not plan.is_deleted) else None,
        open_billings=open_billings,
        next_due_date=next_due_date,
    )


def validate_links(db: Session, client_id: int, vehicle_id: int | None, tracker_id: int | None):
    if vehicle_id:
        vehicle = db.get(Vehicle, vehicle_id)
        if not vehicle or vehicle.is_deleted:
            raise HTTPException(status_code=404, detail='Veículo não encontrado')
        if vehicle.client_id != client_id:
            raise HTTPException(status_code=400, detail='O veículo selecionado não pertence ao cliente informado.')
    if tracker_id:
        tracker = db.get(Tracker, tracker_id)
        if not tracker or tracker.is_deleted:
            raise HTTPException(status_code=404, detail='Rastreador não encontrado')
        if tracker.client_id and tracker.client_id != client_id:
            raise HTTPException(status_code=400, detail='O rastreador selecionado não pertence ao cliente informado.')
        if vehicle_id and tracker.vehicle_id and tracker.vehicle_id != vehicle_id:
            raise HTTPException(status_code=400, detail='O rastreador selecionado está vinculado a outro veículo.')


@router.get('/', response_model=list[ContractOut])
def list_items(
    client_id: int | None = None,
    interveniente_client_id: int | None = None,
    plan_id: int | None = None,
    vehicle_id: int | None = None,
    tracker_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, le=300),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL)),
):
    refresh_overdue_statuses(db)
    query = db.query(Contract).filter(Contract.is_deleted == False)
    if client_id:
        query = query.filter(Contract.client_id == client_id)
    if interveniente_client_id:
        query = query.filter(Contract.interveniente_client_id == interveniente_client_id)
    if plan_id:
        query = query.filter(Contract.plan_id == plan_id)
    if vehicle_id:
        query = query.filter(Contract.vehicle_id == vehicle_id)
    if tracker_id:
        query = query.filter(Contract.tracker_id == tracker_id)
    if status:
        query = query.filter(Contract.status == status)
    if search:
        term = f'%{search.strip()}%'
        client_ids = db.scalars(
            select(Client.id).where(Client.name.ilike(term), Client.is_deleted.is_(False))
        ).all()
        vehicle_ids = db.scalars(
            select(Vehicle.id).where(Vehicle.plate.ilike(term), Vehicle.is_deleted.is_(False))
        ).all()
        from sqlalchemy import or_
        query = query.filter(
            or_(
                Contract.client_id.in_(client_ids),
                Contract.vehicle_id.in_(vehicle_ids),
            )
        )
    items = query.order_by(Contract.created_at.desc()).limit(limit).all()

    client_ids = {c.client_id for c in items if c.client_id}
    plan_ids = {c.plan_id for c in items if c.plan_id}
    vehicle_ids = {c.vehicle_id for c in items if getattr(c, 'vehicle_id', None)}
    tracker_ids = {c.tracker_id for c in items if getattr(c, 'tracker_id', None)}
    contract_ids = [c.id for c in items]

    client_map = {o.id: o for o in db.scalars(select(Client).where(Client.id.in_(client_ids))).all()} if client_ids else {}
    plan_map = {o.id: o for o in db.scalars(select(Plan).where(Plan.id.in_(plan_ids))).all()} if plan_ids else {}
    vehicle_map = {o.id: o for o in db.scalars(select(Vehicle).where(Vehicle.id.in_(vehicle_ids))).all()} if vehicle_ids else {}
    tracker_map = {o.id: o for o in db.scalars(select(Tracker).where(Tracker.id.in_(tracker_ids))).all()} if tracker_ids else {}

    billing_agg_map: dict[int, tuple[int, date | None]] = {}
    if contract_ids:
        rows = (
            db.query(Billing.contract_id, func.count(Billing.id), func.min(Billing.due_date))
            .filter(
                Billing.is_deleted == False,
                Billing.contract_id.in_(contract_ids),
                Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            )
            .group_by(Billing.contract_id)
            .all()
        )
        billing_agg_map = {contract_id: (count, min_due) for contract_id, count, min_due in rows}

    return [
        serialize_contract(
            db, item,
            client_map=client_map,
            plan_map=plan_map,
            vehicle_map=vehicle_map,
            tracker_map=tracker_map,
            billing_agg_map=billing_agg_map,
        )
        for item in items
    ]


@router.post('/', response_model=ContractOut)
def create_item(payload: ContractCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    client = db.get(Client, payload.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    plan = db.get(Plan, payload.plan_id)
    if not plan or plan.is_deleted or not plan.active:
        raise HTTPException(status_code=404, detail='Plano não encontrado ou inativo')
    validate_links(db, payload.client_id, payload.vehicle_id, payload.tracker_id)
    data = payload.model_dump()
    if not data.get('billing_day'):
        data['billing_day'] = payload.start_date.day if payload.start_date.day <= 28 else 28
    obj = Contract(**data)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return serialize_contract(db, obj)



class ContractGeneratePayload(BaseModel):
    plan_id: int
    start_date: date | None = None
    end_date: date | None = None
    installation_fee: float | None = None
    uninstall_fee: float | None = None


@router.post('/generate-pdf')
def generate_contract_pdf(
    payload: ContractGeneratePayload,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL)),
):
    """Gera o TERMO/CONTRATO em branco (só plano, vigência e taxas) para o
    cliente preencher e assinar. NÃO salva contrato nem preenche dados do cliente."""
    plan = db.get(Plan, payload.plan_id)
    if not plan or plan.is_deleted or not plan.active:
        raise HTTPException(status_code=404, detail='Plano não encontrado ou inativo')
    contrato = SimpleNamespace(
        id='', start_date=payload.start_date, end_date=payload.end_date,
        billing_day=None, installation_fee=payload.installation_fee,
        uninstall_fee=payload.uninstall_fee,
    )
    cliente_branco = SimpleNamespace(
        name='', cpf_cnpj='', address_line='', address_number='', neighborhood='',
        zip_code='', city='', state='', phone='', email='', rg_ie='', birth_date=None,
        emergency_contacts=[],
    )
    from app.services.contract_pdf import gerar_contrato_pdf
    pdf = gerar_contrato_pdf(contrato, cliente_branco, plan, None)
    return Response(
        content=pdf,
        media_type='application/pdf',
        headers={'Content-Disposition': 'inline; filename="contrato-modelo.pdf"'},
    )


@router.post('/validate-signed')
async def validate_signed_contract(
    client_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL)),
):
    """Confere, quando dá, se o PDF assinado enviado parece o contrato do cliente.

    Não bloqueia nada — só devolve um parecer para a tela avisar o operador.
    """
    client = db.get(Client, client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    data = await file.read()
    from app.services.contract_check import verificar_contrato_assinado
    return verificar_contrato_assinado(data, file.content_type or '', client)


@router.get('/{item_id}', response_model=ContractOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(Contract, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Contrato não encontrado')
    return serialize_contract(db, obj)


@router.put('/{item_id}', response_model=ContractOut)
def update_item(item_id: int, payload: ContractUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(Contract, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Contrato não encontrado')
    data = payload.model_dump(exclude_unset=True)
    client_id = data.get('client_id', obj.client_id)
    if 'client_id' in data:
        client = db.get(Client, client_id)
        if not client or client.is_deleted:
            raise HTTPException(status_code=404, detail='Cliente não encontrado')
    if 'plan_id' in data:
        plan = db.get(Plan, data['plan_id'])
        if not plan or plan.is_deleted or not plan.active:
            raise HTTPException(status_code=404, detail='Plano não encontrado ou inativo')
    validate_links(db, client_id, data.get('vehicle_id', getattr(obj, 'vehicle_id', None)), data.get('tracker_id', getattr(obj, 'tracker_id', None)))
    # Marcar como assinado sem informar a data carimba hoje; desmarcar limpa a
    # data, senão fica um contrato "não assinado" com data de assinatura.
    if data.get('signed') and not data.get('signed_at') and not obj.signed_at:
        data['signed_at'] = date.today()
    if data.get('signed') is False:
        data['signed_at'] = None
    for key, value in data.items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return serialize_contract(db, obj)


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    # O contrato é o recurso que serializa exclusão e fechamento financeiro.
    # Em PostgreSQL, uma geração concorrente que tomou o mesmo row lock termina
    # antes desta revalidação; em SQLite o modificador é deliberadamente no-op.
    obj = db.scalar(
        select(Contract)
        .where(Contract.id == item_id)
        .with_for_update()
    )
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Contrato não encontrado')
    active_items = db.scalars(
        select(ClientChargeItem)
        .where(
            ClientChargeItem.contract_id == obj.id,
            ClientChargeItem.is_deleted.is_(False),
            ClientChargeItem.active.is_(True),
        )
        .order_by(ClientChargeItem.id.asc())
    ).all()
    pending_item = next((
        item for item in active_items
        if charge_item_effective_billing_count(db, item.id)
        < max(int(item.installment_count or 1), 1)
    ), None)
    if pending_item:
        raise HTTPException(
            status_code=409,
            detail=(
                f'Contrato possui lançamento ativo ainda não faturado '
                f'(#{pending_item.id}). Fature ou remova o lançamento antes de excluir o contrato.'
            ),
        )
    try:
        boletos_ativos = cancel_open_billings_for_contract(db, obj.id, f'contrato #{obj.id} excluído')
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    obj.is_deleted = True
    obj.status = 'cancelado'
    db.commit()
    response = {'message': 'Contrato removido com sucesso'}
    if boletos_ativos:
        response['boletos_ativos'] = boletos_ativos
    return response


@router.get('/{item_id}/pdf')
def contract_pdf(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    """Gera o PDF do contrato (TERMO DE ADESÃO + cláusulas) no padrão MasterSat."""
    obj = db.get(Contract, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Contrato não encontrado')
    client = db.get(Client, obj.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente do contrato não encontrado')
    plan = db.get(Plan, obj.plan_id)
    vehicle = db.get(Vehicle, obj.vehicle_id) if getattr(obj, 'vehicle_id', None) else None
    from app.services.contract_pdf import gerar_contrato_pdf
    pdf = gerar_contrato_pdf(obj, client, plan, vehicle)
    return Response(
        content=pdf,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="contrato_{obj.id}.pdf"'},
    )
