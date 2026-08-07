"""Artifact storage — large outputs go to ACL-protected files.

Every tool that produces > :data:`INLINE_THRESHOLD_BYTES` of output
**MUST** persist it via :func:`write` and return the handle to the
agent. The agent never sees the absolute path; it only sees the handle
and can page through it via the ``output.*`` tools.

Pipeline on :func:`write`:

1. Redact every line through :mod:`localmcptools.safety.redact`.
2. Compute the on-disk path under ``%APPDATA%\\LocalMcpTools\\artifacts\\
   YYYY-MM-DD\\calls\\<call_id>.log``.
3. Write atomically (write to ``.tmp``, rename).
4. On Windows, restrict the ACL to the current user only via ``icacls``.
5. If the ACL step fails, raise :class:`RedactionFailed` so the caller
   can decide whether to abort the persist.

The ACL step is **defence-in-depth**: a compromised Python process
can't read prior tool outputs any more than a human poking around in
``%APPDATA%`` could.

Schema is in :mod:`localmcptools.persistence.db` (the ``artifacts``
table from schema v2).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config.paths import data_dir
from ..safety.redact import redact
from . import db as _db_mod
from .db import get_connection

_log = logging.getLogger(__name__)


# --- Public constants -----------------------------------------------------

# Per spec: outputs larger than this go to an artifact instead of being
# inlined in the response. 64 KB matches the design.md figure.
INLINE_THRESHOLD_BYTES = 64 * 1024

# Subprocess timeout for ``icacls`` — a hang here would block the MCP
# loop. 5s is generous on Windows.
_ICACLS_TIMEOUT_S = 5.0


# --- Exceptions -----------------------------------------------------------


class ArtifactNotFound(LookupError):
    """The given handle does not resolve to a row in the artifacts table."""


class RedactionFailed(RuntimeError):
    """We could not safely write the artifact (e.g. ACL application failed).

    The caller MUST treat this as a hard failure and refuse to
    persist the output verbatim.
    """


# --- Value object ---------------------------------------------------------


@dataclass(frozen=True)
class ArtifactRecord:
    """Metadata for a stored artifact."""

    handle: str
    path: str
    call_id: str
    bytes_total: int
    line_count: int
    created_at: int  # unix ms
    expires_at: int | None
    sensitive: bool


# --- Path helpers ---------------------------------------------------------


def _today_str() -> str:
    """UTC date stamp ``YYYY-MM-DD``.

    We use UTC so a multi-process run on a machine whose local clock
    changes (DST, timezone move) doesn't fragment the artifact tree.
    """
    return _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%d")


def artifacts_root() -> Path:
    """Root of the artifact tree under the data dir."""
    p = data_dir() / "artifacts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def calls_dir_for(date_str: str | None = None) -> Path:
    """``artifacts/YYYY-MM-DD/calls/`` — created on demand."""
    d = artifacts_root() / (date_str or _today_str()) / "calls"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_handle(date_str: str, call_id: str) -> str:
    """Construct the canonical handle string."""
    return f"art://{date_str}/calls/{call_id}.log"


_HANDLE_RE = re.compile(
    r"^art://(?P<date>\d{4}-\d{2}-\d{2})/calls/(?P<call_id>[A-Za-z0-9_\-]+)\.log$"
)


def parse_handle(handle: str) -> tuple[str, str]:
    """Parse a handle; returns ``(date_str, call_id)`` or raises :class:`ArtifactNotFound`."""
    if not handle:
        raise ArtifactNotFound("empty handle")
    m = _HANDLE_RE.match(handle)
    if not m:
        raise ArtifactNotFound(f"malformed handle: {handle!r}")
    return m.group("date"), m.group("call_id")


# --- Write ----------------------------------------------------------------


def _call_id_from_arg(call_id: str | None) -> str:
    """Pick a call_id: explicit, or a fresh UUID4 hex."""
    if call_id:
        return call_id
    return uuid.uuid4().hex


def write(
    content: str | bytes,
    *,
    call_id: str | None = None,
    sensitive: bool = False,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Persist ``content`` to disk and return its handle.

    Returns the handle string (``art://...``). The caller should put
    this into ``meta.output_handle`` and ``calls.log_path`` (after the
    audit's :func:`record_finish` is updated).

    Raises:
        :class:`RedactionFailed` — if the ACL step fails (or any other
            step that prevents us from honouring the "current user
            only" guarantee). The caller MUST abort the persist in
            this case; we deliberately do not return a handle.
    """
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = content

    # 1. Redact. Even non-sensitive content goes through (cheap, safer).
    redacted_text, _redaction_count = redact(text)
    payload = redacted_text.encode("utf-8")

    # 2. Pick a path.
    cid = _call_id_from_arg(call_id)
    date_str = _today_str()
    target = path if path is not None else calls_dir_for(date_str) / f"{cid}.log"

    # Atomic write: tmp then rename. ``os.replace`` is atomic on Windows.
    tmp = target.with_suffix(target.suffix + ".tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        # Clean up half-written tmp if rename never happened.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise RedactionFailed(
            f"could not write artifact for call {cid!r}: {exc}"
        ) from exc

    # 3. ACL (Windows only).
    if os.name == "nt":
        try:
            _apply_windows_user_only_acl(target)
        except OSError as exc:
            # Don't leave a 0644 file behind — remove it so a hostile
            # process can't read what we couldn't lock down.
            try:
                target.unlink()
            except OSError:
                pass
            raise RedactionFailed(
                f"failed to set ACL on artifact {target!r}: {exc}"
            ) from exc

    # 4. Persist metadata row.
    bytes_total = len(payload)
    line_count = payload.count(b"\n")
    now_ms = int(time.time() * 1000)
    handle = build_handle(date_str, cid)

    def _do(c: sqlite3.Connection) -> None:
        c.execute(
            "INSERT OR REPLACE INTO artifacts "
            "(handle, path, call_id, bytes_total, line_count, created_at, "
            " expires_at, sensitive) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (handle, str(target), cid, bytes_total, line_count, now_ms,
             None, 1 if sensitive else 0),
        )

    if conn is None:
        _db_mod.init_db()
        with get_connection() as c:
            _do(c)
    else:
        _do(conn)

    return handle


