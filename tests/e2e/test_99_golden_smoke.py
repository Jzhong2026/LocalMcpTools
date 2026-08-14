"""Regression test for ``scripts/golden-smoke.py``.

Boots a fresh HTTP server in a temp data dir, runs the smoke script
against it in-process, then diffs the captured output (after
normalising non-deterministic fields) against
``tests/golden-set-baseline.txt``.

This is what makes the baseline file actually useful — without this
test, the baseline is a static snapshot that nobody reads. With it,
every PR that changes a tool's response shape will fail loudly.

If the baseline needs to be regenerated (e.g. a tool's payload grew
a new stable field), run::

    .venv\\Scripts\\python.exe scripts\\golden-smoke.py > tests\\golden-set-baseline.txt

and commit the result.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from .conftest import HttpHarness

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = REPO_ROOT / "tests" / "golden-set-baseline.txt"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Non-deterministic fields that change on every run. The placeholders
# are stable strings that survive a diff round-trip. ``workspace_id``
# is a fresh UUID per workspace.register call, so it has to be
# normalised too — it shows up both inside JSON data and on the
# one-line summary line. ``head`` is the workspace's current git HEAD,
# which moves on every commit so it can't be part of a stable baseline.
_NORMALIZE_RULES: dict[str, str] = {
    "audit_id": "<AUDIT_ID>",
    "run_id": "<RUN_ID>",
    "duration_ms": "<DURATION_MS>",
    "workspace_id": "<WORKSPACE_ID>",
    "head": "<GIT_HEAD>",
}


def _normalize(text: str) -> str:
    """Replace non-deterministic JSON values with stable placeholders.

    Handles both string (``"audit_id": "abc-123"``) and numeric
    (``"duration_ms": 21``) forms. The one-line summary produced by
    ``_print_block`` shows the field in its own format
    (``workspace_id='bd0d...'``); we replace any quoted 32-char hex
    string after the field name with the placeholder, which covers
    both styles.

    Also normalises four environment-dependent patterns that aren't
    tied to a specific field name:
      * version strings shaped like X.Y.Z.W (4 dot-separated ints)
      * partial IP addresses inside the 400-char body truncation window
        (e.g. "192.168." or "172.") — these can leak through because
        the truncation cuts mid-address and version_pat requires
        complete segments
      * ``"pid": <int>`` and ``pid=<int>`` in process-list output
      * ephemeral test ports in the smoke header URL
    """
    out: list[str] = []
    version_pat = re.compile(r"\b\d+\.\d+\.\d+(?:\.\d+)+\b")
    # IP-style fragments that the 400-char body print can cut off
    # mid-address (and even mid-quote, leaving no closing ``"`` to
    # anchor against). Match an opening ``"``, one or more digits,
    # then any number of ``.<digit>`` pairs with an optional trailing
    # ``.``. Catches ``"172.`` (truncated right after the first dot)
    # through ``"192.168.1.1"`` (full quad).
    partial_ip_pat = re.compile(r'"\d+(?:\.\d+)*\.?')
    for line in text.splitlines():
        for field, placeholder in _NORMALIZE_RULES.items():
            line = re.sub(rf'"{field}":\s*"[^"]*"', f'"{field}": "{placeholder}"', line)
            line = re.sub(rf'"{field}":\s*-?\d+', f'"{field}": {placeholder}', line)
            # summary-line form: field='<uuid>' or field="<uuid>"
            line = re.sub(rf"{field}='[^']*'", f"{field}='{placeholder}'", line)
        # Generic 4-segment versions (PowerShell, OS, etc.) drift across
        # Windows updates; replace so the baseline stays portable.
        line = version_pat.sub("<VERSION>", line)
        # IP fragments that survived version_pat because the 400-char
        # body print cut them mid-segment. Replace the partial numeric
        # string with a stable placeholder.
        line = partial_ip_pat.sub('"<IP>"', line)
        # PIDs in process-list output differ on every boot.
        line = re.sub(r'"pid":\s*-?\d+', '"pid": <PID>', line)
        line = re.sub(r"pid=\d+", "pid=<PID>", line)
        # The smoke header line prints the URL; the port is _free_port()
        # and changes per run. Match the URL form specifically so we
        # don't accidentally nuke unrelated :1234 sequences.
        line = re.sub(r"http://[^/\s]+:\d+", "http://<HOST>:<PORT>", line)
        out.append(line.rstrip())
    return "\n".join(out) + "\n"


def _load_smoke_module() -> ModuleType:
    """Import the smoke script as a module so we can call main() and
    monkeypatch DATA_DIR without spawning a subprocess.

    The file is named ``golden-smoke.py`` (with a hyphen), which is
    not a valid Python module identifier. We use the file-based
    loader instead of ``import_module``.
    """
    spec = importlib.util.spec_from_file_location("golden_smoke", SCRIPTS_DIR / "golden-smoke.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module spec for {SCRIPTS_DIR / 'golden-smoke.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # let @dataclass / recursive imports find it
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _port_from_url(url: str) -> int:
    return int(url.rsplit(":", 1)[1])


async def _run_smoke(harness: HttpHarness, capture: io.StringIO) -> int:
    """Run ``golden_smoke.main()`` with stdout redirected to *capture*.

    Monkeypatches the module's DATA_DIR + _load_token so the script
    targets our test server (whose csrf_token is in the harness's
    data dir) instead of the user's %APPDATA%\\LocalMcpTools.
    """
    smoke = _load_smoke_module()
    # ModuleType doesn't tell mypy about runtime-injected attrs, so
    # go through vars()/setattr to keep the strict mode happy.
    setattr(smoke, "DATA_DIR", harness.data_dir)
    setattr(smoke, "_load_token", lambda: (harness.bearer_token, _port_from_url(harness.base_url)))
    with contextlib.redirect_stdout(capture):
        # main() is declared `-> int`; redirect_stdout can hand back Any.
        return cast(int, await smoke.main())


@pytest.mark.e2e
async def test_golden_smoke_matches_baseline(live_server_http: HttpHarness) -> None:
    """Run the smoke script and diff the normalised output against
    the saved baseline. On drift, fail with a short unified diff and
    a hint about how to regenerate.

    Set ``LMCP_REGEN_BASELINE=1`` to overwrite the baseline file with
    the current output instead of asserting (useful after a payload
    change). The file is written with a UTF-8 BOM so ``utf-8-sig``
    on read stays consistent.
    """
    capture = io.StringIO()
    rc = await _run_smoke(live_server_http, capture)
    assert rc == 0, f"golden-smoke.py exited with {rc}"

    # The baseline was captured with a UTF-8 BOM on Windows; utf-8-sig
    # strips it transparently so the diff doesn't show a phantom char.
    baseline = _normalize(BASELINE_PATH.read_text(encoding="utf-8-sig"))
    actual = _normalize(capture.getvalue())

    if os.environ.get("LMCP_REGEN_BASELINE") == "1":
        # Write the raw (non-normalised) capture so the file is a faithful
        # record of what the script emitted; the test normalises on read.
        BASELINE_PATH.write_bytes(b"\xef\xbb\xbf" + capture.getvalue().encode("utf-8"))
        pytest.skip("LMCP_REGEN_BASELINE=1 — baseline rewritten, re-run without it to assert")

    if actual == baseline:
        return

    # Mismatch — show a scannable diff (first 80 lines) and the
    # regenerate hint.
    import difflib

    diff_lines = list(
        difflib.unified_diff(
            baseline.splitlines(keepends=False),
            actual.splitlines(keepends=False),
            fromfile="tests/golden-set-baseline.txt",
            tofile="golden-smoke (actual)",
            lineterm="",
        )
    )
    head = "\n".join(diff_lines[:80])
    hint = (
        "\n\nTo regenerate the baseline after an intentional payload change:\n"
        r"  $env:LMCP_REGEN_BASELINE=1; .venv\Scripts\python.exe -m pytest "
        "tests/e2e/test_99_golden_smoke.py -m e2e -v\n"
    )
    pytest.fail(f"golden-smoke output drifted from baseline:\n{head}{hint}")
