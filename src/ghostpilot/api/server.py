"""FastAPI control plane server factory."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from ghostpilot.api.routes_hitl import HITLStateManager
from ghostpilot.api.routes_hitl import router as hitl_router
from ghostpilot.config import Settings
from ghostpilot.models.schemas import SessionState


def create_app(
    settings: Settings,
    telegram_notifier: Callable[[Any], Any] | None = None,
    session_state: SessionState | None = None,
) -> FastAPI:
    """Create and configure FastAPI control plane instance."""
    app = FastAPI(
        title="GhostPilot Control Plane",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    app.state.settings = settings
    app.state.hitl_state = HITLStateManager(
        telegram_notifier=telegram_notifier,
        default_timeout=settings.hitl_timeout_seconds,
    )
    app.state.session_state = session_state or SessionState()

    app.include_router(hitl_router)
    return app
