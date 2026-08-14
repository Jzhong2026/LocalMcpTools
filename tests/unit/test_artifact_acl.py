"""Tests for :mod:`localmcptools.persistence.artifacts`.

Covers the spike DoD bullets:

- ``write()`` returns a handle shaped ``art://YYYY-MM-DD/calls/<uuid>.log``.
- ``write()`` applies Windows ACL on Windows (skipped on Linux).
- ``lookup()`` raises :class:`ArtifactNotFound` for unknown handles.
- ``tail()``, ``read_range()``, ``search()`` work and respect caps.
- Redaction happens before disk write.
- 64 KB threshold enforced.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from localmcptools.persistence.artifacts import (
    INLINE_THRESHOLD_BYTES,
    ArtifactNotFound,
    ArtifactRecord,
    build_handle,
    exists,
    lookup,
    parse_handle,
    read_range,
    search,
    should_artifact_size,
    tail,
    write,
)

# --- Helpers / fixtures ---------------------------------------------------


@pytest.fixture
def fresh_db(tmp_path: Path) -> Iterator[Path]:
    """Set LMCP_DATA_DIR to tmp_path via the audit DB at that path."""
    from localmcptools.persistence import db

    p = tmp_path / "audit.sqlite"
    db.init_db(p)
    # Point artifacts module at the same root by re-pointing env.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    yield p
    monkeypatch.undo()


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """LMCP_DATA_DIR root for the artifact tree."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    return tmp_path


# --- handle shape --------------------------------------------------------


def test_write_returns_canonical_handle(data_root: Path, fresh_db: Path) -> None:
    call_id = "abc123def4567890"
    h = write("hello world", call_id=call_id, conn=db_connection(fresh_db))
    assert h.startswith("art://")
    assert h.endswith(f"/calls/{call_id}.log")
    # Date component matches today's UTC date.
    import re as _re

    m = _re.match(r"^art://(\d{4}-\d{2}-\d{2})/calls/[\w\-]+\.log$", h)
    assert m is not None


def test_handle_matches_build_handle(data_root: Path, fresh_db: Path) -> None:
    cid = "fixed-id-001"
    h = write("x", call_id=cid, conn=db_connection(fresh_db))
    # Re-derive and compare the date portion.
    import re as _re

    m = _re.match(r"^art://(\d{4}-\d{2}-\d{2})/", h)
    assert m is not None, "test setup must produce a valid handle date prefix"
    date_str = m.group(1)
    assert build_handle(date_str, cid) == h


def test_parse_handle_roundtrip(data_root: Path, fresh_db: Path) -> None:
    cid = "round-id-1"
    h = write("y", call_id=cid, conn=db_connection(fresh_db))
    date_str, parsed_cid = parse_handle(h)
    assert parsed_cid == cid
    assert len(date_str) == 10  # YYYY-MM-DD


def test_parse_handle_rejects_malformed() -> None:
    for bad in (
        "",
        "not-a-handle",
        "art://not-a-date/calls/x.log",
        "art://2026-08-07/notcalls/x.log",
        "art://2026-08-07/calls/x.txt",
    ):
        with pytest.raises(ArtifactNotFound):
            parse_handle(bad)


# --- write: redaction + atomicity ---------------------------------------


def test_write_redacts_before_disk(data_root: Path, fresh_db: Path) -> None:
    """Tokens are gone from the on-disk file even if they were in the input."""
    cid = "redaction-test"
    content = "Authorization: Bearer abcdef12345\nconfig: api_key=sk_live_42424242424242\n"
    h = write(content, call_id=cid, conn=db_connection(fresh_db))
    rec = lookup(h, conn=db_connection(fresh_db))
    on_disk = Path(rec.path).read_text(encoding="utf-8")
    assert "abcdef12345" not in on_disk
    assert "sk_live_42424242424242" not in on_disk
    assert "***" in on_disk


def test_write_records_metadata(data_root: Path, fresh_db: Path) -> None:
    cid = "metadata-test"
    content = "line 1\nline 2\nline 3\n"
    h = write(content, call_id=cid, conn=db_connection(fresh_db))
    rec = lookup(h, conn=db_connection(fresh_db))
    assert isinstance(rec, ArtifactRecord)
    assert rec.handle == h
    assert rec.call_id == cid
    assert rec.bytes_total == len(content.encode("utf-8"))
    assert rec.line_count == 3
    assert rec.sensitive is False


def test_write_sensitive_flag(data_root: Path, fresh_db: Path) -> None:
    cid = "sensitive-test"
    h = write("a", call_id=cid, sensitive=True, conn=db_connection(fresh_db))
    rec = lookup(h, conn=db_connection(fresh_db))
    assert rec.sensitive is True


def test_write_atomic_no_tmp_files_left(data_root: Path, fresh_db: Path) -> None:
    """After successful write, no .tmp files are left behind."""
    h = write("hi", call_id="atomic-test", conn=db_connection(fresh_db))
    rec = lookup(h, conn=db_connection(fresh_db))
    parent = Path(rec.path).parent
    tmps = list(parent.glob("*.tmp"))
    assert tmps == []


