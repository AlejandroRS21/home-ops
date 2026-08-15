"""Tests for the Typer CLI app module."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from home_ops.models.schema import ScheduleConfig, ScoringThresholds

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
        mock_load_config.return_value.hitl_approval_required = True
        mock_load_config.return_value.telegram_chat_id = ""
        mock_cold_start.return_value = []

        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0

    @patch("home_ops.scraper.lifecycle.subsequent_run")
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
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN HITL enabled and listing not approved WHEN scan THEN alert not sent."""
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.hitl_approval_required = True
        mock_load_config.return_value.telegram_chat_id = ""

        # Scored listing (above threshold) but not approved
        listing = Listing(
            content_hash="hitl_test_001",
            url="https://test.com/hitl",
            address="Calle HITL 1",
        )
        db.insert_listing(listing)
        mock_subsequent.return_value = [listing]
        mock_send_alert.return_value = True

        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0
        # Alert should NOT be sent because listing is pending approval
        mock_send_alert.assert_not_called()

    @patch("home_ops.scraper.lifecycle.subsequent_run")
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
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN scan with new listings WHEN run THEN processes them."""
        from home_ops.models.data_storage import DuckDBConnection
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
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
        mock_subsequent.return_value = [listing]
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
        """GIVEN cold_start raises WHEN _run_scan THEN exception re-raised."""
        mock_db = MagicMock()
        mock_db.conn.execute.return_value.fetchone.return_value = (0,)
        mock_get_conn.return_value.__enter__.return_value = mock_db
        mock_cold_start.side_effect = RuntimeError("Network error")

        with patch("home_ops.cli.app.load_config") as mock_load:
            mock_load.return_value.portal_url = "https://test.url"
            mock_load.return_value.hitl_approval_required = False
            mock_load.return_value.telegram_chat_id = ""

            with pytest.raises(RuntimeError, match="Network error"):
                _run_scan()

    @patch("home_ops.cli.app.get_connection")
    def test_run_scan_alert_failure_writes_failed_status(
        self,
        mock_get_conn: MagicMock,
    ) -> None:
        """GIVEN send_alert fails WHEN _run_scan runs THEN daily_alert_log gets status='failed' and quota is 0."""
        from home_ops.models.data_storage import DuckDBConnection
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db

        listing = Listing(
            content_hash="hash_alert_fail",
            url="https://test.com/alert-fail",
            address="Calle Alert Fail 1",
            price=Decimal("200000.00"),
            m2=80.0,
            floor="2",
        )

        with patch("home_ops.cli.app.load_config") as mock_load, \
             patch("home_ops.scraper.lifecycle.cold_start") as mock_cold, \
             patch("home_ops.cli.app.TelegramAlerter") as mock_alerter_cls:

            mock_load.return_value.portal_url = "https://test.url"
            mock_load.return_value.scoring = ScoringThresholds(min_score_to_alert=50)
            mock_load.return_value.hitl_approval_required = False
            mock_load.return_value.telegram_bot_token = "invalid"
            mock_load.return_value.telegram_chat_id = "invalid"
            mock_load.return_value.alert_schedule.max_alerts_per_day = 5
            mock_load.return_value.euribor_rate = 3.5

            mock_cold.return_value = [listing]

            mock_alerter_inst = MagicMock()
            mock_alerter_inst.send_alert.return_value = False
            mock_alerter_cls.return_value = mock_alerter_inst

            _run_scan()

            # Check DB: status should be 'failed'
            rows = db.conn.execute("SELECT listing_hash, status FROM daily_alert_log").fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "hash_alert_fail"
            assert rows[0][1] == "failed"

            # Check daily sent count remains 0
            from home_ops.cli.app import _get_daily_alert_count
            assert _get_daily_alert_count(db.conn) == 0

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
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""

        with patch("home_ops.scraper.lifecycle.subsequent_run") as mock_sr:
            mock_sr.return_value = []
            _run_scan(force=True)
            mock_sr.assert_called_once_with(
                "https://test.url", db, max_pages=5, force=True
            )


class TestScanScamPersistence:
    """Buyer-protection output must reach the real pipeline: alerts and DB."""

    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_scan_persists_scam_fields_and_passes_cost_breakdown(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_get_conn: MagicMock,
        mock_load_config: MagicMock,
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN buyer protection enabled WHEN scan runs THEN scam fields are
        persisted and the cost breakdown reaches send_alert."""
        from home_ops.models.data_storage import DuckDBConnection
        from home_ops.models.schema import (
            BuyerProtectionConfig,
            Listing,
            ScheduleConfig,
            ScoringThresholds,
        )

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring = ScoringThresholds(min_score_to_alert=0)
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""
        mock_load_config.return_value.euribor_rate = 3.5
        mock_load_config.return_value.alert_schedule = ScheduleConfig(max_alerts_per_day=10)
        mock_load_config.return_value.buyer_protection = BuyerProtectionConfig()
        mock_send_alert.return_value = True

        listing = Listing(
            content_hash="scam_wired_001",
            url="https://test.com/scam-wired",
            address="Calle Scam Wire 1",
            price=Decimal("300000"),
            m2=85.0,
            floor="2",
            certificado_energetico_present=True,
        )
        mock_subsequent.return_value = [listing]
        # Seed one row so the scan takes the incremental (subsequent_run) path
        db.insert_listing(Listing(content_hash="seed_scan"))

        _run_scan()

        # Scam fields persisted to the row despite insert-before-score ordering
        stored = db.get_listing("scam_wired_001")
        assert stored is not None
        assert stored["scam_flags"] == []
        assert stored["scam_risk_score"] == 0.0
        assert stored["total_acquisition_cost"] is not None
        assert stored["total_acquisition_cost"] > Decimal("300000")

        # Cost breakdown wired through as the 4th arg to the real alerter
        call_args = mock_send_alert.call_args
        assert call_args is not None
        assert len(call_args[0]) == 4
        assert call_args[0][3] is not None

    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_scan_persists_scam_fields_below_threshold(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_get_conn: MagicMock,
        mock_load_config: MagicMock,
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN listing gated below the alert threshold WHEN scan runs THEN
        scam fields are still persisted before the gate."""
        from home_ops.models.data_storage import DuckDBConnection
        from home_ops.models.schema import (
            BuyerProtectionConfig,
            Listing,
            ScheduleConfig,
            ScoringThresholds,
        )

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring = ScoringThresholds(min_score_to_alert=95)
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""
        mock_load_config.return_value.euribor_rate = 3.5
        mock_load_config.return_value.alert_schedule = ScheduleConfig(max_alerts_per_day=10)
        mock_load_config.return_value.buyer_protection = BuyerProtectionConfig()

        listing = Listing(
            content_hash="scam_wired_002",
            url="https://test.com/scam-wired-2",
            address="Calle Scam Wire 2",
            price=Decimal("300000"),
            m2=85.0,
            floor="2",
            certificado_energetico_present=True,
        )
        mock_subsequent.return_value = [listing]
        # Seed one row so the scan takes the incremental (subsequent_run) path
        db.insert_listing(Listing(content_hash="seed_scan"))

        _run_scan()

        # No alert sent, but the score output is still recorded
        mock_send_alert.assert_not_called()
        stored = db.get_listing("scam_wired_002")
        assert stored is not None
        assert stored["total_acquisition_cost"] is not None


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


class TestRunScanExtra:
    """Additional tests for _run_scan edge cases."""

    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_queued_alerts_from_yesterday_re_attempted(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN queued alert from yesterday WHEN scan runs THEN re-attempted."""
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring = ScoringThresholds(min_score_to_alert=0)
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""
        mock_load_config.return_value.euribor_rate = 3.5
        mock_load_config.return_value.alert_schedule = ScheduleConfig()
        mock_send_alert.return_value = True

        # Insert a listing
        listing = Listing(
            content_hash="queued_yesterday",
            url="https://test.com/queued",
            address="Calle Queued 1",
            price=Decimal("100000"),
            m2=80.0,
        )
        db.insert_listing(listing)

        # Insert a queued alert from yesterday
        yesterday = datetime.now(UTC) - timedelta(days=1)
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, sent_at, status) VALUES (?, ?, 'queued')",
            ["queued_yesterday", yesterday],
        )

        mock_subsequent.return_value = []  # no new listings

        _run_scan()

        # Alert should have been sent and status changed to 'sent'
        row = db.conn.execute(
            "SELECT status FROM daily_alert_log WHERE listing_hash = ?",
            ["queued_yesterday"],
        ).fetchone()
        assert row is not None
        assert row[0] == "sent"
        mock_send_alert.assert_called_once()

    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_queued_re_attempt_respects_daily_quota(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN queued alerts and daily quota full WHEN scan runs THEN does not exceed quota."""
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring = ScoringThresholds(min_score_to_alert=0)
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""
        mock_load_config.return_value.euribor_rate = 3.5

        # Set max_alerts_per_day to 1
        mock_load_config.return_value.alert_schedule.max_alerts_per_day = 1

        # Insert two listings
        for i in range(2):
            listing = Listing(
                content_hash=f"queued_{i}",
                url=f"https://test.com/queued_{i}",
                address=f"Calle Queued {i}",
                price=Decimal("100000"),
                m2=80.0,
            )
            db.insert_listing(listing)

        # Insert queued alerts from yesterday for both
        yesterday = datetime.now(UTC) - timedelta(days=1)
        for i in range(2):
            db.conn.execute(
                "INSERT INTO daily_alert_log (listing_hash, sent_at, status) VALUES (?, ?, 'queued')",
                [f"queued_{i}", yesterday],
            )

        mock_subsequent.return_value = []

        _run_scan()

        # Only 1 alert should be sent (quota is 1)
        assert mock_send_alert.call_count <= 1

        # First queued should be 'sent', second still 'queued'
        sent = db.conn.execute(
            "SELECT COUNT(*) FROM daily_alert_log WHERE status = 'sent' AND listing_hash LIKE 'queued_%'"
        ).fetchone()[0]
        assert sent == 1

    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_todays_queued_alerts_not_re_attempted(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN queued alert from TODAY WHEN scan runs THEN not re-attempted (queued this cycle)."""
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring = None
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""
        mock_load_config.return_value.alert_schedule = ScheduleConfig()
        mock_send_alert.return_value = True

        # Insert a listing
        listing = Listing(
            content_hash="queued_today",
            url="https://test.com/queued_today",
            address="Calle Today 1",
        )
        db.insert_listing(listing)

        # Insert a queued alert from today (no sent_at or today's date)
        now = datetime.now(UTC)
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, sent_at, status) VALUES (?, ?, 'queued')",
            ["queued_today", now],
        )

        mock_subsequent.return_value = []

        _run_scan()

        # Alert should NOT be sent (today's queued alerts are skipped)
        row = db.conn.execute(
            "SELECT status FROM daily_alert_log WHERE listing_hash = ?",
            ["queued_today"],
        ).fetchone()
        assert row is not None
        assert row[0] == "queued"  # still queued
        mock_send_alert.assert_not_called()

    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_failed_requeue_skips_listings_still_pending_approval(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN failed row for a listing still pending approval WHEN scan runs
        THEN delivered exactly once via step-3 (no step-4 duplicate), while a
        failed row for a non-pending listing IS re-attempted by step-4."""
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring = ScoringThresholds(min_score_to_alert=0)
        mock_load_config.return_value.hitl_approval_required = True
        mock_load_config.return_value.telegram_chat_id = ""
        mock_load_config.return_value.euribor_rate = 3.5
        mock_load_config.return_value.alert_schedule = ScheduleConfig()
        mock_send_alert.return_value = True
        mock_subsequent.return_value = []

        # Listing approved but not yet alerted — step-3 pipeline owns it,
        # and a stale 'failed' row from the previous day exists (R3-1 source)
        pending = Listing(
            content_hash="failed_pending_001",
            url="https://test.com/pending",
            address="Calle Pending 1",
            price=Decimal("100000"),
            m2=80.0,
        )
        pending_id = db.insert_listing(pending)
        db.conn.execute(
            "INSERT INTO pending_approvals (listing_id, approved, alerted) "
            "VALUES (?, TRUE, FALSE)",
            [pending_id],
        )
        yesterday = datetime.now(UTC) - timedelta(days=1)
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, sent_at, status) VALUES (?, ?, 'failed')",
            [pending.content_hash, yesterday],
        )

        # Not pending approval — step-4 must still re-attempt this one
        other = Listing(
            content_hash="failed_other_002",
            url="https://test.com/other",
            address="Calle Other 1",
            price=Decimal("100000"),
            m2=80.0,
        )
        db.insert_listing(other)
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, sent_at, status) VALUES (?, ?, 'failed')",
            [other.content_hash, yesterday],
        )

        _run_scan()

        sends = [call.args[0].content_hash for call in mock_send_alert.call_args_list]
        # exactly once via step-3 approval path — no step-4 duplicate
        assert sends.count(pending.content_hash) == 1
        # step-4 re-attempt for the non-pending failed row still works
        assert sends.count(other.content_hash) == 1
        other_row = db.conn.execute(
            "SELECT status FROM daily_alert_log WHERE listing_hash = ?",
            [other.content_hash],
        ).fetchone()
        assert other_row is not None
        assert other_row[0] == "sent"

    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.scraper.lifecycle.cold_start")
    def test_queued_alert_deleted_listing_removed_from_queue(
        self,
        mock_cold_start: MagicMock,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
    ) -> None:
        """GIVEN queued alert for deleted listing WHEN scan runs THEN queue entry deleted."""
        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring = None
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""
        mock_load_config.return_value.alert_schedule = ScheduleConfig()

        # Insert queued alert for a listing that doesn't exist
        yesterday = datetime.now(UTC) - timedelta(days=1)
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, sent_at, status) VALUES (?, ?, 'queued')",
            ["nonexistent_hash", yesterday],
        )

        mock_cold_start.return_value = []

        _run_scan()

        # Queue entry should be deleted
        row = db.conn.execute(
            "SELECT COUNT(*) FROM daily_alert_log WHERE listing_hash = ?",
            ["nonexistent_hash"],
        ).fetchone()
        assert row is not None
        assert row[0] == 0


class TestApprovedAlertRetry:
    """Approved-alert delivery: alerted set only on success, no log row on failure."""

    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_approved_alert_failure_keeps_pending_until_success(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN approved send fails THEN alerted stays FALSE with no log row;
        WHEN it succeeds next cycle THEN alerted=TRUE with a 'sent' row.

        Verifies design D3: on failure no daily_alert_log row is written, so
        the approved listing is re-picked solely via pending_approvals.alerted.
        """
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring = ScoringThresholds(min_score_to_alert=50)
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""
        mock_load_config.return_value.euribor_rate = 3.5
        mock_load_config.return_value.alert_schedule = ScheduleConfig()

        listing = Listing(
            content_hash="approved_retry",
            url="https://test.com/approved-retry",
            address="Calle Approved 1",
            price=Decimal("150000"),
            m2=75.0,
        )
        inserted_id = db.insert_listing(listing)
        assert inserted_id is not None
        db.conn.execute(
            "INSERT INTO pending_approvals (listing_id, approved, score) "
            "VALUES (?, TRUE, ?)",
            [inserted_id, 80.0],
        )

        mock_cold_start.return_value = []
        mock_subsequent.return_value = []

        # Cycle 1 — send fails: alerted must stay FALSE and no log row written
        mock_send_alert.return_value = False
        _run_scan()
        pending = db.conn.execute(
            "SELECT alerted FROM pending_approvals WHERE listing_id = ?",
            [inserted_id],
        ).fetchone()
        assert pending is not None
        assert pending[0] is False
        log_count = db.conn.execute("SELECT COUNT(*) FROM daily_alert_log").fetchone()[0]
        assert log_count == 0
        mock_send_alert.assert_called_once()

        # Cycle 2 — send succeeds: alerted=TRUE and a 'sent' log row
        mock_send_alert.return_value = True
        _run_scan()
        pending = db.conn.execute(
            "SELECT alerted FROM pending_approvals WHERE listing_id = ?",
            [inserted_id],
        ).fetchone()
        assert pending is not None
        assert pending[0] is True
        log_row = db.conn.execute(
            "SELECT listing_hash, status FROM daily_alert_log"
        ).fetchone()
        assert log_row is not None
        assert log_row == (listing.content_hash, "sent")
        assert mock_send_alert.call_count == 2


class TestFailedRowRequeue:
    """Previously-failed daily_alert_log rows must be re-selected for delivery."""

    @patch("home_ops.scraper.lifecycle.subsequent_run")
    @patch("home_ops.cli.app.get_connection")
    @patch("home_ops.cli.app.load_config")
    @patch("home_ops.scraper.lifecycle.cold_start")
    @patch("home_ops.alerter.telegram.TelegramAlerter.send_alert")
    def test_failed_row_with_null_sent_at_requeued_and_sent(
        self,
        mock_send_alert: MagicMock,
        mock_cold_start: MagicMock,
        mock_load_config: MagicMock,
        mock_get_conn: MagicMock,
        mock_subsequent: MagicMock,
    ) -> None:
        """GIVEN daily_alert_log row status='failed', sent_at=NULL WHEN scan runs
        THEN it is re-selected, sent exactly once and marked 'sent'."""
        from home_ops.models.schema import Listing

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db
        mock_load_config.return_value.portal_url = "https://test.url"
        mock_load_config.return_value.scoring = ScoringThresholds(min_score_to_alert=0)
        mock_load_config.return_value.hitl_approval_required = False
        mock_load_config.return_value.telegram_chat_id = ""
        mock_load_config.return_value.euribor_rate = 3.5
        mock_load_config.return_value.alert_schedule = ScheduleConfig()

        listing = Listing(
            content_hash="failed_requeue",
            url="https://test.com/failed-requeue",
            address="Calle Failed 1",
            price=Decimal("120000"),
            m2=70.0,
        )
        db.insert_listing(listing)

        # Seed a failed row with explicit NULL sent_at (defensive branch of the
        # step-4 predicate; schema default would stamp CURRENT_TIMESTAMP).
        db.conn.execute(
            "INSERT INTO daily_alert_log (listing_hash, sent_at, status) "
            "VALUES (?, NULL, 'failed')",
            ["failed_requeue"],
        )

        mock_cold_start.return_value = []
        mock_subsequent.return_value = []
        mock_send_alert.return_value = True

        _run_scan()

        row = db.conn.execute(
            "SELECT status, sent_at FROM daily_alert_log WHERE listing_hash = ?",
            ["failed_requeue"],
        ).fetchone()
        assert row is not None
        assert row[0] == "sent"
        assert row[1] is not None  # stamped on successful re-send
        mock_send_alert.assert_called_once()


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

    @patch("home_ops.cli.app.get_connection")
    def test_run_daemon_cycle_sets_failed_on_exception(self, mock_get_conn: MagicMock) -> None:
        """GIVEN run_fn raises exception WHEN _run_daemon_cycle THEN status='failed'."""
        from home_ops.cli.app import _run_daemon_cycle
        from home_ops.models.schema import Config, ScheduleConfig

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        mock_get_conn.return_value.__enter__.return_value = db

        run_fn = MagicMock(side_effect=RuntimeError("pipeline failed"))
        config = Config(alert_schedule=ScheduleConfig(timezone="UTC"))
        now = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)

        result = _run_daemon_cycle(config, run_fn=run_fn, now=now)

        assert result is True
        row = db.conn.execute(
            "SELECT status FROM scraping_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == "failed"

    @patch("home_ops.cli.app.get_connection")
    def test_stale_running_rows_cleaned_on_cycle_start(self, mock_get_conn: MagicMock) -> None:
        """GIVEN stale 'running' row exists WHEN cycle starts THEN marked as 'failed'."""
        from home_ops.cli.app import _run_daemon_cycle
        from home_ops.models.schema import Config, ScheduleConfig

        db = DuckDBConnection(":memory:")
        db.connect()
        db.init_db()
        yesterday = datetime(2026, 6, 17, 9, 0, 0, tzinfo=UTC)
        db.conn.execute(
            "INSERT INTO scraping_runs (started_at, status) VALUES (?, 'running')",
            [yesterday],
        )
        mock_get_conn.return_value.__enter__.return_value = db

        run_fn = MagicMock()
        config = Config(alert_schedule=ScheduleConfig(timezone="UTC"))
        now = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)

        result = _run_daemon_cycle(config, run_fn=run_fn, now=now)

        assert result is True
        run_fn.assert_called_once()
        row = db.conn.execute(
            "SELECT status, finished_at FROM scraping_runs WHERE started_at = ?",
            [yesterday],
        ).fetchone()
        assert row is not None
        assert row[0] == "failed"
        assert row[1].astimezone(UTC) == yesterday


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
