"""Smoke test: TUI mounts and renders against an in-memory DB, no crash."""

import pytest

pytest.importorskip("textual")

from home_ops.models.data_storage import get_connection  # noqa: E402
from home_ops.tui import HomeOpsTUI  # noqa: E402


@pytest.mark.asyncio
async def test_tui_mounts_and_refreshes() -> None:
    with get_connection(":memory:") as db:
        db.init_db()

    app = HomeOpsTUI(":memory:")
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one("#status")
        assert "Listings: 0" in str(status.render())
