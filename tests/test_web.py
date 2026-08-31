"""Smoke test: web dashboard renders (empty DB and with listings), no crash."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from home_ops import web as web_mod  # noqa: E402
from home_ops.models.data_storage import get_connection  # noqa: E402


def test_index_renders_empty(tmp_path, monkeypatch) -> None:
    db_path = str(tmp_path / "home_ops.duckdb")
    with get_connection(db_path) as db:
        db.init_db()
    monkeypatch.setattr(web_mod, "_get_db_path", lambda: db_path)

    client = TestClient(web_mod.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Sin listings todavía" in resp.text


def test_index_renders_listing(tmp_path, monkeypatch) -> None:
    db_path = str(tmp_path / "home_ops.duckdb")
    with get_connection(db_path) as db:
        db.init_db()
        db.conn.execute(
            "INSERT INTO listings (content_hash, address, price, m2, url) "
            "VALUES ('h1', 'Calle Falsa 123', 150000, 80, 'https://example.com/1')"
        )
    monkeypatch.setattr(web_mod, "_get_db_path", lambda: db_path)

    client = TestClient(web_mod.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Calle Falsa 123" in resp.text
    assert "150,000 €" in resp.text
