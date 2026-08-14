from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.persistence import artifacts, db


def test_streaming_artifact_can_be_tailed_then_sealed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "audit_db_path", lambda: tmp_path / "audit.sqlite")
    db.init_db()
    handle = artifacts.create_stream(call_id="stream-test")
    assert artifacts.lookup(handle).sealed is False
    artifacts.append(handle, "first\n")
    artifacts.append(handle, "token=secret-value\n")
    assert artifacts.tail(handle, 1) == ["token=***"]
    artifacts.seal(handle)
    record = artifacts.lookup(handle)
    assert record.sealed is True
    assert record.line_count == 2
    with pytest.raises(artifacts.RedactionFailed):
        artifacts.append(handle, "late\n")
