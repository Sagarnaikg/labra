-- Labra mapping-template schema (SQLite).
--
-- Rule of thumb: a template stores ONLY the customer's own choices.
-- The list of Labra fields lives in exactly one place (labra_fields) and is
-- joined in at read time, so a newly introduced field shows up on every
-- template without touching any template row.

-- 1. Shared Labra field list. Owned by Labra/AWS, one row per field.
CREATE TABLE IF NOT EXISTS labra_fields (
    name        TEXT    PRIMARY KEY,               -- e.g. 'company_name', 'customer_duns'
    label       TEXT    NOT NULL,                  -- e.g. 'Company name'
    required    INTEGER NOT NULL DEFAULT 0         -- 1 = AWS requires it
                CHECK (required IN (0, 1)),
    sort_order  INTEGER NOT NULL,                  -- display order on GET
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 2. One row per customer template.
CREATE TABLE IF NOT EXISTS templates (
    id          TEXT    PRIMARY KEY,               -- e.g. 'northwind-default'
    name        TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    crm_type    TEXT    NOT NULL CHECK (crm_type IN ('salesforce', 'hubspot')),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 3. The customer's choices for one template: which CRM object + field fills a
--    Labra field, or that the field is unused. No row = "not mapped yet".
CREATE TABLE IF NOT EXISTS template_mappings (
    template_id TEXT    NOT NULL REFERENCES templates (id) ON DELETE CASCADE,
    labra_field TEXT    NOT NULL REFERENCES labra_fields (name),
    crm_object  TEXT,                              -- e.g. 'Account'   (NULL when unused)
    crm_field   TEXT,                              -- e.g. 'Name'      (NULL when unused)
    unused      INTEGER NOT NULL DEFAULT 0 CHECK (unused IN (0, 1)),
    PRIMARY KEY (template_id, labra_field),
    -- A used mapping must name both the CRM object and field.
    CHECK (unused = 1 OR (crm_object IS NOT NULL AND crm_field IS NOT NULL)),
    -- One CRM value cannot feed two Labra fields on the same template.
    UNIQUE (template_id, crm_object, crm_field)
);
