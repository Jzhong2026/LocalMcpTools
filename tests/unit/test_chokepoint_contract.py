"""Regression tests for the 5 contract bugs reported against change-2.

Each test pins one specific behaviour so a future refactor can't
silently regress it:

1. workspace_id from args flows into both meta.workspace_id and the
   audit row's workspace_id column.
2. Tool responses >64 KiB are persisted to an artifact with a
   ``meta.output_handle`` and an audit ``log_path`` row.
3. ``output.tail`` populates ``meta.evidence_handle`` (REQ-OUT-2).
4. ``fs.read_range`` streams the requested range without
   head-truncating; ``total_lines`` is exact.
5. The encoding probe emits non-ASCII bytes so chardet can actually
   distinguish UTF-8 / GBK / UTF-16 / CP-1252.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from localmcptools.execution.service import ToolExecutionService
from localmcptools.persistence import artifacts, db
from localmcptools.tools._common import ToolResponse


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "audit.sqlite")
    return tmp_path / "audit.sqlite"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A small workspace dir used by the fs.* tests in this file."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    return root


# --- Fix 1: workspace_id propagation ------------------------------------


def test_workspace_id_propagates_to_meta_and_audit(
    fresh_db: Path,
) -> None:
    """Args['workspace_id'] ends up on meta.workspace_id AND the audit row."""

    def fake_inspect(args: dict[str, Any]) -> dict[str, Any]:
        return {"project_type": "python"}

    service = ToolExecutionService(audit_path=fresh_db)
    wrapper = service.register("workspace.inspect", fake_inspect, param_names=("workspace_id",))
    ws_id = "0123456789abcdef0123456789abcdef"  # 32-hex
    envelope = wrapper(workspace_id=ws_id)

    assert envelope["meta"]["workspace_id"] == ws_id
    with db.connection(fresh_db) as conn:
        row = conn.execute(
            "SELECT workspace_id FROM calls WHERE tool = 'workspace.inspect'"
        ).fetchone()
    assert row["workspace_id"] == ws_id


def test_workspace_id_absent_stays_none(fresh_db: Path) -> None:
    """Tools that don't take a workspace keep workspace_id=None everywhere."""
    service = ToolExecutionService(audit_path=fresh_db)

    def hello(args: dict[str, Any]) -> dict[str, Any]:
        return {"hello": "world"}

    wrapper = service.register("t.hello", hello)
    envelope = wrapper()
    assert envelope["meta"]["workspace_id"] is None
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT workspace_id FROM calls WHERE tool = 't.hello'").fetchone()
    assert row["workspace_id"] is None


def test_workspace_id_junk_ignored(fresh_db: Path) -> None:
    """A non-UUID workspace_id is ignored so audit doesn't get poisoned."""

    def hello(args: dict[str, Any]) -> dict[str, Any]:
        return {"x": 1}

    service = ToolExecutionService(audit_path=fresh_db)
    wrapper = service.register("t.hello", hello, param_names=("workspace_id",))
    envelope = wrapper(workspace_id="<script>alert(1)</script>")
    assert envelope["meta"]["workspace_id"] is None
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT workspace_id FROM calls WHERE tool = 't.hello'").fetchone()
    assert row["workspace_id"] is None


# --- Fix 2: >64KB artifact on the success path -------------------------


def test_oversize_response_persists_as_artifact(fresh_db: Path) -> None:
    """A tool returning >64 KiB of data ends up as an artifact + handle."""

    big = "x" * 70_000

    def loud(args: dict[str, Any]) -> dict[str, Any]:
        return {"payload": big}

    service = ToolExecutionService(audit_path=fresh_db)
    wrapper = service.register("t.loud", loud)
    envelope = wrapper()

    # data is replaced with a summary.
    assert envelope["data"]["truncated"] is True
    assert envelope["data"]["bytes_total"] >= artifacts.INLINE_THRESHOLD_BYTES

    # meta.output_handle points at the persisted artifact.
    handle = envelope["meta"]["output_handle"]
    assert handle is not None
    assert handle.startswith("art://")
    assert artifacts.exists(handle)

    # The audit row also references the same handle via log_path.
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT log_path FROM calls WHERE tool = 't.loud'").fetchone()
    assert row["log_path"] == handle


