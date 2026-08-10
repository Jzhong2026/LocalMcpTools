# LocalMcpTools 鈥?E2E Test Plan

> Status: **draft v1**
> Last updated: 2026-08-10
> Scope: end-to-end coverage of all 7 OpenSpec changes against the
> real running server on Windows. Supplements (does **not** replace)
> the existing 341 unit / integration tests.

---

## 1. Why a separate e2e layer

The unit suite exercises modules in isolation. The existing
integration suite (`tests/integration/`) only covers **two** slices:

| File | What it does |
|---|---|
| `test_managed_process_lifecycle.py` | managed-process state machine in-process |
| `test_workspace_inspect_stdio.py` | 7 tools over stdio with a fixture workspace |

What's missing: full-surface round-trips through **stdio + HTTP**,
real Windows artifacts (ports, windows, OCR text), **cross-tool
flows** (register 鈫?inspect 鈫?search 鈫?build 鈫?tail log),
**multi-client concurrency**, **policy enforcement** on real tool
calls, **packaging smoke** (installed task, autostart .lnk), and
**reboot persistence**. That's the gap this plan closes.

## 2. Goals & non-goals

**Goals**

1. Every OpenSpec DoD has at least one e2e proof.
2. Every one of the **40 MCP tools** is invoked at least once
   against a real server process.
3. Every one of the **21 HTTP control endpoints** is exercised.
4. Every **deny rule** has a positive proof that firing it triggers
   approval / denial.
5. Multi-client (2 stdio + 1 HTTP) concurrency is proven not to
   corrupt audit, artifact ACLs, or background-process state.
6. The install / uninstall flows are exercised against a real
   Windows Task Scheduler and Startup folder.

**Non-goals**

- Performance / load testing beyond the concurrency scenarios.
- Model-quality assertions on agent output.
- macOS / Linux 鈥?the project is Windows-only by spec.
- OCR accuracy benchmarks (separate manual effort; recorded in
  `ui-automation-and-ocr/tasks.md`).

## 3. Environment

```
OS:    Windows 10 / 11 (en-US + zh-CN locales for encoding probes)
User:  standard user (UAC off) + one admin user for the reboot test
Shell: PowerShell 7 + Windows PowerShell 5.1 (run-once probes)
Python: 3.11+ in a clean venv matching requirements.txt
LocalMcpTools: built from current `main` branch
Browsers: Edge + Chrome for the UI tests
```

## 4. Architecture of the e2e suite

```
tests/
鈹溾攢鈹€ integration/          鈫?existing in-process tests (kept)
鈹斺攢鈹€ e2e/                  鈫?NEW: this plan
    鈹溾攢鈹€ conftest.py                shared fixtures + helpers
    鈹溾攢鈹€ _clients/                  reusable stdio + http clients
    鈹溾攢鈹€ _fixtures/                 workspace / fixture / cleanup helpers
    鈹溾攢鈹€ _report/                   junit + html + screenshot collector
    鈹溾攢鈹€ test_00_boot_stdio.py
    鈹溾攢鈹€ test_01_tool_surface_stdio.py
    鈹溾攢鈹€ test_02_tool_surface_http.py
    鈹溾攢鈹€ test_03_workspace_lifecycle.py
    鈹溾攢鈹€ test_04_managed_process.py
    鈹溾攢鈹€ test_05_policy_enforcement.py
    鈹溾攢鈹€ test_06_artifact_redaction.py
    鈹溾攢鈹€ test_07_http_control_plane.py
    鈹溾攢鈹€ test_08_concurrent_clients.py
    鈹溾攢鈹€ test_09_angular_ui.py
    鈹溾攢鈹€ test_10_ui_automation_windows.py
    鈹溾攢鈹€ test_11_ocr_round_trip.py
    鈹溾攢鈹€ test_12_install_uninstall.py
    鈹溾攢鈹€ test_13_reboot_persistence.py        鈫?manual gate, not in CI
    鈹斺攢鈹€ test_99_dod_checklist.py             鈫?mirrors each change's DoD
```

The directory is excluded from the default pytest run by default
(`addopts = -m "not e2e"`); CI on `push to main` opts in via
`pytest -m e2e`.

## 5. Pytest markers

