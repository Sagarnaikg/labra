"""FastAPI entry point.

`create_app(crm, db)` is the seam the tests use: the CRM client and the
database are passed in, never constructed inside a route.

Run locally with:

    uvicorn app.main:create_app --factory --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from app.db import Database
from app.errors import install_error_handlers
from app.fields import router as fields_router
from app.templates import router as templates_router


def create_app(crm=None, db: Database | None = None) -> FastAPI:
    db = db or Database()
    db.init()

    app = FastAPI(title="Labra mapping templates")
    app.state.crm = crm
    app.state.db = db

    install_error_handlers(app)
    app.include_router(fields_router)
    app.include_router(templates_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
