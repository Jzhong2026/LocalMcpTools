"""Tests for output.* tool bodies."""

from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.persistence import artifacts, db
from localmcptools.tools.output import output_read_range, output_search, output_tail


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point LMCP_DATA_DIR at tmp_path so ``output.*`` lookups hit the same DB."""
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "audit.sqlite")
    return tmp_path / "audit.sqlite"


def test_output_tail(fresh_db: Path) -> None:
    conn = db.get_connection(fresh_db)
    content = "\n".join(f"line {i}" for i in range(50)) + "\n"
    handle = artifacts.write(content, call_id="test-tail", conn=conn)
    res = output_tail({"handle": handle, "n": 5})
    # ``output.tail`` returns a ToolResponse; the chokepoint wraps it.
    # At the unit-test level we get the ToolResponse directly so
    # assert against its data + meta fields.
    from localmcptools.tools._common import ToolResponse

    assert isinstance(res, ToolResponse)
    assert res.data is not None, "ToolResponse.data must be set for output tools"
    assert res.ok is True
    assert res.data["lines"] == ["line 45", "line 46", "line 47", "line 48", "line 49"]
    assert res.data["handle"] == handle
    # REQ-OUT-2: the handle is mirrored on meta.evidence_handle.
    assert res.meta.evidence_handle == handle
    assert res.meta.output_handle == handle


def test_output_read_range(fresh_db: Path) -> None:
    conn = db.get_connection(fresh_db)
    handle = artifacts.write(
        "\n".join(f"L{i}" for i in range(10)) + "\n", call_id="test-range", conn=conn
    )
    res = output_read_range({"handle": handle, "start_line": 3, "end_line": 6})
    assert res["lines"] == ["L3", "L4", "L5"]


def test_output_search(fresh_db: Path) -> None:
    conn = db.get_connection(fresh_db)
    content = "alpha\nbeta\ngamma alpha\nepsilon\n"
    handle = artifacts.write(content, call_id="test-search", conn=conn)
    res = output_search({"handle": handle, "pattern": "alpha", "max_results": 10})
    assert len(res["matches"]) == 2
    assert res["matches"][0]["line_no"] == 0
    assert res["matches"][1]["line_no"] == 2


def test_output_unknown_handle_raises(fresh_db: Path) -> None:
    with pytest.raises(Exception):
        output_tail({"handle": "art://2099-01-01/calls/nope.log", "n": 5})


def test_output_missing_handle_arg_raises(fresh_db: Path) -> None:
    with pytest.raises(Exception):
        output_tail({})
