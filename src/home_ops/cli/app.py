"""Typer CLI entry point for Home-Ops pipeline orchestration.

Usage:
    homeops scan
    homeops status
    homeops snapshots-reset
    homeops approve <listing_id>
    homeops daemon
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from home_ops.alerter.telegram import TelegramAlerter
from home_ops.config.loader import load_config
from home_ops.models.data_storage import get_connection
from home_ops.models.schema import Listing, ScheduleConfig
from home_ops.scorer import RulesScorer

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Home-Ops: Real estate agentic pipeline — scrape, score, alert.",
    no_args_is_help=True,
)
console = Console()

# Shared Typer argument for optional config path
ConfigPathArg = Annotated[
    Path | None,
    typer.Argument(
        help="Path to user_profile.yml (default: auto-discover)",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
]

ConfigOpt = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="Path to user_profile.yml (default: auto-discover)",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
]


def _get_db_path() -> str:
    """Resolve the DuckDB path from the default."""
    from home_ops.models.data_storage import DEFAULT_DB_PATH

    return str(DEFAULT_DB_PATH)


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------


def _next_run_time(
    schedule: ScheduleConfig,
    last_run: datetime | None = None,
    now: datetime | None = None,
) -> datetime:
    """Compute the next scheduled run time as a pure function.

    Args:
        schedule: The schedule configuration (mode, daily_time, interval_hours, timezone).
        last_run: The last recorded pipeline run time, or None if never run.
        now: The current time (injectable for testing). Defaults to UTC now.

    Returns:
        The next datetime when the pipeline should run (timezone-aware in UTC).
    """
    from zoneinfo import ZoneInfo

    if now is None:
        now = datetime.now(UTC)

    if schedule.mode == "interval":
        if last_run is None:
            return now
        return last_run + timedelta(hours=schedule.interval_hours)

    # Daily mode — use the configured timezone
    tz = ZoneInfo(schedule.timezone)
    hour_str, min_str = schedule.daily_time.split(":", 1)
    target_hour = int(hour_str)
    target_min = int(min_str)

    if last_run is not None:
        # Compute next daily occurrence AFTER last_run (enables catch-up detection)
        last_local = last_run.astimezone(tz)
        candidate = last_local.replace(
            hour=target_hour, minute=target_min, second=0, microsecond=0
        )
        if candidate <= last_local:
            candidate += timedelta(days=1)
    else:
        # No prior run — return now so daemon runs immediately on first start
        return now

    return candidate.astimezone(UTC)


def _get_daily_alert_count(conn: Any) -> int:
    """Query the daily_alert_log for today's sent alert count.

    Args:
        conn: DuckDB connection.

    Returns:
        Number of alerts sent today with status 'sent'.
    """
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    row = conn.execute(
        "SELECT COUNT(*) FROM daily_alert_log "
        "WHERE status = 'sent' AND sent_at >= ?",
        [today_start],
    ).fetchone()
    return row[0] if row else 0


def _run_daemon_cycle(
    config: Any,
    run_fn: Any = None,
    now: datetime | None = None,
) -> bool:
    """Execute one daemon cycle: check schedule and run pipeline if due.

    Args:
        config: The application Config object.
        run_fn: Injectable run function (defaults to _run_scan).
        now: The current time (injectable for testing). Defaults to UTC now.

    Returns:
        True if the pipeline was executed, False if skipped.
    """
    if run_fn is None:
        run_fn = _run_scan
    if now is None:
        now = datetime.now(UTC)

    schedule = config.alert_schedule
    db_path = _get_db_path()

    with get_connection(db_path) as db:
        db.init_db()

        # Cleanup stale 'running' rows from crashed/interrupted runs
        # Use started_at as finished_at so the schedule computer sees the
        # original timestamp, not the current time — otherwise it would
        # think a run just completed and skip the current cycle.
        db.conn.execute(
            "UPDATE scraping_runs SET status = 'failed', finished_at = started_at "
            "WHERE status = 'running'",
        )

        # Check if a run is already in progress (overlapping guard)
        running = db.conn.execute(
            "SELECT COUNT(*) FROM scraping_runs WHERE status = 'running'"
        ).fetchone()
        if running and running[0] > 0:
            logger.warning("Daemon cycle: previous run still in progress, skipping")
            return False

        # Get last completed run for schedule computation
        last_row = db.conn.execute(
            "SELECT finished_at FROM scraping_runs "
            "WHERE status IN ('success', 'failed') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_run: datetime | None = last_row[0] if last_row else None

        # Compute next run time
        next_time = _next_run_time(schedule, last_run, now)

        if next_time > now:
            logger.debug("Daemon cycle: next run at %s, skipping", next_time)
            return False

        # Mark run as started
        run_id = db.conn.execute(
            "INSERT INTO scraping_runs (started_at, status) VALUES (?, 'running') "
            "RETURNING id",
            [now],
        ).fetchone()[0]

    # Execute the pipeline outside the DB context manager
    status = "success"
    try:
        run_fn(None)  # run_fn accepts config_path; None = auto-discover
    except BaseException as exc:
        logger.error("Daemon cycle: pipeline failed: %s", exc)
        status = "failed"
        if not isinstance(exc, Exception):
            raise
    finally:
        # Mark run as finished — always update status even on Ctrl+C
        with get_connection(db_path) as db:
            db.init_db()
            db.conn.execute(
                "UPDATE scraping_runs SET finished_at = ?, status = ? "
                "WHERE id = ?",
                [datetime.now(UTC), status, run_id],
            )

    return True


def _run_daemon_inner_loop(config: Any, dry_run: bool = False) -> None:
    """Run the daemon loop: check schedule every 60s, execute when due.

    Args:
        config: The application Config object.
        dry_run: If True, only print the next scheduled time and exit.
    """
    schedule = config.alert_schedule
    now = datetime.now(UTC)

    # Compute next run for dry-run or initial display
    db_path = _get_db_path()
    with get_connection(db_path) as db:
        db.init_db()
        last_row = db.conn.execute(
            "SELECT finished_at FROM scraping_runs "
            "WHERE status IN ('success', 'failed') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_run: datetime | None = last_row[0] if last_row else None

    next_time = _next_run_time(schedule, last_run, now)
    console.print(f"[cyan]Schedule mode:[/cyan] {schedule.mode}")
    console.print(f"[cyan]Next scheduled run:[/cyan] {next_time}")

    if dry_run:
        console.print("[green]Dry-run complete. No loop started.[/green]")
        return

    console.print("[green]Daemon loop started. Press Ctrl+C to stop.[/green]")

    try:
        while True:
            cycle_now = datetime.now(UTC)
            _run_daemon_cycle(config, now=cycle_now)
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped by user.[/yellow]")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


ForceOpt = Annotated[
    bool,
    typer.Option(
        "--force",
        "-f",
        help="Force full scan bypassing early-stop pagination",
    ),
]


@app.command()
def scan(
    config_path: ConfigPathArg = None,
    force: ForceOpt = False,
) -> None:
    """Run the full pipeline: scrape → deduplicate → score → alert.

    Loads configuration, executes a cold-start or incremental scrape of
    the portal (auto-detected from database state), inserts new listings
    into the database, scores each listing with the deterministic rules
    engine, and alerts via Telegram when the score meets the configured
    threshold and HITL approval is granted.
    """
    try:
        _run_scan(config_path, force)
    except Exception as exc:
        console.print(f"[bold red]Pipeline failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def status(
    config_path: ConfigPathArg = None,
) -> None:
    """Show pipeline state and recent results.

    Queries the DuckDB database for:
    - Total listings tracked
    - Last scan time (most recent fetch across all listings)
    - Pending HITL approvals
    """
    try:
        config = load_config(config_path)
        _display_status(config)
    except Exception as exc:
        console.print(f"[bold red]Status failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command(name="snapshots-reset")
def snapshots_reset() -> None:
    """Invalidate all cached scraper snapshots.

    The next ``scan`` will perform a full cold-start fetch instead of
    using cached data.
    """
    from home_ops.scraper.lifecycle import invalidate_snapshots

    try:
        invalidate_snapshots()
        console.print("[green]All snapshots invalidated. Next scan will cold-start.[/green]")
    except Exception as exc:
        console.print(f"[bold red]Failed to reset snapshots:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def approve(
    listing_id: Annotated[int, typer.Argument(help="The listing ID to approve")],
    config_path: ConfigOpt = None,
) -> None:
    """Approve a listing via the human-in-the-loop gate.

    Once approved the listing becomes eligible for alerting (contact
    actions) on the next scan cycle.
    """
    load_config(config_path)  # validate config exists
    db_path = _get_db_path()

    try:
        with get_connection(db_path) as db:
            db.init_db()
            now = datetime.now(UTC)
            db.conn.execute(
                """INSERT INTO pending_approvals (listing_id, approved, approved_at)
                   VALUES (?, TRUE, ?)
                   ON CONFLICT (listing_id) DO UPDATE SET approved = TRUE, approved_at = ?;""",
                [listing_id, now, now],
            )
            console.print(
                f"[green]Listing {listing_id} approved. "
                f"Alerts will be sent on next scan.[/green]"
            )
    except Exception as exc:
        console.print(f"[bold red]Failed to approve listing {listing_id}:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def daemon(
    config_path: ConfigOpt = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print next scheduled run and exit without starting the loop",
        ),
    ] = False,
) -> None:
    """Run the automated pipeline daemon.

    Loads configuration, checks the alert_schedule, and runs the pipeline
    on a configurable schedule (daily or interval mode). Supports
    catch-up recovery, overlapping-run protection, and daily alert quotas.
    """
    try:
        config = load_config(config_path)
        _run_daemon_inner_loop(config, dry_run=dry_run)
    except Exception as exc:
        console.print(f"[bold red]Daemon failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# Internal pipeline logic
# ---------------------------------------------------------------------------


def _run_scan(config_path: Path | None = None, force: bool = False) -> None:
    """Orchestrate one pipeline scan cycle."""
    config = load_config(config_path)

    # Prefer the new typed ScoringThresholds path; fall back to legacy dict
    if config.scoring is not None:
        threshold = config.scoring.min_score_to_alert
    else:
        threshold = float(config.scoring_thresholds.get("min_score_to_alert", 70.0))
    scorer = RulesScorer(config)

    db_path = _get_db_path()
    with get_connection(db_path) as db:
        db.init_db()

        # 1. Auto-detect: cold start (empty DB) vs subsequent run
        console.print("[bold]Scanning portal...[/bold]")
        row = db.conn.execute("SELECT COUNT(*) FROM listings").fetchone()
        has_data = row is not None and row[0] is not None and row[0] != 0

        if has_data:
            from home_ops.scraper.lifecycle import subsequent_run

            try:
                listings: list[Listing] = subsequent_run(
                    config.portal_url, db, max_pages=5, force=force
                )
            except Exception as exc:
                console.print(f"[yellow]Scraper returned no data: {exc}[/yellow]")
                listings = []
        else:
            from home_ops.scraper.lifecycle import cold_start

            try:
                listings = cold_start(config.portal_url)
            except Exception as exc:
                console.print(f"[yellow]Scraper returned no data: {exc}[/yellow]")
                listings = []

        # 2. Process new listings (if any)
        if listings:
            scored: list[tuple[Listing, float, list[str]]] = []

            for listing in listings:
                inserted_id = db.insert_listing(listing)

                if inserted_id is not None:
                    listing.id = inserted_id

                if inserted_id is None:
                    console.print(
                        f"  [dim]Skipped (duplicate): {listing.address or listing.url}[/dim]"
                    )
                    continue

                # Score — use RulesScorer; multiply by 100 for 0-100 threshold compatibility
                score_result = scorer.score(listing, db_conn=db.conn)
                score_value = score_result.total * 100.0
                if score_result.flags:
                    console.print(
                        f"  [yellow]Flags:[/yellow] {', '.join(score_result.flags)}"
                    )
                console.print(
                    f"  [cyan]Scored:[/cyan] {listing.address or listing.url} "
                    f"→ [bold]{score_value:.1f}[/bold] (threshold {threshold})"
                )
                scored.append((listing, score_value, score_result.flags))

            # Alert gating
            approval_required = config.hitl_approval_required
            alerter = TelegramAlerter(
                bot_token=config.telegram_bot_token or None,
                chat_id=config.telegram_chat_id or None,
                score_threshold=threshold,
            )

            for listing, score, flags in scored:
                if score < threshold:
                    console.print(
                        f"  [dim]Alert gated (score {score:.1f} < {threshold}): "
                        f"{listing.address or listing.url}[/dim]"
                    )
                    continue

                if listing.id is None:
                    console.print("  [yellow]Listing has no id — skipping[/yellow]")
                    continue

                if approval_required:
                    db.conn.execute(
                        """INSERT INTO pending_approvals (listing_id, approved, score)
                           VALUES (?, FALSE, ?)
                           ON CONFLICT (listing_id) DO NOTHING;""",
                        [listing.id, score],
                    )
                    row = db.conn.execute(
                        "SELECT approved FROM pending_approvals WHERE listing_id = ?",
                        [listing.id],
                    ).fetchone()
                    if not row or not row[0]:
                        console.print(
                            f"  [yellow]Awaiting HITL approval: listing {listing.id}[/yellow]"
                        )
                        continue

                # Check daily alert quota
                max_per_day = config.alert_schedule.max_alerts_per_day
                daily_count = _get_daily_alert_count(db.conn)
                if daily_count >= max_per_day:
                    console.print(
                        f"  [yellow]Daily alert limit reached ({max_per_day}), "
                        f"queued: {listing.address or listing.url}[/yellow]"
                    )
                    db.conn.execute(
                        "INSERT INTO daily_alert_log (listing_hash, status) VALUES (?, 'queued')",
                        [listing.content_hash],
                    )
                    continue

                alerter.send_alert(listing, score, flags)
                db.conn.execute(
                    "INSERT INTO daily_alert_log (listing_hash, status) VALUES (?, 'sent')",
                    [listing.content_hash],
                )
                console.print(
                    f"  [green]Alert sent:[/green] {listing.address or listing.url} "
                    f"(score {score:.1f})"
                )

        # 3. Process approved-but-not-alerted listings from previous scans (always runs)
        alerter = TelegramAlerter(
            bot_token=config.telegram_bot_token or None,
            chat_id=config.telegram_chat_id or None,
            score_threshold=threshold,
        )
        pending_rows = db.conn.execute(
            "SELECT listing_id, score FROM pending_approvals "
            "WHERE approved = TRUE AND (alerted = FALSE OR alerted IS NULL) "
            "ORDER BY listing_id ASC"
        ).fetchall()

        for listing_id, stored_score in pending_rows:
            row = db.conn.execute(
                "SELECT id, content_hash, url, address, m2, floor, price, "
                "garage_price, price_includes_garage, certificado_energetico_present, "
                "rooms, description, portal, external_id, fetched_at "
                "FROM listings WHERE id = ?", [listing_id]
            ).fetchone()
            if row is None:
                continue
            cols = (
                "id", "content_hash", "url", "address", "m2", "floor", "price",
                "garage_price", "price_includes_garage", "certificado_energetico_present",
                "rooms", "description", "portal", "external_id", "fetched_at"
            )
            data = dict(zip(cols, row, strict=True))
            listing = Listing(**data)

            # Re-score to get flags; use stored score when available
            score_result = scorer.score(listing, db_conn=db.conn)
            flags = score_result.flags
            score = stored_score if stored_score is not None else score_result.total * 100.0

            # Check daily alert quota
            max_per_day = config.alert_schedule.max_alerts_per_day
            daily_count = _get_daily_alert_count(db.conn)
            if daily_count >= max_per_day:
                console.print(
                    f"  [yellow]Daily alert limit reached ({max_per_day}), "
                    f"queued (approved): {listing.address or listing.url}[/yellow]"
                )
                db.conn.execute(
                    "INSERT INTO daily_alert_log (listing_hash, status) VALUES (?, 'queued')",
                    [listing.content_hash],
                )
                db.conn.execute(
                    "UPDATE pending_approvals SET alerted = TRUE WHERE listing_id = ?",
                    [listing_id],
                )
                continue

            alerter.send_alert(listing, score, flags)
            console.print(
                f"  [green]Alert sent (approved):[/green] {listing.address or listing.url} "
                f"(score {score:.1f})"
            )
            db.conn.execute(
                "UPDATE pending_approvals SET alerted = TRUE WHERE listing_id = ?",
                [listing_id],
            )
            db.conn.execute(
                "INSERT INTO daily_alert_log (listing_hash, status) VALUES (?, 'sent')",
                [listing.content_hash],
            )

        # 4. Re-attempt queued alerts from previous days (always runs)
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        queued_rows = db.conn.execute(
            "SELECT dlh.id, dlh.listing_hash, dlh.sent_at FROM daily_alert_log dlh "
            "WHERE dlh.status = 'queued' AND dlh.sent_at IS NOT NULL AND dlh.sent_at < ? "
            "ORDER BY dlh.id ASC",
            [today_start],
        ).fetchall()

        queued_max_per_day = config.alert_schedule.max_alerts_per_day
        for queued_id, listing_hash, _queued_at in queued_rows:
            daily_count = _get_daily_alert_count(db.conn)
            if daily_count >= queued_max_per_day:
                console.print(
                    f"  [yellow]Daily alert limit reached ({queued_max_per_day}), "
                    f"deferred (queued): hash {listing_hash}[/yellow]"
                )
                break

            row = db.conn.execute(
                "SELECT id, url, address, content_hash, m2, floor, price, "
                "garage_price, price_includes_garage, certificado_energetico_present, "
                "rooms, description, portal, external_id, fetched_at "
                "FROM listings WHERE content_hash = ? LIMIT 1",
                [listing_hash],
            ).fetchone()
            if row is None:
                db.conn.execute("DELETE FROM daily_alert_log WHERE id = ?", [queued_id])
                continue

            cols = (
                "id", "url", "address", "content_hash", "m2", "floor", "price",
                "garage_price", "price_includes_garage", "certificado_energetico_present",
                "rooms", "description", "portal", "external_id", "fetched_at"
            )
            data = dict(zip(cols, row, strict=True))
            listing = Listing(**data)

            score_result = scorer.score(listing, db_conn=db.conn)
            score_value = score_result.total * 100.0

            if score_value < threshold:
                console.print(
                    f"  [dim]Alert gated (queued re-attempt, score {score_value:.1f} "
                    f"< {threshold}): {listing.address or listing.url}[/dim]"
                )
                continue

            alerter.send_alert(listing, score_value, score_result.flags)
            db.conn.execute(
                "UPDATE daily_alert_log SET status = 'sent', sent_at = ? WHERE id = ?",
                [datetime.now(UTC), queued_id],
            )
            console.print(
                f"  [green]Alert sent (queued re-attempt):[/green] "
                f"{listing.address or listing.url} (score {score_value:.1f})"
            )

    console.print("[bold green]Pipeline scan complete.[/bold green]")


def _display_status(config: Any) -> None:
    """Query the database and print a status summary."""
    db_path = _get_db_path()

    with get_connection(db_path) as db:
        db.init_db()

        # Total listings
        total_row = db.conn.execute("SELECT COUNT(*) FROM listings").fetchone()
        total = total_row[0] if total_row else 0

        # Last scan time
        last_scan_row = db.conn.execute(
            "SELECT MAX(fetched_at) FROM listings"
        ).fetchone()
        last_scan = last_scan_row[0] if last_scan_row else None

        # Pending approvals
        rows = db.conn.execute(
            "SELECT listing_id, created_at FROM pending_approvals "
            "WHERE approved = FALSE ORDER BY created_at ASC"
        ).fetchall()
        pending = [{"listing_id": int(r[0]), "created_at": str(r[1])} for r in rows]

    # Render a summary table
    table = Table(title="Home-Ops Pipeline Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Total listings", str(total))
    table.add_row(
        "Last scan",
        str(last_scan) if last_scan else "[dim]never[/dim]",
    )
    table.add_row("Pending approvals", str(len(pending)))

    if pending:
        from rich import box

        detail = Table(box=box.SIMPLE)
        detail.add_column("Listing ID")
        detail.add_column("Created at")
        for p in pending:
            detail.add_row(str(p["listing_id"]), str(p["created_at"]))
        console.print(table)
        console.print("\n[bold]Pending approvals:[/bold]")
        console.print(detail)
    else:
        console.print(table)


if __name__ == "__main__":
    app()
