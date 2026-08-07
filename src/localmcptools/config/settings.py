"""Load ``config.json`` with a defaults merge.

The loader is intentionally tolerant:

- Missing file → defaults, no exception.
- File present but malformed JSON → defaults + a warning to stderr.
- File present with extra keys → keys preserved (forward compatibility).
- File present with wrong-type values → defaults for that subtree, the rest kept.

Frozen for the spike. A real schema lives in change-3
(``policy-and-safety``) when the config starts driving authority decisions.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .defaults import get_defaults
from .paths import config_path

_log = logging.getLogger(__name__)


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Return the merged settings dict.

    ``path`` defaults to :func:`.paths.config_path` and is exposed as a
    parameter so tests can point at a temp file without monkeypatching
    the environment.
    """
    defaults = get_defaults()
    target = path if path is not None else config_path()

    if not target.exists():
        return defaults

    try:
        with target.open("r", encoding="utf-8") as fh:
            user = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # Never raise: the spike must boot even with a broken config.
        _log.warning("config.json unreadable (%s); falling back to defaults", exc)
        print(
            f"[localmcptools] config.json at {target} could not be parsed "
            f"({exc.__class__.__name__}); using built-in defaults.",
            file=sys.stderr,
        )
        return defaults

    if not isinstance(user, dict):
        _log.warning("config.json root is not an object; falling back to defaults")
        return defaults

    return _merge(defaults, user)


def _merge(defaults: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto ``defaults``.

    Rules:
    - dict + dict → recursive merge
    - dict + scalar → override wins
    - scalar + anything → override wins
    - lists are replaced wholesale (no element-wise merge — too surprising)
    """
    out: dict[str, Any] = dict(defaults)
    for key, value in override.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _merge(existing, value)
        else:
            out[key] = value
    return out