"""Introducing Labra fields, and customer choices (map / unused) per template."""
import time

import pytest

from tests.payloads import NORTHWIND_MAPPINGS, northwind

DUNS = {"labra_field": "customer_duns", "label": "Customer DUNS", "required": False}


def _by_field(body):
    return {f["labra_field"]: f for f in body["fields"]}


def _template_rows(db):
    """Every stored template + mapping row: proof of what was (not) written."""
    with db.connect() as conn:
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM templates ORDER BY id")],
            [tuple(r) for r in conn.execute(
                "SELECT * FROM template_mappings ORDER BY template_id, labra_field")],
        )


@pytest.fixture
def northwind_saved(client):
    assert client.post("/mapping-templates", json=northwind()).status_code == 201


# ---------------------------------------------------------------- introducing a field


def test_introduce_customer_duns_shows_on_template_saved_before(client, db, northwind_saved):
    before = _template_rows(db)

    response = client.post("/labra-fields", json=DUNS)
    assert response.status_code == 201
    assert response.json() == {"labra_field": "customer_duns", "label": "Customer DUNS", "required": False}

    # No second request resubmits Northwind's mappings, and no template row was written.
    assert _template_rows(db) == before

    fields = _by_field(client.get("/mapping-templates/northwind-default").json())
    assert fields["customer_duns"] == {
        "labra_field": "customer_duns",
        "label": "Customer DUNS",
        "required": False,
        "crm_object": None,
        "crm_field": None,
        "mapped": False,
        "unused": False,
    }
    assert (fields["company_name"]["crm_object"], fields["company_name"]["crm_field"]) == ("Account", "Name")
    for labra_field, crm_object, crm_field in NORTHWIND_MAPPINGS:
        assert (fields[labra_field]["crm_object"], fields[labra_field]["crm_field"]) == (crm_object, crm_field)


def test_introducing_again_updates_definition_and_keeps_mappings(client, northwind_saved):
    client.post("/labra-fields", json=DUNS)
    client.patch(
        "/mapping-templates/northwind-default/fields/customer_duns",
        json={"crm_object": "Account", "crm_field": "DUNS__c"},
    )

    response = client.post("/labra-fields", json={**DUNS, "label": "D-U-N-S number"})
    assert response.status_code == 200

    duns = _by_field(client.get("/mapping-templates/northwind-default").json())["customer_duns"]
    assert duns["label"] == "D-U-N-S number"
    assert (duns["crm_object"], duns["crm_field"], duns["mapped"]) == ("Account", "DUNS__c", True)


def test_new_field_required_defaults_to_false(client):
    response = client.post("/labra-fields", json={"labra_field": "customer_duns", "label": "Customer DUNS"})
    assert response.json()["required"] is False


def test_new_field_appears_once_at_the_end_of_the_list(client):
    client.post("/labra-fields", json=DUNS)
    client.post("/labra-fields", json=DUNS)
    names = [f["labra_field"] for f in client.get("/labra-fields").json()]
    assert names[-1] == "customer_duns"
    assert names.count("customer_duns") == 1


@pytest.mark.parametrize(
    "body, fix",
    [
        ({"labra_field": "Customer DUNS", "label": "x"}, "Send a name such as customer_duns."),
        ({"labra_field": "customer_duns", "label": "  "}, 'Send a non-empty label such as "Customer DUNS".'),
    ],
)
def test_introduce_rejects_bad_definitions(client, body, fix):
    response = client.post("/labra-fields", json=body)
    assert response.status_code == 422
    assert fix in [e["fix"] for e in response.json()["errors"]]


def test_new_field_reaches_10000_existing_templates_without_writing_them(client, db):
    """Scale check: one new field, 10,000 saved templates, zero template rows written."""
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO templates (id, name, crm_type) VALUES (?, ?, 'salesforce')",
            [(f"t-{i}", f"Template {i}") for i in range(10_000)],
        )
        conn.executemany(
            "INSERT INTO template_mappings (template_id, labra_field, crm_object, crm_field) "
            "VALUES (?, ?, ?, ?)",
            [(f"t-{i}", f, o, c) for i in range(10_000) for f, o, c in NORTHWIND_MAPPINGS],
        )
    before = _template_rows(db)

    started = time.perf_counter()
    assert client.post("/labra-fields", json=DUNS).status_code == 201
    elapsed = time.perf_counter() - started

    assert _template_rows(db) == before
    assert elapsed < 1.0  # one row insert; generous bound for slow CI machines
    for template_id in ("t-0", "t-4999", "t-9999"):
        fields = _by_field(client.get(f"/mapping-templates/{template_id}").json())
        assert fields["customer_duns"]["mapped"] is False
        assert fields["company_name"]["crm_object"] == "Account"


# ---------------------------------------------------------------- unused / mapping choices


def test_unused_survives_a_label_change(client, northwind_saved):
    client.post("/labra-fields", json=DUNS)
    response = client.patch("/mapping-templates/northwind-default/fields/customer_duns", json={"unused": True})
    assert response.status_code == 200

    client.post("/labra-fields", json={**DUNS, "label": "DUNS number"})

    duns = _by_field(client.get("/mapping-templates/northwind-default").json())["customer_duns"]
    assert duns["unused"] is True
    assert duns["mapped"] is False
    assert duns["label"] == "DUNS number"


def test_marking_required_field_unused_is_rejected(client, northwind_saved):
    response = client.patch("/mapping-templates/northwind-default/fields/company_name", json={"unused": True})

    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["field"] == "company_name"
    assert "required by AWS" in error["message"]
    assert "required by AWS" in error["fix"]

    fields = _by_field(client.get("/mapping-templates/northwind-default").json())
    assert fields["company_name"]["mapped"] is True


def test_unmapping_required_field_is_rejected(client, northwind_saved):
    response = client.patch("/mapping-templates/northwind-default/fields/website", json={})
    assert response.status_code == 422


def test_optional_field_can_go_back_to_unmapped(client, northwind_saved):
    client.post("/labra-fields", json=DUNS)
    client.patch("/mapping-templates/northwind-default/fields/customer_duns", json={"unused": True})
    client.patch("/mapping-templates/northwind-default/fields/customer_duns", json={"unused": False})

    duns = _by_field(client.get("/mapping-templates/northwind-default").json())["customer_duns"]
    assert (duns["unused"], duns["mapped"]) == (False, False)


def test_mapping_a_crm_value_already_in_use_is_rejected(client, northwind_saved):
    client.post("/labra-fields", json=DUNS)
    response = client.patch(
        "/mapping-templates/northwind-default/fields/customer_duns",
        json={"crm_object": "account", "crm_field": "name"},
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["fix"] == "Pick one Labra field for that CRM value."


def test_mapping_needs_object_and_field(client, northwind_saved):
    client.post("/labra-fields", json=DUNS)
    response = client.patch(
        "/mapping-templates/northwind-default/fields/customer_duns", json={"crm_object": "Account"}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["fix"] == "Fill both, or mark the field unused."


def test_choice_on_unknown_template_or_field_is_not_found(client, northwind_saved):
    assert client.patch("/mapping-templates/nope/fields/website", json={"unused": True}).status_code == 404
    assert client.patch(
        "/mapping-templates/northwind-default/fields/favorite_color", json={"unused": True}
    ).status_code == 404
