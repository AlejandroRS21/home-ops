"""Shared test fixtures for Home-Ops tests."""

import pytest

from home_ops.models.data_storage import DuckDBConnection


@pytest.fixture
def db() -> DuckDBConnection:
    """Create an in-memory DuckDB connection with initialized schema."""
    conn = DuckDBConnection(":memory:")
    conn.connect()
    conn.init_db()
    return conn
