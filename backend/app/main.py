from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.config import settings
from .core.database import connect_to_mongo, close_mongo_connection
from .api.api import api_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Set up CORS - temporary hardcoded for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "message": "Welcome to CivicStream API",
        "version": settings.VERSION,
        "docs": f"{settings.API_V1_STR}/docs"
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION
    }


@app.get("/debug/cors")
async def debug_cors():
    return {
        "cors_origins": settings.BACKEND_CORS_ORIGINS,
        "cors_origins_type": type(settings.BACKEND_CORS_ORIGINS).__name__
    }


# Database events
@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()


@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()


# Include API routes
app.include_router(api_router, prefix=settings.API_V1_STR)