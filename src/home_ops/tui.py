"""Textual TUI — full control panel for the Home-Ops pipeline.

Run every pipeline action from one screen:

- ``s``  scan   — run one pipeline cycle (scrape→dedup→score→alert) in a
                  background thread; its output streams into the log pane.
- ``r``  refresh — re-read DuckDB and repaint all tables.
- ``a``  approve — approve the selected pending listing (HITL gate).
- ``x``  reset  — invalidate cached scraper snapshots (next scan cold-starts).
- ``q``  quit

``status``/``analytics`` output is what the tables already show; ``daemon`` is
replaced by this screen itself (a foreground monitor).
"""

from __future__ import annotations

import contextlib
import io
from datetime import UTC, datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from home_ops import analytics as analytics_mod
from home_ops.models.data_storage import get_connection


class HomeOpsTUI(App[None]):
    """Pipeline control panel: scan/approve/reset + live status/analytics."""

    BINDINGS = [
        ("s", "scan", "Scan"),
        ("r", "refresh", "Refresh"),
        ("a", "approve", "Approve"),
        ("x", "reset_snapshots", "Reset snapshots"),
        ("q", "quit", "Quit"),
    ]
    CSS = "DataTable, Static, RichLog { margin: 1 2; }"

    def __init__(self, db_path: str, config_path: Path | None = None) -> None:
        super().__init__()
        self.db_path = db_path
        self.config_path = config_path
        self._scanning = False
        self._pending_ids: list[int] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(id="status")
            yield DataTable(id="pending")
            yield DataTable(id="prices")
            yield DataTable(id="evolution")
            yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prices", DataTable).add_columns("Stat", "Value")
        self.query_one("#evolution", DataTable).add_columns("Week", "N", "Mean €/m²", "P50 €/m²")
        self.query_one("#pending", DataTable).add_columns("ID", "Address", "Score")
        self.query_one("#log", RichLog).write("[b]Ready.[/b] Press s to scan, a to approve.")
        self.action_refresh()

    # ------------------------------------------------------------------ scan

    def action_scan(self) -> None:
        if self._scanning:
            self.notify("Scan already running", severity="warning")
            return
        self._scanning = True
        self.query_one("#log", RichLog).write("[b]Scanning portal...[/b]")
        self.run_worker(self._scan_task, thread=True, exclusive=True)

    def _scan_task(self) -> None:
        """Run one pipeline cycle off the event loop; capture its output."""
        from home_ops.cli import app as cli_app

        buf = io.StringIO()
        old_file = cli_app.console.file
        cli_app.console.file = buf
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                cli_app._run_scan(self.config_path)
        except Exception as exc:  # surface scraper/parse failures in the log
            buf.write(f"\n[red]Scan failed: {exc}[/red]\n")
        finally:
            cli_app.console.file = old_file
        self.call_from_thread(self._scan_finished, buf.getvalue())

    def _scan_finished(self, output: str) -> None:
        self._scanning = False
        self.query_one("#log", RichLog).write(output)
        self.action_refresh()

    # -------------------------------------------------------------- approve

    def action_approve(self) -> None:
        table = self.query_one("#pending", DataTable)
        if table.row_count == 0 or not self._pending_ids:
            self.notify("No pending approvals", severity="warning")
            return
        listing_id = self._pending_ids[table.cursor_row]
        now = datetime.now(UTC)
        with get_connection(self.db_path) as db:
            db.init_db()
            db.conn.execute(
                """INSERT INTO pending_approvals (listing_id, approved, approved_at)
                   VALUES (?, TRUE, ?)
                   ON CONFLICT (listing_id) DO UPDATE SET approved = TRUE, approved_at = ?;""",
                [listing_id, now, now],
            )
        self.query_one("#log", RichLog).write(f"[green]Listing {listing_id} approved.[/green]")
        self.action_refresh()

    # --------------------------------------------------------------- reset

    def action_reset_snapshots(self) -> None:
        from home_ops.scraper.lifecycle import invalidate_snapshots

        invalidate_snapshots()
        self.query_one("#log", RichLog).write(
            "[green]Snapshots invalidated — next scan cold-starts.[/green]"
        )

    # ------------------------------------------------------------- refresh

    def action_refresh(self) -> None:
        with get_connection(self.db_path) as db:
            db.init_db()
            total_row = db.conn.execute("SELECT COUNT(*) FROM listings").fetchone()
            total = total_row[0] if total_row else 0
            last_row = db.conn.execute("SELECT MAX(fetched_at) FROM listings").fetchone()
            last_scan = last_row[0] if last_row else None
            pending_rows = db.conn.execute(
                """SELECT p.listing_id, l.address, p.score
                   FROM pending_approvals p
                   LEFT JOIN listings l ON l.id = p.listing_id
                   WHERE p.approved = FALSE
                   ORDER BY p.created_at ASC"""
            ).fetchall()
            prices = analytics_mod.price_stats(db)
            evolution = analytics_mod.price_evolution_by_week(db)

        self.query_one("#status", Static).update(
            f"[b]Listings:[/b] {total}   "
            f"[b]Last scan:[/b] {last_scan or 'never'}   "
            f"[b]Pending approvals:[/b] {len(pending_rows)}"
        )

        pending_table = self.query_one("#pending", DataTable)
        pending_table.clear()
        self._pending_ids = []
        for r in pending_rows:
            listing_id = int(r[0])
            self._pending_ids.append(listing_id)
            score = f"{r[2]:.1f}" if r[2] is not None else "—"
            pending_table.add_row(str(listing_id), str(r[1] or ""), score)

        price_table = self.query_one("#prices", DataTable)
        price_table.clear()
        for key in ("count", "mean", "min", "max", "p25", "p50", "p75"):
            value = prices[key]
            price_table.add_row(key, f"{value:.0f}" if isinstance(value, (int, float)) else "—")

        evo_table = self.query_one("#evolution", DataTable)
        evo_table.clear()
        for week_row in evolution[-10:]:
            mean = f"{week_row['mean_eur_m2']:.0f}" if week_row["mean_eur_m2"] is not None else "—"
            p50 = f"{week_row['p50_eur_m2']:.0f}" if week_row["p50_eur_m2"] is not None else "—"
            evo_table.add_row(week_row["week"][:10], str(week_row["n"]), mean, p50)


def run(db_path: str, config_path: Path | None = None) -> None:
    HomeOpsTUI(db_path, config_path).run()
