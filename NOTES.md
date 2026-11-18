# NOTES

**Status: complete.** Every behaviour in the brief is implemented and covered by
tests (`pytest`: 62 passing, no network). Limits and things I did not build are
listed at the end.

The one idea behind the design: **a template stores only the customer's own
choices. The list of Labra fields lives in one table and is joined in when a
template is read.** Nothing is ever copied from the field list onto a template,
so there is nothing to backfill when the list grows.

---

## 1. Schema

```sql
labra_fields      (name PK, label, required, sort_order, created_at, updated_at)
templates         (id PK, name, crm_type CHECK IN ('salesforce','hubspot'), created_at, updated_at)
template_mappings (template_id FK, labra_field FK -> labra_fields.name,
                   crm_object NULL, crm_field NULL, unused 0/1,
                   PRIMARY KEY (template_id, labra_field),
                   UNIQUE (template_id, crm_object, crm_field),
                   CHECK (unused = 1 OR (crm_object IS NOT NULL AND crm_field IS NOT NULL)))
```

Full DDL is in [app/schema.sql](app/schema.sql) and the README.

**Where things live**
- **The shared Labra field list:** `labra_fields`, one row per field. Owned by Labra/AWS.
- **Northwind's CRM mappings:** `template_mappings`, six rows keyed by
  `('northwind-default', labra_field)`. Each row holds `Account.Name` and so on, or `unused = 1`.
- **A field with no row is "not mapped".** `GET` builds the template from
  `labra_fields LEFT JOIN template_mappings`. The same `load_template()` also drives the
  CRM fetch plan and the referral model, so "the template the customer sees" has one
  definition in code.

**Introducing `customer_duns`**
- `POST /labra-fields {"labra_field": "customer_duns", "label": "Customer DUNS", "required": false}`
- **Written:** 1 row inserted into `labra_fields`.
- **Not written:** every `templates` row and every `template_mappings` row.
- The next `GET` of Northwind joins the new row in. `customer_duns` has no Northwind row,
  so it comes back with `crm_object: null`, `crm_field: null`, `mapped: false`.
  `company_name` still reads `Account.Name`, because nothing touched that row.
- `test_introduce_customer_duns_shows_on_template_saved_before` snapshots every
  template and mapping row before the call and asserts they are identical afterwards.
- Calling the endpoint again is an upsert. It changes only the label or `required`, so
  mappings and `unused` choices stay as the customer left them. The choice lives on
  `template_mappings` and the label on `labra_fields`.

**What I refuse to store**
- A copy of the default field JSON on each template. That copy is exactly what goes
  stale when AWS adds a field.
- Without it, someone would have had to open each of the saved templates and paste
  `customer_duns` in, or write a one-off script that rewrites every JSON document.

## 2. 10,000 existing templates, one new AWS field

- **Cost of the deploy:** one `INSERT` into `labra_fields`. It takes about a millisecond and
  does not depend on the number of templates. There is no migration and no backfill job.
- **Evidence:** `test_new_field_reaches_10000_existing_templates_without_writing_them`
  seeds 10,000 templates × 6 mappings, introduces the field, and asserts:
  - zero template rows changed
  - the request took under 1 second (in practice a few ms)
  - templates t-0, t-4999 and t-9999 all show `customer_duns` unmapped
- **What is locked:** in SQLite, the database write lock for the length of a one-row insert.
  In Postgres, one new row. Templates stay readable and editable throughout. A referral
  being built at that moment sees either the old or the new list, and both are valid,
  because a new field is optional by default.
- **What my choice costs instead:**
  1. **Every read is a join.** It is an indexed lookup on the
     `(template_id, labra_field)` primary key, so the cost grows with the number of fields
     (6–7), not the number of templates. It is cheap, but it happens on every GET and every
     referral, where a copied document would be a single key lookup.
  2. **Flipping a field to `required: true` affects every template at once.** Templates that
     have not mapped it start failing referral validation, with a fix that names the field.
     That is the correct signal, but the rollout should be: introduce it optional, let
     customers map it, then flip it. A query that lists the templates still missing a
     required field would support that, and it is one `LEFT JOIN … WHERE m.labra_field IS NULL`.
  3. **The field list is global.** Customers cannot invent fields. That matches the brief.
- **The alternative I rejected:** a full JSON copy per template plus a migration script.
  It would make 10,000 row rewrites per new field, has to be batched to avoid long locks,
  and races with customers who are editing their templates during the migration.

## 3. Several CRM objects

- **How the fetch list is built:** `plan_fetches()` in [app/crm.py](app/crm.py) walks the
  merged template in field order. It keeps only fields that are mapped and not unused, and
  groups their `crm_field`s by `crm_object`. Northwind becomes:
  - Account: Name, Website, BillingCountry
  - Opportunity: Amount, CloseDate
  - Contact: Email

  That is 3 calls.
- **New fields:** a newly introduced field is fetched only once it is mapped. It then joins
  its object's existing call (`Account` gains `DUNS__c`), so there is still no extra call.
- **Unused fields:** they are not fetched. If all of Contact's fields are unused, Contact
  is not called at all.
- **Ten fields on one object:** still one call, with a list of ten field names.
  Duplicates are dropped.
- **Failures are per object.** An object that is rate limited twice, or has no record, is
  reported once by name. Its fields are skipped rather than reported as missing, and the
  other objects are still validated.

