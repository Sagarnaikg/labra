"""One error shape for every failure the API reports.

    {
      "field": "website",            # Labra field (or None for template-level problems)
      "crm_object": "Account",       # may be None
      "crm_field": "Website",        # may be None
      "received": "not a url",       # the value that was wrong
      "message": "...",              # what is wrong
      "fix": "..."                   # what the user should change
    }
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorItem(BaseModel):
    field: str | None = None
    crm_object: str | None = None
    crm_field: str | None = None
    received: Any = None
    message: str
    fix: str


class ApiError(Exception):
    """Raise from anywhere in a request to return `{"errors": [...]}`."""

    def __init__(self, status_code: int, errors: list[ErrorItem], extra: dict | None = None):
        super().__init__(errors[0].message if errors else "error")
        self.status_code = status_code
        self.errors = errors
        self.extra = extra or {}


def not_found(message: str, fix: str, **kwargs: Any) -> ApiError:
    return ApiError(404, [ErrorItem(message=message, fix=fix, **kwargs)])


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        body = {**exc.extra, "errors": [e.model_dump() for e in exc.errors]}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _bad_request_body(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Malformed JSON / wrong types in the request body, reported in the
        # same shape as every other error instead of FastAPI's default.
        errors = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            errors.append(
                ErrorItem(
                    field=loc or None,
                    received=err.get("input"),
                    message=f"Request body is invalid at '{loc or 'body'}': {err.get('msg')}.",
                    fix="Send a JSON body that matches the documented request shape.",
                ).model_dump()
            )
        return JSONResponse(status_code=422, content={"errors": errors})
