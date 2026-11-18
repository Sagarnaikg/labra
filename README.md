# Labra mapping templates

Small HTTP service that maps CRM fields (Salesforce / HubSpot) onto Labra referral
fields, picks up newly introduced Labra fields on templates saved earlier, and
builds a validated referral from a (fake) CRM.

Stack: Python 3.11+ (tested on 3.13), FastAPI, Pydantic v2, SQLite, pytest.

Design write-up and trade-offs: [NOTES.md](NOTES.md).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Create the database

```bash
python -m app.db          # creates ./labra.db (set LABRA_DB_PATH to change the path)
```

The API also creates the tables and seeds the six original fields on startup,
so this step is optional. Seeding is idempotent and never overwrites a field
that was changed later.

## Run the API

```bash
uvicorn app.main:create_app --factory --reload
```

Interactive docs: http://localhost:8000/docs

## Run the tests

```bash
pytest
```

## API

All errors use one shape: `{"errors": [{"field", "crm_object", "crm_field", "received", "message", "fix"}]}`.

### `POST /mapping-templates`: create a template

```json
{
  "id": "northwind-default",
  "name": "Northwind default",
  "crm_type": "salesforce",
  "mappings": [
    {"labra_field": "company_name", "crm_object": "Account", "crm_field": "Name"},
    {"labra_field": "website", "crm_object": "Account", "crm_field": "Website"},
    {"labra_field": "country", "crm_object": "Account", "crm_field": "BillingCountry"},
    {"labra_field": "expected_monthly_revenue", "crm_object": "Opportunity", "crm_field": "Amount"},
    {"labra_field": "target_close_date", "crm_object": "Opportunity", "crm_field": "CloseDate"},
    {"labra_field": "primary_sales_rep_email", "crm_object": "Contact", "crm_field": "Email"}
  ]
}
```

- `id` is optional (defaults to a slug of `name`).
- A mapping may send `"unused": true` instead of `crm_object`/`crm_field` for an optional field.
- `201` returns the template (same shape as GET). `422` lists every problem and stores nothing.
  `409` if the id already exists.

### `GET /mapping-templates/{template_id}`: read a template

Returns every current Labra field. Unmapped fields have `crm_object: null`,
`crm_field: null`, `mapped: false`.

```json
{
  "id": "northwind-default", "name": "Northwind default", "crm_type": "salesforce",
  "fields": [
    {"labra_field": "company_name", "label": "Company name", "required": true,
     "crm_object": "Account", "crm_field": "Name", "mapped": true, "unused": false}
  ]
}
```

### `POST /labra-fields`: introduce a Labra field (or update one)

```json
{"labra_field": "customer_duns", "label": "Customer DUNS", "required": false}
```

- `required` defaults to `false`.
- `201` when the field is new, `200` when an existing field's label/required was updated.
- Writes one row in `labra_fields`. No template is written; every template shows the
  field on its next GET (unmapped, unless that template already mapped it).

`GET /labra-fields` lists the current field list.

### `PATCH /mapping-templates/{template_id}/fields/{labra_field}`: one customer choice

| Body | Effect |
|---|---|
| `{"unused": true}` | Hide an optional field (not fetched, not on the referral). Rejected for a required field. |
| `{"crm_object": "Account", "crm_field": "DUNS__c"}` | Map the field. |
| `{"unused": false}` | Back to unmapped (optional fields only). |

Returns the full template. The choice lives on the template, so it survives label
changes to the field.

### `POST /mapping-templates/{template_id}/crm-field-values`: build a referral

```json
{"record_id": "opp-100"}
```

The body carries only the record id. The service reads the template the customer
sees, groups mapped and used fields by CRM object (Northwind = 3 fetches: Account,
Opportunity, Contact), asks the injected CRM client, then validates with Pydantic.

| Status | When |
|---|---|
| `200` | `{"referral": {...}, "errors": []}` |
| `422` | Invalid CRM data: `{"referral": null, "errors": [...]}`, one error per bad field |
| `503` | An object was rate limited twice (`Retry-After` header); the error names the object, not its fields |
| `404` | Unknown template, or no CRM object has that record id |

When started with `uvicorn`, the app uses a demo FakeCrm seeded with `opp-100`
(Northwind) and `deal-200` (Contoso), see [app/demo.py](app/demo.py). Tests inject
their own `FakeCrm` through `create_app(crm)`.

## Schema

Three SQLite tables ([app/schema.sql](app/schema.sql)):

| Table | Holds | Example |
|---|---|---|
| `labra_fields` | The shared Labra field list (one row per field) | `company_name`, `customer_duns` |
| `templates` | One row per customer template | `northwind-default`, salesforce |
| `template_mappings` | Only the customer's own choices per template | `northwind-default` / `company_name` -> `Account.Name` |

A template never stores a copy of the field list. `GET` joins `labra_fields`
with that template's rows; a field with no row shows as unmapped.

