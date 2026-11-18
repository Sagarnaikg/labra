"""Shared pytest fixtures."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.crm import FakeCrm
from app.db import Database
from app.main import create_app

FROZEN_TODAY = date(2026, 11, 18)


@pytest.fixture
def db(tmp_path) -> Database:
    """A fresh SQLite file per test, so tests never share state."""
    database = Database(tmp_path / "test.db")
    database.init()
    return database


@pytest.fixture
def crm() -> FakeCrm:
    return FakeCrm()


@pytest.fixture
def client(db, crm) -> TestClient:
    # "Today" is frozen so date rules do not change with the calendar.
    return TestClient(create_app(crm, db=db, today=lambda: FROZEN_TODAY))
