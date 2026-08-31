"""Minimal public-facing web view — read-only listings dashboard.

No login (portfolio, not a product): defended by nginx/host rate-limit,
not application auth. Ponytail: no pagination — add if listing count
grows past a screenful (currently tens of rows).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from home_ops.cli.app import _get_db_path
from home_ops.models.data_storage import get_connection

app = FastAPI(title="Home-Ops")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    with get_connection(_get_db_path()) as db:
        db.init_db()
        rows = db.conn.execute(
            """SELECT l.id, l.address, l.price, l.m2, l.url, l.portal,
                      COALESCE(p.score, NULL) AS score
               FROM listings l
               LEFT JOIN pending_approvals p ON p.listing_id = l.id
               ORDER BY l.fetched_at DESC
               LIMIT 100"""
        ).fetchall()
    listings = [
        {
            "id": r[0],
            "address": r[1] or "—",
            "price": f"{r[2]:,.0f} €" if r[2] else "—",
            "m2": f"{r[3]:.0f} m²" if r[3] else "—",
            "url": r[4],
            "portal": r[5],
            "score": f"{r[6]:.1f}" if r[6] is not None else "—",
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        request, "index.html", {"listings": listings}
    )
