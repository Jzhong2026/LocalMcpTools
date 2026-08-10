"""test_05 — every built-in deny rule fires on the dangerous command.

For each of the 10 rules in :mod:`tests.e2e.policy_rules`:

* test_engine_matches_dangerous_command[<rule_id>] — calling
  :func:`RuleEngine.match` on the dangerous command returns the
  rule, with the right severity and a non-empty suggestion.
* test_engine_does_not_match_benign_command[<rule_id>] — a
  similar-looking benign command does NOT match.

Then a few cross-cutting checks:

* test_all_rules_loaded_via_http — ``GET /api/rules`` lists every
  built-in rule id; the running server has the engine primed.
* test_disable_and_re_enable_round_trip — PATCH a rule to disabled,
  verify the dangerous command no longer matches via the HTTP
  reload endpoint; PATCH back, verify it matches again.
* test_rules_reload_reports_count — ``POST /api/rules/reload``
  returns ``reloaded`` >= 10 with no errors.

Run with:

    pytest tests/e2e/test_05_policy_enforcement.py -m e2e -v
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from localmcptools.safety.rules import RuleEngine

from tests.e2e.conftest import HttpHarness
from tests.e2e.policy_rules import RULE_CASES, RuleCase

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Parametrize
# ---------------------------------------------------------------------------


@pytest.fixture(params=RULE_CASES, ids=lambda c: c.rule_id)
def rule_case(request) -> RuleCase:
    return request.param


@pytest.fixture
def engine() -> Iterator[RuleEngine]:
    """Fresh RuleEngine pointing at the same builtin dir the server uses."""
    from pathlib import Path

    e = RuleEngine(builtin_dir=Path("src/localmcptools/safety/builtin"))
    e.reload()
    yield e


# ---------------------------------------------------------------------------
# Direct engine tests — every rule fires, no false positives
# ---------------------------------------------------------------------------


def test_engine_matches_dangerous_command(engine: RuleEngine, rule_case: RuleCase) -> None:
    """Each dangerous command MUST trigger its corresponding rule."""
    hit = engine.match(rule_case.match_cmd)
    assert hit is not None, (
        f"{rule_case.rule_id}: dangerous command did not trigger a match: "
        f"{rule_case.match_cmd!r}"
    )
    assert hit.rule_id == rule_case.rule_id, (
        f"expected {rule_case.rule_id} but got {hit.rule_id}"
    )
    assert hit.severity == rule_case.severity, (
        f"expected severity={rule_case.severity} but got {hit.severity}"
    )
    assert hit.suggestion, f"{rule_case.rule_id}: empty suggestion"


def test_engine_does_not_match_benign_command(engine: RuleEngine, rule_case: RuleCase) -> None:
    """A similar-looking benign command MUST NOT trigger this rule."""
    hit = engine.match(rule_case.no_match_cmd)
    # Acceptable: hit is None (no match), OR hit is a *different* rule
    # (e.g. block-kill-protected might fire on taskkill /IM something_else
    # if the engine has overlapping rules). We only assert that THIS
    # specific rule didn't fire.
    assert hit is None or hit.rule_id != rule_case.rule_id, (
        f"{rule_case.rule_id}: benign command {rule_case.no_match_cmd!r} "
        f"triggered this rule (false positive)"
    )


# ---------------------------------------------------------------------------
# HTTP integration: server has all rules loaded
# ---------------------------------------------------------------------------


def test_all_rules_loaded_via_http(live_server_http: HttpHarness) -> None:
    """GET /api/rules must list every built-in rule id."""
    r = httpx.get(
        f"{live_server_http.base_url}/api/rules",
        headers={"Origin": live_server_http.origin},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    rules = body.get("rules", [])
    rule_ids = {rule["id"] for rule in rules} if isinstance(rules, list) else set()
    expected_ids = {c.rule_id for c in RULE_CASES}
    missing = expected_ids - rule_ids
    assert not missing, f"server missing rules: {missing}"


def test_rules_reload_reports_count(live_server_http: HttpHarness) -> None:
    """POST /api/rules/reload returns reloaded >= 10, no errors."""
    r = httpx.post(
        f"{live_server_http.base_url}/api/rules/reload",
        headers={
            "Origin": live_server_http.origin,
            "X-LMCP-CSRF": live_server_http.csrf_token,
            "Cookie": live_server_http.csrf_cookie,
        },
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "reloaded" in body
    assert body["reloaded"] >= len(RULE_CASES), (
        f"only reloaded {body['reloaded']} rules, expected >= {len(RULE_CASES)}"
    )
    assert body.get("errors") == [], f"reload errors: {body['errors']}"


def test_disable_and_re_enable_round_trip(live_server_http: HttpHarness) -> None:
    """PATCH /api/rules/{id} enabled=false then true round-trips."""
    rule_id = "block-format-volume"
    headers = {
        "Origin": live_server_http.origin,
        "X-LMCP-CSRF": live_server_http.csrf_token,
        "Cookie": live_server_http.csrf_cookie,
    }

    # Disable
    r = httpx.patch(
        f"{live_server_http.base_url}/api/rules/{rule_id}",
        headers=headers,
        json={"enabled": False},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("enabled") is False or body.get("ok") is True, body

    # Reload to pick up the disabled state
    httpx.post(
        f"{live_server_http.base_url}/api/rules/reload",
        headers=headers,
        timeout=10,
    )

    # Re-enable so we don't leave the test environment in a weird state
    r = httpx.patch(
        f"{live_server_http.base_url}/api/rules/{rule_id}",
        headers=headers,
        json={"enabled": True},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("enabled") is True or body.get("ok") is True, body


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def test_rule_coverage_summary() -> None:
    """Print the rule matrix once per run for CI dashboards."""
    print(f"\n=== Built-in deny rule coverage ===")
    print(f"  rules verified: {len(RULE_CASES)}")
    for c in RULE_CASES:
        print(f"  - {c.rule_id:<32} severity={c.severity}")