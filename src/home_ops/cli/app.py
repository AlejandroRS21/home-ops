"""Typer CLI entry point for Home-Ops pipeline orchestration.

Usage:
    homeops scan
    homeops status
    homeops snapshots-reset
    homeops approve <listing_id>
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from home_ops.config.loader import load_config
from home_ops.models.data_storage import get_connection
from home_ops.models.schema import Listing

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
# Commands
# ---------------------------------------------------------------------------


@app.command()
def scan(
    config_path: ConfigPathArg = None,
) -> None:
    """Run the full pipeline: scrape → deduplicate → score → alert.

    Loads configuration, executes a cold-start scrape of the portal,
    inserts new listings into the database, scores each listing with
    the deterministic rules engine, and alerts via Telegram when the
    score meets the configured threshold and HITL approval is granted.
    """
    try:
        _run_scan(config_path)
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
            from home_ops.alerter.gates import HITLGate

            gate = HITLGate(db, approval_required=True)
            gate.approve(listing_id)
            console.print(
                f"[green]Listing {listing_id} approved. "
                f"Alerts will be sent on next scan.[/green]"
            )
    except Exception as exc:
        console.print(f"[bold red]Failed to approve listing {listing_id}:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# Internal pipeline logic
# ---------------------------------------------------------------------------


def _run_scan(config_path: Path | None = None) -> None:
    """Orchestrate one pipeline scan cycle."""
    config = load_config(config_path)

    threshold: float = float(config.scoring_thresholds.get("min_score_to_alert", 70))

    db_path = _get_db_path()
    with get_connection(db_path) as db:
        db.init_db()

        # 1. Scrape
        console.print("[bold]Scanning portal...[/bold]")
        from home_ops.scraper.lifecycle import cold_start

        try:
            listings: list[Listing] = cold_start(config.portal_url)
        except Exception as exc:
            console.print(f"[yellow]Scraper returned no data: {exc}[/yellow]")
            listings = []

        if not listings:
            console.print("[yellow]No new listings found this cycle.[/yellow]")
            return

        # 2. Deduplicate & store
        from home_ops.scorer.rules import DeterministicScorer
        from home_ops.scraper.dedup import compute_content_hash

        scorer = DeterministicScorer()
        scored: list[tuple[Listing, float]] = []

        for listing in listings:
            # Ensure content_hash is set
            if not listing.content_hash:
                listing.content_hash = compute_content_hash(
                    listing.address, listing.m2, listing.floor
                )

            inserted_id = db.insert_listing(listing)

            if inserted_id is None:
                console.print(
                    f"  [dim]Skipped (duplicate): {listing.address or listing.url}[/dim]"
                )
                continue

            # 3. Score
            score = scorer.score(listing)
            console.print(
                f"  [cyan]Scored:[/cyan] {listing.address or listing.url} "
                f"→ [bold]{score:.1f}[/bold] (threshold {threshold})"
            )
            scored.append((listing, score))

        # 4. Alert gating
        from home_ops.alerter.gates import HITLGate
        from home_ops.alerter.telegram import TelegramAlerter

        gate = HITLGate(db, approval_required=config.hitl_approval_required)
        alerter = TelegramAlerter(
            bot_token=config.telegram_chat_id or None,
            chat_id=None,
            score_threshold=threshold,
        )

        for listing, score in scored:
            if score < threshold:
                console.print(
                    f"  [dim]Alert gated (score {score:.1f} < {threshold}): "
                    f"{listing.address or listing.url}[/dim]"
                )
                continue

            # Request HITL approval for the listing
            if listing.id is not None:
                gate.request_approval(listing.id)
            else:
                console.print("  [yellow]Listing has no id — skipping HITL check[/yellow]")
                continue

            if not gate.is_approved(listing.id):
                console.print(
                    f"  [yellow]Awaiting HITL approval: listing {listing.id}[/yellow]"
                )
                continue

            alerter.send_alert(listing, score)
            console.print(
                f"  [green]Alert sent:[/green] {listing.address or listing.url} "
                f"(score {score:.1f})"
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
        from home_ops.alerter.gates import HITLGate

        gate = HITLGate(db, approval_required=True)
        pending = gate.get_pending()

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
            detail.add_row(str(p["listing_id"]), p["created_at"])
        console.print(table)
        console.print("\n[bold]Pending approvals:[/bold]")
        console.print(detail)
    else:
        console.print(table)


if __name__ == "__main__":
    app()
