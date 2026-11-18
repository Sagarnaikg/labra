"""POST /mapping-templates/{template_id}/crm-field-values

Body: {"record_id": "opp-100"}. The service never receives CRM values in the
body; it computes the fetch plan from the template the customer sees, asks the
injected CRM client (one call per object, one retry on RateLimited), then builds
and validates the referral.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.crm import fetch_all, plan_fetches
from app.errors import ApiError, ErrorItem
from app.referral import CRM_LABELS, build_referral
from app.templates import load_template

router = APIRouter(prefix="/mapping-templates", tags=["referrals"])


class CrmFieldValuesIn(BaseModel):
    record_id: str


@router.post("/{template_id}/crm-field-values")
def crm_field_values(template_id: str, body: CrmFieldValuesIn, request: Request) -> JSONResponse:
    state = request.app.state
    template = load_template(state.db, template_id)  # 404 if unknown
    crm_label = CRM_LABELS.get(template.crm_type, template.crm_type)
    record_id = body.record_id.strip()

    plan = plan_fetches(template)
    fetched = fetch_all(state.crm, plan, record_id, sleep=state.sleep)

    if fetched.nothing_found:
        raise ApiError(404, [ErrorItem(
            received=record_id,
            message=f"No {crm_label} record '{record_id}' was found for "
                    f"{', '.join(fetched.not_found)}.",
            fix=f"Check the record id, or create the record in {crm_label}.",
        )], extra={"referral": None})

    object_errors: list[ErrorItem] = []
    for obj in fetched.rate_limited:
        object_errors.append(ErrorItem(
            crm_object=obj, received=None,
            message=f"The {crm_label} rate limit held for {obj}: the fetch was retried once "
                    f"and was rate limited again, so {obj} fields were not read.",
            fix=f"Try again in a few seconds. If it keeps happening, lower how many referrals "
                f"are prepared at once for this {crm_label} account.",
        ))
    for obj in fetched.not_found:
        object_errors.append(ErrorItem(
            crm_object=obj, received=record_id,
            message=f"No {obj} record was found for '{record_id}', so its fields were not read.",
            fix=f"Link a {obj} to '{record_id}' in {crm_label}, or mark its fields unused.",
        ))

    result = build_referral(
        template,
        fetched.values,
        skip_objects=set(fetched.rate_limited) | set(fetched.not_found),
        today=state.today,
    )
    errors = object_errors + result.errors

    if errors:
        # 503: the upstream CRM refused us, the caller did nothing wrong.
        status = 503 if fetched.rate_limited else 422
        headers = {"Retry-After": "1"} if fetched.rate_limited else None
        return JSONResponse(
            status_code=status,
            content={"referral": None, "errors": [e.model_dump() for e in errors]},
            headers=headers,
        )
    return JSONResponse(status_code=200, content={"referral": result.referral, "errors": []})
