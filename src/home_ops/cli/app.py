"""Typer CLI entry point for Home-Ops pipeline orchestration."""

import typer

app = typer.Typer(help="Home-Ops: Real estate agentic pipeline")


@app.command()
def scan() -> None:
    """Run the full pipeline: scrape → score → alert."""
    typer.echo("Pipeline not yet implemented.")


@app.command()
def status() -> None:
    """Show pipeline state and recent results."""
    typer.echo("Status not yet implemented.")


@app.command(name="snapshots")
def snapshots_reset() -> None:
    """Invalidate all cached snapshots. Next run re-scrapes."""
    typer.echo("Snapshots reset not yet implemented.")


if __name__ == "__main__":
    app()