# --- ACL on Windows -----------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="icacls is Windows-only")
def test_acl_applied_on_windows(data_root: Path, fresh_db: Path) -> None:
    """The artifact must exist and ``icacls`` must show only the user ACE."""
    cid = "acl-test"
    h = write("private content", call_id=cid, conn=db_connection(fresh_db))
    rec = lookup(h, conn=db_connection(fresh_db))
    assert Path(rec.path).exists()
    # Run icacls and confirm "Everyone" is NOT in the ACE list.
    import subprocess

    res = subprocess.run(
        ["icacls", str(rec.path)],
        capture_output=True,
        timeout=5.0,
    )
    body = res.stdout.decode("utf-8", errors="replace")
    # The line for "Everyone:" must be absent.
    assert "Everyone" not in body, f"Everyone still has access: {body}"


def test_acl_skipped_on_non_windows(data_root: Path, fresh_db: Path) -> None:
    """On non-Windows, write() does not raise RedactionFailed."""
    if os.name == "nt":
        pytest.skip("Windows test elsewhere")
    h = write("non-windows content", call_id="non-windows", conn=db_connection(fresh_db))
    assert h.startswith("art://")


# --- lookup / exists -----------------------------------------------------


def test_lookup_unknown_raises(data_root: Path, fresh_db: Path) -> None:
    with pytest.raises(ArtifactNotFound):
        lookup("art://2099-01-01/calls/no-such-id.log", conn=db_connection(fresh_db))


def test_exists_true_for_known(data_root: Path, fresh_db: Path) -> None:
    h = write("ok", call_id="exists-1", conn=db_connection(fresh_db))
    assert exists(h) is True


def test_exists_false_for_unknown(data_root: Path, fresh_db: Path) -> None:
    assert exists("art://2099-01-01/calls/no-such.log") is False


# --- tail / read_range / search ------------------------------------------


def test_tail_returns_last_n_lines(data_root: Path, fresh_db: Path) -> None:
    cid = "tail-test"
    content = "\n".join(f"line {i}" for i in range(100)) + "\n"
    h = write(content, call_id=cid, conn=db_connection(fresh_db))
    last5 = tail(h, n=5)
    assert last5 == ["line 95", "line 96", "line 97", "line 98", "line 99"]


def test_tail_smaller_than_n_returns_all(data_root: Path, fresh_db: Path) -> None:
    cid = "tail-small"
    h = write("a\nb\nc\n", call_id=cid, conn=db_connection(fresh_db))
    assert tail(h, n=200) == ["a", "b", "c"]


def test_tail_zero_returns_empty(data_root: Path, fresh_db: Path) -> None:
    cid = "tail-zero"
    h = write("a\nb\n", call_id=cid, conn=db_connection(fresh_db))
    assert tail(h, n=0) == []


def test_tail_negative_n_returns_empty(data_root: Path, fresh_db: Path) -> None:
    cid = "tail-neg"
    h = write("a\nb\n", call_id=cid, conn=db_connection(fresh_db))
    assert tail(h, n=-3) == []


def test_read_range_basic(data_root: Path, fresh_db: Path) -> None:
    cid = "range-test"
    content = "\n".join(f"L{i}" for i in range(50)) + "\n"
    h = write(content, call_id=cid, conn=db_connection(fresh_db))
    out = read_range(h, 10, 15)
    assert out == ["L10", "L11", "L12", "L13", "L14"]


def test_read_range_empty_when_start_eq_end(data_root: Path, fresh_db: Path) -> None:
    cid = "range-empty"
    h = write("a\nb\nc\n", call_id=cid, conn=db_connection(fresh_db))
    assert read_range(h, 2, 2) == []


def test_read_range_negative_start_raises(data_root: Path, fresh_db: Path) -> None:
    cid = "range-neg"
    h = write("a\nb\n", call_id=cid, conn=db_connection(fresh_db))
    with pytest.raises(ValueError):
        read_range(h, -1, 5)


def test_search_returns_matches(data_root: Path, fresh_db: Path) -> None:
    cid = "search-test"
    content = "alpha\nbeta\ngamma alpha delta\nepsilon\n"
    h = write(content, call_id=cid, conn=db_connection(fresh_db))
    matches = search(h, r"alpha", max_results=10)
    assert len(matches) == 2
    assert matches[0]["line_no"] == 0
    assert matches[1]["line_no"] == 2


def test_search_caps_results(data_root: Path, fresh_db: Path) -> None:
    cid = "search-cap"
    content = "match\n" * 50
    h = write(content, call_id=cid, conn=db_connection(fresh_db))
    matches = search(h, r"match", max_results=10)
    assert len(matches) == 10


def test_search_empty_pattern_raises(data_root: Path, fresh_db: Path) -> None:
    cid = "search-empty"
    h = write("a\n", call_id=cid, conn=db_connection(fresh_db))
    with pytest.raises(ValueError):
        search(h, "", max_results=10)


def test_search_zero_max_returns_empty(data_root: Path, fresh_db: Path) -> None:
    cid = "search-zero"
    h = write("a\n", call_id=cid, conn=db_connection(fresh_db))
    assert search(h, r".*", max_results=0) == []


# --- threshold helper ----------------------------------------------------


def test_should_artifact_size_boundary(data_root: Path, fresh_db: Path) -> None:
    below = "x" * (INLINE_THRESHOLD_BYTES - 1)
    at = "x" * INLINE_THRESHOLD_BYTES
    above = "x" * (INLINE_THRESHOLD_BYTES + 1)
    assert should_artifact_size(below) is False
    assert should_artifact_size(at) is False
    assert should_artifact_size(above) is True


# --- internal: helpers exposed for tests ---------------------------------


def db_connection(path: Path):
    """Open a sqlite connection at the test's audit DB."""
    from localmcptools.persistence import db

    return db.get_connection(path)
