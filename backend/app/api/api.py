from fastapi import APIRouter

from .endpoints import workflows, instances
from .v1 import performance

api_router = APIRouter()

api_router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
api_router.include_router(instances.router, prefix="/instances", tags=["instances"])
api_router.include_router(performance.router, prefix="/performance", tags=["performance"])