"""Stable action digests used to bind a human approval to one request."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_args(args: dict[str, Any]) -> str:
    """Return deterministic JSON for approval binding.

    ``approval_id`` is a transport handle added only on the retry, so it is
    intentionally excluded. All actual operation arguments remain bound.
    """
    bound_args = {key: value for key, value in args.items() if key != "approval_id"}
    return json.dumps(bound_args, sort_keys=True, separators=(",", ":"), default=str)


def digest_for(tool: str, args: dict[str, Any], workspace_id: str, profile: str) -> str:
    """Compute SHA-256(tool | workspace | profile | canonical arguments)."""
    payload = f"{tool}|{workspace_id}|{profile}|{canonical_args(args)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["canonical_args", "digest_for"]
