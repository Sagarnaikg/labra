"""The shared Labra field list: introduce a new field or update one.

Introducing a field writes exactly one row in labra_fields. No template row is
read or written; templates pick the field up through the LEFT JOIN in
load_template().
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import Database
from app.errors import ApiError, ErrorItem

router = APIRouter(prefix="/labra-fields", tags=["labra fields"])

FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class LabraFieldIn(BaseModel):
    labra_field: str
    label: str = ""
    required: bool = False


class LabraFieldOut(BaseModel):
    labra_field: str
    label: str
    required: bool


def _db(request: Request) -> Database:
    return request.app.state.db


@router.get("", response_model=list[LabraFieldOut])
def list_fields(request: Request) -> list[LabraFieldOut]:
    with _db(request).connect() as conn:
        rows = conn.execute(
            "SELECT name, label, required FROM labra_fields ORDER BY sort_order"
        ).fetchall()
    return [LabraFieldOut(labra_field=r["name"], label=r["label"], required=bool(r["required"])) for r in rows]


@router.post("", response_model=LabraFieldOut)
def upsert_field(body: LabraFieldIn, request: Request):
    """Introduce a Labra field, or update its label/required if it exists.

    201 when the field is new, 200 when an existing definition was updated.
    Customer mappings and unused choices live in template_mappings and are
    never touched here.
    """
    errors = []
    if not FIELD_NAME.match(body.labra_field):
        errors.append(ErrorItem(
            field=body.labra_field, received=body.labra_field,
            message="labra_field must be snake_case: lowercase letters, digits and underscores.",
            fix="Send a name such as customer_duns.",
        ))
    if not body.label.strip():
        errors.append(ErrorItem(
            field=body.labra_field, received=body.label,
            message="label is blank.",
            fix="Send a non-empty label such as \"Customer DUNS\".",
        ))
    if errors:
        raise ApiError(422, errors)

    with _db(request).connect() as conn:
        existed = conn.execute(
            "SELECT 1 FROM labra_fields WHERE name = ?", (body.labra_field,)
        ).fetchone() is not None
        conn.execute(
            """
            INSERT INTO labra_fields (name, label, required, sort_order)
            VALUES (?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM labra_fields))
            ON CONFLICT (name) DO UPDATE SET
                label      = excluded.label,
                required   = excluded.required,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (body.labra_field, body.label.strip(), int(body.required)),
        )

    out = LabraFieldOut(labra_field=body.labra_field, label=body.label.strip(), required=body.required)
    return JSONResponse(status_code=200 if existed else 201, content=out.model_dump())
