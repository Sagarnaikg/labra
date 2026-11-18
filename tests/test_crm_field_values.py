"""POST /mapping-templates/{id}/crm-field-values against the injected FakeCrm."""
import pytest

from app.crm import RateLimited
from tests.payloads import contoso, northwind

URL = "/mapping-templates/northwind-default/crm-field-values"

SUCCESS_REFERRAL = {
    "company_name": "Northwind Traders",
    "website": "https://northwind.example",
    "country": "United States",
    "expected_monthly_revenue": 5000,
    "target_close_date": "2026-12-01",
    "primary_sales_rep_email": "ada@northwind.example",
}

EXPECTED_CALLS = [
    {"object": "Account", "fields": ["Name", "Website", "BillingCountry"]},
    {"object": "Opportunity", "fields": ["Amount", "CloseDate"]},
    {"object": "Contact", "fields": ["Email"]},
]


def seed_valid(crm, record_id="opp-100", **overrides):
    account = {
        "Name": "Northwind Traders",
        "Website": "https://northwind.example",
        "BillingCountry": "United States",
        "InternalNotes": "do not copy me",
    }
    opportunity = {"Amount": 5000, "CloseDate": "2026-12-01"}
    contact = {"Email": "ada@northwind.example"}
    for key, value in overrides.items():
        obj, fld = key.split("__")
        {"Account": account, "Opportunity": opportunity, "Contact": contact}[obj][fld] = value
    crm.seed("Account", record_id, account)
    crm.seed("Opportunity", record_id, opportunity)
    crm.seed("Contact", record_id, contact)


@pytest.fixture
def northwind_saved(client):
    assert client.post("/mapping-templates", json=northwind()).status_code == 201


def post(client, record_id="opp-100", url=URL):
    return client.post(url, json={"record_id": record_id})


# ---------------------------------------------------------------- happy path


def test_valid_opp_100_builds_referral_with_one_call_per_object(client, crm, northwind_saved):
    seed_valid(crm)
    response = post(client)

    assert response.status_code == 200, response.json()
    assert response.json() == {"referral": SUCCESS_REFERRAL, "errors": []}
    assert crm.calls == EXPECTED_CALLS


def test_internal_notes_is_never_requested_or_copied(client, crm, northwind_saved):
    seed_valid(crm)
    referral = post(client).json()["referral"]

    assert "InternalNotes" not in referral
    assert "do not copy me" not in referral.values()
    assert all("InternalNotes" not in call["fields"] for call in crm.calls)


def test_numeric_string_amount_becomes_number(client, crm, northwind_saved):
    seed_valid(crm, Opportunity__Amount="5000")
    referral = post(client).json()["referral"]
    assert referral["expected_monthly_revenue"] == 5000
    assert isinstance(referral["expected_monthly_revenue"], int)


def test_contoso_hubspot_groups_companies_deals_contacts(client, crm):
    assert client.post("/mapping-templates", json=contoso()).status_code == 201
    crm.seed("companies", "deal-200", {"name": "Contoso Ltd", "website": "https://contoso.example", "country": "Germany"})
    crm.seed("deals", "deal-200", {"amount": 12000, "closedate": "2026-12-15"})
    crm.seed("contacts", "deal-200", {"email": "lee@contoso.example"})

    response = post(client, "deal-200", "/mapping-templates/contoso-default/crm-field-values")

    assert response.status_code == 200, response.json()
    assert response.json()["referral"]["company_name"] == "Contoso Ltd"
    assert crm.calls == [
        {"object": "companies", "fields": ["name", "website", "country"]},
        {"object": "deals", "fields": ["amount", "closedate"]},
        {"object": "contacts", "fields": ["email"]},
    ]


# ---------------------------------------------------------------- invalid data


