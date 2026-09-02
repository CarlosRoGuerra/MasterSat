from datetime import datetime
from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import OrderPriority, OrderStatus, OrderType
from app.db.session import Base
class ServiceOrder(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__='service_orders'
    id: Mapped[int]=mapped_column(primary_key=True, index=True)
    number: Mapped[str]=mapped_column(String(30), unique=True, index=True)
    type: Mapped[OrderType]=mapped_column(Enum(OrderType))
    status: Mapped[OrderStatus]=mapped_column(Enum(OrderStatus), default=OrderStatus.OPEN)
    # server_default é o NOME do membro ('NORMAL'), não o .value ('normal') —
    # sa.Enum(PyEnumClass) grava .name no Postgres por padrão (confirmado nos
    # enums já existentes: orderstatus guarda 'OPEN', não 'aberta').
    priority: Mapped[OrderPriority]=mapped_column(Enum(OrderPriority), default=OrderPriority.NORMAL, nullable=False, server_default=OrderPriority.NORMAL.name)
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id'))
    vehicle_id: Mapped[int|None]=mapped_column(ForeignKey('vehicles.id'), nullable=True)
    tracker_id: Mapped[int|None]=mapped_column(ForeignKey('trackers.id'), nullable=True)
    technician_id: Mapped[int|None]=mapped_column(ForeignKey('users.id'), nullable=True)
    scheduled_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
    checklist: Mapped[dict|None]=mapped_column(JSON, nullable=True)
    observations: Mapped[str|None]=mapped_column(Text, nullable=True)
    # "Descrição do problema" (relatado na abertura) e "descrição do serviço
    # executado" (preenchida pelo técnico ao concluir) — campos deliberadamente
    # separados de `observations` (que segue recebendo notas de mudança de
    # status, como já fazia antes desta extensão).
    problem_description: Mapped[str|None]=mapped_column(Text, nullable=True)
    execution_description: Mapped[str|None]=mapped_column(Text, nullable=True)
    # Assinatura digital: a imagem em si é um Document (mesmo pipeline de
    # upload/MinIO de qualquer anexo) — aqui só a referência + carimbo de hora.
    technician_signature_document_id: Mapped[int|None]=mapped_column(ForeignKey('documents.id'), nullable=True)
    technician_signed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
    client_signature_document_id: Mapped[int|None]=mapped_column(ForeignKey('documents.id'), nullable=True)
    client_signed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