| Marker | Meaning | Run by |
|---|---|---|
| `e2e` | new e2e suite | CI nightly + on-demand |
| `e2e_slow` | > 60s | manual only |
| `e2e_manual` | needs user / reboot | manual checklist only |
| `e2e_admin` | needs admin shell | local dev only |

## 6. Shared fixtures

| Fixture | Purpose |
|---|---|
| `live_server` | boots `python -m localmcptools --http` in a tmp LMCP_DATA_DIR, returns base URL + csrf + token |
| `stdio_session` | MCP `ClientSession` over stdio pointed at the same data dir |
| `http_client` | httpx async client w/ Bearer + csrf cookie pre-wired |
| `fixture_workspace` | a temp dir with pyproject + app.py + build.log |
| `fixture_long_log` | 50 MB log file for tail/read_range streaming tests |
| `cleanup_backgrounds` | autouse: stops every managed process at session teardown |
| `cleanup_db` | autouse: drops the tmp audit.sqlite at session end |
| `record_screenshot` | helper: saves a Playwright screenshot to `_report/` on failure |

## 7. Scenarios per area

### 7.1 Boot (test_00_boot_stdio)

- Spawn stdio with `python -m localmcptools`, assert initialize() succeeds, `server.initialized` event seen.
- Spawn HTTP with `--http --port 0`, parse `server.json`, assert `/api/status` 200 with matching csrf + token.
- Cold-boot: assert `audit.sqlite` is created with schema_version=5 on first call.
- Tear down: assert `localmcptools stop` removes `server.json` and frees the port.

### 7.2 Tool surface 鈥?stdio (test_01)

For **every one of the 40 tools**:

1. Call once with the documented minimal arguments.
2. Assert envelope shape: `ok`, `meta.tool`, `meta.run_id`,
   `meta.audit_id`, `meta.profile`, `meta.next_actions`,
   `data.summary`.
3. Cross-check `audit.sqlite` has a matching row.
4. Verify redaction: feed a known bearer token / API key in inputs;
   confirm it never appears in `data`, `meta`, or the raw log.

Exceptions:

- Tools with **destructive defaults** (e.g. `shell.run_command` with
  a deny rule) 鈥?exercise the deny path, not the success path.

### 7.3 Tool surface 鈥?HTTP (test_02)

Same matrix as 7.2 but routed through `/mcp` with Bearer auth.
Prove:

- Missing Bearer 鈫?401.
- Wrong Bearer 鈫?401.
- Expired Bearer 鈫?401.
- Origin not in allowlist 鈫?403.
- Origin in allowlist but missing CSRF 鈫?403.
- Origin in allowlist + CSRF 鈫?200 + valid envelope.

### 7.4 Workspace lifecycle (test_03)

End-to-end multi-step:

```
register 鈫?inspect 鈫?search_text 鈫?fs.read_range 鈫?
output.tail(of build.log) 鈫?workspace.build (or skip on no make) 鈫?
output.search 鈫?workspace.list
```

Asserts:

- `workspace.inspect` output schema stable across the 4 project
  types (pyproject / package.json / csproj / none).
- `next_actions` present whenever a step fails (e.g. `build` exits
  non-zero).
- `audit.sqlite` rows for each step in correct order, linked by
  `run_id` for the same logical session.
- Artifact created for any output > 64KB.

### 7.5 Managed process (test_04)

- `process.start_dev_server` with a real, harmless preset
  (`python -m http.server 0` in fixture dir).
- Poll `process.get_status` until `running`.
- `process.find_by_port` finds the bound port.
- `process.list_managed` returns exactly one entry.
- Read the live log via `output.tail` while it's still writing.
- `process.stop_managed` and confirm port is freed and `list_managed` is empty.
- Failure path: start a server with a deliberately bad command,
  assert error_code, ensure no orphan process leaks (verified by
  psutil tree scan).

### 7.6 Policy enforcement (test_05)

For **every deny rule in `policy/builtin_rules.json`**:

1. Construct a tool call that would match (e.g.
   `shell.run_command` with `format c:` for `format-volume`).
2. Assert the call is denied / requires approval.
3. Assert `audit.sqlite` row has `rule_hit` populated.
4. Approve via `policy.approve` (when approval UI exists) or by
   patching `policy_rules.json` to whitelist the call, re-run,
   assert it now succeeds.