def test_invalid_seed_returns_six_errors_with_object_field_value_and_fix(client, crm, northwind_saved):
    crm.seed("Account", "opp-100", {"Name": " ", "Website": "not a url", "BillingCountry": "USA"})
    crm.seed("Opportunity", "opp-100", {"Amount": -10, "CloseDate": "2020-01-01"})
    crm.seed("Contact", "opp-100", {"Email": "ada"})

    response = post(client)

    assert response.status_code == 422
    body = response.json()
    assert body["referral"] is None
    got = {(e["field"], e["crm_object"], e["crm_field"], e["received"]): e for e in body["errors"]}
    assert set(got) == {
        ("company_name", "Account", "Name", " "),
        ("website", "Account", "Website", "not a url"),
        ("country", "Account", "BillingCountry", "USA"),
        ("expected_monthly_revenue", "Opportunity", "Amount", -10),
        ("target_close_date", "Opportunity", "CloseDate", "2020-01-01"),
        ("primary_sales_rep_email", "Contact", "Email", "ada"),
    }
    for e in body["errors"]:
        assert e["message"] and e["fix"]
        assert "Value error" not in e["message"]  # raw Pydantic text is translated
        assert f'{e["crm_object"]}.{e["crm_field"]}' in e["fix"]

    website = got[("website", "Account", "Website", "not a url")]
    assert website["message"] == "Website must be an http or https URL with a host."
    assert website["fix"] == "Set Account.Website in Salesforce to a URL such as https://northwind.example."
    assert "United States, India, Germany, or United Kingdom" in got[("country", "Account", "BillingCountry", "USA")]["fix"]


@pytest.mark.parametrize(
    "override, field",
    [
        ({"Account__Website": "northwind.example"}, "website"),
        ({"Account__Name": "x" * 256}, "company_name"),
        ({"Opportunity__Amount": 0}, "expected_monthly_revenue"),
        ({"Opportunity__CloseDate": "2026-11-17"}, "target_close_date"),
        ({"Opportunity__CloseDate": "12/01/2026"}, "target_close_date"),
    ],
)
def test_single_rule_failures(client, crm, northwind_saved, override, field):
    seed_valid(crm, **override)
    response = post(client)
    assert response.status_code == 422
    assert [e["field"] for e in response.json()["errors"]] == [field]


def test_close_date_today_is_valid(client, crm, northwind_saved):
    seed_valid(crm, Opportunity__CloseDate="2026-11-18")  # frozen today
    assert post(client).status_code == 200


def test_missing_required_value_names_the_crm_field(client, crm, northwind_saved):
    seed_valid(crm)
    crm.seed("Contact", "opp-100", {})
    [error] = post(client).json()["errors"]
    assert (error["field"], error["crm_object"], error["crm_field"]) == ("primary_sales_rep_email", "Contact", "Email")
    assert "Contact.Email" in error["fix"]


# ---------------------------------------------------------------- rate limits


def test_one_rate_limit_is_retried_and_referral_succeeds(client, crm, northwind_saved):
    seed_valid(crm)
    crm.fail_next("Opportunity")

    response = post(client)

    assert response.status_code == 200, response.json()
    assert response.json()["referral"] == SUCCESS_REFERRAL
    assert [c["object"] for c in crm.calls] == ["Account", "Opportunity", "Opportunity", "Contact"]


def test_rate_limit_twice_names_opportunity_and_not_its_fields(client, crm, northwind_saved):
    seed_valid(crm)
    original_fetch = crm.fetch

    def always_limited(object_name, record_id, fields):
        if object_name == "Opportunity":
            crm.calls.append({"object": object_name, "fields": list(fields)})
            raise RateLimited(object_name, retry_after_ms=5)
        return original_fetch(object_name, record_id, fields)

    crm.fetch = always_limited

    response = post(client)

    assert response.status_code == 503
    body = response.json()
    assert body["referral"] is None
    [error] = body["errors"]
    assert error["crm_object"] == "Opportunity"
    assert "rate limit held" in error["message"]
    fields_reported = {e["field"] for e in body["errors"]}
    assert "expected_monthly_revenue" not in fields_reported
    assert "target_close_date" not in fields_reported
    assert [c["object"] for c in crm.calls].count("Opportunity") == 2


# ---------------------------------------------------------------- not found / unused / new fields


def test_unknown_template_is_not_found(client, crm):
    seed_valid(crm)
    response = post(client, url="/mapping-templates/nope/crm-field-values")
    assert response.status_code == 404
    assert crm.calls == []


