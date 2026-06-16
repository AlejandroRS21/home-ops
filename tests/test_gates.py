"""Tests for the HITL approval gate module."""

import pytest

from home_ops.alerter.gates import HITLGate


class TestHITLGate:
    """HITLGate tests."""

    def test_init_with_db(self, db) -> None:
        """GIVEN db connection WHEN init THEN gate created."""
        gate = HITLGate(db, approval_required=True)
        assert gate.approval_required is True

    def test_init_no_approval(self, db) -> None:
        """GIVEN approval_required=False WHEN init THEN gate transparent."""
        gate = HITLGate(db, approval_required=False)
        assert gate.approval_required is False

    def test_init_without_db_raises(self) -> None:
        """GIVEN no db connection WHEN init with approval_required THEN RuntimeError."""
        from home_ops.models.data_storage import DuckDBConnection

        disconnected = DuckDBConnection(":memory:")
        # Don't call connect() so the connection is unavailable
        with pytest.raises(RuntimeError, match="active database connection"):
            HITLGate(disconnected, approval_required=True)

    def test_request_approval(self, db) -> None:
        """GIVEN listing WHEN request_approval THEN row inserted."""
        gate = HITLGate(db, approval_required=True)
        gate.request_approval(42)
        row = db.conn.execute(
            "SELECT approved FROM pending_approvals WHERE listing_id = 42"
        ).fetchone()
        assert row is not None
        assert row[0] is False

    def test_request_approval_idempotent(self, db) -> None:
        """GIVEN duplicate request WHEN request_approval THEN no error."""
        gate = HITLGate(db, approval_required=True)
        gate.request_approval(42)
        gate.request_approval(42)  # should not raise

    def test_approve(self, db) -> None:
        """GIVEN pending listing WHEN approve THEN marked approved."""
        gate = HITLGate(db, approval_required=True)
        gate.request_approval(42)
        gate.approve(42)
        row = db.conn.execute(
            "SELECT approved FROM pending_approvals WHERE listing_id = 42"
        ).fetchone()
        assert row is not None
        assert row[0] is True

    def test_approve_without_request(self, db) -> None:
        """GIVEN no prior request WHEN approve THEN creates approved row."""
        gate = HITLGate(db, approval_required=True)
        gate.approve(99)
        assert gate.is_approved(99) is True

    def test_is_approved_false(self, db) -> None:
        """GIVEN listing not requested WHEN is_approved THEN False."""
        gate = HITLGate(db, approval_required=True)
        assert gate.is_approved(42) is False

    def test_is_approved_after_request(self, db) -> None:
        """GIVEN requested but not approved WHEN is_approved THEN False."""
        gate = HITLGate(db, approval_required=True)
        gate.request_approval(42)
        assert gate.is_approved(42) is False

    def test_is_approved_after_approve(self, db) -> None:
        """GIVEN approved listing WHEN is_approved THEN True."""
        gate = HITLGate(db, approval_required=True)
        gate.approve(42)
        assert gate.is_approved(42) is True

    def test_is_approved_no_gate(self, db) -> None:
        """GIVEN approval_required=False WHEN is_approved THEN True."""
        gate = HITLGate(db, approval_required=False)
        assert gate.is_approved(42) is True  # No gate → always approved

    def test_get_pending_empty(self, db) -> None:
        """GIVEN no pending approvals WHEN get_pending THEN empty list."""
        gate = HITLGate(db, approval_required=True)
        pending = gate.get_pending()
        assert pending == []

    def test_get_pending_returns_only_unapproved(self, db) -> None:
        """GIVEN mixed approvals WHEN get_pending THEN only unapproved listed."""
        gate = HITLGate(db, approval_required=True)
        gate.request_approval(1)
        gate.request_approval(2)
        gate.approve(2)

        pending = gate.get_pending()
        ids = [p["listing_id"] for p in pending]
        assert 1 in ids
        assert 2 not in ids  # approved
