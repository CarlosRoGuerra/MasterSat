from fastapi import APIRouter

from app.api.v1.endpoints import (
    audit_logs,
    auth,
    billings,
    client_charge_items,
    clients,
    contracts,
    dashboard,
    integration_multiportal,
    documents,
    plans,
    service_products,
    service_orders,
    trackers,
    users,
    utils,
    vehicles,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix='/auth', tags=['auth'])
api_router.include_router(users.router, prefix='/users', tags=['users'])
api_router.include_router(utils.router, prefix='/utils', tags=['utils'])
api_router.include_router(clients.router, prefix='/clients', tags=['clients'])
api_router.include_router(vehicles.router, prefix='/vehicles', tags=['vehicles'])
api_router.include_router(trackers.router, prefix='/trackers', tags=['trackers'])
api_router.include_router(service_orders.router, prefix='/service-orders', tags=['service-orders'])
api_router.include_router(plans.router, prefix='/plans', tags=['plans'])
api_router.include_router(contracts.router, prefix='/contracts', tags=['contracts'])
api_router.include_router(billings.router, prefix='/billings', tags=['billings'])
api_router.include_router(dashboard.router, prefix='/dashboard', tags=['dashboard'])
api_router.include_router(documents.router, prefix='/documents', tags=['documents'])

api_router.include_router(integration_multiportal.router, prefix='/integrations/multiportal', tags=['integrations-multiportal'])

api_router.include_router(service_products.router, prefix='/service-products', tags=['service-products'])
api_router.include_router(client_charge_items.router, prefix='/client-charge-items', tags=['client-charge-items'])
api_router.include_router(audit_logs.router, prefix='/audit-logs', tags=['audit-logs'])
