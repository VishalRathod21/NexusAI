"""NexusAI backend entry point.

Run:  uvicorn backend.main:app --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.routes import router
from backend.auth import AuthService
from backend.config.settings import Settings, get_settings
from backend.orchestrator.orchestrator import Orchestrator
from backend.service import ResearchService
from database.connection import Database
from database.repository import Repository


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # These log every HTTP request / full agent cards at INFO.
    for noisy in ("httpx", "a2a.client.card_resolver", "mcp.client.streamable_http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(settings: Settings | None = None, orchestrator: Orchestrator | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        db = Database(settings.database_url)
        await db.init()
        repository = Repository(db)
        interrupted = await repository.fail_interrupted()
        if interrupted:
            logging.getLogger(__name__).warning(
                "%d request(s) were interrupted by a restart and marked failed", interrupted
            )
        orch = orchestrator or Orchestrator(settings)
        app.state.service = ResearchService(settings, repository, orch)
        app.state.auth = AuthService(settings, repository)
        yield
        await app.state.service.wait_idle()
        await orch.close()
        await db.close()

    app = FastAPI(
        title="NexusAI",
        description="A2A-powered multi-agent business intelligence platform.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
        # Cookies are never accepted cross-origin: other origins must use an API key.
        allow_credentials=False,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def protect(request: Request, call_next):
        """CSRF check and security headers.

        A browser will not let another site add a custom header to a request
        without our CORS permission. Requiring one on every state-changing
        call that relies on the session cookie therefore blocks cross-site
        request forgery. API-key calls carry no cookie, so they are exempt.
        """
        unsafe = request.method in ("POST", "PUT", "PATCH", "DELETE")
        uses_cookie = "authorization" not in request.headers
        if (
            unsafe
            and uses_cookie
            and request.url.path.startswith("/api/")
            and request.headers.get("x-requested-with") != "nexusai"
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": {"code": "csrf", "message": "Missing X-Requested-With header."}},
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "orchestration_mode": settings.orchestration_mode,
            "llm": f"{settings.llm_provider}:{settings.resolved_model}",
            "database": settings.database_url.split(":", 1)[0],
        }

    return app


app = create_app()
