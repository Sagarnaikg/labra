"""Demo CRM used when the API is started without an injected client
(`uvicorn app.main:create_app --factory`). Lets the README walkthrough call
crm-field-values for opp-100 with no real Salesforce or HubSpot.
"""
from datetime import datetime, timedelta, timezone

from app.crm import FakeCrm


def demo_crm() -> FakeCrm:
    # Close dates relative to today so the demo never goes stale.
    soon = (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()
    crm = FakeCrm()
    crm.seed("Account", "opp-100", {
        "Name": "Northwind Traders",
        "Website": "https://northwind.example",
        "BillingCountry": "United States",
        "InternalNotes": "do not copy me",
    })
    crm.seed("Opportunity", "opp-100", {"Amount": 5000, "CloseDate": soon})
    crm.seed("Contact", "opp-100", {"Email": "ada@northwind.example"})

    crm.seed("companies", "deal-200", {
        "name": "Contoso Ltd",
        "website": "https://contoso.example",
        "country": "Germany",
    })
    crm.seed("deals", "deal-200", {"amount": "12000", "closedate": soon})
    crm.seed("contacts", "deal-200", {"email": "lee@contoso.example"})
    return crm
