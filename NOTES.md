# Notes

_Final write-up goes here at the end (schema, 10k templates, several CRM objects,
rate limits, one thing left out)._

## Working log (rough, cleaned up at the end)

- Chose SQLite: real database, zero setup for the reviewer.
- `create_app(crm)` takes the CRM client as a parameter so tests inject `FakeCrm`.