```sql
-- Labra mapping-template schema (SQLite).
--
-- Rule of thumb: a template stores ONLY the customer's own choices.
-- The list of Labra fields lives in exactly one place (labra_fields) and is
-- joined in at read time, so a newly introduced field shows up on every
-- template without touching any template row.

-- 1. Shared Labra field list. Owned by Labra/AWS, one row per field.
CREATE TABLE IF NOT EXISTS labra_fields (
    name        TEXT    PRIMARY KEY,               -- e.g. 'company_name', 'customer_duns'
    label       TEXT    NOT NULL,                  -- e.g. 'Company name'
    required    INTEGER NOT NULL DEFAULT 0         -- 1 = AWS requires it
                CHECK (required IN (0, 1)),
    sort_order  INTEGER NOT NULL,                  -- display order on GET
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 2. One row per customer template.
CREATE TABLE IF NOT EXISTS templates (
    id          TEXT    PRIMARY KEY,               -- e.g. 'northwind-default'
    name        TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    crm_type    TEXT    NOT NULL CHECK (crm_type IN ('salesforce', 'hubspot')),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 3. The customer's choices for one template: which CRM object + field fills a
--    Labra field, or that the field is unused. No row = "not mapped yet".
CREATE TABLE IF NOT EXISTS template_mappings (
    template_id TEXT    NOT NULL REFERENCES templates (id) ON DELETE CASCADE,
    labra_field TEXT    NOT NULL REFERENCES labra_fields (name),
    crm_object  TEXT,                              -- e.g. 'Account'   (NULL when unused)
    crm_field   TEXT,                              -- e.g. 'Name'      (NULL when unused)
    unused      INTEGER NOT NULL DEFAULT 0 CHECK (unused IN (0, 1)),
    PRIMARY KEY (template_id, labra_field),
    -- A used mapping must name both the CRM object and field.
    CHECK (unused = 1 OR (crm_object IS NOT NULL AND crm_field IS NOT NULL)),
    -- One CRM value cannot feed two Labra fields on the same template.
    UNIQUE (template_id, crm_object, crm_field)
);
```

## Walkthrough (curl)

Start the API in one terminal (`uvicorn app.main:create_app --factory --reload`),
then run these in another. The running app uses a demo FakeCrm seeded with
`opp-100`, see [app/demo.py](app/demo.py).

**1. Create Northwind's template**

```bash
curl -s -X POST localhost:8000/mapping-templates -H 'Content-Type: application/json' -d '{
  "id": "northwind-default", "name": "Northwind default", "crm_type": "salesforce",
  "mappings": [
    {"labra_field": "company_name", "crm_object": "Account", "crm_field": "Name"},
    {"labra_field": "website", "crm_object": "Account", "crm_field": "Website"},
    {"labra_field": "country", "crm_object": "Account", "crm_field": "BillingCountry"},
    {"labra_field": "expected_monthly_revenue", "crm_object": "Opportunity", "crm_field": "Amount"},
    {"labra_field": "target_close_date", "crm_object": "Opportunity", "crm_field": "CloseDate"},
    {"labra_field": "primary_sales_rep_email", "crm_object": "Contact", "crm_field": "Email"}
  ]}'
```

**2. Introduce `customer_duns` (after Northwind was saved)**

```bash
curl -s -X POST localhost:8000/labra-fields -H 'Content-Type: application/json' \
  -d '{"labra_field": "customer_duns", "label": "Customer DUNS", "required": false}'
```

**3. GET the old template: `customer_duns` is there, unmapped; `company_name` is still `Account.Name`**

```bash
curl -s localhost:8000/mapping-templates/northwind-default | python -m json.tool
```

```json
{"labra_field": "customer_duns", "label": "Customer DUNS", "required": false,
 "crm_object": null, "crm_field": null, "mapped": false, "unused": false}
```

**4. Optional: mark it unused, or try to hide a required field (rejected)**

```bash
curl -s -X PATCH localhost:8000/mapping-templates/northwind-default/fields/customer_duns \
  -H 'Content-Type: application/json' -d '{"unused": true}'
curl -s -X PATCH localhost:8000/mapping-templates/northwind-default/fields/company_name \
  -H 'Content-Type: application/json' -d '{"unused": true}'
```

**5. Build the referral for `opp-100`**

```bash
curl -s -X POST localhost:8000/mapping-templates/northwind-default/crm-field-values \
  -H 'Content-Type: application/json' -d '{"record_id": "opp-100"}' | python -m json.tool
```

```json
{
  "referral": {
    "company_name": "Northwind Traders",
    "website": "https://northwind.example",
    "country": "United States",
    "expected_monthly_revenue": 5000,
    "target_close_date": "<today + 30 days>",
    "primary_sales_rep_email": "ada@northwind.example"
  },
  "errors": []
}
```

## Project layout

```
app/
  main.py              create_app(crm, db, today, sleep): the seam tests use
  db.py, schema.sql    SQLite schema + idempotent seed of the six fields
  templates.py         create / get template, per-field choices (map, unused)
  fields.py            introduce / update a Labra field
  crm.py               FakeCrm, fetch plan (one call per object), single retry
  referral.py          Pydantic referral model built from the field list + error translation
  crm_field_values.py  POST .../crm-field-values
  errors.py            shared error shape
  demo.py              demo FakeCrm for local runs
tests/                 pytest, no network; FakeCrm injected, "today" frozen
```
