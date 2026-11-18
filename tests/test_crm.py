"""Unit tests for the CRM layer: fetch plan grouping and the single retry."""
import pytest

from app.crm import FakeCrm, RateLimited, fetch_all, fetch_with_retry, plan_fetches
from app.templates import FieldOut, TemplateOut


def _template(*rows):
    """rows: (labra_field, crm_object, crm_field, unused)"""
    fields = []
    for name, obj, fld, unused in rows:
        mapped = obj is not None and not unused
        fields.append(FieldOut(
            labra_field=name, label=name, required=False,
            crm_object=obj if mapped else None, crm_field=fld if mapped else None,
            mapped=mapped, unused=unused,
        ))
    return TemplateOut(id="t", name="t", crm_type="salesforce", fields=fields)


def test_plan_groups_by_object_in_template_order():
    tpl = _template(
        ("company_name", "Account", "Name", False),
        ("website", "Account", "Website", False),
        ("country", "Account", "BillingCountry", False),
        ("expected_monthly_revenue", "Opportunity", "Amount", False),
        ("target_close_date", "Opportunity", "CloseDate", False),
        ("primary_sales_rep_email", "Contact", "Email", False),
    )
    assert plan_fetches(tpl) == {
        "Account": ["Name", "Website", "BillingCountry"],
        "Opportunity": ["Amount", "CloseDate"],
        "Contact": ["Email"],
    }


def test_plan_skips_unused_and_unmapped_fields():
    tpl = _template(
        ("company_name", "Account", "Name", False),
        ("customer_duns", None, None, False),        # introduced, not mapped
        ("primary_sales_rep_email", None, None, True),  # unused -> Contact never fetched
    )
    assert plan_fetches(tpl) == {"Account": ["Name"]}


def test_ten_fields_on_one_object_is_one_fetch():
    tpl = _template(*[(f"f{i}", "Account", f"F{i}", False) for i in range(10)])
    plan = plan_fetches(tpl)
    assert list(plan) == ["Account"]
    assert len(plan["Account"]) == 10


def test_retry_waits_retry_after_and_succeeds():
    crm = FakeCrm()
    crm.seed("Opportunity", "opp-100", {"Amount": 5000})
    crm.fail_next("Opportunity")
    waits = []

    values = fetch_with_retry(crm, "Opportunity", "opp-100", ["Amount"], sleep=waits.append)

    assert values == {"Amount": 5000}
    assert waits == [0.005]
    assert [c["object"] for c in crm.calls] == ["Opportunity", "Opportunity"]


class AlwaysRateLimited(FakeCrm):
    def fetch(self, object_name, record_id, fields):
        self.calls.append({"object": object_name, "fields": list(fields)})
        raise RateLimited(object_name, retry_after_ms=5)


def test_second_rate_limit_propagates_after_exactly_two_calls():
    crm = AlwaysRateLimited()
    with pytest.raises(RateLimited):
        fetch_with_retry(crm, "Opportunity", "opp-100", ["Amount"], sleep=lambda s: None)
    assert len(crm.calls) == 2


def test_fetch_all_reports_rate_limited_and_missing_objects_separately():
    crm = FakeCrm()
    crm.seed("Account", "opp-100", {"Name": "Northwind Traders"})
    plan = {"Account": ["Name"], "Contact": ["Email"]}

    result = fetch_all(crm, plan, "opp-100", sleep=lambda s: None)

    assert result.values == {"Account": {"Name": "Northwind Traders"}}
    assert result.not_found == ["Contact"]
    assert result.rate_limited == []
    assert result.nothing_found is False
