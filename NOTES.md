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
- New field endpoint is an upsert (`POST /labra-fields`, ON CONFLICT DO UPDATE): first call
  introduces, later calls update label/required. Touches only `labra_fields`.
- Test proves "no second write": snapshot of every templates + template_mappings row is
  identical before/after introducing customer_duns.
- 10k test: 10,000 templates x 6 mappings seeded, introduce field, still 0 template rows
  written; the request itself is one INSERT. GET per template is an indexed LEFT JOIN on
  the (template_id, labra_field) primary key.
- Unused is stored per template in `template_mappings.unused`; label lives in
  `labra_fields`, so changing the label cannot reset the choice.
- Marking unused clears crm_object/crm_field (frees that CRM value for another field).
- Open question: if a field is later flipped to required=true, templates that marked it
  unused/unmapped will fail referral validation with a "map this field" fix. I chose not
  to block the flip; AWS owns the list.
