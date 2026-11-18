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
- GET and crm-field-values both go through one `load_template()` (LEFT JOIN of
  labra_fields + template_mappings), so "the template the customer sees" has one definition.
- Create validates everything and returns ALL errors, then writes template + mappings in one
  transaction, so a rejected create stores nothing.
- Request model uses loose `str` types on purpose so business-rule failures (blank name,
  bad crm_type) come back with our `fix`, not a generic schema error.
- Duplicate CRM value check is case-insensitive (Salesforce API names are).
- Added 409 on duplicate template id (not in the spec, but silently overwriting would be worse).
