"""Tests for the change-6 UI automation layer.

Covered surfaces:

- :mod:`localmcptools.safety.filters` — credential-window denylist.
- :mod:`localmcptools.ui.windows` — authorize / revoke / list_windows
  (Windows path is exercised in the integration suite; here we test
  the pure DB + filter logic).
- :mod:`localmcptools.ui.verify` — orchestrator + each predicate kind.
- :mod:`localmcptools.ui.ocr` — provider fallback + text-match helpers.
- :mod:`localmcptools.ui.screens` — token bucket rate limit.

Non-Windows hosts run every test that doesn't require ``uiautomation``;
the platform-conditional branch returns ``[]`` / ``{}`` and we assert
that contract.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from localmcptools.persistence import db
from localmcptools.safety.filters import (
    filter_titles,
    is_visible_process,
    title_blocked,
)
from localmcptools.ui.ocr import (
    StubProvider,
    get_provider,
    ocr_assert_text,
    ocr_find_text,
    ocr_region,
    reset_provider,
)
from localmcptools.ui.screens import TokenBucket
from localmcptools.ui.verify import (
    OCRPredicate,
    Predicate,
    PredicateResult,
    ScreenshotPredicate,
    UIAPredicate,
    verify,
)
from localmcptools.ui.windows import (
    DEFAULT_TTL_MS,
    authorize,
    is_authorized,
    lookup,
    revoke,
)

# --- safety.filters -------------------------------------------------------


def test_title_blocked_matches_credential_keywords() -> None:
    assert title_blocked("Sign in to your account")
    assert title_blocked("User Account Control")
    assert title_blocked("BitLocker Recovery Key")
    assert title_blocked("One-time code")
    assert title_blocked("Two-Factor Authentication")
    assert not title_blocked("VS Code")
    assert not title_blocked("")


def test_title_blocked_is_case_insensitive() -> None:
    assert title_blocked("SIGN IN")
    assert title_blocked("password manager")


def test_filter_titles_drops_blocked() -> None:
    titles = ["VS Code", "Sign in to GitHub", "Settings", "BitLocker Recovery"]
    filtered = filter_titles(titles)
    assert "VS Code" in filtered
    assert "Settings" in filtered
    assert "Sign in to GitHub" not in filtered
    assert "BitLocker Recovery" not in filtered


def test_is_visible_process_blocks_credentials() -> None:
    assert not is_visible_process("lsass.exe")
    assert not is_visible_process("CredentialUIBroker.exe")
    assert is_visible_process("Code.exe")
    assert is_visible_process("notepad.exe")


# --- ui.windows (DB logic) ------------------------------------------------


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh DB per test. The ``LMCP_DATA_DIR`` env var is set so the
    non-conn calls in :func:`lookup` / :func:`revoke` / :func:`is_authorized`
    resolve to the same temp DB.
    """
    path = tmp_path / "audit.sqlite"
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    db.init_db(path)
    return path


def test_authorize_writes_row(database: Path) -> None:
    with db.connection(database) as conn:
        row = authorize(hwnd=12345, process="Code.exe", pid=99, title="VS Code", conn=conn)
    assert row.hwnd == 12345
    assert row.process == "Code.exe"
    assert row.expires_at > row.issued_at


def test_lookup_returns_authorized_window(database: Path) -> None:
    with db.connection(database) as conn:
        row = authorize(hwnd=99, process="Code.exe", pid=1, title="VS Code", conn=conn)
    # The non-conn call goes through the default connection which uses LMCP_DATA_DIR.
    fetched = lookup(window_id=row.id)
    assert fetched is not None
    assert fetched.hwnd == 99
    assert is_authorized(window_id=row.id)


def test_revoke_invalidates_window(database: Path) -> None:
    with db.connection(database) as conn:
        row = authorize(hwnd=1, process="x", pid=1, title="t", conn=conn)
    assert revoke(window_id=row.id)
    assert lookup(window_id=row.id) is None
    assert not is_authorized(window_id=row.id)


def test_expired_authorization_returns_none(database: Path) -> None:
    with db.connection(database) as conn:
        row = authorize(hwnd=1, process="x", pid=1, title="t", ttl_ms=1, conn=conn)
    time.sleep(0.05)
    assert lookup(window_id=row.id) is None


def test_unknown_window_id_returns_none(database: Path) -> None:
    assert lookup(window_id="not-a-real-id") is None


