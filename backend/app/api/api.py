from fastapi import APIRouter

from .endpoints import workflows, instances, documents, admin, auth_keycloak, public, plugins, categories, teams, themes, assignments
from .v1 import performance, entities

api_router = APIRouter()

api_router.include_router(auth_keycloak.router, prefix="/auth", tags=["authentication"])
api_router.include_router(public.router, prefix="/public", tags=["public"])
api_router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
api_router.include_router(instances.router, prefix="/instances", tags=["instances"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(plugins.router, prefix="/plugins", tags=["plugins"])
api_router.include_router(categories.router, prefix="/categories", tags=["categories"])
api_router.include_router(teams.router, prefix="/teams", tags=["teams"])
api_router.include_router(themes.router, prefix="/themes", tags=["themes"])
api_router.include_router(performance.router, prefix="/performance", tags=["performance"])
api_router.include_router(entities.router, prefix="/entities", tags=["entities"])
api_router.include_router(assignments.router, prefix="/assignments", tags=["assignments"])