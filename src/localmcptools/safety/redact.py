"""Secret / token redactor — pure function, no I/O.

Used by every layer that persists user-supplied content (audit args,
artifact writes, error messages). The function is **pure**: same input
yields same output, no global state, no logging side effects.

Pattern catalogue (ordered — first match for a given span wins):

1. Bearer tokens:  ``bearer <token>``  →  ``Bearer ***``
2. Assignment secrets: ``api_key=...``, ``api-key: ...``, ``token = ...``,
   ``password='...'`` →  ``<key>: ***`` (or ``<key>=***`` depending on
   the original separator)
3. JWTs:  three base64url segments separated by ``.``
4. .env-style lines:  ``KEY=VALUE`` where ``KEY`` matches a known
   secret variable name → ``KEY=***``

The ``.env`` rule is conservative: it only matches variable names on a
curated denylist (see :data:`SECRET_ENV_NAMES`), so a line like
``NAME=John`` is left untouched.

A redactor that *under*-redacts leaks secrets; one that *over*-redacts
makes the audit log useless. The unit tests in
``tests/unit/test_redact.py`` enforce the boundary.
"""

from __future__ import annotations

import re
from typing import Callable


# --- Pattern catalogue ---------------------------------------------------

# 1. Bearer tokens: literal ``bearer`` (any case) followed by whitespace
#    and a non-whitespace token.
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")

# 2. Assignment secrets: capture the key name, separator, and value.
#    Accept ``:`` or ``=`` between key and value; accept optional
#    matching single/double quotes around the value. Value must be
#    at least 4 chars to avoid redacting trivial strings.
_ASSIGN_SECRET_RE = re.compile(
    r"""(?ix)
    \b(
        api[_-]?key
      | apikey
      | token
      | secret
      | password
      | passwd
      | pwd
      | access[_-]?key
      | private[_-]?key
    )
    \s*[:=]\s*          # separator
    (?P<q>['"]?)        # optional opening quote
    (?P<value>[^\s'\"]{4,})  # value: >=4 chars, no whitespace, no quote
    (?P=q)              # matching closing quote
    """
)

# 3. JWT: three base64url segments separated by dots.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")

# 3b. Known provider PAT prefixes. These tokens appear in URLs and
#     config without an obvious ``key=value`` shape, so we catch them
#     by prefix. Add new providers as they're encountered.
_PROVIDER_PAT_RE = re.compile(
    r"\b(glpat-[A-Za-z0-9_\-]{20,}|ghp_[A-Za-z0-9]{30,}|"
    r"xox[baprs]-[A-Za-z0-9\-]+|sk_(live|test)_[A-Za-z0-9]{16,})\b"
)

# 4. .env-style lines: ALL_CAPS_KEY followed by = and a value.
#    Strict case: the key must start uppercase. The callback decides
#    per-key whether to redact; we use a wrapper to count only the
#    *changed* spans (subn counts every match even if the replacement
#    equals the original).
_ENV_LINE_RE = re.compile(
    r"(?m)^(?P<key>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>\S+)\s*$"
)

# Curated denylist of env var names that almost always carry secrets.
# Lowercase comparison only.
SECRET_ENV_NAMES: frozenset[str] = frozenset(
    {
        # Generic
        "secret", "secret_key", "secret_token", "api_key", "apikey",
        "access_key", "access_token", "refresh_token", "auth_token",
        "authorization", "password", "passwd", "pwd",
        "private_key", "session_key", "signing_key",
        # Common providers
        "aws_secret_access_key", "aws_access_key_id",
        "azure_client_secret", "azure_password",
        "github_token", "gh_token", "gitlab_token",
        "openai_api_key", "anthropic_api_key",
        "slack_token", "slack_webhook_url",
        "stripe_secret_key", "stripe_publishable_key",
        "sendgrid_api_key", "twilio_auth_token",
        # LocalMcpTools-specific
        "lmcp_client_secret", "lmcp_bearer",
    }
)


# --- Replacement helpers --------------------------------------------------


def _looks_like_secret(var_name: str) -> bool:
    name = var_name.strip().lower()
    if name in SECRET_ENV_NAMES:
        return True
    # Heuristic for keys that *contain* one of the secret tokens.
    for token in ("secret", "token", "key", "password", "passwd", "pwd"):
        if token in name:
            return True
    return False


def _replace_assign_secret(match: re.Match[str]) -> str:
    """Replace an assignment-style secret, preserving key + separator style."""
    full = match.group(0)
    key = match.group(1)
    # Find the separator that sits between the key and the value.
    sep_match = re.search(r"\s*([:=])\s*", full[len(key):])
    sep = sep_match.group(1) if sep_match else "="
    quote = match.group("q")  # '' | "'" | '"'
    return f"{key}{sep}{quote}***{quote}"


def _replace_env_line(match: re.Match[str]) -> str:
    """Redact a .env line iff the key looks like a secret.

    Returns the original text for non-secret lines; the outer
    :func:`redact` only counts substitutions that actually changed
    something.
    """
    key = match.group("key")
    if _looks_like_secret(key):
        return f"{key}=***"
    return match.group(0)


# --- Public API -----------------------------------------------------------


# A :class:`Pattern` is a 2-tuple of (compiled_regex, replacement_or_callable).
Pattern = tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]

PATTERNS: tuple[Pattern, ...] = (
    (_BEARER_RE, "Bearer ***"),
    (_ASSIGN_SECRET_RE, _replace_assign_secret),
    (_JWT_RE, "***.***.***"),
    (_PROVIDER_PAT_RE, "***"),
    (_ENV_LINE_RE, _replace_env_line),
)


def _sub_count_changed(
    pattern: re.Pattern[str],
    callback: Callable[[re.Match[str]], str],
    text: str,
) -> tuple[str, int]:
    """Apply ``pattern.sub`` with a counting wrapper.

    ``re.subn`` counts every match, even when the replacement equals
    the original span. We want only *real* redactions in the count
    so the test expectations stay stable.
    """
    n = 0

    def _wrap(m: re.Match[str]) -> str:
        nonlocal n
        original = m.group(0)
        replacement = callback(m)
        if replacement != original:
            n += 1
        return replacement

    return pattern.sub(_wrap, text), n


def redact(text: str) -> tuple[str, int]:
    """Return ``(redacted_text, redaction_count)``.

    Pure function. ``redaction_count`` is the number of spans that
    were *actually changed* by a rule. Patterns whose callback
    returns the original text (e.g. a non-secret .env variable) do
    not inflate the count.
    """
    if not text:
        return text, 0

    count = 0
    for pattern, replacement in PATTERNS:
        if callable(replacement):
            new_text, n = _sub_count_changed(pattern, replacement, text)
        else:
            new_text, n = pattern.subn(replacement, text)
        count += n
        text = new_text
    return text, count


__all__ = [
    "PATTERNS",
    "SECRET_ENV_NAMES",
    "redact",
]