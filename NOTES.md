# Labra mapping templates: design notes

## Contents

1. [The problem](#1-the-problem)
2. [The solution in one picture](#2-the-solution-in-one-picture)
3. [Schema](#3-schema)
4. [Introducing a new field](#4-introducing-a-new-field)
5. [Customer choices: map, unused, required](#5-customer-choices-map-unused-required)
6. [Building a referral](#6-building-a-referral)
7. [Rate limits at scale](#7-rate-limits-at-scale)
8. [Boundaries and decisions](#8-boundaries-and-decisions)
9. [Scoped out, and how to extend](#9-scoped-out-and-how-to-extend)
10. [How it was built](#10-how-it-was-built)
11. [Test map](#11-test-map)
- [Appendix A: API reference](#appendix-a-api-reference)
- [Appendix B: Project layout](#appendix-b-project-layout)

---

## 1. The problem

Labra turns a CRM deal into a **referral** that is shared with AWS. The referral is a
fixed set of Labra fields (`company_name`, `website`, `expected_monthly_revenue`, …).
Salesforce and HubSpot use their own names, so each customer has a **mapping
template**: `Account.Name` fills `company_name`, `Opportunity.Amount` fills
`expected_monthly_revenue`, and so on.

**What broke before.** A large default JSON lived in code, and creating a template
copied the whole JSON onto the template row. When AWS added a field such as
`customer_duns`, only templates created *after* the change had it. Every older
template had to be edited by hand, or rewritten by a one-off script.

**What must be true now.**
- After a new Labra field is introduced, a template saved earlier shows that field.
- The customer's existing CRM mappings are still there.
- Nobody edits that template, by hand or by script, to add the field.

It must hold at 10,000 templates, and the cost of the design has to be stated.

## 2. The solution in one picture

**A template stores only the customer's own choices. The list of Labra fields lives in
one table and is joined in whenever a template is read.** Nothing is copied from the
field list onto a template, so there is nothing to backfill when the list grows.

```
labra_fields  (shared, owned by Labra/AWS)     template_mappings  (per template, owned by the customer)
  company_name    required                       northwind-default  company_name  -> Account.Name
  website         required                       northwind-default  website       -> Account.Website
  ...                                            ...
  customer_duns   optional   <- new row          (no row for customer_duns)

                 \                                  /
                  +------- LEFT JOIN at read time -+
                                  |
                  "the template the customer sees"
                  every field; customer_duns shows as unmapped
                                  |
          +-----------------------+------------------------+
          |                       |                        |
  GET /mapping-templates    CRM fetch plan          Pydantic referral model
                            (one call per object)   (built from the same list)
```

The merged view comes from one function, `load_template()` in
[app/templates.py](app/templates.py). GET, the CRM fetch plan and the referral model all
use it, so "the template the customer sees" has exactly one definition in code.

## 3. Schema

SQLite, three tables. Full DDL is in [app/schema.sql](app/schema.sql).

```sql
labra_fields      (name PK, label, required, sort_order, created_at, updated_at)
templates         (id PK, name, crm_type CHECK IN ('salesforce','hubspot'), created_at, updated_at)
template_mappings (template_id FK, labra_field FK -> labra_fields.name,
                   crm_object NULL, crm_field NULL, unused 0/1,
                   PRIMARY KEY (template_id, labra_field),
                   UNIQUE (template_id, crm_object, crm_field),
                   CHECK (unused = 1 OR (crm_object IS NOT NULL AND crm_field IS NOT NULL)))
```

**Short answers to the brief's three schema questions**

| Question | Answer |
|---|---|
| Where does the field list live, and where do Northwind's mappings live? | Field list: `labra_fields`, one row per field. Northwind's mappings: `template_mappings`, one row per mapped or unused field, keyed by `('northwind-default', labra_field)`. |
| When `customer_duns` is introduced, what is written? | One row in `labra_fields`. No `templates` or `template_mappings` row is written. |
| At 10,000 templates, what does that cost, and what would I refuse to do by hand? | Still one row, about 10 ms, nothing to migrate. I refuse to copy the field list onto each template, and to edit or script-rewrite 10,000 templates. |

**Why each piece is there**
- **No row means "not mapped".** A field missing from `template_mappings` is a state, not
  an error. That is what lets a new field appear on old templates with no write.
- **`unused` is a column on the mapping row, not on the field.** The choice belongs to the
  customer and the label belongs to Labra, so a label change cannot reset the choice.
- **The database backs up the API rules.** The foreign key blocks a mapping to a field that
  is not on the list. The `CHECK` blocks a used mapping with a blank object or field. The
  `UNIQUE` constraint blocks one CRM value filling two Labra fields on the same template.
- **Seeding is idempotent.** `INSERT OR IGNORE` seeds the six original fields, so a restart
  never undoes a label or `required` change made later through the API.

**What I refuse to store**
- A copy of the default field list on each template. That copy is exactly what goes stale.
- Without it, someone would have had to open each saved template and paste
  `customer_duns` in, or write a one-off script that rewrites every JSON document.

## 4. Introducing a new field

**The call**

```
POST /labra-fields
{"labra_field": "customer_duns", "label": "Customer DUNS", "required": false}
```

**What happens**
- **Written:** one row inserted into `labra_fields`, placed at the end of the field order.
- **Not written:** every `templates` row and every `template_mappings` row.
- **The next GET of Northwind** joins the new row in. Northwind has no row for
  `customer_duns`, so it comes back as `crm_object: null`, `crm_field: null`,
  `mapped: false`. `company_name` still reads `Account.Name`, because nothing touched it.
- **Calling it again is an upsert.** It changes only the label or `required`. Mappings and
  `unused` choices stay as the customer left them.
- **Proof:** `test_introduce_customer_duns_shows_on_template_saved_before` snapshots every
  template and mapping row before the call and asserts they are identical afterwards.

**At 10,000 templates**
- **Cost:** one `INSERT`, whatever the number of templates. No migration, no backfill job.
- **Measured** on a laptop with 10,000 templates (60,000 mapping rows), through the API:
  - introducing the field: about 10 ms
  - GET of one template afterwards: about 1 ms
- **Tested:** `test_new_field_reaches_10000_existing_templates_without_writing_them` seeds
  10,000 templates × 6 mappings, introduces the field, and asserts:
  - zero template rows changed
  - the request took under 1 second (a loose bound so slow CI machines don't fail it)
  - templates t-0, t-4999 and t-9999 all show `customer_duns` unmapped
- **What is locked:** in SQLite, the database write lock for the length of a one-row
  insert. In Postgres, only the new row. Templates stay readable and editable throughout.
  A referral built at that moment sees either the old or the new list, and both are
  valid, because a new field is optional by default.

**What this design costs**
1. **Every read is a join.** It is an indexed lookup on the `(template_id, labra_field)`
   primary key, so the cost grows with the number of fields (6–7), not the number of
   templates. It is cheap, but it happens on every GET and every referral, where a copied
   document would be a single key lookup.
2. **Flipping a field to `required: true` affects every template at once.** Templates that
   have not mapped it start failing referral validation, with a fix that names the field.
   That is the correct signal, but it needs a rollout (see [section 9](#9-scoped-out-and-how-to-extend)).
3. **The field list is global.** Customers cannot invent fields, which matches the brief.

**The alternative I rejected:** a full JSON copy per template, plus a migration script
for each new field. That is 10,000 row rewrites per field. It has to be batched to avoid
long locks, and it races with customers who are editing templates while it runs.

## 5. Customer choices: map, unused, required

A customer changes one field on one template with
`PATCH /mapping-templates/{id}/fields/{labra_field}`. Each call writes at most one
mapping row.

| Body | Effect |
|---|---|
| `{"crm_object": "Account", "crm_field": "DUNS__c"}` | Map the field |
| `{"unused": true}` | Hide an optional field: not fetched, not on the referral |
| `{"unused": false}` | Back to unmapped (optional fields only) |

**Rules**
- **A required field cannot be marked unused.** The request is rejected, and the fix says
  the field is required by AWS. The same rule applies when a template is created.
- **Unused survives a label change.** The choice is on `template_mappings` and the label is
  on `labra_fields`. Tested by `test_unused_survives_a_label_change`.
- **Marking a field unused clears its CRM object and field,** which frees that CRM value
  for another Labra field.
- **Template create validates everything first and reports every problem at once:** blank
  name, bad `crm_type`, an unknown Labra field, a Labra field listed twice, the same CRM
  value used twice, a used mapping with a blank object or field, and a required field left
  unmapped. Then it writes the template and its mappings in one transaction, so a rejected
  create stores nothing.

## 6. Building a referral

```
POST /mapping-templates/{id}/crm-field-values   {"record_id": "opp-100"}
```

The body carries only the record id. CRM values never come from the caller.

**Step 1: the fetch plan (several CRM objects).** `plan_fetches()` in
[app/crm.py](app/crm.py) walks the merged template in field order. It keeps only fields
that are mapped and not unused, and groups their CRM fields by CRM object. Northwind
becomes 3 calls, not 6:
- Account: Name, Website, BillingCountry
- Opportunity: Amount, CloseDate
- Contact: Email

Details of the plan:
- **A new field** is fetched only once it is mapped, and then it joins its object's
  existing call (Account gains `DUNS__c`), so there is no extra call.
- **Unused fields are not fetched.** If every Contact field is unused, Contact is never called.
- **Ten fields on one object** is still one call, with a list of ten field names.
- **Fields that were not asked for stay out.** `InternalNotes` sits in the seeded Account
  record, but it is never requested and never appears on the referral.

**Step 2: fetch, with one retry.** `fetch_with_retry()` wraps every `crm.fetch`. On a
`RateLimited`, it waits `retry_after_ms` and calls once more. A second `RateLimited` stops
that object. Failures are tracked per object:
- **Rate limited twice:** the request returns `503` with a `Retry-After` header, and one
  error that names the object ("The Salesforce rate limit held for Opportunity…"). Its
  fields, revenue and close date, are not also reported as missing.
- **No record in any object:** `404`.
- **No record in some objects:** one error per missing object. Its fields are skipped.
- Objects that did return data are still validated.

**Step 3: validate with Pydantic.** The referral model is built per request with
`create_model`, from the same merged field list. There is no second hardcoded list of
fields.
- Required field: included, must be valid.
- Optional field, unmapped or with an empty CRM value: the key is left out.
- Optional field with a value: included and validated (DUNS must be 9 digits).

Format rules live in `FIELD_RULES` in [app/referral.py](app/referral.py):

| Field | Rule |
|---|---|
| `company_name` | trimmed, 1–255 characters |
| `website` | http or https with a host |
| `country` | United States, India, Germany or United Kingdom |
| `expected_monthly_revenue` | number > 0; `"5000"` becomes `5000` |
| `target_close_date` | `YYYY-MM-DD`, today or later (UTC) |
| `primary_sales_rep_email` | an email address |
| `customer_duns` | 9 digits, when present |
| any field without a rule | non-blank text |

`FIELD_RULES` says *how* to check a field, not *which* fields exist. A field with no rule
is still on the model, and a rule for a field that is not on the list is never used, so
the two cannot drift into a missing or extra referral key.

**Step 4: translate errors.** Each Pydantic error is turned into one error per field,
in a shape the customer can act on:

```json
{
  "field": "website",
  "crm_object": "Account",
  "crm_field": "Website",
  "received": "not a url",
  "message": "Website must be an http or https URL with a host.",
  "fix": "Set Account.Website in Salesforce to a URL such as https://northwind.example."
}
```

Every failed field comes back in one response, and raw Pydantic text such as
"Value error, …" never reaches the user. Template, field-list and request-body errors use
the same shape, with `crm_object` and `crm_field` set to `null` where they don't apply.

## 7. Rate limits at scale

**The load.** 200 prepares per minute × 3 objects = 600 calls per minute. The CRM allows
100 per 10 seconds, which is also 600 per minute. That is exactly at the limit on average,
so any burst goes over. Several API processes, each counting on its own, cannot see that.

**Where the limiter lives.** In a shared Redis, called from `fetch_with_retry()` in
[app/crm.py](app/crm.py). That function is the single place every CRM call goes through,
and it already wraps `FakeCrm.fetch` / `crm.fetch` with the one retry. The limiter goes
directly around the `crm.fetch(...)` line: acquire first, then call.

**How it is keyed.** `crm_rl:{crm_type}:{org_id or portal_id}`. The limit belongs to the
customer's CRM account (their Salesforce org or HubSpot portal), not to our process or
to a template.
- **A token bucket in a small Lua script,** so acquiring a token is atomic across
  processes. Capacity 100, refill 10 per second.
- **Reserve every token a prepare needs in one step,** 3 for Northwind. A prepare then gets
  all its calls or none, instead of fetching Account and stalling on Contact.
- **Share the CRM's back-off.** When the CRM still answers `RateLimited` with a
  retry-after, write `crm_cooldown:{key}` with that TTL, so every process backs off, not
  only the one that was told.

**A prepare that cannot get a slot within about 2 seconds**
- Don't hold the HTTP request open. Put the prepare on a queue for that CRM account and
  return `202 Accepted` with a job id. One worker per key drains the queue at the bucket
  rate, and the caller polls or receives a callback.
- Without a queue, return `503` with `Retry-After`. That is what the double-rate-limit path
  already does today.

**Implemented today:** only the single retry. Tested by
`test_one_rate_limit_is_retried_and_referral_succeeds` and
`test_rate_limit_twice_names_opportunity_and_not_its_fields`.

## 8. Boundaries and decisions

**In scope and built**
- Shared field list, templates, mappings and unused choices, stored in SQLite.
- Introducing and updating Labra fields with no template writes.
- `crm-field-values`: fetch plan, one fetch per object, one retry, Pydantic validation,
  actionable errors.

**Mocked, as the brief asks**
- **The CRM is a fake.** `FakeCrm` from the brief is passed in through `create_app(crm)`.
  Routes never construct a client. When the API is run with no client injected, it uses a
  demo FakeCrm with `opp-100` (Northwind) and `deal-200` (Contoso), so the README examples
  work.
- **Nothing is sent to AWS.** The work ends when the API returns the validated referral.
  That JSON is what would be shared with AWS.
- **No real Salesforce, HubSpot or Labra calls, no credentials.**

**Decisions worth flagging**
- **Rate limited twice returns `503`, not `422`.** The upstream CRM refused us; the
  customer's data is not wrong.
- **Extras not in the brief:**
  - `409` on a duplicate template id, instead of silently overwriting.
  - The duplicate CRM value check ignores case, because Salesforce API names do.
  - Malformed request bodies come back in the shared error shape.
- **Website is checked with `urlparse`, not `HttpUrl`.** `HttpUrl` rewrites
  `https://northwind.example` to `…example/`, which breaks the expected success body.
- **The clock and sleep are injected.** "Today" is frozen to 2026-11-18 in tests, so
  date rules don't change with the calendar.

## 9. Scoped out, and how to extend

**The one thing left out (brief question 5): optimistic concurrency on templates.**
Today, if two people edit the same template, the last write wins. Here is where the fix
would attach:
- **Store:** add `templates.version INTEGER NOT NULL DEFAULT 1`. Return it from GET as
  `version` and as an `ETag`.
- **Write path:** `PATCH /mapping-templates/{id}/fields/{field}` (and any future
  full-template update) requires `If-Match: <version>`.
- **In the same transaction:** first run
  `UPDATE templates SET version = version + 1 WHERE id = ? AND version = ?`.
  - 0 rows updated means someone else saved first. Roll back and return `409` with a fix
    such as "Reload the template and reapply your change".
  - 1 row updated means it is safe to write the mapping row.
- **Adding a Labra field never conflicts with a customer's edit,** because it does not
  write templates.

**Other things scoped out, and how I'd add them**

| Not built | How to extend |
|---|---|
| Shared rate limiter and queue | The Redis token bucket and per-account queue from [section 7](#7-rate-limits-at-scale), around `fetch_with_retry()`. |
| Short-lived CRM record cache | Cache `(crm account, object, record_id, fields)` for a few seconds in front of `crm.fetch`, so repeated prepares of the same deal use fewer tokens. |
| Choosing a format rule when a field is introduced | Add a `value_type` column on `labra_fields` (`text`, `url`, `duns`, …) and pick the rule by type instead of by name, so a new field with a special format needs no code change. |
| Safe rollout of `required: true` | Introduce the field as optional, let customers map it, then flip it. Add `GET /labra-fields/{name}/unmapped-templates` (one `LEFT JOIN … WHERE m.labra_field IS NULL`) to see who still needs to map it. |
| Retiring a Labra field | A `retired_at` column, filtered out of the join, instead of a hard delete that the foreign key blocks. |
| Full-template update and delete | `PUT` reusing the create validation inside one transaction, guarded by the version check above. |
| Parallel CRM calls | With a real async client, run the per-object fetches together (`asyncio.gather`), still one call per object. |
| Postgres | The schema is plain SQL. Swap the connection layer; the upserts already use `ON CONFLICT`. |
| Auth, UI, real CRM clients, sending to AWS | Out of scope per the brief. A real client only has to implement `fetch(object_name, record_id, fields)`. |

## 10. How it was built

The work was done in small steps, one commit each, with tests added in the same step:

| Commit | Step |
|---|---|
| `3db0372` | Project skeleton, `create_app(crm)` seam, smoke test |
| `65d604c` | SQLite schema, idempotent seed of the six fields |
| `3405328` | Create and GET template, every create rule, shared error shape |
| `69f5c9d` | Introduce Labra fields, per-template map/unused, the 10,000-template test |
| `bb84d31` | Fake CRM, fetch plan, single retry |
| `38048c1` | Referral model, error translation, `crm-field-values` endpoint |
| later | README, NOTES and documentation polish |

**Things I hit along the way**
- **A Pydantic gotcha.** Putting `StringConstraints` after a `BeforeValidator` in the same
  `Annotated` silently drops `min_length`, so a blank company name passed validation. The
  invalid-seed test caught it; nesting the constrained `str` fixed it.
- **`HttpUrl` adds a trailing slash,** which would have broken the exact success body, so
  website uses a plain URL check.
- **A hardcoded demo close date goes stale.** The demo CRM now uses "today + 30 days";
  tests freeze today instead.

## 11. Test map

Every row of the brief's test table, and where it is checked.

| Brief test | Test |
|---|---|
| Create Northwind and GET it | `test_create_northwind_and_get_returns_all_six_mappings` |
| Introduce `customer_duns` after Northwind is saved | `test_introduce_customer_duns_shows_on_template_saved_before` |
| Mark `customer_duns` unused, then change its label | `test_unused_survives_a_label_change` |
| Mark `company_name` unused | `test_marking_required_field_unused_is_rejected` |
| Valid `opp-100` | `test_valid_opp_100_builds_referral_with_one_call_per_object` |
| Invalid seed | `test_invalid_seed_returns_six_errors_with_object_field_value_and_fix` |
| Amount stored as `"5000"` | `test_numeric_string_amount_becomes_number` |
| Extra key `Account.InternalNotes` | `test_internal_notes_is_never_requested_or_copied` |
| `fail_next("Opportunity")` | `test_one_rate_limit_is_retried_and_referral_succeeds` |
| Opportunity rate limited on both attempts | `test_rate_limit_twice_names_opportunity_and_not_its_fields` |
| Mark every Contact field unused | `test_every_contact_field_unused_means_no_contact_fetch` |
| Unknown template id, unknown record_id | `test_unknown_template_is_not_found`, `test_unknown_record_is_not_found` |
| One Contoso (HubSpot) test | `test_contoso_hubspot_groups_companies_deals_contacts` |

Beyond the table: every create rejection rule, DUNS mapped / empty / invalid / required,
a new optional field keeping old referrals valid, the 10,000-template check, and unit tests
for the fetch plan and the retry. 62 tests in total.

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

`GET /labra-fields` lists the current field list.

### `PATCH /mapping-templates/{template_id}/fields/{labra_field}`: one customer choice

| Body | Effect |
|---|---|
| `{"unused": true}` | Hide an optional field. Rejected for a required field. |
| `{"crm_object": "Account", "crm_field": "DUNS__c"}` | Map the field. |
| `{"unused": false}` | Back to unmapped (optional fields only). |

Returns the full template.

### `POST /mapping-templates/{template_id}/crm-field-values`: build a referral

```json
{"record_id": "opp-100"}
```

| Status | When |
|---|---|
| `200` | `{"referral": {...}, "errors": []}` |
| `422` | Invalid CRM data: `{"referral": null, "errors": [...]}`, one error per bad field |
| `503` | An object was rate limited twice (`Retry-After` header); the error names the object, not its fields |
| `404` | Unknown template, or no CRM object has that record id |

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