5. Assert the same hit on a non-allowed profile (`workspace_exec`
   vs `managed_process`) is still denied.

### 7.7 Artifact redaction & ACL (test_06)

- Run `shell.run_command` that prints a fake Bearer token.
- Assert raw artifact never contains the token (grep the artifact
  file via OS open).
- Assert artifact ACL denies a low-priv process token from
  reading it (use `icacls` to inspect).
- 50 MB fixture 鈫?assert `output.tail` does not load all bytes
  (memory profiler check).

### 7.8 HTTP control plane (test_07)

| Endpoint | Test |
|---|---|
| `GET  /status` | 200 + expected JSON shape |
| `GET  /audit` | paginates, filter by profile, filter by tool |
| `GET  /audit/{id}` | 200; 404 for unknown id |
| `GET  /audit/{id}/log` | streams log artifact; 403 for unauth |
| `GET  /settings` | returns full schema |
| `POST /settings` | validates and persists |
| `GET  /rules` | lists every rule with hit stats |
| `PATCH /rules/{id}` | toggles enabled + records audit |
| `POST /rules/reload` | reloads from disk |
| `GET  /backgrounds` | lists live managed processes |
| `POST /backgrounds/{id}/stop` | stops and removes row |
| `GET  /mcp-config-snippet` | returns valid JSON for codebuddy + copilot |
| `POST /shutdown` | server actually exits |
| `GET  /windows` | lists at least one window on a real desktop session |
| `POST /windows/authorize` | adds a row to `authorized_windows` |
| `POST /windows/{id}/revoke` | removes the row |
| `POST /ui/get_ui_tree` | returns tree for an authorized window |
| `POST /ui/find_element` | finds by automationId / name |
| `POST /ui/screenshot_window` | returns PNG bytes + content-type |
| `POST /ocr/ocr_region` | returns text for a real region |
| `POST /ocr/assert_text` | asserts text presence / absence |

Every endpoint is also exercised against:

- Wrong / missing Bearer 鈫?401
- Wrong / missing CSRF 鈫?403
- Wrong Origin 鈫?403
- After `POST /shutdown` 鈫?connection refused

### 7.9 Concurrent clients (test_08)

- 2 stdio sessions + 1 HTTP session, all pointed at the same
  `LMCP_DATA_DIR`.
- All three call `environment.get` 50 times in interleaved order.
- Assert: every audit row is recorded, every `run_id` is unique,
  no row has mismatched `audit_id` 鈫?log path.
- Start the same managed dev server from 3 sessions concurrently 鈥?
  only one wins, the other two get `process_already_running`.
- Tail a shared log file from 3 sessions 鈥?all see the same
  bytes.

### 7.10 Angular UI (test_09)

Playwright-driven:

- `dashboard` page 鈫?status card renders, audit count > 0 after we
  make one call.
- `audit-list` page 鈫?table populated, click a row 鈫?drill-down
  renders envelope + meta + log handle.
- `settings` page 鈫?form pre-filled, change one field, refresh,
  field persists.
- `rules-list` page 鈫?toggle one rule, assert row count +
  reload.
- `mcp-config` page 鈫?both snippets present, copy button works.
- `automation` page 鈫?window list, authorize / revoke, tree
  viewer, OCR preview.
- All pages: screenshot on failure, save to `_report/`.

### 7.11 UI automation 鈥?Windows (test_10)

Real desktop session:

- `ui.list_windows` returns 鈮?1 window.
- `ui.authorize_window` on Calculator (or another always-present
  app) succeeds.
- `ui.get_ui_tree` returns a tree with `> 0` nodes.
- `ui.find_element` by name finds a known button (e.g.
  "Equals" in Calculator).
- `ui.click_element` + `ui.act_and_verify` round-trips and the
  verify predicate succeeds.
- `ui.type_text` types into Notepad, `output.tail` (which we'd
  attach to Notepad window changes) reflects it.
- Cleanup: `ui.revoke_window` on every window we authorized.

### 7.12 OCR round-trip (test_11)

- `screenshot_window` of Calculator 鈫?PNG.
- `ocr.ocr_region` on a known region returns the expected digit
  string (e.g. "0" on the display).
- `ocr.find_text` against a saved screenshot finds the string.
- `ocr.assert_text` confirms and returns a confidence score.
- Screenshot the result, attach to report.

