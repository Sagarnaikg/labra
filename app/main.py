"""FastAPI entry point.

`create_app(crm, db, today)` is the seam the tests use: the CRM client, the
database and the clock are passed in, never constructed inside a route.

Run locally with:

    uvicorn app.main:create_app --factory --reload
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Callable

from fastapi import FastAPI

from app.crm import CrmClient
from app.crm_field_values import router as crm_field_values_router
from app.db import Database
from app.errors import install_error_handlers
from app.fields import router as fields_router
from app.templates import router as templates_router


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def create_app(
    crm: CrmClient | None = None,
    db: Database | None = None,
    today: Callable[[], date] = utc_today,
    sleep: Callable[[float], None] = time.sleep,
) -> FastAPI:
    if crm is None:
        from app.demo import demo_crm  # local run without an injected client

        crm = demo_crm()
    db = db or Database()
    db.init()

    app = FastAPI(title="Labra mapping templates")
    app.state.crm = crm
    app.state.db = db
    app.state.today = today
    app.state.sleep = sleep

    install_error_handlers(app)
    app.include_router(fields_router)
    app.include_router(templates_router)
    app.include_router(crm_field_values_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
