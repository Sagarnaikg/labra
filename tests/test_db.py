"""Database bootstrap: tables exist and the six original fields are seeded."""
import sqlite3

import pytest

from app.db import SEED_FIELDS


def _fields(db):
    with db.connect() as conn:
        return conn.execute(
            "SELECT name, label, required FROM labra_fields ORDER BY sort_order"
        ).fetchall()


def test_seeds_six_required_fields(db):
    rows = _fields(db)
    assert [r["name"] for r in rows] == [
        "company_name",
        "website",
        "country",
        "expected_monthly_revenue",
        "target_close_date",
        "primary_sales_rep_email",
    ]
    assert all(r["required"] == 1 for r in rows)


def test_init_is_idempotent_and_keeps_later_label_changes(db):
    with db.connect() as conn:
        conn.execute("UPDATE labra_fields SET label = 'Account name' WHERE name = 'company_name'")

    db.init()  # e.g. a restart

    rows = _fields(db)
    assert len(rows) == len(SEED_FIELDS)
    assert rows[0]["label"] == "Account name"


def test_used_mapping_needs_crm_object_and_field(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO templates (id, name, crm_type) VALUES ('t1', 'T1', 'salesforce')")
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO template_mappings (template_id, labra_field, crm_object, crm_field) "
                "VALUES ('t1', 'website', 'Account', NULL)"
            )


def test_mapping_must_reference_a_known_labra_field(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO templates (id, name, crm_type) VALUES ('t1', 'T1', 'salesforce')")
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO template_mappings (template_id, labra_field, crm_object, crm_field) "
                "VALUES ('t1', 'favorite_color', 'Account', 'Color')"
            )