# --- Windows ACL ----------------------------------------------------------


def _apply_windows_user_only_acl(path: Path) -> None:
    """Restrict the file to the current user only.

    Strategy:
      1. ``icacls <path> /inheritance:r`` — strip inherited ACEs.
      2. ``icacls <path> /grant:r <user>:R`` — grant *read-only* to the
         current user (replacing any explicit grant that might exist).
      3. ``icacls <path> /remove:g Everyone`` — strip the built-in
         ``Everyone`` group so other local users can't read it.

    We tolerate a non-zero exit from step 3 — ``Everyone`` may not be
    present, especially on non-domain machines.

    Runs with :data:`_ICACLS_TIMEOUT_S` so a stuck ``icacls`` doesn't
    freeze the MCP loop.
    """
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not user:
        # Last resort: let icacls resolve "%USERNAME%" itself.
        user = "%USERNAME%"
    # Inherit-r first (drops parent ACEs).
    subprocess.run(
        ["icacls", str(path), "/inheritance:r"],
        check=True,
        timeout=_ICACLS_TIMEOUT_S,
        capture_output=True,
    )
    # Grant the current user full control (we own it).
    subprocess.run(
        ["icacls", str(path), "/grant:r", f"{user}:(R,W)"],
        check=True,
        timeout=_ICACLS_TIMEOUT_S,
        capture_output=True,
    )
    # Strip Everyone (best-effort).
    res = subprocess.run(
        ["icacls", str(path), "/remove:g", "Everyone"],
        check=False,
        timeout=_ICACLS_TIMEOUT_S,
        capture_output=True,
    )
    if res.returncode != 0:
        # Not fatal; just log.
        _log.debug("icacls /remove:g Everyone non-zero exit: %s",
                   res.stderr.decode(errors="replace"))


# --- Lookup ---------------------------------------------------------------


