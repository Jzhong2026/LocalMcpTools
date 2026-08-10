"""Credential-window denylist + other UI-text filters.

Used by :mod:`localmcptools.ui.windows` to ensure we never enumerate a
window whose title suggests it carries credentials. The list is
intentionally broad on Windows where credential UIs are common — the
operator can always widen it via ``%APPDATA%\\LocalMcpTools\\config.json``.

The matching is case-insensitive substring; the patterns are anchored
to whole words when written with ``\\b`` boundaries, otherwise plain
substring. We deliberately keep the patterns conservative (false
negatives are worse than false positives — a leaked credential window
cannot be undone).
"""

from __future__ import annotations

import re
from typing import Iterable

# Patterns matched against the window title (case-insensitive). The
# regex strings are compiled at import time.
_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\bsign[\s-]?in\b",
        r"\blogin\b",
        r"\bpassword\b",
        r"\bcredential(s)?\b",
        r"\bbitlocker\b",
        r"\buac\b",
        r"\buser account control\b",
        r"\bpassphrase\b",
        r"\bone[\s-]?time[\s-]?code\b",
        r"\botp\b",
        r"\btwo[\s-]?factor\b",
        r"\b2fa\b",
        r"\bmfa\b",
        r"\bsecret\s*question",
        r"\brecovery\s*key\b",
    )
)


def title_blocked(title: str) -> bool:
    """Return True if the window title matches any credential denylist pattern."""
    if not title:
        return False
    return any(pattern.search(title) for pattern in _PATTERNS)


def is_visible_process(process_name: str) -> bool:
    """Lightweight check — never enumerate windows from these processes.

    Anything in this list is assumed credential-bearing by reputation;
    the UI is hidden from the operator entirely. Keep this list short
    and obvious.
    """
    blocked: frozenset[str] = frozenset({
        "lsass.exe",
        "winlogon.exe",
        "csrss.exe",
        "credentialuibroker.exe",
        "credentialdialoghost.exe",
        "consent.exe",
    })
    return process_name.lower() not in blocked


def filter_titles(titles: Iterable[str]) -> list[str]:
    """Drop titles that match the credential denylist.

    Used by tests and the UI to keep examples honest.
    """
    return [t for t in titles if not title_blocked(t)]


__all__ = ["filter_titles", "is_visible_process", "title_blocked"]