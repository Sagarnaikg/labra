"""Mapping templates: create and read.

A template stores only the customer's own choices (rows in template_mappings).
Reading a template LEFT JOINs the shared labra_fields list, so any field added
later appears automatically as unmapped.
"""
from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.db import Database
from app.errors import ApiError, ErrorItem, not_found

CRM_TYPES = ("salesforce", "hubspot")

router = APIRouter(prefix="/mapping-templates", tags=["mapping templates"])


# ---------------------------------------------------------------- request / response


class MappingIn(BaseModel):
    labra_field: str
    crm_object: str | None = None
    crm_field: str | None = None
    unused: bool = False


class TemplateCreate(BaseModel):
    # Types are deliberately loose (plain str) so that business rules such as
    # "blank name" or "crm_type must be salesforce/hubspot" come back in our
    # error shape with a fix, not as a generic schema error.
    id: str | None = Field(default=None, description="Optional. Defaults to a slug of name.")
    name: str = ""
    crm_type: str = ""
    mappings: list[MappingIn] = []


class FieldOut(BaseModel):
    labra_field: str
    label: str
    required: bool
    crm_object: str | None
    crm_field: str | None
    mapped: bool
    unused: bool


class TemplateOut(BaseModel):
    id: str
    name: str
    crm_type: str
    fields: list[FieldOut]


# ---------------------------------------------------------------- data access


def load_labra_fields(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT name, label, required FROM labra_fields ORDER BY sort_order"
    ).fetchall()
    return {r["name"]: r for r in rows}


def load_template(db: Database, template_id: str) -> TemplateOut:
    """The template as the customer sees it: every current Labra field, merged
    with whatever this template chose. Single source for GET and crm-field-values."""
    with db.connect() as conn:
        tpl = conn.execute(
            "SELECT id, name, crm_type FROM templates WHERE id = ?", (template_id,)
        ).fetchone()
        if tpl is None:
            raise not_found(
                message=f"Mapping template '{template_id}' was not found.",
                fix="Check the template id, or create the template first.",
                received=template_id,
            )
        rows = conn.execute(
            """
            SELECT f.name, f.label, f.required,
                   m.crm_object, m.crm_field, COALESCE(m.unused, 0) AS unused
            FROM labra_fields f
            LEFT JOIN template_mappings m
                   ON m.labra_field = f.name AND m.template_id = ?
            ORDER BY f.sort_order
            """,
            (template_id,),
        ).fetchall()

    fields = []
    for r in rows:
        unused = bool(r["unused"])
        mapped = not unused and r["crm_object"] is not None and r["crm_field"] is not None
        fields.append(
            FieldOut(
                labra_field=r["name"],
                label=r["label"],
                required=bool(r["required"]),
                crm_object=r["crm_object"] if mapped else None,
                crm_field=r["crm_field"] if mapped else None,
                mapped=mapped,
                unused=unused,
            )
        )
    return TemplateOut(id=tpl["id"], name=tpl["name"], crm_type=tpl["crm_type"], fields=fields)


# ---------------------------------------------------------------- validation


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def validate_create(body: TemplateCreate, labra_fields: dict[str, sqlite3.Row]) -> list[ErrorItem]:
    """Every rule from the spec. Returns ALL problems, not just the first."""
    errors: list[ErrorItem] = []

    if _blank(body.name):
        errors.append(ErrorItem(
            received=body.name,
            message="Template name is blank.",
            fix="Send a non-empty template name.",
        ))

    if body.crm_type not in CRM_TYPES:
        errors.append(ErrorItem(
            received=body.crm_type,
            message=f"crm_type '{body.crm_type}' is not supported.",
            fix="Send salesforce or hubspot.",
        ))

    seen_fields: set[str] = set()
    seen_crm: dict[tuple[str, str], str] = {}

    for m in body.mappings:
        obj = m.crm_object.strip() if m.crm_object else None
        fld = m.crm_field.strip() if m.crm_field else None
        where = dict(field=m.labra_field, crm_object=obj or None, crm_field=fld or None)

        if m.labra_field not in labra_fields:
            errors.append(ErrorItem(
                **where, received=m.labra_field,
                message=f"'{m.labra_field}' is not on the shared Labra field list.",
                fix="Remove that mapping or add the field first.",
            ))
            continue

        if m.labra_field in seen_fields:
            errors.append(ErrorItem(
                **where, received=m.labra_field,
                message=f"'{m.labra_field}' is mapped more than once.",
                fix="Keep one CRM object and field per Labra field.",
            ))
            continue
        seen_fields.add(m.labra_field)

        if m.unused:
            if labra_fields[m.labra_field]["required"]:
                errors.append(ErrorItem(
                    **where, received=True,
                    message=f"'{m.labra_field}' is required by AWS and cannot be marked unused.",
                    fix=f"Map {m.labra_field} to a CRM object and field instead.",
                ))
            continue

        if _blank(obj) or _blank(fld):
            errors.append(ErrorItem(
                **where, received={"crm_object": m.crm_object, "crm_field": m.crm_field},
                message=f"'{m.labra_field}' needs both a CRM object and a CRM field.",
                fix="Fill both, or mark the field unused.",
            ))
            continue

        # Salesforce API names are case-insensitive, so compare that way.
        key = (obj.casefold(), fld.casefold())
        if key in seen_crm:
            errors.append(ErrorItem(
                **where, received=f"{obj}.{fld}",
                message=f"{obj}.{fld} already fills '{seen_crm[key]}'.",
                fix="Pick one Labra field for that CRM value.",
            ))
            continue
        seen_crm[key] = m.labra_field

    for name, f in labra_fields.items():
        if f["required"] and name not in seen_fields:
            errors.append(ErrorItem(
                field=name, received=None,
                message=f"'{name}' is required by AWS and has no mapping.",
                fix="Map that field before saving.",
            ))

    return errors


# ---------------------------------------------------------------- routes


def _db(request: Request) -> Database:
    return request.app.state.db


@router.post("", status_code=201, response_model=TemplateOut)
def create_template(body: TemplateCreate, request: Request) -> TemplateOut:
    db = _db(request)
    template_id = (body.id or "").strip() or _slug(body.name)

    with db.connect() as conn:
        errors = validate_create(body, load_labra_fields(conn))
        if not template_id and not errors:
            errors.append(ErrorItem(
                received=body.id,
                message="Template id is blank.",
                fix="Send an id, or a name we can build one from.",
            ))
        if errors:
            raise ApiError(422, errors)

        if conn.execute("SELECT 1 FROM templates WHERE id = ?", (template_id,)).fetchone():
            raise ApiError(409, [ErrorItem(
                received=template_id,
                message=f"Mapping template '{template_id}' already exists.",
                fix="Use a different id, or edit the existing template.",
            )])

        # One transaction: either the template and all its mappings are stored, or nothing.
        conn.execute(
            "INSERT INTO templates (id, name, crm_type) VALUES (?, ?, ?)",
            (template_id, body.name.strip(), body.crm_type),
        )
        conn.executemany(
            "INSERT INTO template_mappings (template_id, labra_field, crm_object, crm_field, unused) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    template_id,
                    m.labra_field,
                    None if m.unused else m.crm_object.strip(),
                    None if m.unused else m.crm_field.strip(),
                    int(m.unused),
                )
                for m in body.mappings
            ],
        )

    return load_template(db, template_id)


@router.get("/{template_id}", response_model=TemplateOut)
def get_template(template_id: str, request: Request) -> TemplateOut:
    return load_template(_db(request), template_id)