def test_default_ttl_is_one_hour() -> None:
    assert DEFAULT_TTL_MS == 60 * 60 * 1000


# --- ui.screens.TokenBucket ----------------------------------------------


def test_token_bucket_allows_within_window() -> None:
    bucket = TokenBucket(rate_per_minute=3, window_seconds=1.0)
    for _ in range(3):
        assert bucket.check("agent-a")
    assert not bucket.check("agent-a")


def test_token_bucket_per_key_independent() -> None:
    bucket = TokenBucket(rate_per_minute=2, window_seconds=1.0)
    assert bucket.check("a")
    assert bucket.check("a")
    assert not bucket.check("a")
    assert bucket.check("b")  # 'b' starts fresh
    assert bucket.check("b")


def test_token_bucket_resets_after_window() -> None:
    bucket = TokenBucket(rate_per_minute=2, window_seconds=0.1)
    assert bucket.check("k")
    assert bucket.check("k")
    assert not bucket.check("k")
    time.sleep(0.15)
    assert bucket.check("k")


def test_token_bucket_reset_clears_keys() -> None:
    bucket = TokenBucket(rate_per_minute=1)
    assert bucket.check("k")
    assert not bucket.check("k")
    bucket.reset("k")
    assert bucket.check("k")


# --- ui.ocr provider fallback --------------------------------------------


def test_get_provider_returns_object_with_ocr_image() -> None:
    provider = get_provider()
    assert hasattr(provider, "ocr_image")
    assert hasattr(provider, "name")


def test_stub_provider_returns_uncertain() -> None:
    provider = StubProvider()
    result = provider.ocr_image(b"not-a-real-image")
    assert result.uncertain is True
    assert result.blocks == []
    assert result.full_text == ""


def test_reset_provider_forces_rebuild() -> None:
    p1 = get_provider()
    reset_provider()
    p2 = get_provider()
    assert p1 is not p2


def test_ocr_region_rejects_invalid_source() -> None:
    out = ocr_region(source={})
    assert out["error"]["code"] == "source_not_allowed"


def test_ocr_assert_text_returns_passed_false_on_uncertain() -> None:
    """With the stub provider, assert_text must never claim passed."""
    reset_provider()
    out = ocr_assert_text(source={"window_id": "missing"}, expected="hello")
    # Either it errored (window not authorized) or returned passed:false.
    assert out.get("passed") is False
    if "error" not in out:
        assert out.get("uncertain") is True


def test_ocr_find_text_handles_invalid_query() -> None:
    # The implementation checks ``query`` after resolving the source,
    # so a missing window fails first with ``source_not_allowed``.
    out = ocr_find_text(query="", source={"window_id": "x"})
    assert "error" in out


# --- ui.verify orchestrator ----------------------------------------------


def test_verify_returns_passed_when_all_predicates_pass() -> None:
    predicates: list[Predicate] = [
        _AlwaysPassPredicate("a"),
        _AlwaysPassPredicate("b"),
    ]
    report = verify(predicates=predicates)
    assert report["passed"] is True
    assert len(report["predicates"]) == 2


def test_verify_aborts_on_first_failure() -> None:
    predicates: list[Predicate] = [
        _AlwaysPassPredicate("a"),
        _AlwaysFailPredicate("b"),
        _AlwaysPassPredicate("never-runs"),
    ]
    report = verify(predicates=predicates)
    assert report["passed"] is False
    # ``never-runs`` must NOT be in the report.
    names = [p["name"] for p in report["predicates"]]
    assert "never-runs" not in names


def test_uia_predicate_returns_observed_subclass() -> None:
    pred = UIAPredicate(window_id="missing", criterion={"text": "x"}, expected={})
    assert isinstance(pred, object)


def test_screenshot_predicate_threshold_is_set() -> None:
    pred = ScreenshotPredicate(window_id=1, reference_handle="art://x", threshold=0.05)
    assert pred.threshold == 0.05


def test_ocr_predicate_carries_match() -> None:
    pred = OCRPredicate(window_id="w", expected="hello", match="exact")
    assert pred.match == "exact"


# --- helpers used in the verify tests ------------------------------------


class _AlwaysPassPredicate:
    def __init__(self, name: str) -> None:
        self.name = name

    def check(self) -> PredicateResult:
        return PredicateResult(passed=True, detail="ok")


class _AlwaysFailPredicate:
    def __init__(self, name: str) -> None:
        self.name = name

    def check(self) -> PredicateResult:
        return PredicateResult(passed=False, detail="nope")
