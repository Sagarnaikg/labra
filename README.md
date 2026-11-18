# Labra mapping templates

A small API that maps CRM fields (Salesforce / HubSpot) to Labra referral fields
and builds a validated referral from a CRM record.

Design, schema details and trade-offs: **[NOTES.md](NOTES.md)**.

## 1. Setup (Python 3.11+)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.db            # creates ./labra.db (optional: the API also creates it on start)
```

## 2. Run the tests

```bash
pytest
```

## 3. Start the API

```bash
uvicorn app.main:create_app --factory --reload
```

- API: http://localhost:8000
- Try every endpoint in the browser: http://localhost:8000/docs

No real CRM is needed. The running app uses a built-in fake CRM that already has
record `opp-100` (Northwind, Salesforce) and `deal-200` (Contoso, HubSpot).

## 4. Try it

Run these in a second terminal while the API is running.

**Create Northwind's template**

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

**Add a new Labra field**

```bash
curl -s -X POST localhost:8000/labra-fields -H 'Content-Type: application/json' \
  -d '{"labra_field": "customer_duns", "label": "Customer DUNS", "required": false}'
```

**Get the template: the old template now shows `customer_duns` (unmapped)**

```bash
curl -s localhost:8000/mapping-templates/northwind-default | python -m json.tool
```

**Mark an optional field unused** (a required field like `company_name` is rejected)

```bash
curl -s -X PATCH localhost:8000/mapping-templates/northwind-default/fields/customer_duns \
  -H 'Content-Type: application/json' -d '{"unused": true}'
```

**Build the referral for `opp-100`**

```bash
curl -s -X POST localhost:8000/mapping-templates/northwind-default/crm-field-values \
  -H 'Content-Type: application/json' -d '{"record_id": "opp-100"}' | python -m json.tool
```

## Schema

Three SQLite tables. Full SQL is in [app/schema.sql](app/schema.sql); the reasoning is in NOTES.md.

| Table | Holds |
|---|---|
| `labra_fields` | The shared list of Labra fields, one row per field |
| `templates` | One row per customer template |
| `template_mappings` | Each template's choices: which CRM object and field fill a Labra field, or "unused" |