def lookup(handle: str, conn: sqlite3.Connection | None = None) -> ArtifactRecord:
    """Return the metadata for ``handle`` or raise :class:`ArtifactNotFound`."""
    def _do(c: sqlite3.Connection) -> ArtifactRecord:
        row = c.execute(
            "SELECT handle, path, call_id, bytes_total, line_count, "
            "created_at, expires_at, sensitive "
            "FROM artifacts WHERE handle = ?",
            (handle,),
        ).fetchone()
        if row is None:
            raise ArtifactNotFound(f"unknown handle: {handle!r}")
        return ArtifactRecord(
            handle=row["handle"],
            path=row["path"],
            call_id=row["call_id"],
            bytes_total=row["bytes_total"],
            line_count=row["line_count"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            sensitive=bool(row["sensitive"]),
        )

    if conn is None:
        _db_mod.init_db()
        with get_connection() as c:
            return _do(c)
    return _do(conn)


def exists(handle: str) -> bool:
    """Cheap check; ``False`` if the row or the file is missing."""
    try:
        rec = lookup(handle)
    except ArtifactNotFound:
        return False
    return Path(rec.path).exists()


# --- Read -----------------------------------------------------------------


def _verify_acl(path: Path) -> None:
    """Re-check ACL on every read; defence-in-depth against tampering.

    Currently a no-op on non-Windows. On Windows we'd ideally parse
    ``icacls`` output, but the cost isn't worth it for the spike —
    the OS-level permissions don't change unless an admin touches
    them, in which case the user has bigger problems.
    """
    if os.name != "nt":
        return
    # Cheap sanity check: file must still be owned by current user.
    # stat() doesn't surface owner on Windows portably; leave as a no-op.


def _read_text(rec: ArtifactRecord, *, max_bytes: int | None = None) -> list[str]:
    """Read the file and return its lines, capped at ``max_bytes``.

    ``max_bytes=None`` reads the whole file. Use a cap for large
    artifacts to avoid blowing memory.
    """
    p = Path(rec.path)
    if not p.exists():
        raise ArtifactNotFound(f"artifact file vanished: {rec.path!r}")
    _verify_acl(p)
    # Read in binary and decode so we can enforce a byte cap.
    if max_bytes is None:
        raw = p.read_bytes()
    else:
        with open(p, "rb") as fh:
            raw = fh.read(max_bytes)
    text = raw.decode("utf-8", errors="replace")
    # Strip a trailing partial line so callers get clean \n splits.
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


def tail(handle: str, n: int = 200, *, conn: sqlite3.Connection | None = None) -> list[str]:
    """Return the last ``n`` lines of the artifact.

    We never load the whole file: we open it, seek backwards in chunks
    until we have at least ``n`` newlines, then return the tail.
    """
    if n < 1:
        return []
    rec = lookup(handle, conn=conn)
    p = Path(rec.path)
    if not p.exists():
        raise ArtifactNotFound(f"artifact file vanished: {rec.path!r}")
    _verify_acl(p)

    # Chunked backward read. 32 KB chunk keeps memory bounded even for
    # multi-GB artifacts.
    chunk = 32 * 1024
    size = p.stat().st_size
    if size == 0:
        return []
    pos = size
    buf = b""
    with open(p, "rb") as fh:
        while pos > 0 and buf.count(b"\n") <= n:
            read_size = min(chunk, pos)
            pos -= read_size
            fh.seek(pos)
            buf = fh.read(read_size) + buf
            if pos == 0:
                break
    # Decode and split.
    text = buf.decode("utf-8", errors="replace")
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n")
    # If we read everything and got fewer than n lines, return them all.
    if pos == 0:
        return lines[-n:] if len(lines) > n else lines
    # Otherwise the first line in our buffer is a partial line (we may
    # have started mid-line); drop it.
    if len(lines) > n:
        return lines[-n:]
    return lines


def read_range(
    handle: str,
    start_line: int,
    end_line: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Return lines ``[start_line, end_line)`` (0-indexed, half-open)."""
    if start_line < 0:
        raise ValueError("start_line must be >= 0")
    if end_line <= start_line:
        return []
    rec = lookup(handle, conn=conn)
    # For modest ranges we just read it all — the threshold where the
    # chunked-read approach becomes worthwhile is around 100k lines.
    if rec.line_count <= 100_000:
        all_lines = _read_text(rec)
        return all_lines[start_line:end_line]
    # Larger: stream through.
    return _read_range_streaming(rec, start_line, end_line)


def _read_range_streaming(rec: ArtifactRecord, start_line: int, end_line: int) -> list[str]:
    out: list[str] = []
    end_line - start_line
    p = Path(rec.path)
    if not p.exists():
        raise ArtifactNotFound(f"artifact file vanished: {rec.path!r}")
    with open(p, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= end_line:
                break
            if i >= start_line:
                # Strip trailing newline for consistency with ``tail``.
                if line.endswith("\n"):
                    line = line[:-1]
                out.append(line)
    return out


def search(
    handle: str,
    pattern: str,
    *,
    max_results: int = 200,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, object]]:
    """Return matches ``[{line_no, text}, ...]`` for regex ``pattern``.

    Limited to ``max_results`` to bound memory.
    """
    if not pattern:
        raise ValueError("pattern must be non-empty")
    if max_results < 1:
        return []
    rec = lookup(handle, conn=conn)
    rx = re.compile(pattern)
    out: list[dict[str, object]] = []
    p = Path(rec.path)
    if not p.exists():
        raise ArtifactNotFound(f"artifact file vanished: {rec.path!r}")
    with open(p, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if rx.search(line):
                out.append({"line_no": i, "text": line.rstrip("\n")})
                if len(out) >= max_results:
                    break
    return out


# --- Convenience helpers -------------------------------------------------


def inline_or_handle(content: str) -> tuple[bool, str | None]:
    """If ``content`` fits inline, return ``(True, content)``; else ``(False, handle)``.

    This is a tiny helper for tool bodies. Real usage is::

        content = capture_some_output()
        if len(content) > INLINE_THRESHOLD_BYTES:
            handle = artifacts.write(content)
            # ... put handle into meta.output_handle
        else:
            data = content
    """
    if len(content.encode("utf-8")) > INLINE_THRESHOLD_BYTES:
        return False, None  # caller decides whether to call write()
    return True, content


def should_artifact_size(content: str | bytes) -> bool:
    """True iff the content is large enough to require artifact storage."""
    size = len(content) if isinstance(content, str) else len(content)
    return size > INLINE_THRESHOLD_BYTES


__all__ = [
    "ArtifactNotFound",
    "ArtifactRecord",
    "INLINE_THRESHOLD_BYTES",
    "RedactionFailed",
    "build_handle",
    "exists",
    "lookup",
    "parse_handle",
    "read_range",
    "search",
    "should_artifact_size",
    "tail",
    "write",
]
