"""Quick smoke test against the running HTTP /mcp — used as the
golden-set baseline right after ``apply-mcp-config.ps1``.

The user runs this any time they want to confirm the integration is
healthy. Each call's full envelope (ok, data, meta, error) is
written to stdout in a tagged block so it's easy to compare
against a known-good reference later.

Usage::

    .venv\\Scripts\\python.exe scripts\\golden-smoke.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DATA_DIR = Path(os.environ["APPDATA"]) / "LocalMcpTools"


def _load_token() -> str:
    cfg = json.loads((DATA_DIR / "server.json").read_text(encoding="utf-8"))
    return cfg["csrf_token"], int(cfg["port"])


async def _call(session: ClientSession, tool: str, args: dict) -> dict:
    result = await session.call_tool(tool, arguments=args)
    return json.loads(result.content[0].text)


def _print_block(label: str, body: dict) -> None:
    """One block per call, with the label, ok flag, and a 1-line summary
    so the golden set stays scannable."""
    ok = body.get("ok")
    summary = ""
    if ok and isinstance(body.get("data"), dict):
        # pick a couple of stable keys
        for k in ("summary", "count", "total", "ok_count", "name",
                  "workspace_id", "os", "python", "shell"):
            if k in body["data"]:
                summary = f"{k}={body['data'][k]!r}"
                break
    if not summary:
        summary = json.dumps(body.get("data") or body.get("error") or {})[:120]
    line = f"[{label}] ok={ok}  {summary}"
    print(line)
    print("  " + json.dumps(body, ensure_ascii=False)[:400].replace("\n", " "))
    print()


async def main() -> int:
    token, port = _load_token()
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": f"Bearer {token}"}
    print(f"== golden-smoke against {base} ==\n")

    async with streamablehttp_client(url=f"{base}/mcp/", headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            # 1. Lightweight read-only tools
            _print_block("environment.get", await _call(session, "environment.get", {}))
            _print_block("runtime.detect_runtime", await _call(session, "runtime.detect_runtime", {}))
            _print_block("diagnostics.collect", await _call(session, "diagnostics.collect", {}))
            _print_block("process.list_listening_ports", await _call(session, "process.list_listening_ports", {}))
            _print_block("process.list_managed", await _call(session, "process.list_managed", {}))

            # 2. Workspace lifecycle on the repo itself
            repo = str(Path(__file__).resolve().parent.parent)
            reg = await _call(session, "workspace.register", {"path": repo})
            _print_block("workspace.register (repo root)", reg)
            ws_id = reg.get("data", {}).get("workspace_id")

            if ws_id:
                _print_block(
                    "workspace.inspect",
                    await _call(session, "workspace.inspect", {"workspace_id": ws_id}),
                )
                _print_block(
                    "workspace.search_text 'Mavis'",
                    await _call(
                        session, "workspace.search_text",
                        {"workspace_id": ws_id, "pattern": "Mavis", "max_results": 3},
                    ),
                )
                _print_block(
                    "fs.read_range README.md (first 10 lines)",
                    await _call(
                        session, "fs.read_range",
                        {"workspace_id": ws_id, "path": "README.md", "start_line": 0, "end_line": 10},
                    ),
                )

            # 3. error paths — these should fail cleanly, not crash
            _print_block(
                "workspace.inspect (bad id) -> expect ok=false",
                await _call(session, "workspace.inspect", {"workspace_id": "nope-not-real"}),
            )
    print("== done ==")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
