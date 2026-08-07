"""Tests for the ToolExecutionService chokepoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.execution.service import ToolExecutionService
from localmcptools.persistence import db


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db.init_db(tmp_path / "audit.sqlite")
    return tmp_path / "audit.sqlite"


def _noop(args):
    return {"echo": args}


def _flaky(args):
    raise RuntimeError("api_key=boom-token-xyz")


def test_register_and_invoke_writes_audit(fresh_db: Path) -> None:
    service = ToolExecutionService(audit_path=fresh_db)
    # _noop has signature ``(args)`` with no annotation; auto-detect
    # must classify it as dict-style.
    wrapper = service.register("t.noop", _noop)
    envelope = wrapper()
    assert envelope["ok"] is True
    assert envelope["data"] == {"echo": {}}
    with db.connection(fresh_db) as conn:
        rows = conn.execute(
            "SELECT * FROM calls WHERE tool = 't.noop'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["profile"] == "observe"


def test_invoke_catches_exception(fresh_db: Path) -> None:
    service = ToolExecutionService(audit_path=fresh_db)
    wrapper = service.register("t.flaky", _flaky)
    envelope = wrapper()
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "internal_error"
    # Secret in the exception message gets redacted.
    assert "boom-token-xyz" not in envelope["error"]["message"]


def test_invoke_meta_has_audit_and_run_ids(fresh_db: Path) -> None:
    service = ToolExecutionService(audit_path=fresh_db)
    wrapper = service.register("t.ids", _noop)
    envelope = wrapper()
    meta = envelope["meta"]
    assert meta["audit_id"]
    assert meta["run_id"]
    assert meta["tool"] == "t.ids"


def test_shutdown_rejects_new_tool_calls(fresh_db: Path) -> None:
    service = ToolExecutionService(audit_path=fresh_db)
    wrapper = service.register("t.noop", _noop)
    assert service.begin_shutdown(grace_seconds=0.1) is True
    envelope = wrapper()
    assert envelope["error"]["code"] == "server_shutting_down"


def test_register_auto_detects_dict_param(tmp_path: Path) -> None:
    service = ToolExecutionService()
    # No explicit param_names — auto-detect must recognise a single
    # ``args: dict`` parameter and declare zero fields.
    wrapper = service.register("t.auto", _noop)
    envelope = wrapper()
    assert envelope["ok"] is True
    reg = service.get_registration("t.auto")
    assert reg.param_names == ()


def test_register_explicit_param_names_used(tmp_path: Path) -> None:
    """param_names drives the FastMCP schema, not the body dispatch.

    Tool bodies always receive an args dict. ``param_names`` is
    purely a hint for the synthesised wrapper to declare each named
    parameter so the JSON schema looks right.
    """
    service = ToolExecutionService()

    def add(args: dict[str, int]) -> dict[str, int]:
        return {"sum": args.get("a", 0) + args.get("b", 0)}

    wrapper = service.register("t.add", add, param_names=("a", "b"))
    envelope = wrapper(a=2, b=3)
    assert envelope["data"] == {"sum": 5}


def test_register_full_envelope_returned(fresh_db: Path) -> None:
    """A tool that returns a fully-built ToolResponse keeps its meta."""
    from localmcptools.tools._common import ToolMeta, ToolResponse

    def with_meta(args):
        meta = ToolMeta(
            tool="t.custom",
            duration_ms=999,
            audit_id="custom-audit",
            run_id="custom-run",
        )
        return ToolResponse.ok_response(data={"hello": True}, meta=meta)

    service = ToolExecutionService(audit_path=fresh_db)
    wrapper = service.register("t.custom", with_meta)
    envelope = wrapper()
    # The chokepoint must overwrite audit_id/run_id to match the real run.
    assert envelope["meta"]["audit_id"] != "custom-audit"
    assert envelope["data"] == {"hello": True}
