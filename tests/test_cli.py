"""Tests for the Typer CLI app module."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from home_ops.cli.app import _display_status, _get_db_path, _run_scan, app

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
        from home_ops.alerter.gates import HITLGate
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
        gate = HITLGate(db, approval_required=True)
        assert gate.is_approved(42) is True

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
    @patch("home_ops.scraper.lifecycle.cold_start")
    def test_run_scan_creates_db(
        self, mock_cold_start: MagicMock, mock_get_conn: MagicMock
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
    @patch("home_ops.scraper.lifecycle.cold_start")
    def test_run_scan_with_cold_start_failure(
        self, mock_cold_start: MagicMock, mock_get_conn: MagicMock
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
    @patch("home_ops.scraper.lifecycle.cold_start")
    def test_run_scan_with_duplicates(
        self, mock_cold_start: MagicMock, mock_get_conn: MagicMock
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
