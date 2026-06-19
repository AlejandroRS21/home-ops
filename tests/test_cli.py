"""Tests for the Typer CLI app module."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from home_ops.cli.app import (
    _display_status,
    _get_db_path,
    _next_run_time,
    _run_scan,
    app,
)
from home_ops.models.data_storage import DuckDBConnection
from home_ops.models.schema import ScheduleConfig

runner = CliRunner()


class TestCLIHelp:
    """CLI help output tests."""

    def test_help_shows_all_commands(self) -> None:
        """GIVEN homeops --help WHEN run THEN exit 0 and show all commands."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scan" in result.output
        assert "status" in result.output
        assert "snapshots-reset" in result.output
        assert "approve" in result.output

    def test_daemon_in_help(self) -> None:
        """GIVEN homeops --help WHEN run THEN shows daemon command."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "daemon" in result.output

    def test_scan_help(self) -> None:
        """GIVEN homeops scan --help WHEN run THEN exit 0."""
        result = runner.invoke(app, ["scan", "--help"])
        assert result.exit_code == 0
        assert "deduplicate" in result.output

    def test_status_help(self) -> None:
        """GIVEN homeops status --help WHEN run THEN exit 0."""
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0
        assert "listings" in result.output

    def test_snapshots_reset_help(self) -> None:
        """GIVEN homeops snapshots-reset --help WHEN run THEN exit 0."""
        result = runner.invoke(app, ["snapshots-reset", "--help"])
        assert result.exit_code == 0
        assert "cold-start" in result.output

    def test_approve_help(self) -> None:
        """GIVEN homeops approve --help WHEN run THEN exit 0."""
        result = runner.invoke(app, ["approve", "--help"])
        assert result.exit_code == 0
        assert "listing_id" in result.output


class TestCLICommands:
    """CLI command execution tests (with mocks for external deps)."""

    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.cli.app.get_connection")
    def test_status_with_empty_db(
        self, mock_get_conn: MagicMock, mock_load_config: MagicMock
    ) -> None:
        """GIVEN empty database WHEN status called THEN shows zero metrics."""
        from home_ops.models.data_storage import DuckDBConnection

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.hitl_approval_required = True

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "0" in result.output  # shows 0 listings
        assert "never" in result.output  # no last scan

    @patch("home_ops.scraper.lifecycle.invalidate_snapshots")
    def test_snapshots_reset_success(self, mock_invalidate: MagicMock) -> None:
        """GIVEN snapshots-reset command WHEN run THEN calls invalidate."""
        result = runner.invoke(app, ["snapshots-reset"])
        assert result.exit_code == 0
        mock_invalidate.assert_called_once()
        assert "invalidated" in result.output.lower()

    @patch("home_ops.scraper.lifecycle.invalidate_snapshots")
    def test_snapshots_reset_failure(self, mock_invalidate: MagicMock) -> None:
        """GIVEN invalidate_snapshots fails WHEN run THEN exit 1."""
        mock_invalidate.side_effect = PermissionError("Not allowed")

        result = runner.invoke(app, ["snapshots-reset"])
        assert result.exit_code == 1
        assert "Failed" in result.output

    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.cli.app.get_connection")
    def test_approve_listing(
        self, mock_get_conn: MagicMock, mock_load_config: MagicMock
    ) -> None:
        """GIVEN approve command WHEN run with listing_id THEN approves it."""
        from home_ops.models.data_storage import DuckDBConnection

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.hitl_approval_required = True

        result = runner.invoke(app, ["approve", "42"])
        assert result.exit_code == 0
        assert "42" in result.output
        assert "approved" in result.output.lower()

        # Verify it's actually approved in DB
        row = db.conn.execute(
            "SELECT approved FROM pending_approvals WHERE listing_id = 42"
        ).fetchone()
        assert row is not None
        assert row[0] is True

    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.scraper.lifecycle.cold_start")
    def test_scan_with_no_listings(
        self,
        mock_cold_start: MagicMock,
        mock_get_conn: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        """GIVEN scan with scraper returning no listings WHEN run THEN reports no listings."""
        from home_ops.models.data_storage import DuckDBConnection

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring_thresholds = {"min_score_to_alert": 70}
        mock_load_config.return_value.hitl_approval_required = True
        mock_load_config.return_value.telegram_chat_id = ""
        mock_cold_start.return_value = []

        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0

    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_hitl_bypass_skips_unapproved_listing(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_get_conn: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        """GIVEN HITL enabled and listing not approved WHEN scan THEN alert not sent."""
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring_thresholds = {"min_score_to_alert": 70}
        mock_load_config.return_value.hitl_approval_required = True
        mock_load_config.return_value.telegram_chat_id = ""

        # Scored listing (above threshold) but not approved
        listing = Listing(
            content_hash="hitl_test_001",
            url="https://test.com/hitl",
            address="Calle HITL 1",
        )
        db.insert_listing(listing)
        mock_cold_start.return_value = [listing]
        mock_send_alert.return_value = True

        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0
        # Alert should NOT be sent because listing is pending approval
        mock_send_alert.assert_not_called()

    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_scan_with_new_listings(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_get_conn: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        """GIVEN scan with new listings WHEN run THEN processes them."""
        from home_ops.models.data_storage import DuckDBConnection
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring_thresholds = {"min_score_to_alert": 70}
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""

        # Create listing with explicit id
        listing = Listing(
            content_hash="test_hash_001",
            url="https://test.com/listing1",
            address="Calle Test 123",
            price=300000.00,
            m2=85.0,
            floor="3B",
        )

        # Insert to get the id, then mock cold_start to return it
        db.insert_listing(listing)
        mock_cold_start.return_value = [listing]
        mock_send_alert.return_value = True

        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0


class TestDisplayStatus:
    """Unit tests for the _display_status helper."""

    def test_display_empty_db(self) -> None:
        """GIVEN empty DB WHEN _display_status THEN shows zeros."""
        from home_ops.models.schema import Config

        with patch("home_ops.cli.app.get_connection") as mock_conn:
            mock_db = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_db
            # Mock COUNT(*) and MAX(fetched_at)
            mock_db.conn.execute.return_value.fetchone.side_effect = [
                (0,),  # COUNT(*)
                (None,),  # MAX(fetched_at)
            ]
            mock_db.conn.execute.return_value.fetchall.return_value = []

            config = Config()
            _display_status(config)  # should not raise

    def test_display_with_data(self) -> None:
        """GIVEN DB with listings WHEN _display_status THEN shows counts."""
        from home_ops.models.schema import Config

        with patch("home_ops.cli.app.get_connection") as mock_conn:
            mock_db = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_db
            mock_db.conn.execute.return_value.fetchone.side_effect = [
                (5,),  # COUNT(*)
                ("2024-01-15 10:00:00",),  # MAX(fetched_at)
            ]
            mock_db.conn.execute.return_value.fetchall.return_value = []
            _display_status(Config())


class TestGetDbPath:
    """DB path resolution tests."""

    def test_returns_default_path(self) -> None:
        """GIVEN _get_db_path WHEN called THEN returns DEFAULT_DB_PATH as string."""
        from home_ops.models.data_storage import DEFAULT_DB_PATH

        path = _get_db_path()
        assert path == str(DEFAULT_DB_PATH)

    def test_always_returns_string(self) -> None:
        """GIVEN _get_db_path WHEN called THEN result is a string."""
        path = _get_db_path()
        assert isinstance(path, str)


class TestRunScan:
    """Unit tests for _run_scan pipeline logic."""

    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.scraper.lifecycle.cold_start")
    def test_run_scan_creates_db(
        self,
        mock_cold_start: MagicMock,
        mock_subsequent_run: MagicMock,
        mock_get_conn: MagicMock,
    ) -> None:
        """GIVEN valid config WHEN _run_scan THEN creates and inits database."""
        mock_db = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_db
        mock_cold_start.return_value = []

        with patch("home_ops.cli.app.load_config") as mock_load:
            mock_load.return_value.portal_url = "https://test.url"
            mock_load.return_value.scoring_thresholds = {"min_score_to_alert": 70}
            mock_load.return_value.hitl_approval_required = False
            mock_load.return_value.telegram_chat_id = ""

            _run_scan()

        # DB init should have been called
        mock_db.init_db.assert_called_once()

    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.scraper.lifecycle.cold_start")
    def test_run_scan_with_cold_start_failure(
        self,
        mock_cold_start: MagicMock,
        mock_subsequent_run: MagicMock,
        mock_get_conn: MagicMock,
    ) -> None:
        """GIVEN cold_start raises WHEN _run_scan THEN handles gracefully."""
        mock_db = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_db
        mock_cold_start.side_effect = RuntimeError("Network error")

        with patch("home_ops.cli.app.load_config") as mock_load:
            mock_load.return_value.portal_url = "https://test.url"
            mock_load.return_value.scoring_thresholds = {"min_score_to_alert": 70}
            mock_load.return_value.hitl_approval_required = False
            mock_load.return_value.telegram_chat_id = ""

            # Should not raise — the function logs and continues
            _run_scan()
            mock_db.init_db.assert_called_once()

    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.scraper.lifecycle.cold_start")
    def test_run_scan_with_duplicates(
        self,
        mock_cold_start: MagicMock,
        mock_subsequent_run: MagicMock,
        mock_get_conn: MagicMock,
    ) -> None:
        """GIVEN scan with existing listings WHEN run THEN skips duplicates."""
        from home_ops.models.schema import Listing

        mock_db = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_db
        mock_db.insert_listing.return_value = None  # simulate duplicate

        listing = Listing(
            content_hash="dup_hash",
            url="https://test.com/dup",
            address="Calle Duplicada",
        )
        mock_cold_start.return_value = [listing]

        with patch("home_ops.cli.app.load_config") as mock_load:
            mock_load.return_value.portal_url = "https://test.url"
            mock_load.return_value.scoring_thresholds = {"min_score_to_alert": 70}
            mock_load.return_value.hitl_approval_required = False
            mock_load.return_value.telegram_chat_id = ""

            _run_scan()
            mock_db.init_db.assert_called_once()

    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    def test_auto_detect_empty_db_calls_cold_start(
        self,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
    ) -> None:
        """GIVEN empty DB WHEN _run_scan THEN calls cold_start."""
        from home_ops.models.data_storage import DuckDBConnection

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()  # 0 rows

        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring_thresholds = {"min_score_to_alert": 70}
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""

        with patch("home_ops.scraper.lifecycle.cold_start") as mock_cs:
            mock_cs.return_value = []
            _run_scan()
            mock_cs.assert_called_once_with("https://test.url")

    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    def test_auto_detect_populated_db_calls_subsequent_run(
        self,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
    ) -> None:
        """GIVEN DB with rows WHEN _run_scan THEN calls subsequent_run."""
        from home_ops.models.data_storage import DuckDBConnection
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        db.insert_listing(Listing(content_hash="existing"))  # 1 row

        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring_thresholds = {"min_score_to_alert": 70}
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""

        with patch("home_ops.scraper.lifecycle.subsequent_run") as mock_sr:
            mock_sr.return_value = []
            _run_scan()
            mock_sr.assert_called_once_with(
                "https://test.url", db, max_pages=5, force=False
            )

    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    def test_force_flag_passed_to_subsequent_run(
        self,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
    ) -> None:
        """GIVEN force=True WHEN _run_scan THEN subsequent_run gets force=True."""
        from home_ops.models.data_storage import DuckDBConnection
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        db.insert_listing(Listing(content_hash="existing"))

        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring_thresholds = {"min_score_to_alert": 70}
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""

        with patch("home_ops.scraper.lifecycle.subsequent_run") as mock_sr:
            mock_sr.return_value = []
            _run_scan(force=True)
            mock_sr.assert_called_once_with(
                "https://test.url", db, max_pages=5, force=True
            )


class TestNextRunTime:
    """Tests for _next_run_time pure function."""

    def test_daily_mode_no_last_run_before_time(self) -> None:
        """GIVEN daily mode at 14:00 and now=10:00, no last_run WHEN computed THEN returns now (immediate)."""
        sched = ScheduleConfig(mode="daily", daily_time="14:00", timezone="UTC")
        now = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, now=now)
        assert result == now

    def test_daily_mode_no_last_run_after_time(self) -> None:
        """GIVEN daily mode at 09:00 and now=14:00, no last_run WHEN computed THEN returns now (immediate)."""
        sched = ScheduleConfig(mode="daily", daily_time="09:00", timezone="UTC")
        now = datetime(2026, 6, 18, 14, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, now=now)
        assert result == now

    def test_daily_mode_with_last_run(self) -> None:
        """GIVEN daily mode at 09:00 and last_run yesterday WHEN computed THEN returns today 09:00."""
        sched = ScheduleConfig(mode="daily", daily_time="09:00", timezone="UTC")
        last_run = datetime(2026, 6, 17, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, last_run=last_run, now=now)
        # Next 09:00 after last_run (June 17 10:00) is June 18 09:00
        expected = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_daily_mode_last_run_already_today(self) -> None:
        """GIVEN daily mode and last_run is today's run WHEN computed THEN returns next day."""
        sched = ScheduleConfig(mode="daily", daily_time="09:00", timezone="UTC")
        last_run = datetime(2026, 6, 18, 9, 5, 0, tzinfo=UTC)
        now = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, last_run=last_run, now=now)
        expected = datetime(2026, 6, 19, 9, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_interval_mode_no_last_run(self) -> None:
        """GIVEN interval mode, no last_run WHEN computed THEN returns now."""
        sched = ScheduleConfig(mode="interval", interval_hours=6, timezone="UTC")
        now = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, now=now)
        assert result == now

    def test_interval_mode_with_last_run(self) -> None:
        """GIVEN interval mode, last_run at 08:00 WHEN computed THEN returns 08:00 + 6h = 14:00."""
        sched = ScheduleConfig(mode="interval", interval_hours=6, timezone="UTC")
        last_run = datetime(2026, 6, 18, 8, 0, 0, tzinfo=UTC)
        now = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, last_run=last_run, now=now)
        expected = datetime(2026, 6, 18, 14, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_interval_mode_crosses_midnight(self) -> None:
        """GIVEN interval mode, last_run at 22:00, interval 6h WHEN computed THEN returns next day 04:00."""
        sched = ScheduleConfig(mode="interval", interval_hours=6, timezone="UTC")
        last_run = datetime(2026, 6, 18, 22, 0, 0, tzinfo=UTC)
        now = datetime(2026, 6, 18, 23, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, last_run=last_run, now=now)
        expected = datetime(2026, 6, 19, 4, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_daily_mode_timezone_aware(self) -> None:
        """GIVEN daily mode with Europe/Madrid timezone, no last_run WHEN computed THEN returns now (immediate)."""
        sched = ScheduleConfig(mode="daily", daily_time="09:00", timezone="Europe/Madrid")
        now = datetime(2026, 6, 18, 7, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, now=now)
        assert result == now

    def test_interval_mode_float_hours(self) -> None:
        """GIVEN interval mode with 1.5h interval WHEN computed THEN returns last_run + 1.5h."""
        sched = ScheduleConfig(mode="interval", interval_hours=1.5, timezone="UTC")
        last_run = datetime(2026, 6, 18, 8, 0, 0, tzinfo=UTC)
        now = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, last_run=last_run, now=now)
        expected = datetime(2026, 6, 18, 9, 30, 0, tzinfo=UTC)
        assert result == expected


class TestGetDailyAlertCount:
    """Tests for _get_daily_alert_count pure function."""

    def test_returns_zero_when_no_alerts(self, db: DuckDBConnection) -> None:
        """GIVEN no alerts today WHEN queried THEN returns 0."""
        from home_ops.cli.app import _get_daily_alert_count

        count = _get_daily_alert_count(db.conn)
        assert count == 0

    def test_returns_sent_count(self, db: DuckDBConnection) -> None:
        """GIVEN sent alerts today WHEN queried THEN returns correct count."""
        from home_ops.cli.app import _get_daily_alert_count

        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, status) VALUES ('h1', 'sent')"
        )
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, status) VALUES ('h2', 'sent')"
        )
        count = _get_daily_alert_count(db.conn)
        assert count == 2

    def test_excludes_queued_alerts(self, db: DuckDBConnection) -> None:
        """GIVEN sent and queued alerts WHEN queried THEN only counts 'sent'."""
        from home_ops.cli.app import _get_daily_alert_count

        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, status) VALUES ('h1', 'sent')"
        )
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, status) VALUES ('h2', 'queued')"
        )
        count = _get_daily_alert_count(db.conn)
        assert count == 1

    def test_handles_old_entries(self, db: DuckDBConnection) -> None:
        """GIVEN sent alerts yesterday WHEN queried TODAY THEN returns 0."""
        from home_ops.cli.app import _get_daily_alert_count

        # Insert with yesterday's date
        yesterday = datetime.now(UTC) - timedelta(days=1)
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, sent_at, status) VALUES (?, ?, ?)",
            ["h1", yesterday, "sent"],
        )
        count = _get_daily_alert_count(db.conn)
        assert count == 0


