"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app


@pytest.fixture
def db(tmp_path) -> Database:
    """A fresh SQLite file per test, so tests never share state."""
    database = Database(tmp_path / "test.db")
    database.init()
    return database


@pytest.fixture
def client(db) -> TestClient:
    return TestClient(create_app(db=db))