## 4. Rate limits at scale

**The load:** 200 prepares/min × 3 objects = 600 calls/min. The CRM allows 100 per 10 s,
which is also 600/min. That is exactly at the limit on average, so any burst goes over.
Several API processes each counting on their own cannot see that.

**Where the limiter lives:** in a shared Redis, called from `fetch_with_retry()` in
[app/crm.py](app/crm.py). That function is the single place every CRM call goes through,
and it already wraps `FakeCrm.fetch` / `crm.fetch` with the one retry. The limiter goes
directly around that `crm.fetch(...)` line: acquire first, then call.

**Key:** `crm_rl:{crm_type}:{org_id or portal_id}`. The limit belongs to the customer's CRM
account (their Salesforce org or HubSpot portal), not to our process or our template.
- It is a token bucket in a small Lua script, so acquire is atomic across processes:
  capacity 100, refill 10/s.
- **Reserve every token a prepare needs in one step.** For Northwind that is 3. A prepare
  then either gets all its calls or none, instead of fetching Account and stalling on Contact.
- When the CRM still answers `RateLimited` with a retry-after, write
  `crm_cooldown:{key}` with that TTL so every process backs off, not only the one that was told.

**A prepare that cannot get a slot within ~2 s:**
- Don't hold the HTTP request open. Put the prepare on a queue for that CRM account and
  return `202 Accepted` with a job id. A worker per key drains the queue at the bucket rate.
  The caller polls or gets a callback.
- If queuing is not available, return `503` with `Retry-After`. That is what the current
  double-rate-limit path already does.

**What is implemented today:** only the single retry.
- On the first `RateLimited`, sleep `retry_after_ms`, then call `crm.fetch` once more.
- On a second `RateLimited`, the request ends with **503 + `Retry-After`** and one error
  naming the object ("The Salesforce rate limit held for Opportunity…").
- `expected_monthly_revenue` and `target_close_date` are not reported as missing.
- Tests: `test_one_rate_limit_is_retried_and_referral_succeeds`,
  `test_rate_limit_twice_names_opportunity_and_not_its_fields`.

## 5. One thing left out: optimistic concurrency on templates

Two people editing the same template today means the last write wins. Here is where the
fix would attach:
- **Store:** add `templates.version INTEGER NOT NULL DEFAULT 1`. Return it from `GET` as
  `version` and as an `ETag`.
- **Write path:** `PATCH /mapping-templates/{id}/fields/{field}` (and any future full-template
  update) requires `If-Match: <version>`.
- **In the same transaction:** first
  `UPDATE templates SET version = version + 1 WHERE id = ? AND version = ?`.
  - 0 rows updated means someone else saved first. Roll back and return `409` with a fix
    like "Reload the template and reapply your change".
  - 1 row updated means it is safe to write the mapping row.
- **Not needed for field changes:** introducing a Labra field never bumps template
  versions, because it does not write templates. A customer's edit cannot conflict with
  AWS adding a field.

---

## Decisions worth flagging

- **Field format rules are code, the field list is data.** The referral Pydantic model is
  built per request with `create_model` from the template's field list, so there is no
  second field list in code. `FIELD_RULES` in [app/referral.py](app/referral.py) maps a
  field name to *how* to validate it (URL, country, > 0, date, email, 9-digit DUNS).
  It cannot drift into a missing or extra referral key:
  - A field with no rule is validated as non-blank text.
  - A rule for a field that is not on the list is never used.

  The cost is that a new field that needs a special format, like DUNS, also needs one
  entry in code. The next step would be a `value_type` column on `labra_fields`, so the
  format is chosen when the field is introduced.
- **Rate limited twice returns `503`, not `422`.** The upstream CRM refused us; the
  customer's data is not wrong.
- **Extras not in the brief:**
  - `409` on a duplicate template id, instead of silently overwriting.
  - The duplicate `crm_object + crm_field` check ignores case, because Salesforce API
    names are case-insensitive.
  - Malformed request bodies come back in the same error shape.
- **Website validation uses `urlparse`, not `HttpUrl`.** `HttpUrl` rewrites
  `https://northwind.example` to `…example/`, which breaks the expected success body.
- **A Pydantic gotcha I hit:** putting `StringConstraints` after a `BeforeValidator` in the
  same `Annotated` silently drops `min_length`, so a blank company name passed. The
  invalid-seed test caught it, and I fixed it by nesting the constrained `str`.
- **Injected dependencies:** "today" is injected (`create_app(today=...)`) and frozen to
  2026-11-18 in tests. `sleep` is injectable too. Routes never construct a CRM client.
  Run without one, `create_app()` uses a seeded demo FakeCrm so the README walkthrough works.

## Not built / limits

- **No full-template update or delete endpoint.** Edits go through the per-field `PATCH`.
- **No delete or rename for a Labra field.** Delete is blocked by the FK from
  `template_mappings`. It would need a "retired" flag rather than a hard delete.
- **CRM calls run one after another.** With a real client they could run concurrently
  (`asyncio.gather`), still one call per object.
- **Not built, as the brief asks:** the shared limiter, queue and cache, plus auth, a UI,
  and real Salesforce or HubSpot clients.

---

## Appendix A: API reference

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

## Appendix B: Project layout

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
