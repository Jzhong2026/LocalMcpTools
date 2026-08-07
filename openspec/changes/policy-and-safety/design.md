# Design: policy-and-safety

## Module layout (additions on top of change-1 + change-2)

```text
src/localmcptools/
├── policy/
│   ├── __init__.py
│   ├── profile.py            # Profile enum + per-workspace current
│   ├── approval.py           # approval table ops + digest
│   ├── digest.py             # sha256(tool + canonical_args + ws + profile)
│   └── authorize.py          # check(profile, capability) -> NeedApproval | Allow | Deny
├── safety/
│   ├── __init__.py
│   ├── rules.py              # engine: load + match + hot reload
│   └── builtin/              # 10 *.json files
├── execution/
│   ├── __init__.py
│   ├── runner.py             # async subprocess + timeout + tee
│   ├── concurrency.py        # Semaphore + queue metrics
│   ├── encoding.py           # chardet decode fallback
│   └── powershell.py         # PS-arg builder per REQ-EXEC-1
└── tools/
    ├── shell.py              # shell.run_command
    └── workspace.py          # + run_test / build / lint / git_status

tests/
├── unit/
│   ├── test_profile.py
│   ├── test_approval.py
│   ├── test_digest.py
│   ├── test_rules_engine.py
│   ├── test_concurrency.py
│   └── test_runner.py
└── integration/
    ├── test_shell_run.py
    └── test_workspace_preset.py
```

## Profile + approval flow

```text
call arrives
   │
   ▼
authorize.check(workspace.profile, tool.required_capability)
   │        │
   │        ├── Deny ────────────► return insufficient_capability
   │        ├── Allow ───────────► run
   │        └── NeedApproval ────► create approval row → return approval_required
   │
   ▼
rules.match(cmd, args)  (defense in depth; only run for shell tools)
   │     │
   │     ├── critical hit ──► return denied_by_rule (approval NOT consumed)
   │     └── no hit ────────► continue
   │
   ▼
concurrency.acquire() with queue_timeout
   │
   ▼
runner.run(cmd, cwd, env, timeout)
   │
   ▼
artifacts.persist(stdout, stderr)  via redact
   │
   ▼
audit.record_finish(... output_handle, approval_id, ...)
   │
   ▼
approval.consume(approval_id) if a matching pending approval was used
```

## Approval digest

```python
# policy/digest.py
def digest_for(tool: str, args: dict, workspace_id: str, profile: str) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    payload = f"{tool}|{workspace_id}|{profile}|{canonical}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

The digest is computed at approval-request time and again at call time;
mismatch → `approval_digest_mismatch`. The canonical form strips comments
and uses sorted keys; both sides must produce the same bytes.

## Built-in rule file format

Same as `docs/implementation-plan.md` §4.1:

```jsonc
{
  "id": "block-format-volume",
  "description": "拒绝任何磁盘格式化操作",
  "severity": "critical",
  "default_action": "block",
  "allow_override": false,         // critical rules always false
  "match": { "type": "any_of", "rules": [
    { "cmd_name": "Format-Volume" },
    { "cmd_name": "format" },
    { "cmd_name": "diskpart" },
    { "cmd_name": "cipher", "args_match": "/w" }
  ] },
  "suggestion": "..."
}
```

## Concurrency model

```python
# execution/concurrency.py
class ConcurrencyGate:
    def __init__(self, max_concurrent: int = 4, queue_timeout_ms: int = 600_000):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._queue_depth = 0
        self._active = 0
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def slot(self):
        deadline = monotonic() + self._queue_timeout_ms / 1000
        async with self._lock:
            self._queue_depth += 1
        try:
            await asyncio.wait_for(self._sem.acquire(),
                                   timeout=max(0, deadline - monotonic()))
        except asyncio.TimeoutError:
            raise QueueTimeout()
        async with self._lock:
            self._queue_depth -= 1
            self._active += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active -= 1
            self._sem.release()
```

The `active` and `queue_depth` counters are exposed for the future UI
(deferred to change-5).

## Runner contract

```python
# execution/runner.py
async def run(cmd: str, *, cwd: str, env: dict, timeout_ms: int) -> RunResult:
    proc = await asyncio.create_subprocess_exec(
        *shlex.split(cmd), cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=_windows_creation_flags(),  # suppress window
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout_ms / 1000)
    except asyncio.TimeoutError:
        await _terminate_tree(proc)
        raise TimedOut()
    return RunResult(exit_code=proc.returncode, stdout=stdout, stderr=stderr)
```

`_terminate_tree` uses `taskkill /T /F` on Windows; on others, `proc.terminate()` → `kill()`.

## SQLite schema (additions; schema_version -> 3)

```sql
CREATE TABLE IF NOT EXISTS approvals (
    id                   TEXT PRIMARY KEY,
    workspace_id         TEXT NOT NULL,
    requested_capability TEXT NOT NULL,
    action_digest        TEXT NOT NULL,
    status               TEXT NOT NULL,  -- pending|approved|consumed|expired|cancelled
    created_at           INTEGER NOT NULL,
    expires_at           INTEGER NOT NULL,
    approved_at          INTEGER,
    consumed_at          INTEGER,
    decided_by           TEXT             -- 'user'|'timeout'|'rule_block'|'server'
);

CREATE INDEX IF NOT EXISTS idx_approvals_ws_status ON approvals(workspace_id, status);

-- rule_hit_stats already in change-2 spec; finalized here.
CREATE TABLE IF NOT EXISTS rule_hit_stats (
    rule_id         TEXT PRIMARY KEY,
    hit_count       INTEGER NOT NULL DEFAULT 0,
    last_hit_at     INTEGER,
    last_hit_cmd    TEXT
);
```

## Config additions

```jsonc
{
  "security": {
    "approval_ttl_seconds": 600
  },
  "shell": {
    "default_timeout_ms": 120000,
    "max_timeout_ms": 3600000,
    "max_concurrent": 4,
    "queue_timeout_ms": 600000,
    "powershell_args": ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
  },
  "rules": {
    "builtin_dir": "safety/builtin",
    "custom_dir": "rules.d/custom",
    "reload_endpoint": "/api/rules/reload"
  }
}
```

## New dependencies

None new — `psutil` was already in `requirements.txt` for change-1.

## Out-of-scope reminders

- The approval **request UI** is not built here; only the data model.
- The `interactive_ui` profile exists in the registry but no tool uses it.
- The runner does not yet implement Job Object; that lands in change-4.