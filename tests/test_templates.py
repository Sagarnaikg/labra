"""Create and read mapping templates, including every create rejection rule."""
import pytest

from tests.payloads import CONTOSO_MAPPINGS, NORTHWIND_MAPPINGS, contoso, northwind


def _by_field(body):
    return {f["labra_field"]: f for f in body["fields"]}


def _template_count(db):
    with db.connect() as conn:
        return (
            conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM template_mappings").fetchone()[0],
        )


# ---------------------------------------------------------------- happy path


def test_create_northwind_and_get_returns_all_six_mappings(client):
    created = client.post("/mapping-templates", json=northwind())
    assert created.status_code == 201, created.json()

    response = client.get("/mapping-templates/northwind-default")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "northwind-default"
    assert body["crm_type"] == "salesforce"

    fields = _by_field(body)
    assert list(fields) == [f for f, _, _ in NORTHWIND_MAPPINGS]
    for labra_field, crm_object, crm_field in NORTHWIND_MAPPINGS:
        assert fields[labra_field]["crm_object"] == crm_object
        assert fields[labra_field]["crm_field"] == crm_field
        assert fields[labra_field]["mapped"] is True
        assert fields[labra_field]["unused"] is False
        assert fields[labra_field]["required"] is True


def test_same_labra_field_mapped_differently_per_template(client):
    assert client.post("/mapping-templates", json=northwind()).status_code == 201
    assert client.post("/mapping-templates", json=contoso()).status_code == 201

    nw = _by_field(client.get("/mapping-templates/northwind-default").json())
    co = _by_field(client.get("/mapping-templates/contoso-default").json())
    assert (nw["company_name"]["crm_object"], nw["company_name"]["crm_field"]) == ("Account", "Name")
    assert (co["company_name"]["crm_object"], co["company_name"]["crm_field"]) == ("companies", "name")
    assert [f for f, _, _ in CONTOSO_MAPPINGS] == list(co)


def test_unknown_template_is_not_found(client):
    response = client.get("/mapping-templates/does-not-exist")
    assert response.status_code == 404
    [error] = response.json()["errors"]
    assert "does-not-exist" in error["message"]
    assert error["fix"]


def test_duplicate_template_id_is_conflict(client):
    assert client.post("/mapping-templates", json=northwind()).status_code == 201
    response = client.post("/mapping-templates", json=northwind())
    assert response.status_code == 409


# ---------------------------------------------------------------- rejections


def _mutate(fn):
    payload = northwind()
    fn(payload)
    return payload


def _set_mapping(payload, labra_field, **changes):
    for m in payload["mappings"]:
        if m["labra_field"] == labra_field:
            m.update(changes)


REJECTIONS = [
    pytest.param(
        lambda p: p.update(name="   "),
        "Send a non-empty template name.",
        id="blank-name",
    ),
    pytest.param(
        lambda p: p.update(crm_type="zoho"),
        "Send salesforce or hubspot.",
        id="bad-crm-type",
    ),
    pytest.param(
        lambda p: p["mappings"].append(
            {"labra_field": "favorite_color", "crm_object": "Account", "crm_field": "Color"}
        ),
        "Remove that mapping or add the field first.",
        id="unknown-labra-field",
    ),
    pytest.param(
        lambda p: p["mappings"].append(
            {"labra_field": "company_name", "crm_object": "Account", "crm_field": "LegalName"}
        ),
        "Keep one CRM object and field per Labra field.",
        id="labra-field-twice",
    ),
    pytest.param(
        lambda p: _set_mapping(p, "website", crm_object="Account", crm_field="Name"),
        "Pick one Labra field for that CRM value.",
        id="same-crm-value-twice",
    ),
    pytest.param(
        lambda p: _set_mapping(p, "website", crm_field="  "),
        "Fill both, or mark the field unused.",
        id="blank-crm-field",
    ),
    pytest.param(
        lambda p: p.update(mappings=[m for m in p["mappings"] if m["labra_field"] != "country"]),
        "Map that field before saving.",
        id="required-field-unmapped",
    ),
]


@pytest.mark.parametrize("mutate, expected_fix", REJECTIONS)
def test_create_rejects_and_stores_nothing(client, db, mutate, expected_fix):
    response = client.post("/mapping-templates", json=_mutate(mutate))

    assert response.status_code == 422, response.json()
    errors = response.json()["errors"]
    assert expected_fix in [e["fix"] for e in errors]
    for e in errors:
        assert set(e) == {"field", "crm_object", "crm_field", "received", "message", "fix"}
    assert _template_count(db) == (0, 0)


def test_create_reports_every_problem_at_once(client):
    payload = northwind()
    payload["name"] = ""
    payload["crm_type"] = "zoho"
    response = client.post("/mapping-templates", json=payload)

    fixes = [e["fix"] for e in response.json()["errors"]]
    assert "Send a non-empty template name." in fixes
    assert "Send salesforce or hubspot." in fixes


def test_create_rejects_required_field_marked_unused(client, db):
    payload = northwind()
    _set_mapping(payload, "company_name", unused=True, crm_object=None, crm_field=None)
    response = client.post("/mapping-templates", json=payload)

    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["field"] == "company_name"
    assert "required by AWS" in error["message"]
    assert _template_count(db) == (0, 0)


def test_malformed_body_uses_the_same_error_shape(client):
    response = client.post("/mapping-templates", json={"mappings": "not a list"})
    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["field"] == "mappings"
    assert error["fix"]
