"""Template payloads from the assignment, reused across tests."""
from copy import deepcopy


def _mappings(rows):
    return [{"labra_field": f, "crm_object": o, "crm_field": c} for f, o, c in rows]


NORTHWIND_MAPPINGS = [
    ("company_name", "Account", "Name"),
    ("website", "Account", "Website"),
    ("country", "Account", "BillingCountry"),
    ("expected_monthly_revenue", "Opportunity", "Amount"),
    ("target_close_date", "Opportunity", "CloseDate"),
    ("primary_sales_rep_email", "Contact", "Email"),
]

CONTOSO_MAPPINGS = [
    ("company_name", "companies", "name"),
    ("website", "companies", "website"),
    ("country", "companies", "country"),
    ("expected_monthly_revenue", "deals", "amount"),
    ("target_close_date", "deals", "closedate"),
    ("primary_sales_rep_email", "contacts", "email"),
]


def northwind() -> dict:
    return {
        "id": "northwind-default",
        "name": "Northwind default",
        "crm_type": "salesforce",
        "mappings": _mappings(NORTHWIND_MAPPINGS),
    }


def contoso() -> dict:
    return {
        "id": "contoso-default",
        "name": "Contoso default",
        "crm_type": "hubspot",
        "mappings": _mappings(CONTOSO_MAPPINGS),
    }


def copy(payload: dict) -> dict:
    return deepcopy(payload)
