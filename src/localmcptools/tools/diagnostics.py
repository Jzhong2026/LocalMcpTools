"""``diagnostics.*`` tools.

Two tools:

- :func:`diagnostics_collect` — fan out to runtime, git, problems,
  ports and recent failures. Per OpenSpec REQ-DIAG-4.

- :func:`diagnostics_explain_failure` — given a ``run_id`` from the
  audit log, classify the failure, surface key evidence lines from
  the artifact, and emit ``next_actions`` the agent can act on.
  Per OpenSpec REQ-DIAG-5.

Both tools are read-only and live in the ``observe`` profile. The
classifier is in :mod:`localmcptools.diagnostics.classify`; the advice
table is in :mod:`localmcptools.diagnostics.next_actions`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..diagnostics import advice_for, classify
from ..persistence import artifacts, db
from ..diagnostics.aggregate import collect as _collect
from ._common import ToolResponse

_log = logging.getLogger(__name__)


# Section byte cap for ``explain_failure``'s key-evidence extraction.
_KEY_EVIDENCE_LINES = 10


def diagnostics_collect(args: dict[str, Any]) -> Any:
    """Tool body for ``diagnostics.collect``."""
    return _collect(args)


def _find_run(run_id: str) -> dict[str, Any] | None:
    """Return the audit row for ``run_id`` or None."""
    if not run_id or not isinstance(run_id, str):
        return None
    try:
        db.init_db()
        with db.connection() as conn:
            row = conn.execute(
                "SELECT id, tool, status, ok, error_code, error_message, "
                "blocked_by, exit_code, log_path, workspace_id "
                "FROM calls WHERE id = ?",
                (run_id,),
            ).fetchone()
    except sqlite3.Error:
        return None
    return dict(row) if row else None


def _read_key_evidence(log_path: str | None) -> list[dict[str, Any]]:
    """Pull the last ``_KEY_EVIDENCE_LINES`` non-empty lines from an artifact."""
    if not log_path:
        return []
    try:
        text = artifacts.read_range(log_path, 0, _KEY_EVIDENCE_LINES * 20)
    except (artifacts.ArtifactNotFound, OSError, ValueError):
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    evidence: list[dict[str, Any]] = []
    for index, line in enumerate(lines[-_KEY_EVIDENCE_LINES:], start=1):
        evidence.append({"line": index, "text": line})
    return evidence


def _related_runs(run: dict[str, Any], *, limit: int = 3) -> list[str]:
    """Find similar recent failures in the same workspace."""
    workspace_id = run.get("workspace_id")
    error_code = run.get("error_code")
    if not workspace_id or not error_code:
        return []
    try:
        db.init_db()
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT id FROM calls WHERE workspace_id = ? AND error_code = ? "
                "AND id != ? ORDER BY timestamp DESC LIMIT ?",
                (workspace_id, error_code, run.get("id"), limit),
            ).fetchall()
        return [row["id"] for row in rows]
    except sqlite3.Error:
        return []


def diagnostics_explain_failure(args: dict[str, Any]) -> Any:
    """Tool body for ``diagnostics.explain_failure``.

    Accepts either a ``run_id`` (preferred) or a complete row dict
    under ``row`` for tests that want to avoid touching the audit DB.
    """
    run_id = args.get("run_id")
    row = args.get("row")
    if row is None and isinstance(run_id, str):
        row = _find_run(run_id)
    if not row:
        return {
            "classification": "unknown",
            "summary": "could not locate the run; pass run_id or row",
            "key_evidence": [],
            "next_actions": ["provide log_handle manually"],
            "related_runs": [],
        }

    log_path = row.get("log_path")
    log_lines: list[str] = []
    if log_path:
        try:
            text = artifacts.read_range(log_path, 0, _KEY_EVIDENCE_LINES * 20)
            log_lines = text.splitlines()
        except (artifacts.ArtifactNotFound, OSError, ValueError):
            log_lines = []

    result = classify(row, log_lines)
    advice = advice_for(result)
    if log_path and not advice:
        advice = ["open the artifact for more context"]

    return {
        "classification": result.classification,
        "summary": result.summary,
        "key_evidence": _read_key_evidence(log_path) if log_path else [],
        "next_actions": advice,
        "related_runs": _related_runs(row),
        "rule_id": result.rule_id,
        "exit_code": result.exit_code,
    }


__all__ = [
    "diagnostics_collect",
    "diagnostics_explain_failure",
]


_ = (ToolResponse, json)