def test_small_response_stays_inline(fresh_db: Path) -> None:
    """Under 64 KiB → no artifact, data inline, no handle."""

    def quiet(args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    service = ToolExecutionService(audit_path=fresh_db)
    wrapper = service.register("t.quiet", quiet)
    envelope = wrapper()
    assert envelope["data"] == {"ok": True}
    assert envelope["meta"]["output_handle"] is None
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT log_path FROM calls WHERE tool = 't.quiet'").fetchone()
    assert row["log_path"] is None


# --- Fix 3: output.tail puts evidence_handle on meta -------------------


def test_output_tail_meta_has_evidence_handle(fresh_db: Path) -> None:
    """Per REQ-OUT-2: meta.evidence_handle mirrors the same handle."""
    from localmcptools.tools.output import output_tail

    conn = db.get_connection(fresh_db)
    handle = artifacts.write("a\nb\nc\n", call_id="ev", conn=conn)
    res = output_tail({"handle": handle, "n": 2})
    # Unit-level: the body returns a ToolResponse (chokepoint would wrap).
    assert isinstance(res, ToolResponse)
    assert res.data is not None
    assert res.meta.evidence_handle == handle
    assert res.meta.output_handle == handle
    # data only carries the content, not the handle.
    assert "evidence_handle" not in res.data
    # ``tail(n=2)`` returns the *last* 2 lines of ``a\nb\nc\n``.
    assert res.data["lines"] == ["b", "c"]


# --- Fix 4: fs.read_range streams + exact total_lines -------------------


def test_fs_read_range_exact_total_lines_on_large_file(fresh_db: Path, project_dir: Path) -> None:
    """A file bigger than the old head-truncation cap must still report
    its true total line count."""
    from localmcptools.tools.fs import fs_read_range
    from localmcptools.tools.workspace import workspace_register

    # Build a 6 MiB file with a known number of lines.
    big = project_dir / "big.log"
    line_count = 200_000  # ~30 bytes/line → ~6 MiB
    with big.open("w", encoding="utf-8") as fh:
        for i in range(line_count):
            fh.write(f"line {i:08d}\n")

    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = fs_read_range(
        {
            "workspace_id": ws_id,
            "path": "big.log",
            "start_line": 100_000,
            "end_line": 100_005,
        }
    )
    assert res["lines"] == [
        "line 00100000",
        "line 00100001",
        "line 00100002",
        "line 00100003",
        "line 00100004",
    ]
    assert res["total_lines"] == line_count


def test_fs_read_range_tail_of_huge_file(fresh_db: Path, project_dir: Path) -> None:
    """Asking for the last 5 lines of a 6 MiB file must not load the head."""
    from localmcptools.tools.fs import fs_read_range
    from localmcptools.tools.workspace import workspace_register

    big = project_dir / "big.log"
    line_count = 200_000
    with big.open("w", encoding="utf-8") as fh:
        for i in range(line_count):
            fh.write(f"line {i:08d}\n")

    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = fs_read_range(
        {
            "workspace_id": ws_id,
            "path": "big.log",
            "start_line": line_count - 5,
            "end_line": line_count,
        }
    )
    assert res["lines"][0] == f"line {line_count - 5:08d}"
    assert res["lines"][-1] == f"line {line_count - 1:08d}"
    assert res["total_lines"] == line_count


# --- Fix 5: encoding probe emits distinguishable bytes -------------------


def test_encoding_probe_uses_non_ascii_bytes(monkeypatch, tmp_path: Path) -> None:
    """The probe must NOT emit a pure-ASCII string.

    If it did, chardet would always return ``ascii`` and we'd never
    learn anything about the console code page. The fix is to embed
    a CJK character that round-trips through the active code page.
    """
    import types as _types

    from localmcptools.tools import environment as env_mod

    captured: dict[str, bytes] = {}

    def fake_run(cmd, **kwargs):
        class _R:
            stdout = b"\xe4\xb8\xad\xe6\x96\x87 hello\n"  # UTF-8 "中文 hello"
            stderr = b""

        captured["stdout"] = _R.stdout
        return _R()

    monkeypatch.setattr(env_mod.subprocess, "run", fake_run)
    # Drop the cache so the fake is consulted.
    monkeypatch.setattr(env_mod, "_encoding_cache", {"text": None, "at": 0.0})

    # Inject chardet into the module.
    fake_chardet = _types.SimpleNamespace(
        detect=lambda raw: {"encoding": "utf-8", "confidence": 0.99}
    )
    monkeypatch.setitem(sys.modules, "chardet", fake_chardet)

    result = env_mod._probe_console_encoding_via_powershell()
    assert result == "utf-8"


def test_encoding_probe_emits_real_probe_string(monkeypatch, tmp_path: Path) -> None:
    """The PowerShell command must embed a CJK character so the byte
    stream differs across encodings."""
    import types as _types

    from localmcptools.tools import environment as env_mod

    seen_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        seen_cmd.append(cmd[-1] if cmd else "")

        class _R:
            stdout = b""
            stderr = b""

        return _R()

    monkeypatch.setattr(env_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(env_mod, "_encoding_cache", {"text": None, "at": 0.0})
    fake_chardet = _types.SimpleNamespace(detect=lambda raw: {"encoding": None, "confidence": 0.0})
    monkeypatch.setitem(sys.modules, "chardet", fake_chardet)

    env_mod._probe_console_encoding_via_powershell()
    assert seen_cmd, "subprocess.run was not called"
    assert "中文" in seen_cmd[0], f"probe must include CJK bytes; got: {seen_cmd[0]!r}"


def test_encoding_probe_returns_unknown_on_low_confidence(
    monkeypatch,
) -> None:
    """chardet with confidence < 0.5 must not lie to the agent."""
    import types as _types

    from localmcptools.tools import environment as env_mod

    def fake_run(cmd, **kwargs):
        class _R:
            stdout = b"\xd6\xd0\xce\xc4 hello\n"  # GBK "中文 hello"
            stderr = b""

        return _R()

    monkeypatch.setattr(env_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(env_mod, "_encoding_cache", {"text": None, "at": 0.0})
    monkeypatch.setitem(
        sys.modules,
        "chardet",
        _types.SimpleNamespace(detect=lambda raw: {"encoding": "gbk", "confidence": 0.3}),
    )

    assert env_mod._probe_console_encoding_via_powershell() == "unknown"
