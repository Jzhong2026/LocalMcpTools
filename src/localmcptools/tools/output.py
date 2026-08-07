"""``output.*`` tools — page through an artifact handle.

Three tools, all thin wrappers over :mod:`localmcptools.persistence.artifacts`:

- :func:`output_tail` — last ``n`` lines.
- :func:`output_read_range` — lines ``[start, end)``.
- :func:`output_search` — regex matches inside the artifact.

An ACL re-check happens inside the artifact module on every read; we
just forward the handle. Unknown handles return ``artifact_not_found``
so the agent can tell "wrong handle" from "file vanished".
"""

from __future__ import annotations

from typing import Any, cast

from ..persistence.artifacts import (
    ArtifactNotFound,
)
from ..persistence.artifacts import (
    read_range as _read_range,
)
from ..persistence.artifacts import (
    search as _search,
)
from ..persistence.artifacts import (
    tail as _tail,
)
from ._errors import fail


def _check_handle(handle: Any, tool: str) -> str:
    if not isinstance(handle, str) or not handle:
        fail(
            code="invalid_args",
            message="`handle` must be a non-empty `art://...` string",
            tool=tool,
            audit_id="pending",
            run_id="pending",
            suggestion="use the `meta.output_handle` returned by the producing tool",
        )
    result: str = handle
    return result


def output_tail(args: dict[str, Any]) -> Any:
    handle = _check_handle(args.get("handle"), "output.tail")
    try:
        n = int(args.get("n") or 200)
    except (TypeError, ValueError):
        n = 200
    if n < 1:
        n = 1
    try:
        lines = _tail(handle, n=n)
    except ArtifactNotFound as exc:
        fail(
            code="artifact_not_found",
            message=str(exc),
            tool="output.tail",
            audit_id="pending",
            run_id="pending",
            suggestion="verify the handle came from the producing tool's meta.output_handle",
        )
    return {"lines": lines, "handle": handle, "evidence_handle": handle}


def output_read_range(args: dict[str, Any]) -> Any:
    handle = _check_handle(args.get("handle"), "output.read_range")
    try:
        start = int(args.get("start_line") or 0)
        end = int(args.get("end_line") or 0)
    except (TypeError, ValueError):
        fail(
            code="invalid_args",
            message="`start_line` and `end_line` must be integers",
            tool="output.read_range",
            audit_id="pending",
            run_id="pending",
        )
    if start < 0 or end < 0:
        fail(
            code="invalid_args",
            message="line numbers must be >= 0",
            tool="output.read_range",
            audit_id="pending",
            run_id="pending",
        )
    try:
        lines = _read_range(handle, start, end)
    except ArtifactNotFound as exc:
        fail(
            code="artifact_not_found",
            message=str(exc),
            tool="output.read_range",
            audit_id="pending",
            run_id="pending",
        )
    return {"lines": lines, "handle": handle}


def output_search(args: dict[str, Any]) -> Any:
    handle = _check_handle(args.get("handle"), "output.search")
    pattern_raw = args.get("pattern")
    if not isinstance(pattern_raw, str) or not pattern_raw:
        fail(
            code="invalid_args",
            message="`pattern` must be a non-empty regex",
            tool="output.search",
            audit_id="pending",
            run_id="pending",
        )
    pattern = cast("str", pattern_raw)
    try:
        max_results = int(args.get("max_results") or 200)
    except (TypeError, ValueError):
        max_results = 200
    if max_results < 1:
        max_results = 200
    try:
        matches = _search(handle, pattern, max_results=max_results)
    except ArtifactNotFound as exc:
        fail(
            code="artifact_not_found",
            message=str(exc),
            tool="output.search",
            audit_id="pending",
            run_id="pending",
        )
    return {"matches": matches, "handle": handle, "truncated": len(matches) >= max_results}


__all__ = [
    "output_tail",
    "output_read_range",
    "output_search",
]
