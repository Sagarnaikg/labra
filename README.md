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

## Run the API

```bash
uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs

## Run the tests

```bash
pytest
```

## Schema

_TODO: added with the database step._

## Walkthrough

_TODO: create Northwind, add `customer_duns`, GET the template, call `crm-field-values`._
