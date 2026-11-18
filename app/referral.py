"""Build and validate the referral.

The Pydantic model is created per request from the template's field list, which
is the same merged view GET returns (labra_fields LEFT JOIN template_mappings).
There is no second list of Labra fields in code.

FIELD_RULES below is a map of *format rules* keyed by field name (how to check a
website, a DUNS number, ...). It is not a list of fields: a field on the shared
list with no rule is still on the model, validated as non-blank text, and a rule
for a field that is not on the list is simply never used. So the two cannot
drift into a missing or extra referral field.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Callable, Literal, Optional
from urllib.parse import urlparse

from pydantic import (
    AfterValidator,
    BeforeValidator,
    EmailStr,
    Field,
    StringConstraints,
    ValidationError,
    create_model,
)

from app.errors import ErrorItem
from app.templates import FieldOut, TemplateOut

CRM_LABELS = {"salesforce": "Salesforce", "hubspot": "HubSpot"}
COUNTRIES = ("United States", "India", "Germany", "United Kingdom")


# ---------------------------------------------------------------- value checks


def _http_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("not an http(s) URL with a host")
    return value.strip()


def _not_bool(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    return value


def _iso_date_string(value: Any) -> Any:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ValueError("not YYYY-MM-DD")
    return value.strip()


def _to_text(value: Any) -> Any:
    return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def _duns(value: str) -> str:
    if not re.fullmatch(r"\d{9}", value):
        raise ValueError("not 9 digits")
    return value


# Note: StringConstraints must sit on the inner type. Placed after a
# BeforeValidator in the same Annotated, Pydantic silently drops min_length.
Text = Annotated[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], BeforeValidator(_to_text)]


def _close_date_type(today: Callable[[], date]):
    def not_in_past(value: date) -> date:
        if value < today():
            raise ValueError("in the past")
        return value

    return Annotated[date, BeforeValidator(_iso_date_string), AfterValidator(not_in_past)]


# ---------------------------------------------------------------- per-field rules


@dataclass(frozen=True)
class Rule:
    type: Any                      # Pydantic type, or a factory taking today()
    message: str                   # what is wrong
    fix: str                       # what to change; {ref} = Object.Field, {crm} = Salesforce/HubSpot


FIELD_RULES: dict[str, Rule] = {
    "company_name": Rule(
        Annotated[
            Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)],
            BeforeValidator(_to_text),
        ],
        "Company name must be non-empty and at most 255 characters.",
        "Set {ref} in {crm} to the company name (1 to 255 characters).",
    ),
    "website": Rule(
        Annotated[str, AfterValidator(_http_url)],
        "Website must be an http or https URL with a host.",
        "Set {ref} in {crm} to a URL such as https://northwind.example.",
    ),
    "country": Rule(
        Literal[COUNTRIES],
        "Country must be one of United States, India, Germany, United Kingdom.",
        "Set {ref} in {crm} to United States, India, Germany, or United Kingdom.",
    ),
    "expected_monthly_revenue": Rule(
        Annotated[int | float, BeforeValidator(_not_bool), Field(gt=0)],
        "Expected monthly revenue must be a number greater than 0.",
        "Set {ref} in {crm} to a number greater than 0, such as 5000.",
    ),
    "target_close_date": Rule(
        _close_date_type,
        "Target close date must be today or later, as YYYY-MM-DD (UTC).",
        "Set {ref} in {crm} to today or a later date as YYYY-MM-DD.",
    ),
    "primary_sales_rep_email": Rule(
        EmailStr,
        "Primary sales rep email must be an email address.",
        "Set {ref} in {crm} to an email address such as ada@northwind.example.",
    ),
    "customer_duns": Rule(
        Annotated[
            Annotated[str, StringConstraints(strip_whitespace=True)],
            BeforeValidator(_to_text),
            AfterValidator(_duns),
        ],
        "Customer DUNS must be a string of 9 digits.",
        "Set {ref} in {crm} to a 9-digit DUNS number such as 123456789.",
    ),
}


def _rule_for(field: FieldOut) -> Rule:
    return FIELD_RULES.get(field.labra_field) or Rule(
        Text,
        f"{field.label} must not be blank.",
        "Fill {ref} in {crm}.",
    )


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


# ---------------------------------------------------------------- build + validate


@dataclass
class ReferralResult:
    referral: dict | None
    errors: list[ErrorItem]


def build_referral(
    template: TemplateOut,
    values: dict[str, dict],
    skip_objects: set[str],
    today: Callable[[], date],
) -> ReferralResult:
    """values: crm_object -> {crm_field: value} for every object that returned data.
    skip_objects: objects that failed (rate limit / not found); their fields are
    neither reported as missing nor validated, the caller reports the object."""
    crm = CRM_LABELS.get(template.crm_type, template.crm_type)
    errors: list[ErrorItem] = []
    model_fields: dict[str, tuple] = {}
    data: dict[str, Any] = {}
    by_name: dict[str, FieldOut] = {}

    for f in template.fields:
        if f.unused:
            if f.required:
                # Marked unused while optional, then AWS made it required.
                errors.append(ErrorItem(
                    field=f.labra_field, received=None,
                    message=f"{f.label} is now required by AWS but is marked unused on "
                            f"template '{template.id}'.",
                    fix=f"Map {f.labra_field} to a {crm} object and field on the template.",
                ))
            continue
        rule = _rule_for(f)

        if not f.mapped:
            if f.required:
                errors.append(ErrorItem(
                    field=f.labra_field, received=None,
                    message=f"{f.label} is required by AWS but is not mapped on template '{template.id}'.",
                    fix=f"Map {f.labra_field} to a {crm} object and field on the template.",
                ))
            continue

        if f.crm_object in skip_objects:
            continue

        value = values.get(f.crm_object, {}).get(f.crm_field)
        if _blank(value) and not f.required:
            continue  # optional + empty: omit the key

        field_type = rule.type(today) if rule.type is _close_date_type else rule.type
        model_fields[f.labra_field] = (field_type, ...) if f.required else (Optional[field_type], None)
        data[f.labra_field] = value
        by_name[f.labra_field] = f

    Referral = create_model(f"Referral_{template.id}", **model_fields)

    try:
        referral = Referral.model_validate(data)
    except ValidationError as exc:
        seen: set[str] = set()
        for err in exc.errors():
            name = str(err["loc"][0]) if err["loc"] else None
            if name is None or name in seen or name not in by_name:
                continue
            seen.add(name)  # one error per field, even if Pydantic raised several
            f = by_name[name]
            rule = _rule_for(f)
            ref = f"{f.crm_object}.{f.crm_field}"
            received = data.get(name)
            message = (
                f"{f.label} is required but {ref} is empty."
                if _blank(received) else rule.message
            )
            errors.append(ErrorItem(
                field=name, crm_object=f.crm_object, crm_field=f.crm_field,
                received=received, message=message,
                fix=rule.fix.format(ref=ref, crm=crm),
            ))
        # keep the template's field order
        order = {f.labra_field: i for i, f in enumerate(template.fields)}
        errors.sort(key=lambda e: order.get(e.field, -1))
        return ReferralResult(None, errors)

    if errors:
        return ReferralResult(None, errors)
    return ReferralResult(referral.model_dump(mode="json", exclude_none=True), [])
