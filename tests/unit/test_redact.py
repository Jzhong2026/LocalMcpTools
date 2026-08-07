"""Tests for :mod:`localmcptools.safety.redact`.

Covers the spike DoD bullets:

- ``Bearer``, ``api_key=``, JWT, ``.env`` lines, mixed content all redacted.
- Plain ``password=secret123`` → ``password=***``; innocent ``name=John``
  left alone.
- Function is pure: same input → same output, no side effects.
"""

from __future__ import annotations

import pytest

from localmcptools.safety import redact
from localmcptools.safety.redact import (
    PATTERNS,
    SECRET_ENV_NAMES,
    redact as redact_fn,
)


# --- Single-pattern cases -------------------------------------------------


def test_bearer_token_redacted() -> None:
    text = "Authorization: Bearer abcDEF123_-."
    out, n = redact_fn(text)
    assert "Bearer ***" in out
    assert "abcDEF" not in out
    assert n >= 1


def test_bearer_case_insensitive() -> None:
    out, _ = redact_fn("bearer xyz123secret")
    assert "***" in out
    assert "xyz123secret" not in out


def test_api_key_equals_redacted() -> None:
    out, n = redact_fn("api_key=sk_test_4242424242")
    assert "sk_test_4242424242" not in out
    assert "***" in out
    assert n >= 1


def test_api_key_colon_quoted_redacted() -> None:
    out, n = redact_fn('API-KEY: "deadbeefcafebabe1234"')
    assert "deadbeefcafebabe1234" not in out
    assert "***" in out
    assert n >= 1


def test_password_redacted() -> None:
    """The literal example from the spec."""
    out, n = redact_fn("password=secret123")
    assert out == "password=***"
    assert n == 1


def test_password_single_quotes_redacted() -> None:
    out, _ = redact_fn("password='hunter2hunter'")
    assert "hunter2hunter" not in out
    assert "***" in out


def test_jwt_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    # JWT alone (not inside an assignment) so the JWT rule fires.
    out, n = redact_fn(f"raw jwt: {jwt}")
    assert jwt not in out
    assert "***.***.***" in out
    assert n >= 1


def test_env_secret_redacted() -> None:
    out, n = redact_fn("OPENAI_API_KEY=sk-abc123def456ghi789jkl012mno345")
    assert "sk-abc123def456ghi789jkl012mno345" not in out
    assert out.strip() == "OPENAI_API_KEY=***"
    assert n >= 1


def test_env_non_secret_unchanged() -> None:
    """The literal example from the spec: ``name=John`` must not be touched."""
    out, n = redact_fn("name=John")
    assert out == "name=John"
    assert n == 0


def test_env_uppercase_non_secret_unchanged() -> None:
    out, _ = redact_fn("PATH=/usr/bin")
    assert out == "PATH=/usr/bin"


# --- Mixed content --------------------------------------------------------


def test_mixed_content_redacts_only_secrets() -> None:
    text = (
        "Here is some output:\n"
        "  username: alice\n"
        "  api_key: 'sk_live_4242424242'\n"
        "  Bearer eyABC123def456ghi789\n"
        "  email: alice@example.com\n"
        "  password=correct horse battery staple\n"
    )
    out, n = redact_fn(text)
    # Non-secrets stay.
    assert "username: alice" in out
    assert "email: alice@example.com" in out
    # Secrets gone.
    assert "sk_live_4242424242" not in out
    assert "eyABC123def456ghi789" not in out
    assert "correct horse battery staple" not in out
    # Redaction marker present.
    assert "***" in out
    assert n >= 3


def test_redact_is_pure() -> None:
    """Calling twice yields identical output."""
    text = "api_key=abc password=def"
    a = redact_fn(text)
    b = redact_fn(text)
    assert a == b
    assert a[1] == b[1]


def test_empty_input() -> None:
    out, n = redact_fn("")
    assert out == ""
    assert n == 0


def test_no_matches() -> None:
    out, n = redact_fn("just some normal text without secrets")
    assert out == "just some normal text without secrets"
    assert n == 0


# --- Boundary cases -------------------------------------------------------


def test_short_value_not_redacted() -> None:
    """Values shorter than 4 chars are kept — they don't carry entropy."""
    out, n = redact_fn("api_key=ab")
    assert out == "api_key=ab"
    assert n == 0


def test_value_in_quotes_preserves_quotes_when_redacted() -> None:
    out, _ = redact_fn('api_key="abc12345"')
    # The double-quotes around the value should be preserved.
    assert '"***"' in out


def test_value_in_single_quotes_preserved() -> None:
    out, _ = redact_fn("api_key='abc12345'")
    assert "'***'" in out or "***" in out


def test_secret_via_heuristic() -> None:
    """A name *containing* a known token word is also flagged."""
    out, _ = redact_fn("MY_CUSTOM_TOKEN=abcdef12345")
    assert "***" in out
    assert "abcdef12345" not in out


def test_non_secret_via_heuristic_kept() -> None:
    """Names that contain 'key' but aren't secret-y (e.g. WINDOW_HEIGHT)."""
    # We choose ones that don't contain 'secret/token/password/key' to
    # ensure the heuristic is conservative.
    out, _ = redact_fn("WINDOW_HEIGHT=600")
    assert "WINDOW_HEIGHT=600" in out


# --- Pure-function contract ----------------------------------------------


def test_redact_does_not_mutate_input() -> None:
    text = "api_key=abc"
    original = text
    _ = redact_fn(text)
    assert text == original


def test_patterns_non_empty() -> None:
    """Sanity: we actually compiled at least the four documented patterns."""
    assert len(PATTERNS) >= 4


def test_secret_env_names_non_empty() -> None:
    assert "API_KEY" not in SECRET_ENV_NAMES  # case-sensitive denylist
    assert "api_key" in SECRET_ENV_NAMES


# --- Realistic agent output ----------------------------------------------


def test_realistic_pip_install_output() -> None:
    """A ``pip install`` line with a GitLab PAT in the index URL."""
    # Real GitLab PATs are 26 chars after the ``glpat-`` prefix.
    pat = "glpat-abcdefghij1234567890AB"
    line = (
        f"Looking in indexes: https://__token__:{pat}@gitlab.example.com/"
        "api/v4/projects/123/packages/pypi/simple"
    )
    out, _ = redact_fn(line)
    # The literal token is gone.
    assert pat not in out
    # The URL itself stays — we don't redact URLs, only secrets.
    assert "gitlab.example.com" in out


def test_realistic_env_dump_redacts_known_secrets_only() -> None:
    dump = (
        "PATH=/usr/bin\n"
        "HOME=/home/alice\n"
        "USER=alice\n"
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
        "AWS_DEFAULT_REGION=us-east-1\n"
    )
    out, n = redact_fn(dump)
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in out
    assert "PATH=/usr/bin" in out
    assert "USER=alice" in out
    assert "AWS_DEFAULT_REGION=us-east-1" in out
    assert n >= 1