class TestRunDaemonCycle:
    """Tests for _run_daemon_cycle daemon loop body."""

    @patch("home_ops.cli.app.get_connection")
    def test_runs_when_schedule_due(self, mock_get_conn: MagicMock) -> None:
        """GIVEN daily schedule is due WHEN _run_daemon_cycle THEN calls run_fn."""
        from home_ops.cli.app import _run_daemon_cycle
        from home_ops.models.schema import Config, ScheduleConfig

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db

        run_fn = MagicMock()
        # Use UTC timezone so 09:00 UTC == 09:00 in schedule
        config = Config(alert_schedule=ScheduleConfig(timezone="UTC"))
        now = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)  # exactly daily_time (09:00 UTC)

        result = _run_daemon_cycle(config, run_fn=run_fn, now=now)

        assert result is True
        run_fn.assert_called_once()

    @patch("home_ops.cli.app.get_connection")
    def test_skips_when_not_due(self, mock_get_conn: MagicMock) -> None:
        """GIVEN schedule is not due WHEN _run_daemon_cycle THEN skips."""
        from home_ops.cli.app import _run_daemon_cycle
        from home_ops.models.schema import Config

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        # Insert prior run so daemon computes next schedule instead of first-start immediate
        db.conn.execute(
            "INSERT INTO scraping_runs (started_at, finished_at, status) "
            "VALUES (?, ?, 'success')",
            [
                datetime(2026, 6, 17, 9, 0, 0, tzinfo=UTC),
                datetime(2026, 6, 17, 9, 5, 0, tzinfo=UTC),
            ],
        )
        mock_get_conn.return_value.__enter__.return_value = db

        run_fn = MagicMock()
        config = Config()
        now = datetime(2026, 6, 18, 6, 0, 0, tzinfo=UTC)  # 06:00, before 09:00

        result = _run_daemon_cycle(config, run_fn=run_fn, now=now)

        assert result is False
        run_fn.assert_not_called()

    @patch("home_ops.cli.app.get_connection")
    def test_skips_overlapping_run(self, mock_get_conn: MagicMock) -> None:
        """GIVEN a run is already in progress WHEN _run_daemon_cycle THEN skips."""
        from home_ops.cli.app import _run_daemon_cycle
        from home_ops.models.schema import Config, ScheduleConfig

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        # Insert a running record
        db.conn.execute(
            "INSERT INTO scraping_runs (started_at, status) VALUES (?, 'running')",
            [datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)],
        )
        mock_get_conn.return_value.__enter__.return_value = db

        run_fn = MagicMock()
        config = Config(alert_schedule=ScheduleConfig(timezone="UTC"))
        now = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)

        result = _run_daemon_cycle(config, run_fn=run_fn, now=now)

        assert result is False
        run_fn.assert_not_called()

    @patch("home_ops.cli.app.get_connection")
    def test_catch_up_runs_immediately(self, mock_get_conn: MagicMock) -> None:
        """GIVEN daemon starts and last_run is yesterday WHEN cycle THEN runs immediately."""
        from home_ops.cli.app import _run_daemon_cycle
        from home_ops.models.schema import Config

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        # Last successful run was yesterday at 09:00
        db.conn.execute(
            "INSERT INTO scraping_runs (started_at, finished_at, status) "
            "VALUES (?, ?, 'success')",
            [
                datetime(2026, 6, 17, 9, 0, 0, tzinfo=UTC),
                datetime(2026, 6, 17, 9, 5, 0, tzinfo=UTC),
            ],
        )
        mock_get_conn.return_value.__enter__.return_value = db

        run_fn = MagicMock()
        config = Config(alert_schedule=ScheduleConfig(mode="daily", daily_time="09:00"))
        # Now is 09:00 on June 18 — same as daily_time, should run (catch-up detected)
        now = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)

        result = _run_daemon_cycle(config, run_fn=run_fn, now=now)

        assert result is True
        run_fn.assert_called_once()


