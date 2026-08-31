"""Textual TUI for live monitoring of the Home-Ops pipeline.

Read-only dashboard over the DuckDB store: status + analytics in one
screen, manual refresh (key `r`) — no polling/background workers, the
pipeline itself runs via `homeops scan`/daemon; this just observes state.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from home_ops import analytics as analytics_mod
from home_ops.models.data_storage import get_connection


class HomeOpsTUI(App[None]):
    """Live-ish dashboard: status + analytics, refresh with `r`."""

    BINDINGS = [("r", "refresh", "Refresh"), ("q", "quit", "Quit")]
    CSS = "DataTable, Static { margin: 1 2; }"

    def __init__(self, db_path: str) -> None:
        super().__init__()
        self.db_path = db_path

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(id="status")
            yield DataTable(id="prices")
            yield DataTable(id="evolution")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prices", DataTable).add_columns("Stat", "Value")
        self.query_one("#evolution", DataTable).add_columns("Week", "N", "Mean €/m²", "P50 €/m²")
        self.action_refresh()

    def action_refresh(self) -> None:
        with get_connection(self.db_path) as db:
            db.init_db()
            total_row = db.conn.execute("SELECT COUNT(*) FROM listings").fetchone()
            total = total_row[0] if total_row else 0
            last_row = db.conn.execute("SELECT MAX(fetched_at) FROM listings").fetchone()
            last_scan = last_row[0] if last_row else None
            pending_row = db.conn.execute(
                "SELECT COUNT(*) FROM pending_approvals WHERE approved = FALSE"
            ).fetchone()
            pending = pending_row[0] if pending_row else 0
            prices = analytics_mod.price_stats(db)
            evolution = analytics_mod.price_evolution_by_week(db)

        self.query_one("#status", Static).update(
            f"[b]Listings:[/b] {total}   "
            f"[b]Last scan:[/b] {last_scan or 'never'}   "
            f"[b]Pending approvals:[/b] {pending}"
        )

        price_table = self.query_one("#prices", DataTable)
        price_table.clear()
        for key in ("count", "mean", "min", "max", "p25", "p50", "p75"):
            value = prices[key]
            price_table.add_row(key, f"{value:.0f}" if isinstance(value, (int, float)) else "—")

        evo_table = self.query_one("#evolution", DataTable)
        evo_table.clear()
        for r in evolution[-10:]:
            mean = f"{r['mean_eur_m2']:.0f}" if r["mean_eur_m2"] is not None else "—"
            p50 = f"{r['p50_eur_m2']:.0f}" if r["p50_eur_m2"] is not None else "—"
            evo_table.add_row(r["week"][:10], str(r["n"]), mean, p50)


def run(db_path: str) -> None:
    HomeOpsTUI(db_path).run()
