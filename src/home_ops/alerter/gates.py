"""Human-in-the-loop approval gates for high-value actions.

The ``HITLGate`` enforces manual approval before committing to actions
that cannot be undone (e.g. contacting an agent or submitting an offer).

Never auto-contacts.  Never auto-submits.  If approval is required and
not granted, the action is blocked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection

logger = logging.getLogger(__name__)


class HITLGate:
    """Human-in-the-loop approval gate backed by the database.

    The gate records pending approvals in the database and exposes methods
    to approve, query, and check the status of individual listings.

    The gate is only enforced when ``approval_required`` is ``True``.
    When it is ``False`` all actions are allowed without manual sign-off.

    Usage::

        gate = HITLGate(db_connection, approval_required=True)
        if gate.is_approved(listing_id):
            proceed_with_action()
    """

    def __init__(
        self,
        db_connection: DuckDBConnection,
        approval_required: bool = True,
    ) -> None:
        """Initialise the gate.

        Args:
            db_connection: An open DuckDB connection with an initialised
                           schema.
            approval_required: Whether manual approval is needed before
                               actions are taken.  When ``False`` the gate
                               is effectively transparent.

        Raises:
            RuntimeError: If ``approval_required`` is ``True`` but the
                          database is not connected (error state — the gate
                          cannot operate safely).
        """
        self.db = db_connection
        self.approval_required = approval_required

        if approval_required:
            try:
                _ = self.db.conn
            except Exception as exc:
                raise RuntimeError(
                    "HITLGate requires an active database connection when "
                    f"approval_required=True. Connection error: {exc}"
                ) from exc

        self._ensure_approvals_table()

    def _ensure_approvals_table(self) -> None:
        """Create the ``pending_approvals`` table if it does not exist."""
        self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_approvals (
                listing_id INTEGER PRIMARY KEY,
                approved BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP
            );
        """)

    def request_approval(self, listing_id: int) -> None:
        """Record a pending approval request for a listing.

        If a row already exists for this listing it is a no-op (the
        existing approval status is preserved).

        Args:
            listing_id: The ID of the listing requiring approval.
        """
        self.db.conn.execute(
            """
            INSERT INTO pending_approvals (listing_id, approved)
            VALUES (?, FALSE)
            ON CONFLICT (listing_id) DO NOTHING;
            """,
            [listing_id],
        )
        logger.info("Approval requested for listing %d", listing_id)

    def approve(self, listing_id: int) -> None:
        """Mark a listing as approved.

        Used by the CLI ``approve`` command.

        Args:
            listing_id: The ID of the listing to approve.
        """
        now = datetime.now(UTC)
        self.db.conn.execute(
            """
            INSERT INTO pending_approvals (listing_id, approved, approved_at)
            VALUES (?, TRUE, ?)
            ON CONFLICT (listing_id) DO UPDATE SET
                approved = TRUE,
                approved_at = ?;
            """,
            [listing_id, now, now],
        )
        logger.info("Listing %d approved", listing_id)

    def is_approved(self, listing_id: int) -> bool:
        """Check whether a listing has been manually approved.

        Returns ``True`` immediately when ``approval_required`` is
        ``False`` (no gate).

        Args:
            listing_id: The ID of the listing to check.

        Returns:
            True if the listing is approved or the gate is disabled.
        """
        if not self.approval_required:
            return True

        row = self.db.conn.execute(
            "SELECT approved FROM pending_approvals WHERE listing_id = ?",
            [listing_id],
        ).fetchone()

        if row is None:
            return False  # Not yet requested / never approved

        return bool(row[0])

    def get_pending(self) -> list[dict[str, Any]]:
        """Return all pending (unapproved) approval requests.

        Returns:
            A list of dicts with ``listing_id`` and ``created_at`` keys.
        """
        rows = self.db.conn.execute(
            "SELECT listing_id, created_at FROM pending_approvals "
            "WHERE approved = FALSE ORDER BY created_at ASC"
        ).fetchall()
        return [
            {"listing_id": int(r[0]), "created_at": str(r[1])}
            for r in rows
        ]