def test_unknown_record_is_not_found(client, crm, northwind_saved):
    seed_valid(crm)
    response = post(client, "opp-999")
    assert response.status_code == 404
    assert response.json()["referral"] is None
    assert "opp-999" in response.json()["errors"][0]["message"]


def test_every_contact_field_unused_means_no_contact_fetch(client, crm, northwind_saved):
    # primary_sales_rep_email is required by AWS, so make it optional first;
    # then it can be marked unused and Contact has no fields left to fetch.
    client.post("/labra-fields", json={
        "labra_field": "primary_sales_rep_email", "label": "Primary sales rep email", "required": False,
    })
    r = client.patch("/mapping-templates/northwind-default/fields/primary_sales_rep_email", json={"unused": True})
    assert r.status_code == 200, r.json()
    seed_valid(crm)

    response = post(client)

    assert response.status_code == 200, response.json()
    assert "primary_sales_rep_email" not in response.json()["referral"]
    assert [c["object"] for c in crm.calls] == ["Account", "Opportunity"]


DUNS = {"labra_field": "customer_duns", "label": "Customer DUNS", "required": False}


def test_new_optional_field_unmapped_keeps_old_referral_valid(client, crm, northwind_saved):
    client.post("/labra-fields", json=DUNS)
    seed_valid(crm)

    response = post(client)

    assert response.json() == {"referral": SUCCESS_REFERRAL, "errors": []}
    assert crm.calls == EXPECTED_CALLS  # unmapped field is not fetched


def _map_duns(client):
    client.post("/labra-fields", json=DUNS)
    client.patch(
        "/mapping-templates/northwind-default/fields/customer_duns",
        json={"crm_object": "Account", "crm_field": "DUNS__c"},
    )


def test_mapped_duns_is_fetched_with_its_object_and_included(client, crm, northwind_saved):
    _map_duns(client)
    seed_valid(crm)
    crm._records[("Account", "opp-100")]["DUNS__c"] = "123456789"

    response = post(client)

    assert response.status_code == 200, response.json()
    assert response.json()["referral"]["customer_duns"] == "123456789"
    assert crm.calls[0] == {"object": "Account", "fields": ["Name", "Website", "BillingCountry", "DUNS__c"]}
    assert len(crm.calls) == 3


def test_mapped_duns_with_empty_value_is_omitted(client, crm, northwind_saved):
    _map_duns(client)
    seed_valid(crm)
    crm._records[("Account", "opp-100")]["DUNS__c"] = "  "

    response = post(client)
    assert response.json() == {"referral": SUCCESS_REFERRAL, "errors": []}


def test_invalid_duns_fails_with_fix(client, crm, northwind_saved):
    _map_duns(client)
    seed_valid(crm)
    crm._records[("Account", "opp-100")]["DUNS__c"] = "ABC"

    response = post(client)
    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert (error["field"], error["crm_field"], error["received"]) == ("customer_duns", "DUNS__c", "ABC")
    assert "9-digit" in error["fix"]


def test_required_new_field_unmapped_fails_and_names_the_field(client, crm, northwind_saved):
    client.post("/labra-fields", json={**DUNS, "required": True})
    seed_valid(crm)

    response = post(client)
    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["field"] == "customer_duns"
    assert "customer_duns" in error["fix"]


def test_field_marked_unused_then_made_required_fails(client, crm, northwind_saved):
    client.post("/labra-fields", json=DUNS)
    client.patch("/mapping-templates/northwind-default/fields/customer_duns", json={"unused": True})
    client.post("/labra-fields", json={**DUNS, "required": True})  # AWS flips it later
    seed_valid(crm)

    response = post(client)

    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["field"] == "customer_duns"
    assert "marked unused" in error["message"]
    assert "customer_duns" in error["fix"]


def test_required_new_field_blank_value_fails(client, crm, northwind_saved):
    _map_duns(client)
    client.post("/labra-fields", json={**DUNS, "required": True})
    seed_valid(crm)
    crm._records[("Account", "opp-100")]["DUNS__c"] = ""

    [error] = post(client).json()["errors"]
    assert error["field"] == "customer_duns"
    assert "Account.DUNS__c" in error["fix"]