### 7.13 Install / uninstall (test_12)

- `python -m localmcptools install --method scheduled-task` 鈫?
  `Get-ScheduledTask` lists `LocalMcpTools`.
- Stop the in-test server, run it via the task, confirm it boots.
- `python -m localmcptools uninstall` 鈫?task gone, server.json
  gone, port freed.
- Same matrix for `--method startup-folder` (the .lnk exists,
  is removed).

### 7.14 Reboot persistence (test_13) 鈥?manual gate

- Install via scheduled-task with `--auto-start`.
- Reboot.
- Confirm task re-launched the server, `server.json` is fresh,
  `/api/status` is 200.
- This test runs **only** on the user's machine after each
  release candidate, **not** in CI. Result is recorded in
  `docs/reports/`.

### 7.15 DoD checklist (test_99)

A single parametric test that loops over each OpenSpec change's
DoD items, asserts the corresponding e2e test exists and passed.
Fails the build if any DoD item lacks e2e coverage.

## 8. Tooling & dependencies

Add to `requirements-dev.txt`:

```
playwright==1.55.*
psutil==7.*
```

Add to `pyproject.toml`:

```
[project.optional-dependencies]
e2e = ["playwright", "psutil"]

[tool.pytest.ini_options]
markers = [
    "e2e: end-to-end suite",
    "e2e_slow: > 60s",
    "e2e_manual: needs user / reboot",
    "e2e_admin: needs admin shell",
]
addopts = "-m 'not e2e'"
```

Add a single `scripts/run_e2e.ps1` that:

1. Builds the package.
2. Creates a clean `LMCP_DATA_DIR`.
3. Runs `pytest -m e2e --junitxml=_report/junit.xml`.
4. On failure, opens the latest `_report/screenshot_*.png` per
   Playwright test.

## 9. Sequencing

| Phase | Area | Days | Blocking |
|---|---|---|---|
| 1 | Fixtures + helpers + stdio boot | 1 | 鈥?|
| 2 | `test_01` (40 tools over stdio) | 2 | phase 1 |
| 3 | `test_07` (control plane) | 2 | phase 2 |
| 4 | `test_05` policy enforcement | 1 | phase 2 |
| 5 | `test_06` artifact redaction | 1 | phase 2 |
| 6 | `test_03` workspace lifecycle | 1 | phase 2 |
| 7 | `test_04` managed process | 1 | phase 2 |
| 8 | `test_02` HTTP /mcp | 2 | phases 3+4 |
| 9 | `test_08` concurrency | 1 | phase 2 |
| 10 | `test_09` Angular UI | 3 | phase 3, needs playwright |
| 11 | `test_10` UI automation | 2 | needs desktop session |
| 12 | `test_11` OCR | 1 | phase 11 |
| 13 | `test_12` install/uninstall | 1 | needs clean test VM or reset |
| 14 | `test_13` reboot persistence | manual | needs user |
| 15 | `test_99` DoD linkage | 1 | all of the above |

Total: **~17 working days** of focused engineering time. The
non-CI gates (test_13 + live accuracy spikes) are tracked but not
on the critical path.

## 10. CI integration**
- **Nightly** at 02:00 UTC on Windows runner: `pytest -m "e2e and not e2e_manual and not e2e_admin"`.
- **On PR** to `main`: smoke subset only 鈥?`pytest -m "e2e and (test_00 or test_02_http_smoke or test_07_smoke)"` (鈮?6 tests, < 5 min).
- **Manual gates** (test_13 + UI accuracy) 鈥?fail-open in CI, blocked for release tagging.

## 11. Reporting**

- `_report/junit.xml` 鈥?machine-readable per test.
- `_report/index.html` 鈥?generated by `pytest-html` from the junit XML.
- `_report/screenshots/` 鈥?captured on any Playwright failure.
- `_report/profile.svg` 鈥?flamegraph of the slowest 20 e2e tests.

## 12. Risks & mitigations**

