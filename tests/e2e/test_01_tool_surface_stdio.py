"""test_01 — every MCP tool goes through the chokepoint once.

For each of the 44 tools registered by the server, this file spawns
a real stdio session and calls the tool with the minimal valid
arguments declared in :mod:`tests.e2e.tool_args`. It then asserts:

1. The response is a well-formed envelope (``ok``, ``meta``, ``data``).
2. ``meta.tool`` matches the requested tool name.
3. ``meta.run_id`` and ``meta.audit_id`` are non-empty.
4. The audit log has a matching row with the same tool name.
5. If ``ok=false``, the ``error_code`` is in the expected set
   (:data:`tests.e2e.tool_args.EXPECTED_ERROR_CODES`). A new,
   unexpected error code is a regression worth catching.

Run with:

    pytest tests/e2e/test_01_tool_surface_stdio.py -m e2e -v
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from ._clients import call
from .tool_args import EXPECTED_ERROR_CODES, MIN_ARGS

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Parametrize
# ---------------------------------------------------------------------------


_ALL_TOOLS = sorted(MIN_ARGS.keys())


@pytest.fixture(params=_ALL_TOOLS, ids=_ALL_TOOLS)
def tool_name(request) -> str:
    return request.param


@pytest.fixture
def expected_codes(tool_name: str) -> set[str] | str:
    return EXPECTED_ERROR_CODES[tool_name]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call_once(
    live_server_params, tool_name: str
) -> tuple[dict, str]:
    """Spawn a stdio session, call the tool once, return (body, db_path).

    The session is opened + closed inside this function so that the
    ``__aexit__`` of ``stdio_client`` runs in the **same task** as its
    ``__aenter__``. Spreading them across a fixture + a test leads to
    pytest-asyncio's "cancel scope in a different task" error when
    combined with parametrization.
    """
    async with stdio_client(live_server_params.params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            body = await call(session, tool_name, MIN_ARGS[tool_name])
    return body, str(live_server_params.data_dir / "audit.sqlite")


def _audit_rows(db_path: str, run_id: str) -> list:
    """Return all audit rows for a given run_id."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT tool, ok, error_code, run_id, profile "
            "FROM calls WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_every_tool_runs_through_chokepoint(
    live_server_params,
    tool_name: str,
) -> None:
    """Call the tool once and assert envelope + error-code contract."""
    body, _db_path = await _call_once(live_server_params, tool_name)

    # 1) Envelope shape
    assert isinstance(body, dict), f"{tool_name}: response is not a dict"
    assert "ok" in body, f"{tool_name}: missing 'ok'"
    assert "meta" in body, f"{tool_name}: missing 'meta'"
    assert "data" in body, f"{tool_name}: missing 'data'"

    meta = body["meta"]
    assert meta.get("tool") == tool_name, (
        f"{tool_name}: meta.tool mismatch ({meta.get('tool')!r})"
    )
    assert meta.get("run_id"), f"{tool_name}: meta.run_id empty"
    assert meta.get("audit_id"), f"{tool_name}: meta.audit_id empty"
    assert "next_actions" in meta, f"{tool_name}: missing meta.next_actions"
    # NOTE: ``profile`` is NOT in ``meta`` (it's per-request, not per-
    # response). It's stored in the audit row instead, and asserted
    # by ``test_every_tool_lands_in_audit_db`` below.

    # 2) ok=true OR ok=false with stable error_code
    if body["ok"]:
        # 2a) When ok=true, body.data should be non-null and contain
        # the tool's actual payload (not the error envelope).
        assert body.get("data") is not None, (
            f"{tool_name}: ok=true but data is None"
        )
        return

    # ok=false path
    err = body.get("error") or {}
    code = err.get("code") or ""
    expected = EXPECTED_ERROR_CODES[tool_name]
    if expected != "*":
        assert code in expected, (
            f"{tool_name}: unexpected error_code={code!r}; "
            f"expected one of {sorted(expected)!r}. Body: {body}"
        )


async def test_every_tool_lands_in_audit_db(
    live_server_params,
    tool_name: str,
) -> None:
    """Each tool call must produce exactly one audit row with the
    right tool name, the run_id from the response envelope, and a
    non-empty profile.
    """
    body, db_path = await _call_once(live_server_params, tool_name)
    run_id = body["meta"]["run_id"]

    rows = _audit_rows(db_path, run_id)
    assert len(rows) == 1, (
        f"{tool_name}: expected 1 audit row, got {len(rows)}"
    )
    row = rows[0]
    assert row["tool"] == tool_name, (
        f"{tool_name}: audit row tool mismatch ({row['tool']!r})"
    )
    assert row["run_id"] == run_id
    assert row["profile"], f"{tool_name}: audit row missing profile"
    # ok column is INTEGER 0/1; align with body.ok
    assert bool(row["ok"]) == bool(body["ok"]), (
        f"{tool_name}: audit row ok={row['ok']} but body ok={body['ok']}"
    )
    if not body["ok"]:
        assert row["error_code"], (
            f"{tool_name}: ok=false in body but audit row error_code is empty"
        )


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def test_tool_coverage_summary() -> None:
    """Print the matrix once per run for CI dashboards."""
    print(f"\n=== Tool surface coverage ===")
    print(f"  tools registered in MIN_ARGS: {len(MIN_ARGS)}")
    print(
        f"  tools with no-arg happy path: "
        f"{sum(1 for c in EXPECTED_ERROR_CODES.values() if c == set())}"
    )
    print(
        f"  tools with expected error codes: "
        f"{sum(1 for c in EXPECTED_ERROR_CODES.values() if c != '*' and c != set())}"
    )
    print(
        f"  tools with '*' (any error_code OK): "
        f"{sum(1 for c in EXPECTED_ERROR_CODES.values() if c == '*')}"
    )