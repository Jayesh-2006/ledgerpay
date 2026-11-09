from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app.api import transfers, wallets
from app.database import configured_url, make_engine
from app.errors import register_error_handlers
from app.schemas import ErrorResponse


def create_app(database_url: URL | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        url = database_url if database_url is not None else configured_url("DATABASE_URL")
        engine = make_engine(url)
        app.state.session_factory = sessionmaker(engine)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="LedgerPay",
        version="1.0.0",
        description="Simulated INR wallet and payment API. All amounts are integer paise.",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    api = APIRouter(
        prefix="/api/v1",
        responses={
            422: {"model": ErrorResponse, "description": "Request validation failed"},
            500: {"model": ErrorResponse, "description": "Internal server error"},
            503: {"model": ErrorResponse, "description": "Database operation unavailable"},
        },
    )
    api.include_router(wallets.router)
    api.include_router(transfers.router)
    app.include_router(api)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Report process liveness."""
        return {"status": "ok"}

    return app


app = create_app()