| Risk | Mitigation |
|---|---|
| Real Windows desktop session required for tests 10 / 11 | Use the same VM the user has; gate behind `e2e_admin`; document the manual run |
| Reboot test cannot run in CI | Tracked separately as `test_13`, manual only |
| OCR quality varies by Windows build | Treat as informational; record baseline in `ui-automation-and-ocr/design.md` |
| Playwright browser drift | Pin `playwright==1.55.*` and commit the bundled browsers |
| `icacls` semantics differ on non-NTFS or OneDrive folders | Run all tests in a plain NTFS temp dir |
| Flaky timing on managed process start | Wrap `process.get_status` polls with `tenacity` retry |

## 13. Definition of done (for the e2e suite itself)**

1. `pytest -m e2e` runs clean on the user's dev machine.
2. CI nightly shows zero flakes over 7 consecutive runs.
3. `test_99_dod_checklist` passes for every OpenSpec change.
4. A new contributor can run `scripts/run_e2e.ps1` on a fresh
   Windows VM and get green in < 30 min.
5. The release checklist includes a line: "manual reboot test
   recorded in `docs/reports/<date>.md`".
---


## 14. Progress

| Phase | Status | Tests | Notes |
|---|---|---|---|
| 1. Fixtures + helpers + stdio boot | **done** | 8 | commit d4fe2f8 |
| 15. DoD linkage framework | **done** | 10 | this commit |
| 2. `test_01` (40 tools over stdio) | not started | 0 | next up |
| 3. `test_07` (control plane) | not started | 0 | |
| 4. `test_05` policy enforcement | not started | 0 | |
| 5. `test_06` artifact redaction | not started | 0 | |
| 6. `test_03` workspace lifecycle | not started | 0 | |
| 7. `test_04` managed process | not started | 0 | |
| 8. `test_02` HTTP /mcp | not started | 0 | |
| 9. `test_08` concurrency | not started | 0 | |
| 10. `test_09` Angular UI | not started | 0 | needs playwright |
| 11. `test_10` UI automation | not started | 0 | needs desktop session |
| 12. `test_11` OCR | not started | 0 | |
| 13. `test_12` install/uninstall | not started | 0 | |
| 14. `test_13` reboot persistence | manual gate | — | |

### DoD coverage (after phase 15)

```
change                           covered  pending  deferred
------------------------------------------------------------
angular-ui-foundation                  5       11         0
bootstrap-mcp-server                   2        0         4
core-shell-and-audit                   4        0         1
extended-tools-and-packaging           1        7         7
policy-and-safety                      1        2         2
ui-automation-and-ocr                  1       13         5
------------------------------------------------------------
TOTAL                                 14       33        19
```

The framework reports these numbers on every `pytest -m e2e` run
via `test_unregistered_unchecked_is_documented`. Once a phase closes,
the relevant entries flip from `pending` to `covered` (and
the framework refuses to accept a `test_id` that no longer exists).

### Bugs found and fixed during phase 1 (commit d4fe2f8)

* `cli.py _cmd_stop` used `taskkill /PID /T` without `/F`. Windows
  rejects graceful kill of python processes that hold a console
  handle or sit in a JOB object. Added `/F` and `unlink(server.json)`
  after success so subsequent `start` doesn't trip the staleness
  guard.
* `subprocess.Popen.pid` on Python 3.14 Windows is unreliable (the
  handle doesn't track the actual child pid). Fixture uses
  `meta["pid"]` from `server.json` instead.
* `/api/status` returns its dict directly (no universal envelope).
  Test updated to assert against `body["server"]["transport"]` and
  `body["server"]["audit_db_initialised"]`.
* Schema version lives in a `schema_version` table, not
  `PRAGMA user_version`. Test updated.

### How the framework works

* `tests/e2e/dod_registry.py` declares every DoD item with
  `(change, section, item, status, test_id?, reason?)`.
* `python -m tests.e2e.dod_registry` prints a coverage report from
  the CLI without running pytest.
* `tests/e2e/test_99_dod_checklist.py` is a parametric test that:
  - confirms every `covered` entry points at a real pytest id
    (errors — not fails — when a test was renamed and the registry
    wasn't updated, so coverage gaps are loud),
  - validates registry shape (every `covered` has a `test_id`,
    every `deferred` has a `reason`, no unknown statuses, no
    duplicates within a (change, section) pair),
  - surfaces `pending` and unregistered items in test output so CI
    dashboards can show progress,
  - emits one test per change asserting "≥ 1 covered entry" so
    every change must have at least one e2e proof in the registry.
