"""Shared fixtures for the e2e suite.

The e2e tests spawn a real ``python -m localmcptools`` process and
talk to it over stdio or HTTP. Every fixture here is autouse-able but
none of them are autouse — each test asks for what it needs.

Key fixtures:

* :func:`live_server_params` — ``StdioServerParameters`` pointing at a
  private ``LMCP_DATA_DIR`` so tests don't share state.
* :func:`live_server_http` — boots the HTTP mode in a temp data dir,
  parses ``server.json``, returns a small holder with the base URL,
  CSRF token, and bearer token.
* :func:`stdio_session` — async MCP ``ClientSession`` over the stdio
  fixture; lazy because MCP sessions are async-only.
* :func:`http_client` — :mod:`httpx` async client wired with Bearer
  + CSRF + Origin.
* :func:`fixture_workspace` — a small on-disk project (pyproject +
  app.py + build.log) ready for ``workspace.*`` tests.
* :func:`audit_db` — connection to the test's private ``audit.sqlite``,
  ready for row-level assertions.

Every fixture that boots a server also cleans it up on teardown so
the suite is safe to interrupt.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import psutil
import pytest

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PKG_PARENT = str(REPO_ROOT / "src")


def _python_executable() -> str:
    """The Python interpreter that runs the test process is the same
    one we use to spawn the server — keeps the venv self-consistent.
    """
    return sys.executable


# ---------------------------------------------------------------------------
# Stdio fixture
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StdioHarness:
    """Handle returned by :func:`live_server_params`."""

    params: object  # mcp.StdioServerParameters (typed loosely to avoid import in sync code)
    data_dir: Path


@pytest.fixture
def live_server_params(tmp_path: Path) -> StdioHarness:
    """Spawn-args for ``python -m localmcptools`` over stdio.

    The process is **not** started here — callers feed it to
    :func:`mcp.client.stdio.stdio_client` which manages the lifecycle.
    """
    from mcp import StdioServerParameters  # local import keeps e2e off the unit path

    env = os.environ.copy()
    env["LMCP_DATA_DIR"] = str(tmp_path)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = PKG_PARENT + os.pathsep + env.get("PYTHONPATH", "")
    return StdioHarness(
        params=StdioServerParameters(
            command=_python_executable(),
            args=["-m", "localmcptools"],
            env=env,
            cwd=str(REPO_ROOT),
        ),
        data_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# HTTP fixture
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpHarness:
    """Handle returned by :func:`live_server_http`."""

    base_url: str
    bearer_token: str
    csrf_token: str
    csrf_cookie: str
    origin: str
    data_dir: Path
    pid: int  # server's actual pid (from server.json, NOT subprocess.Popen.pid)
    _proc: subprocess.Popen  # not exposed in API but kept alive by the fixture

    def shutdown(self) -> None:
        # Always go via ``taskkill`` to the server's real pid — it
        # matches whatever the server itself recorded in server.json,
        # which is what ``cli stop`` would also do.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(self.pid), "/T"],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
        with contextlib.suppress(Exception):
            self._proc.terminate()
            self._proc.wait(timeout=5)
        with contextlib.suppress(Exception):
            self._proc.kill()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server_http(tmp_path: Path) -> Iterator[HttpHarness]:
    """Boot the HTTP control plane in a temp data dir.

    Yields once ``/api/status`` is reachable and ``server.json`` has
    been written. Kills the server on teardown.
    """
    port = _free_port()
    env = os.environ.copy()
    env["LMCP_DATA_DIR"] = str(tmp_path)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = PKG_PARENT + os.pathsep + env.get("PYTHONPATH", "")

    cmd = [
        _python_executable(),
        "-m",
        "localmcptools",
        "start",
        "--http",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        # --auto-open-browser defaults to False, so no flag needed
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    server_json = tmp_path / "server.json"
    base_url = f"http://127.0.0.1:{port}"
    try:
        # Wait for server.json WITH csrf_token (max 15 s).
        # ``cli.py`` writes a stub first; the real server-side write
        # comes from ``run_http`` ~200 ms later.
        deadline = time.monotonic() + 15
        meta = None
        while time.monotonic() < deadline:
            if server_json.exists():
                try:
                    candidate = json.loads(server_json.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    candidate = None
                if candidate and candidate.get("csrf_token"):
                    meta = candidate
                    break
            if proc.poll() is not None:
                stdout = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
                raise RuntimeError(f"server exited early: code={proc.returncode}\n{stdout}")
            time.sleep(0.1)
        else:
            proc.terminate()
            raise RuntimeError("server.json never grew a csrf_token within 15 s")

        # The same token serves as both /mcp Bearer and /api/* CSRF.
        token = meta["csrf_token"]

        # Wait until /api/status actually responds. /api/* uses Origin
        # gate + (for unsafe methods) CSRF cookie+header. No Bearer
        # required for GET.
        import httpx

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                r = httpx.get(
                    f"{base_url}/api/status",
                    headers={"Origin": "http://127.0.0.1"},
                    timeout=2,
                )
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("/api/status never returned 200")

        yield HttpHarness(
            base_url=base_url,
            bearer_token=token,
            csrf_token=token,
            csrf_cookie=f"lmcp_csrf={token}",
            origin="http://127.0.0.1",
            data_dir=tmp_path,
            pid=meta["pid"],  # server.json's view, not subprocess.Popen.pid
            _proc=proc,
        )
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        # Best-effort cleanup of any orphan child processes (uvicorn worker, etc.)
        try:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                with contextlib.suppress(psutil.NoSuchProcess):
                    child.kill()
        except psutil.NoSuchProcess:
            pass


# ---------------------------------------------------------------------------
# Audit DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection to the test's audit.sqlite.

    Works for both stdio and HTTP fixtures because both honour
    ``LMCP_DATA_DIR``. The server is responsible for migrating the
    schema — this fixture only opens the file. If the server hasn't
    booted yet, the file may not exist; callers should request this
    fixture **after** the server fixture has run at least once.
    """
    db_file = tmp_path / "audit.sqlite"
    if not db_file.exists():
        # Server hasn't been booted in this test yet; create a blank
        # connection so tests that ask for the fixture *before* the
        # server fixture (rare) don't crash on import. The server's
        # own ``init_db`` will migrate the schema on first boot.
        db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Workspace fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_workspace(tmp_path: Path) -> Path:
    """Tiny on-disk project for workspace.* / fs.* / output.* tests."""
    root = tmp_path / "fixture_project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\n', encoding="utf-8"
    )
    (root / "app.py").write_text(
        '"""Tiny fixture module."""\n'
        "TODO: replace this with real code\n"
        'def greet(name: str) -> str:\n    return f"hello, {name}"\n',
        encoding="utf-8",
    )
    log = root / "build.log"
    log.write_text(
        "\n".join(f"line {i}: status=ok bytes={i * 7}" for i in range(50)) + "\n",
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# Async helpers (kept here so individual tests don't repeat the boilerplate)
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    """Force asyncio for ``pytest-asyncio``."""
    return "asyncio"


@pytest.fixture
def mcp_timeout() -> float:
    """Default per-test timeout when talking to the server."""
    return 30.0


# ---------------------------------------------------------------------------
# Background-process cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_managed_processes(tmp_path: Path) -> Iterator[None]:
    """Best-effort cleanup of any background process the test might have left.

    The managed-process subsystem owns its lifecycle but a crash mid-test
    can leak a child python.exe; we sweep after each test using psutil.
    """
    yield
    try:
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = p.info.get("cmdline") or []
                if any("localmcptools" in (c or "") for c in cmdline):
                    if p.pid == os.getpid():
                        continue
                    with contextlib.suppress(psutil.NoSuchProcess):
                        p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass  # never fail the test on teardown noise