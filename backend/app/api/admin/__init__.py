from fastapi import APIRouter, Depends

from app.api.admin import (
    auth,
    clients,
    dashboard,
    health,
    links,
    orders,
    products,
    refs,
    sync,
    users,
)
from app.api.deps import check_origin

router = APIRouter(prefix="/api/admin", dependencies=[Depends(check_origin)])
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(links.router)
router.include_router(clients.router)
router.include_router(orders.router)
router.include_router(products.router)
router.include_router(refs.router)
router.include_router(dashboard.router)
router.include_router(sync.router)
router.include_router(health.router)