class TestDaemonCommand:
    """Tests for the homeops daemon CLI command."""

    def test_daemon_help(self) -> None:
        """GIVEN homeops daemon --help WHEN run THEN shows daemon description."""
        result = runner.invoke(app, ["daemon", "--help"])
        assert result.exit_code == 0
        assert "daemon" in result.output.lower()

    @patch("home_ops.cli.app._run_daemon_inner_loop")
    @patch("home_ops.cli.app.load_config")
    def test_daemon_starts_without_error(
        self,
        mock_load_config: MagicMock,
        mock_loop: MagicMock,
    ) -> None:
        """GIVEN daemon command WHEN run THEN loads config and starts loop."""
        mock_load_config.return_value.alert_schedule = ScheduleConfig()
        mock_loop.return_value = None

        result = runner.invoke(app, ["daemon", "--dry-run"])

        assert result.exit_code == 0


class TestNextRunTimeDailyModeDST:
    """DST edge case tests for _next_run_time."""

    def test_daily_mode_uses_dst_transition(self) -> None:
        """GIVEN daily mode in Europe/Madrid during DST transition, with last_run WHEN computed THEN handles correctly."""
        from zoneinfo import ZoneInfo

        sched = ScheduleConfig(mode="daily", daily_time="09:00", timezone="Europe/Madrid")
        madrid = ZoneInfo("Europe/Madrid")
        # March 29, 2026: DST starts on last Sunday of March (March 29, 2026)
        # At 2026-03-29 02:00 clocks spring forward to 03:00
        # Last run was March 28 at 10:00 CET (09:00 UTC)
        last_run = datetime(2026, 3, 28, 9, 0, 0, tzinfo=UTC)
        now = datetime(2026, 3, 28, 22, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, last_run=last_run, now=now)
        # Next 09:00 after last_run (March 28 10:00 CET): March 29 at 09:00 CEST = 07:00 UTC
        expected = datetime(2026, 3, 29, 7, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_interval_mode_ignores_timezone_for_computation(self) -> None:
        """GIVEN interval mode WHEN computed THEN timezone only affects display."""
        sched = ScheduleConfig(mode="interval", interval_hours=6, timezone="America/New_York")
        last_run = datetime(2026, 6, 18, 8, 0, 0, tzinfo=UTC)
        now = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
        result = _next_run_time(sched, last_run=last_run, now=now)
        expected = datetime(2026, 6, 18, 14, 0, 0, tzinfo=UTC)
        assert result == expected
