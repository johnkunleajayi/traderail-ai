from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.sessions import SessionMiddleware

from app.api.health import router as health_router
from app.market_intelligence.router import router as intelligence_router
from app.market_regimes.router import router as regimes_router
from app.assets.router import router as assets_router
from app.instruments.router import router as instruments_router
from app.auth.oauth import create_oauth
from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.portfolio.router import router as portfolio_router
from app.venues.router import router as venues_router
from app.market_data.router import router as market_data_router
from app.market_data.candle_router import router as candle_router
from app.market_data.store_router import router as store_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(title=settings.app_name, version="0.1.0")
    application.state.settings = settings
    application.state.oauth = create_oauth(settings)
    if settings.auth_enabled:
        # Only temporary OAuth state is signed here. Login sessions live in PostgreSQL.
        application.add_middleware(
            SessionMiddleware, secret_key=settings.session_secret.get_secret_value(),
            session_cookie="traderail_oauth", max_age=600, same_site="lax",
            https_only=settings.session_cookie_secure,
        )
    application.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
    )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        return JSONResponse({"detail": "Database is temporarily unavailable"}, status_code=503,
                            headers={"Cache-Control": "no-store"})

    @application.middleware("http")
    async def auth_response_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/auth/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    application.include_router(health_router, prefix="/api")
    application.include_router(auth_router, prefix="/api")
    application.include_router(portfolio_router, prefix="/api")
    application.include_router(assets_router, prefix="/api")
    application.include_router(instruments_router, prefix="/api")
    application.include_router(venues_router, prefix="/api")
    application.include_router(market_data_router, prefix="/api")
    application.include_router(candle_router, prefix="/api")
    application.include_router(store_router, prefix="/api")
    application.include_router(intelligence_router, prefix="/api")
    application.include_router(regimes_router, prefix="/api")
    return application


app = create_app()