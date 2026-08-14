"""Quick smoke test against the running HTTP /mcp - used as the
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
from pathlib import Path
from typing import Any, cast

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import TextContent

DATA_DIR = Path(os.environ["APPDATA"]) / "LocalMcpTools"

# Preferred summary keys in priority order. The first one present
# in the response's `data` dict becomes the one-line summary. Falls
# back to a JSON dump if none match.
_SUMMARY_KEYS: tuple[str, ...] = (
    "summary",
    "count",
    "total",
    "ok_count",
    "name",
    "workspace_id",
    "os",
    "python",
    "shell",
)

# Soft cap on the per-call body print so a long response doesn't
# bury the next block. The first-line summary stays short by design.
_BODY_PRINT_CHARS = 400


def _load_token() -> tuple[str, int]:
    cfg: dict[str, Any] = json.loads((DATA_DIR / "server.json").read_text(encoding="utf-8"))
    return cfg["csrf_token"], int(cfg["port"])


async def _call(session: ClientSession, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(tool, arguments=args)
    # MCP tools return a list of content blocks; we only ever produce
    # text blocks (server.json tools don't embed images/audio). Narrow
    # the union so mypy stops flagging result.content[0].text.
    block = result.content[0]
    if not isinstance(block, TextContent):
        raise TypeError(f"unexpected non-text content from {tool}: {type(block).__name__}")
    return cast(dict[str, Any], json.loads(block.text))


def _print_block(label: str, body: dict[str, Any]) -> None:
    """One block per call: label + ok flag + 1-line summary + truncated body.

    The shape is what the regression diff in
    tests/e2e/test_99_golden_smoke.py compares against the baseline.
    """
    ok = body.get("ok")
    summary = ""
    data = body.get("data")
    if ok and isinstance(data, dict):
        for k in _SUMMARY_KEYS:
            if k in data:
                summary = f"{k}={data[k]!r}"
                break
    if not summary:
        fallback = data or body.get("error") or {}
        summary = json.dumps(fallback)[:120]
    line = f"[{label}] ok={ok}  {summary}"
    print(line)
    print("  " + json.dumps(body, ensure_ascii=False)[:_BODY_PRINT_CHARS].replace("\n", " "))
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
            _print_block(
                "runtime.detect_runtime", await _call(session, "runtime.detect_runtime", {})
            )
            _print_block("diagnostics.collect", await _call(session, "diagnostics.collect", {}))
            _print_block(
                "process.list_listening_ports",
                await _call(session, "process.list_listening_ports", {}),
            )
            _print_block("process.list_managed", await _call(session, "process.list_managed", {}))

            # 2. Workspace lifecycle on the repo itself
            repo = str(Path(__file__).resolve().parent.parent)
            reg = await _call(session, "workspace.register", {"path": repo})
            _print_block("workspace.register (repo root)", reg)
            reg_data = reg.get("data")
            ws_id = reg_data.get("workspace_id") if isinstance(reg_data, dict) else None

            if ws_id:
                _print_block(
                    "workspace.inspect",
                    await _call(session, "workspace.inspect", {"workspace_id": ws_id}),
                )
                _print_block(
                    "workspace.search_text 'Mavis'",
                    await _call(
                        session,
                        "workspace.search_text",
                        {"workspace_id": ws_id, "pattern": "Mavis", "max_results": 3},
                    ),
                )
                _print_block(
                    "fs.read_range README.md (first 10 lines)",
                    await _call(
                        session,
                        "fs.read_range",
                        {
                            "workspace_id": ws_id,
                            "path": "README.md",
                            "start_line": 0,
                            "end_line": 10,
                        },
                    ),
                )
            else:
                print(
                    "[workspace.inspect] SKIPPED — workspace.register did not return a workspace_id"
                )
                print("[workspace.search_text] SKIPPED")
                print("[fs.read_range] SKIPPED")
                print()

            # 3. error path - expect ok=False, do not crash
            _print_block(
                "workspace.inspect (bad id) -> expect ok=false",
                await _call(session, "workspace.inspect", {"workspace_id": "nope-not-real"}),
            )
    print("== done ==")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
