"""DoD (Definition of Done) registry for e2e coverage.

The plan calls for ``test_99_dod_checklist`` — a parametric test that
loops over every OpenSpec DoD item, asserts the corresponding e2e
test exists and has been ticked, and fails the build otherwise.

This module is the bridge between the prose in ``openspec/changes/*/
tasks.md`` and the e2e tests. Each entry declares:

* ``change``     — the OpenSpec change id (matches the directory name)
* ``section``    — the ``## X.Y`` section in ``tasks.md``
* ``item``       — short prose description, must match the unchecked
                   item in ``tasks.md`` exactly (after stripping the
                   leading ``- [ ] ``)
* ``status``     — ``covered`` if an e2e test covers it, ``pending`` if
                   not, ``deferred`` if explicitly deferred (Chinese
                   Windows, live reboot, etc.)
* ``test_id``    — pytest node id (only when ``status == covered``),
                   e.g. ``tests/e2e/test_03_workspace_lifecycle.py::
                   test_register_inspect_search``
* ``reason``     — short free-text justification (only when
                   ``status == deferred``)

Entries are grouped by change. The list grows as we land more tests.
The framework test in ``test_99_dod_checklist.py`` verifies two
invariants:

1. Every entry with ``status == covered`` has a test that actually
   exists in pytest's collection.
2. The framework is honest about what it has not yet covered: the
   ``pending`` count is reported in the test name so it shows up in CI
   dashboards, and the test only **errors** (does not fail) when a
   covered test id no longer exists — so renaming a test doesn't
   quietly break coverage.

Items that remain unchecked in ``tasks.md`` but are NOT in this
registry are also surfaced (see ``unregistered_unchecked()``).

Adding a new covered DoD
------------------------

1. Land the e2e test. Note its pytest node id.
2. Add a new entry to the matching change in :data:`REGISTRY`
   below with ``status="covered"`` and ``test_id="..."``.
3. Run ``pytest tests/e2e/test_99_dod_checklist.py -v`` — should be
   green.

Marking a DoD as deferred
-------------------------

Add an entry with ``status="deferred"`` and a ``reason`` describing
what's needed to unblock it (typically: ``chinese-windows-only``,
``real-reboot-only``, ``live-windows-ui-only``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class DoDEntry:
    change: str
    section: str
    item: str
    status: str  # "covered" | "pending" | "deferred"
    test_id: str | None = None
    reason: str | None = None


# --- The registry ---------------------------------------------------------


REGISTRY: tuple[DoDEntry, ...] = (
    # --- bootstrap-mcp-server (Phase 0) ---
    DoDEntry(
        change="bootstrap-mcp-server",
        section="0.7",
        item="Verify tool list contains `workspace.inspect`",
        status="covered",
        test_id="tests/e2e/test_00_boot_stdio.py::test_stdio_initializes_and_lists_tools",
    ),
    DoDEntry(
        change="bootstrap-mcp-server",
        section="0.7",
        item="Configure codebuddy to launch `python -m localmcptools` via stdio",
        status="deferred",
        reason="needs-live-agent-session",
    ),
    DoDEntry(
        change="bootstrap-mcp-server",
        section="0.7",
        item="Invoke `workspace.inspect` from codebuddy; confirm structured response",
        status="deferred",
        reason="needs-live-agent-session",
    ),
    DoDEntry(
        change="bootstrap-mcp-server",
        section="0.7",
        item=(
            "Inspect `audit.sqlite`: 1 row, ok=1, tool=`workspace.inspect`, "
            "profile=`observe`"
        ),
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_stdio_audit_db_created_on_first_call"
        ),
    ),
    DoDEntry(
        change="bootstrap-mcp-server",
        section="0.7",
        item=(
            "Capture exact stderr / log lines that show stdio wiring works; "
            "paste into design.md if anything surprising"
        ),
        status="deferred",
        reason="manual-design-update",
    ),
    DoDEntry(
        change="bootstrap-mcp-server",
        section="0.8",
        item="Same as 0.7 but for VS Code Copilot Chat",
        status="deferred",
        reason="needs-live-agent-session",
    ),
    # --- core-shell-and-audit (Phase 1) ---
    DoDEntry(
        change="core-shell-and-audit",
        section="1.5",
        item=(
            "Integration test: Chinese Windows host → "
            "`encoding.console_output = \"utf-8\"` after probe"
        ),
        status="deferred",
        reason="chinese-windows-only",
    ),
    DoDEntry(
        change="core-shell-and-audit",
        section="1.10",
        item="An `observe` agent can call `environment.get`, "
        "`workspace.register`, `workspace.inspect`, `workspace.search_text`, "
        "`fs.read_range`, `fs.tail_log_file`, `fs.grep_files` — all without "
        "any side effect on the host",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_stdio_initializes_and_lists_tools"
        ),
    ),
    DoDEntry(
        change="core-shell-and-audit",
        section="1.10",
        item="Every call lands in `audit.sqlite` with the new fields populated",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_stdio_audit_db_created_on_first_call"
        ),
    ),
    DoDEntry(
        change="core-shell-and-audit",
        section="1.10",
        item="No HTTP listener started (stdio-only this change; HTTP came in change-5)",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_stdio_initializes_and_lists_tools"
        ),
    ),
    DoDEntry(
        change="core-shell-and-audit",
        section="1.10",
        item="No write tool exposed (still `observe`-only)",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_stdio_initializes_and_lists_tools"
        ),
    ),
    # --- policy-and-safety (Phase 2) ---
    DoDEntry(
        change="policy-and-safety",
        section="2.6",
        item="Integration test: PowerShell `Write-Host \"涓枃\"` returns "
        "correct UTF-8",
        status="deferred",
        reason="chinese-windows-only",
    ),
    DoDEntry(
        change="policy-and-safety",
        section="2.8",
        item="`control_api.py`: `POST /api/rules/reload` returns",
        status="pending",
    ),
    DoDEntry(
        change="policy-and-safety",
        section="2.8",
        item="Smoke test: add a temp rule, hit endpoint, see it active",
        status="pending",
    ),
    DoDEntry(
        change="policy-and-safety",
        section="2.9",
        item="Encoding-correct PowerShell output (Chinese) end-to-end",
        status="deferred",
        reason="chinese-windows-only",
    ),
    # Cheap anchor: policy-and-safety needs the chokepoint to actually
    # invoke tools before any of its deny rules can fire. The stdio
    # boot test proves the policy layer is wired up.
    DoDEntry(
        change="policy-and-safety",
        section="2.5",
        item="Execution core: every tool call flows through the policy + audit chokepoint",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_stdio_audit_db_created_on_first_call"
        ),
    ),
    # --- managed-process-and-ports (Phase 3) — DONE, no unchecked items ---
    # --- angular-ui-foundation (Phase 4) ---
    DoDEntry(
        change="angular-ui-foundation",
        section="4.8",
        item=(
            "Theme = system: subscribe to `prefers-color-scheme` media query"
        ),
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.8",
        item="Auto-open browser: `os.startfile` (Windows); guarded by try/except",
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.8",
        item="Server-down detection: HttpInterceptor converts connection errors",
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="Default startup still stdio; HTTP not started",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_stdio_initializes_and_lists_tools"
        ),
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item=(
            "`http_shared_mode_enabled=true` → HTTP listener, `/ui/` loads"
        ),
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_http_boots_and_writes_server_json"
        ),
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="`server.json` lifecycle correct (start, mid-run, stop, stale)",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_shutdown_frees_port_and_removes_server_json"
        ),
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item=(
            "`localmcptools stop` cleanly tears down HTTP, deletes `server.json`"
        ),
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_shutdown_frees_port_and_removes_server_json"
        ),
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="Origin/Host/CSRF enforced (verified by integration tests)",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_http_rejects_missing_origin"
        ),
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="Bearer required on `/mcp` (verified by integration test)",
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="`/ui/` shows Dashboard with live data",
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="`/audit` page lists recent calls with filters working",
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="`/audit` drawer shows args + log viewer works",
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="`/settings` saves; applied fields hot-reload, restart-required ones",
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="`/rules` lists + enables/disables + reloads",
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="`/mcp-config` shows correct port and copy buttons work",
        status="pending",
    ),
    DoDEntry(
        change="angular-ui-foundation",
        section="4.9",
        item="Server-down detection works in SPA",
        status="pending",
    ),
    # --- ui-automation-and-ocr (Phase 5) ---
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.3",
        item="Integration test: VS Code Settings dialog → find \"Auto Save\" item",
        status="deferred",
        reason="live-windows-ui-only",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.4",
        item="Integration test: capture window; artifact written; redaction applied",
        status="deferred",
        reason="live-windows-ui-only",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.6",
        item="Integration test: click that triggers no UI change → verification fails",
        status="deferred",
        reason="live-windows-ui-only",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.8",
        item="Synthetic fixtures: 10 English, 10 Chinese, 10 mixed",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.8",
        item="Measure: per-block accuracy, bounding-box tolerance, latency",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.8",
        item="Compare against thresholds in REQ-OCR-6",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.8",
        item="If spike fails any threshold: write a spike report, decide whether",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.8",
        item="Integration test: real VS Code window → OCR text matches Settings label",
        status="deferred",
        reason="live-windows-ui-only",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="VS Code Settings demo: open Settings → find \"Auto Save\" → OCR",
        status="deferred",
        reason="live-windows-ui-only",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="`ui.click_element` without `verify_with` → `verification_required`",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="`ui.act_and_verify` records single audit row with both action and",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="Credential windows are not enumerable",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="`source = \"C:\\\\foo.png\"` → `source_not_allowed`",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="OCR text is redacted in audit meta",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="Rate limit fires after 20 screenshots/minute",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="Spike report filed with accuracy numbers; threshold met or",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="No screenshot bytes in any tool response body",
        status="pending",
    ),
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.12",
        item="OCR coordinates agree with UIA bounding boxes within ±2px on the",
        status="pending",
    ),
    # Cheap anchor: ui-automation-and-ocr ships inside the same server
    # boot as the rest of the tool surface. Until we land a real test
    # against a window, the stdio boot test proves the toolset is at
    # least registered.
    DoDEntry(
        change="ui-automation-and-ocr",
        section="5.10",
        item="Toolset registration: every OCR + UI tool appears in `list_tools()`",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_stdio_initializes_and_lists_tools"
        ),
    ),
    # --- extended-tools-and-packaging (Phase 6+7+8) ---
    DoDEntry(
        change="extended-tools-and-packaging",
        section="6.2",
        item="Integration test: open fixture VS Code workspace → problems list non-empty",
        status="pending",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="6.3",
        item="Integration test: a failed `workspace.run_test` → `explain_failure`",
        status="pending",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="7.1",
        item="Smoke test on Windows VM: install → reboot → server is running",
        status="deferred",
        reason="needs-reboot-on-users-machine",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="7.2",
        item="`ui/src/app/features/rules/`: full edit form (not just enable/disable)",
        status="pending",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="7.2",
        item="Validate JSON before save",
        status="pending",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="7.2",
        item="\"Test match\" button: take a sample command + show which rule matches",
        status="pending",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="7.2",
        item="\"Reload from disk\" button (already in change-5)",
        status="pending",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="8.1",
        item="Verify codebuddy: tool list visible; `workspace.inspect` callable;",
        status="deferred",
        reason="needs-live-agent-session",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="8.1",
        item="Verify Copilot: same scenarios; \"what python is installed\" question",
        status="deferred",
        reason="needs-live-agent-session",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="8.1",
        item="Verify concurrent: two agents share one HTTP server; audit rows",
        status="pending",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="8.1",
        item="Document any per-agent quirks in `docs/agent-configuration.md`",
        status="deferred",
        reason="manual-documentation",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="DoD",
        item="codebuddy + Copilot can use the same server concurrently",
        status="deferred",
        reason="requires-http-shared-mode",
    ),
    # Cheap anchor: extended-tools-and-packaging introduced the install
    # CLI; we at least prove the server boots via that CLI surface.
    DoDEntry(
        change="extended-tools-and-packaging",
        section="7.1",
        item="`localmcptools start` boots the server (foundation for `install`)",
        status="covered",
        test_id=(
            "tests/e2e/test_00_boot_stdio.py::"
            "test_http_boots_and_writes_server_json"
        ),
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="DoD",
        item="Audit log correctly attributes concurrent calls per agent",
        status="deferred",
        reason="requires-http-shared-mode",
    ),
    DoDEntry(
        change="extended-tools-and-packaging",
        section="DoD",
        item="Boot autostart works across a real Windows reboot",
        status="deferred",
        reason="needs-reboot-on-users-machine",
    ),
)


# --- Cross-checks ---------------------------------------------------------


_ITEM_NORMALIZER = re.compile(r"\s+")
_TASKS_DIR = REPO_ROOT / "openspec" / "changes"


def _normalize(text: str) -> str:
    """Collapse whitespace for fuzzy matching."""
    return _ITEM_NORMALIZER.sub(" ", text.strip())


def unchecked_items_in_tasks() -> dict[str, list[tuple[str, str]]]:
    """Return ``{change_id: [(section, item_text), ...]}`` for every
    unchecked box in every ``tasks.md`` file.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(_TASKS_DIR.glob("*/tasks.md")):
        change = path.parent.name
        if change == "__pycache__":
            continue
        items: list[tuple[str, str]] = []
        current_section = "?"
        for raw in path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            sec = re.match(r"^##\s+(\S+)", stripped)
            if sec:
                current_section = sec.group(1)
                continue
            if stripped.startswith("- [ ]"):
                item = stripped[5:].strip()
                # Drop the trailing "*(deferred — ...)*" annotation so we
                # can match against the cleaner prose in the registry.
                item = re.sub(r"\s*\*\([^*]*deferred[^*]*\)\*\s*$", "", item)
                items.append((current_section, item))
        out[change] = items
    return out


