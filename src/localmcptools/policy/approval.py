"""Persistent, expiring, one-shot approvals."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, cast

from ..persistence import db
from .digest import digest_for

DEFAULT_TTL_MS = 10 * 60 * 1000
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_CONSUMED = "consumed"
STATUS_EXPIRED = "expired"


class ApprovalError(LookupError):
    """Base class for approval lifecycle failures."""


class ApprovalDigestMismatch(ApprovalError):
    """The supplied approval belongs to a different action."""


class ApprovalExpired(ApprovalError):
    """The approval is past its expiry and cannot be consumed."""


class ApprovalNotApproved(ApprovalError):
    """The approval is still pending or was already consumed."""


@dataclass(frozen=True)
class Approval:
    id: str
    workspace_id: str
    requested_capability: str
    action_digest: str
    status: str
    requested_at: int
    expires_at: int
    approved_at: int | None
    consumed_at: int | None


def request(
    workspace_id: str,
    capability: str,
    args: dict[str, Any],
    *,
    profile: str,
    ttl_ms: int = DEFAULT_TTL_MS,
    conn: sqlite3.Connection | None = None,
) -> Approval:
    """Create a pending approval bound to one workspace, profile and action."""
    if not workspace_id or not capability or ttl_ms <= 0:
        raise ValueError("workspace_id, capability and a positive ttl_ms are required")
    now = _now_ms()
    tool = capability.split(":", 1)[-1]
    approval = Approval(
        id=uuid.uuid4().hex,
        workspace_id=workspace_id,
        requested_capability=capability,
        action_digest=digest_for(tool, args, workspace_id, profile),
        status=STATUS_PENDING,
        requested_at=now,
        expires_at=now + ttl_ms,
        approved_at=None,
        consumed_at=None,
    )

    def _insert(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO approvals (id, workspace_id, requested_capability, action_digest, "
            "status, requested_at, expires_at, approved_at, consumed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                approval.id, approval.workspace_id, approval.requested_capability,
                approval.action_digest, approval.status, approval.requested_at,
                approval.expires_at, approval.approved_at, approval.consumed_at,
            ),
        )

    if conn is not None:
        _insert(conn)
    else:
        db.init_db()
        with db.connection() as connection:
            _insert(connection)
    return approval


def approve(approval_id: str, *, conn: sqlite3.Connection | None = None) -> bool:
    """Mark a non-expired pending approval approved (operator/UI boundary)."""
    return _transition_to_approved(approval_id, conn=conn)


def consume(approval_id: str, presented_digest: str, *, conn: sqlite3.Connection | None = None) -> bool:
    """Consume one matching, approved approval, or raise a typed lifecycle error."""
    def _consume(connection: sqlite3.Connection) -> bool:
        row = _get_row(connection, approval_id)
        now = _now_ms()
        if row["expires_at"] <= now:
            connection.execute(
                "UPDATE approvals SET status = ? WHERE id = ? AND status IN (?, ?)",
                (STATUS_EXPIRED, approval_id, STATUS_PENDING, STATUS_APPROVED),
            )
            raise ApprovalExpired(f"approval_id={approval_id!r} is expired")
        if row["action_digest"] != presented_digest:
            raise ApprovalDigestMismatch("approval digest does not match this action")
        if row["status"] != STATUS_APPROVED:
            raise ApprovalNotApproved(f"approval_id={approval_id!r} is not approved")
        updated = connection.execute(
            "UPDATE approvals SET status = ?, consumed_at = ? WHERE id = ? AND status = ?",
            (STATUS_CONSUMED, now, approval_id, STATUS_APPROVED),
        )
        if updated.rowcount != 1:
            raise ApprovalNotApproved(f"approval_id={approval_id!r} was already consumed")
        return True

    if conn is not None:
        return _consume(conn)
    db.init_db()
    with db.connection() as connection:
        return _consume(connection)


def expire_due(*, conn: sqlite3.Connection | None = None) -> int:
    """Expire all pending/approved rows that reached their deadline."""
    def _expire(connection: sqlite3.Connection) -> int:
        result = connection.execute(
            "UPDATE approvals SET status = ? WHERE status IN (?, ?) AND expires_at <= ?",
            (STATUS_EXPIRED, STATUS_PENDING, STATUS_APPROVED, _now_ms()),
        )
        return result.rowcount

    if conn is not None:
        return _expire(conn)
    db.init_db()
    with db.connection() as connection:
        return _expire(connection)


def _transition_to_approved(approval_id: str, *, conn: sqlite3.Connection | None) -> bool:
    def _approve(connection: sqlite3.Connection) -> bool:
        row = _get_row(connection, approval_id)
        now = _now_ms()
        if row["expires_at"] <= now:
            connection.execute("UPDATE approvals SET status = ? WHERE id = ?", (STATUS_EXPIRED, approval_id))
            raise ApprovalExpired(f"approval_id={approval_id!r} is expired")
        result = connection.execute(
            "UPDATE approvals SET status = ?, approved_at = ? WHERE id = ? AND status = ?",
            (STATUS_APPROVED, now, approval_id, STATUS_PENDING),
        )
        return result.rowcount == 1

    if conn is not None:
        return _approve(conn)
    db.init_db()
    with db.connection() as connection:
        return _approve(connection)


def _get_row(connection: sqlite3.Connection, approval_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if row is None:
        raise ApprovalError(f"approval_id={approval_id!r} was not found")
    return cast(sqlite3.Row, row)


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "Approval", "ApprovalDigestMismatch", "ApprovalError", "ApprovalExpired",
    "ApprovalNotApproved", "DEFAULT_TTL_MS", "approve", "consume", "expire_due", "request",
]
