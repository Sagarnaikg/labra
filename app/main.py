"""FastAPI entry point.

`create_app(crm)` is the seam the tests use: the CRM client is passed in,
never constructed inside a route.
"""
from fastapi import FastAPI


def create_app(crm=None) -> FastAPI:
    app = FastAPI(title="Labra mapping templates")
    app.state.crm = crm

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


# Used by `uvicorn app.main:app`
app = create_app()