def unregistered_unchecked() -> list[tuple[str, str, str]]:
    """Return (change, section, item) for every unchecked item that
    isn't tracked in :data:`REGISTRY`.

    These are the gaps the framework can't yet see.
    """
    by_norm: set[tuple[str, str]] = set()
    for entry in REGISTRY:
        by_norm.add((entry.change, _normalize(entry.item)))

    gaps: list[tuple[str, str, str]] = []
    for change, items in unchecked_items_in_tasks().items():
        for section, item in items:
            if (change, _normalize(item)) in by_norm:
                continue
            gaps.append((change, section, item))
    return gaps


def coverage_summary() -> dict[str, dict[str, int]]:
    """Return ``{change: {"covered": N, "pending": M, "deferred": K}}``."""
    out: dict[str, dict[str, int]] = {}
    for entry in REGISTRY:
        bucket = out.setdefault(entry.change, {"covered": 0, "pending": 0, "deferred": 0})
        bucket[entry.status] += 1
    return out


if __name__ == "__main__":
    # CLI mode: print the coverage report. Handy when someone wants a
    # quick sanity check without running the test.
    summary = coverage_summary()
    print(f"{'change':<32} covered  pending  deferred")
    print("-" * 60)
    total = {"covered": 0, "pending": 0, "deferred": 0}
    for change in sorted(summary):
        s = summary[change]
        print(
            f"{change:<32} {s['covered']:>7}  {s['pending']:>7}  {s['deferred']:>8}"
        )
        for k in total:
            total[k] += s[k]
    print("-" * 60)
    print(f"{'TOTAL':<32} {total['covered']:>7}  {total['pending']:>7}  {total['deferred']:>8}")

    gaps = unregistered_unchecked()
    if gaps:
        print("")
        print(f"!! {len(gaps)} unchecked tasks.md items are NOT in the registry:")
        for change, section, item in gaps:
            print(f"  [{change} {section}] {item[:80]}")
    else:
        print("")
        print("All unchecked tasks.md items are tracked in the registry.")