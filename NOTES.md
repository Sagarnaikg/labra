# Notes

_Final write-up goes here at the end (schema, 10k templates, several CRM objects,
rate limits, one thing left out)._

## Working log (rough, cleaned up at the end)

- Chose SQLite: real database, zero setup for the reviewer.
- `create_app(crm)` takes the CRM client as a parameter so tests inject `FakeCrm`.
- Schema = 3 tables. Field list lives only in `labra_fields`; templates store only the
  customer's choices in `template_mappings`. A new field = 1 row, 0 template rows written.
- DB constraints back up the API checks: FK to `labra_fields`, used mapping needs
  object+field, UNIQUE (template, crm_object, crm_field).
- Seed uses INSERT OR IGNORE so restarts never undo a label/required change.
- Switched to `uvicorn app.main:create_app --factory` so importing the app in tests
  never creates a stray `labra.db`.
