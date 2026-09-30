"""Capacidades por perfil — fonte única da política de acesso a dados.

Antes, cada endpoint tinha sua própria tupla de perfis (VIEW_ROLES,
ALLOWED_ROLES, CONTRACT_ROLES...). Resultado (SEC-01): /billings e
/reports negavam o operacional, mas /exports/billings, o PDF da linha do
tempo e o dashboard entregavam os mesmos valores a ele. Uma capacidade
nomeia O DADO protegido; todo canal que entrega esse dado — JSON, CSV,
XLSX, PDF — consulta a mesma capacidade, e o conteúdo é filtrado no
backend (o frontend só esconde botões).

Matriz vigente (docs/seguranca/matriz-capacidades.md):

  capacidade            admin  operacional  financeiro  cliente
  REGISTRY_READ          sim       sim          sim        não
  FINANCIAL_READ         sim       não          sim        não
  AUDIT_READ             sim       não          não        não

FINANCIAL_READ cobre valores, títulos, pagamentos, contratos e montantes
em atraso. Contagens de status de CLIENTE (ex.: quantos clientes estão
inadimplentes) não são dado financeiro: o operacional usa isso para
bloqueio/desinstalação e continua vendo.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from app.models.enums import UserRole


class Capability(str, Enum):
    REGISTRY_READ = 'registry:read'
    FINANCIAL_READ = 'financial:read'
    AUDIT_READ = 'audit:read'


CAPABILITY_ROLES: dict[Capability, tuple[UserRole, ...]] = {
    Capability.REGISTRY_READ: (UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL),
    Capability.FINANCIAL_READ: (UserRole.ADMIN, UserRole.FINANCIAL),
    Capability.AUDIT_READ: (UserRole.ADMIN,),
}


def roles_with(capability: Capability) -> tuple[UserRole, ...]:
    return CAPABILITY_ROLES[capability]


def has_capability(role: UserRole | str | None, capability: Capability) -> bool:
    if role is None:
        return False
    try:
        role = UserRole(role)
    except ValueError:
        return False
    return role in CAPABILITY_ROLES[capability]


def require_capability(capability: Capability) -> Callable:
    """Dependency FastAPI: 403 para perfil sem a capacidade (401 sem login)."""
    from app.api.deps import require_roles

    return require_roles(*roles_with(capability))
