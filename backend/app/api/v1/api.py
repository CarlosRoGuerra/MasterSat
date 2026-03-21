from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    billings,
    client_portal,
    clients,
    contracts,
    dashboard,
    documents,
    plans,
    service_orders,
    trackers,
    users,
    vehicles,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix='/auth', tags=['auth'])
api_router.include_router(client_portal.router, prefix='/client-portal', tags=['client-portal'])
api_router.include_router(users.router, prefix='/users', tags=['users'])
api_router.include_router(clients.router, prefix='/clients', tags=['clients'])
api_router.include_router(vehicles.router, prefix='/vehicles', tags=['vehicles'])
api_router.include_router(trackers.router, prefix='/trackers', tags=['trackers'])
api_router.include_router(service_orders.router, prefix='/service-orders', tags=['service-orders'])
api_router.include_router(plans.router, prefix='/plans', tags=['plans'])
api_router.include_router(contracts.router, prefix='/contracts', tags=['contracts'])
api_router.include_router(billings.router, prefix='/billings', tags=['billings'])
api_router.include_router(dashboard.router, prefix='/dashboard', tags=['dashboard'])
api_router.include_router(documents.router, prefix='/documents', tags=['documents'])
