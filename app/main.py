"""FastAPI entry point.

`create_app(crm, db)` is the seam the tests use: the CRM client and the
database are passed in, never constructed inside a route.

Run locally with:

    uvicorn app.main:create_app --factory --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from app.db import Database


def create_app(crm=None, db: Database | None = None) -> FastAPI:
    db = db or Database()
    db.init()

    app = FastAPI(title="Labra mapping templates")
    app.state.crm = crm
    app.state.db = db

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
