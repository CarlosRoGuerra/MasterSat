"""
Importa todos os modelos por efeito colateral, registrando-os no metadata do
SQLAlchemy. Útil para scripts standalone (fora do app) que fazem operações de
banco e precisam do grafo completo de FKs resolvido — senão o flush levanta
NoReferencedTableError ao encontrar uma FK cuja tabela-alvo não foi importada.

O app em si (main.py) já faz esse import; este módulo evita duplicá-lo nos
scripts.
"""
from app.models import (  # noqa: F401
    ailos_api_log,
    ailos_boleto,
    ailos_client_token,
    ailos_integration,
    ailos_lote,
    ailos_retorno_arquivo,
    audit_log,
    billing,
    billing_change_log,
    client,
    client_charge_item,
    closure_job,
    contract,
    document,
    integration_log,
    nfse_lote,
    nfse_nota,
    password_reset_token,
    payable,
    plan,
    service_order,
    service_order_status_log,
    service_product,
    system_setting,
    tracker,
    tracker_history,
    uninstall_event,
    user,
    vehicle,
)
