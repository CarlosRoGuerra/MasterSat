from enum import Enum


class UserRole(str, Enum):
    ADMIN = 'admin'
    OPERATIONAL = 'operacional'
    FINANCIAL = 'financeiro'
    CLIENT = 'cliente'


class ClientStatus(str, Enum):
    ACTIVE = 'ativo'
    INACTIVE = 'inativo'
    DELINQUENT = 'inadimplente'
    SUSPENDED = 'suspenso'


class VehicleStatus(str, Enum):
    ACTIVE = 'ativo'
    NO_TRACKER = 'sem_rastreador'
    REMOVED = 'retirado'
    BLOCKED = 'bloqueado'


class TrackerStatus(str, Enum):
    STOCK = 'em_estoque'
    INSTALLED = 'instalado'
    MAINTENANCE = 'em_manutencao'
    LOST = 'extraviado'
    DISCARDED = 'descartado'


class OrderType(str, Enum):
    INSTALL = 'instalacao'
    MAINTENANCE = 'manutencao'
    REMOVAL = 'retirada'
    TECH_VISIT = 'visita_tecnica'


class OrderStatus(str, Enum):
    OPEN = 'aberta'
    IN_PROGRESS = 'em_andamento'
    COMPLETED = 'concluida'
    CANCELED = 'cancelada'


class BillingStatus(str, Enum):
    PENDING = 'pendente'
    PAID = 'paga'
    OVERDUE = 'vencida'
    CANCELED = 'cancelada'
