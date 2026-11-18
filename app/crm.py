"""CRM access: the fake client, the fetch plan, and the single rate-limit retry.

The service only ever talks to an object with a `fetch(object_name, record_id,
fields)` method. Tests pass a FakeCrm into create_app(); a real Salesforce or
HubSpot client would implement the same method.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from app.templates import TemplateOut


# ---------------------------------------------------------------- client contract


class RateLimited(Exception):
    def __init__(self, object_name: str, retry_after_ms: int):
        super().__init__(f"{object_name} rate limited; retry after {retry_after_ms} ms")
        self.object_name = object_name
        self.retry_after_ms = retry_after_ms


class CrmRecordNotFound(Exception):
    def __init__(self, object_name: str, record_id: str):
        super().__init__(f"{object_name} record {record_id} not found")
        self.object_name = object_name
        self.record_id = record_id


class CrmClient(Protocol):
    def fetch(self, object_name: str, record_id: str, fields: list[str]) -> dict: ...


# ---------------------------------------------------------------- fake CRM (from the brief)


class FakeCrm:
    def __init__(self):
        self._records = {}
        self.calls = []
        self._fail_once = set()

    def seed(self, object_name, record_id, values):
        self._records[(object_name, record_id)] = values

    def fail_next(self, object_name):
        """Next fetch of this object raises RateLimited. The one after succeeds."""
        self._fail_once.add(object_name)

    def fetch(self, object_name, record_id, fields):
        self.calls.append({"object": object_name, "fields": list(fields)})
        if object_name in self._fail_once:
            self._fail_once.remove(object_name)
            raise RateLimited(object_name, retry_after_ms=5)
        row = self._records.get((object_name, record_id))
        if row is None:
            raise CrmRecordNotFound(object_name, record_id)
        return {field: row[field] for field in fields if field in row}


# ---------------------------------------------------------------- fetch plan


def plan_fetches(template: TemplateOut) -> dict[str, list[str]]:
    """Group the template's mapped, used fields by CRM object: one fetch per object.

    Built from the same merged view GET returns, so a newly introduced field is
    fetched only once a customer maps it. Unused and unmapped fields are skipped,
    so an object whose fields are all unused is never fetched. Ten fields on one
    object is still one call with ten field names.
    """
    plan: dict[str, list[str]] = {}
    for f in template.fields:
        if not f.mapped:
            continue
        names = plan.setdefault(f.crm_object, [])
        if f.crm_field not in names:
            names.append(f.crm_field)
    return plan


# ---------------------------------------------------------------- one retry


def fetch_with_retry(
    crm: CrmClient,
    object_name: str,
    record_id: str,
    fields: list[str],
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Call crm.fetch; on RateLimited wait retry_after_ms and try exactly once more.

    A second RateLimited propagates to the caller. This is the seam where a
    shared, cross-process limiter would wrap the call (see NOTES.md).
    """
    try:
        return crm.fetch(object_name, record_id, fields)
    except RateLimited as first:
        sleep(first.retry_after_ms / 1000)
        return crm.fetch(object_name, record_id, fields)


@dataclass
class FetchResult:
    values: dict[str, dict] = field(default_factory=dict)       # object -> {crm_field: value}
    rate_limited: list[str] = field(default_factory=list)       # objects that failed twice
    not_found: list[str] = field(default_factory=list)          # objects with no record

    @property
    def nothing_found(self) -> bool:
        return bool(self.not_found) and not self.values and not self.rate_limited


def fetch_all(crm: CrmClient, plan: dict[str, list[str]], record_id: str, sleep=time.sleep) -> FetchResult:
    result = FetchResult()
    for object_name, fields in plan.items():
        try:
            result.values[object_name] = fetch_with_retry(crm, object_name, record_id, fields, sleep)
        except RateLimited:
            result.rate_limited.append(object_name)
        except CrmRecordNotFound:
            result.not_found.append(object_name)
    return result
