# Labra mapping templates

Small HTTP service that maps CRM fields (Salesforce / HubSpot) onto Labra referral
fields, picks up newly introduced Labra fields on templates saved earlier, and
builds a validated referral from a (fake) CRM.

Stack: Python 3.11+, FastAPI, Pydantic v2, SQLite, pytest.

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

## Walkthrough

_TODO: create Northwind, add `customer_duns`, GET the template, call `crm-field-values`._
