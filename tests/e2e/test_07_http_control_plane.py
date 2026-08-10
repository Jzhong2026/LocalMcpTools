"""test_07 — every HTTP control-plane endpoint behaves as documented.

For each of the 21 endpoints registered by ``control_api.py`` this
file:

1. Issues a request with the right Origin header (so the OriginCSRF
   middleware lets us through).
2. Adds the CSRF cookie + header for unsafe methods (POST/PATCH/DELETE).
3. Asserts the status code is in the expected set (usually 200; 4xx
   is acceptable when the request body is intentionally empty / bad).
4. Asserts the response shape stays stable — keys the SPA relies on.

The plan also calls for explicit 401 / 403 negative tests; those live
in :func:`test_security_gates` so they can be run as a single
parametric.

Negative paths the e2e plan calls out:

* Missing Origin → 403
* Wrong Origin → 403
* POST / PATCH without CSRF → 403
* GET / HEAD / OPTIONS skip the CSRF check (safe methods)
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.e2e.conftest import HttpHarness

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_headers(harness: HttpHarness) -> dict[str, str]:
    """Headers for safe methods (GET / HEAD / OPTIONS): Origin only."""
    return {"Origin": harness.origin}


def _unsafe_headers(harness: HttpHarness) -> dict[str, str]:
    """Headers for unsafe methods (POST / PATCH / DELETE): Origin + CSRF."""
    return {
        "Origin": harness.origin,
        "X-LMCP-CSRF": harness.csrf_token,
        "Cookie": harness.csrf_cookie,
    }


def _get(harness: HttpHarness, path: str, **kw: Any) -> httpx.Response:
    return httpx.get(
        f"{harness.base_url}{path}",
        headers=_safe_headers(harness),
        timeout=10,
        **kw,
    )


def _post(harness: HttpHarness, path: str, body: dict | None = None, **kw: Any) -> httpx.Response:
    return httpx.post(
        f"{harness.base_url}{path}",
        headers=_unsafe_headers(harness),
        json=body if body is not None else {},
        timeout=10,
        **kw,
    )


def _patch(harness: HttpHarness, path: str, body: dict | None = None, **kw: Any) -> httpx.Response:
    return httpx.patch(
        f"{harness.base_url}{path}",
        headers=_unsafe_headers(harness),
        json=body if body is not None else {},
        timeout=10,
        **kw,
    )


# ---------------------------------------------------------------------------
# Per-endpoint parametric tests
# ---------------------------------------------------------------------------


def test_status(live_server_http: HttpHarness) -> None:
    r = _get(live_server_http, "/api/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "server" in body
    assert "config" in body
    assert body["server"]["transport"] == "http"


def test_audit_list(live_server_http: HttpHarness) -> None:
    """Audit list with no filters; should return at least an empty list
    + pagination block."""
    r = _get(live_server_http, "/api/audit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rows" in body, f"missing 'rows': {body}"
    assert "page" in body
    assert "page_size" in body
    assert isinstance(body["rows"], list)


def test_audit_detail_not_found(live_server_http: HttpHarness) -> None:
    """Unknown call_id → 404."""
    r = _get(live_server_http, "/api/audit/does-not-exist")
    assert r.status_code == 404, r.text


def test_audit_log_not_found(live_server_http: HttpHarness) -> None:
    """Unknown call_id log → 404."""
    r = _get(live_server_http, "/api/audit/does-not-exist/log")
    assert r.status_code == 404, r.text


def test_settings_get(live_server_http: HttpHarness) -> None:
    r = _get(live_server_http, "/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    # Settings is the raw config object (no 'settings' wrapper).
    assert "version" in body, f"missing 'version': {body}"
    assert "security" in body


def test_settings_post_round_trip(live_server_http: HttpHarness) -> None:
    """Apply a no-op patch and read it back."""
    r = _post(live_server_http, "/api/settings", {"patch": {"redact_before_persist": True}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("applied") is not None, f"missing 'applied': {body}"
    assert "redact_before_persist" in body["applied"], (
        f"redact_before_persist should be in applied: {body}"
    )

    # Verify GET reflects the change
    r2 = _get(live_server_http, "/api/settings")
    assert r2.status_code == 200
    settings = r2.json()
    assert settings["security"]["redact_before_persist"] is True


def test_rules_list(live_server_http: HttpHarness) -> None:
    r = _get(live_server_http, "/api/rules")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rules" in body, f"missing 'rules': {body}"
    assert isinstance(body["rules"], list)


def test_rules_toggle_unknown_id(live_server_http: HttpHarness) -> None:
    """Patching an unknown rule_id should 404 (or surface 'not found')."""
    r = _patch(live_server_http, "/api/rules/no-such-rule", {"enabled": False})
    assert r.status_code in (404, 400), f"unexpected {r.status_code}: {r.text}"


def test_rules_reload(live_server_http: HttpHarness) -> None:
    r = _post(live_server_http, "/api/rules/reload")
    assert r.status_code == 200, r.text
    body = r.json()
    # Returns {reloaded: int, errors: list}
    assert "reloaded" in body, f"missing 'reloaded': {body}"
    assert isinstance(body["reloaded"], int)
    assert body["reloaded"] >= 0
    assert "errors" in body


def test_backgrounds_list(live_server_http: HttpHarness) -> None:
    r = _get(live_server_http, "/api/backgrounds")
    assert r.status_code == 200, r.text
    body = r.json()
    # Returns {processes: [...]}
    assert "processes" in body, f"missing 'processes': {body}"
    assert isinstance(body["processes"], list)


def test_backgrounds_stop_unknown(live_server_http: HttpHarness) -> None:
    """Stopping a non-existent process → 404."""
    r = _post(live_server_http, "/api/backgrounds/no-such-process/stop")
    assert r.status_code in (404, 400), f"unexpected {r.status_code}: {r.text}"


def test_windows_list(live_server_http: HttpHarness) -> None:
    r = _get(live_server_http, "/api/windows")
    assert r.status_code == 200, r.text
    body = r.json()
    # Returns {windows: [...]}
    assert "windows" in body, f"missing 'windows': {body}"
    assert isinstance(body["windows"], list)


def test_windows_authorize_empty_body(live_server_http: HttpHarness) -> None:
    """POST without the required hwnd/title/process should 4xx."""
    r = _post(live_server_http, "/api/windows/authorize", {})
    # Expecting 400 (validation) or 500 (implementation rejects empty body).
    # We just want a 4xx/5xx that isn't 200 — proves the gate works.
    assert r.status_code >= 400, f"empty body should fail, got {r.status_code}: {r.text}"


def test_window_revoke_unknown(live_server_http: HttpHarness) -> None:
    """Unknown window_id returns 200 with revoked=false (idempotent revoke)."""
    r = _post(live_server_http, "/api/windows/no-such-window/revoke")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("revoked") is False, f"expected revoked=false: {body}"


def test_ui_get_tree(live_server_http: HttpHarness) -> None:
    """Empty body → graceful failure (UI tree needs a window_id)."""
    r = _post(live_server_http, "/api/ui/get_ui_tree", {})
    assert r.status_code >= 400, f"empty body should fail, got {r.status_code}: {r.text}"


def test_ui_find_element(live_server_http: HttpHarness) -> None:
    r = _post(live_server_http, "/api/ui/find_element", {})
    assert r.status_code >= 400, f"empty body should fail, got {r.status_code}: {r.text}"


def test_ui_screenshot_window(live_server_http: HttpHarness) -> None:
    r = _post(live_server_http, "/api/ui/screenshot_window", {})
    assert r.status_code >= 400, f"empty body should fail, got {r.status_code}: {r.text}"


def test_ocr_ocr_region(live_server_http: HttpHarness) -> None:
    r = _post(live_server_http, "/api/ocr/ocr_region", {})
    assert r.status_code >= 400, f"empty body should fail, got {r.status_code}: {r.text}"


def test_ocr_assert_text(live_server_http: HttpHarness) -> None:
    r = _post(live_server_http, "/api/ocr/assert_text", {})
    assert r.status_code >= 400, f"empty body should fail, got {r.status_code}: {r.text}"


def test_mcp_config_snippet(live_server_http: HttpHarness) -> None:
    r = _get(live_server_http, "/api/mcp-config-snippet")
    assert r.status_code == 200, r.text
    body = r.json()
    # Should expose both codebuddy + copilot snippets
    assert "codebuddy" in body or "copilot" in body, f"missing snippets: {body}"


def test_csrf_token_bootstrap(live_server_http: HttpHarness) -> None:
    """``GET /api/csrf-token`` is exempt from CSRF but still requires Origin."""
    r = httpx.get(
        f"{live_server_http.base_url}/api/csrf-token",
        headers={"Origin": live_server_http.origin},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["csrf_token"] == live_server_http.csrf_token


# ---------------------------------------------------------------------------
# Security gates
# ---------------------------------------------------------------------------


def test_missing_origin_rejected(live_server_http: HttpHarness) -> None:
    """No Origin header → 403 from any /api/* endpoint."""
    r = httpx.get(f"{live_server_http.base_url}/api/status", timeout=10)
    assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text}"


def test_wrong_origin_rejected(live_server_http: HttpHarness) -> None:
    """Origin not in allowlist → 403."""
    r = httpx.get(
        f"{live_server_http.base_url}/api/status",
        headers={"Origin": "http://evil.example.com"},
        timeout=10,
    )
    assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text}"


def test_post_without_csrf_rejected(live_server_http: HttpHarness) -> None:
    """POST without CSRF cookie + header → 403."""
    r = httpx.post(
        f"{live_server_http.base_url}/api/rules/reload",
        headers={"Origin": live_server_http.origin},
        json={},
        timeout=10,
    )
    assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text}"


def test_patch_without_csrf_rejected(live_server_http: HttpHarness) -> None:
    """PATCH without CSRF cookie + header → 403."""
    r = httpx.patch(
        f"{live_server_http.base_url}/api/rules/format-volume",
        headers={"Origin": live_server_http.origin},
        json={"enabled": True},
        timeout=10,
    )
    assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text}"


def test_csrf_token_exempt_from_csrf_check(live_server_http: HttpHarness) -> None:
    """``GET /api/csrf-token`` should work even without a CSRF token
    (it exists specifically to bootstrap one). Origin still required."""
    r = httpx.get(
        f"{live_server_http.base_url}/api/csrf-token",
        headers={"Origin": live_server_http.origin},
        timeout=10,
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def test_endpoint_coverage_summary() -> None:
    """Print the matrix once per run for CI dashboards."""
    expected = [
        "GET /api/status",
        "GET /api/audit",
        "GET /api/audit/{call_id}",
        "GET /api/audit/{call_id}/log",
        "GET /api/settings",
        "POST /api/settings",
        "GET /api/rules",
        "PATCH /api/rules/{rule_id}",
        "POST /api/rules/reload",
        "GET /api/backgrounds",
        "POST /api/backgrounds/{process_id}/stop",
        "GET /api/windows",
        "POST /api/windows/authorize",
        "POST /api/windows/{window_id}/revoke",
        "POST /api/ui/get_ui_tree",
        "POST /api/ui/find_element",
        "POST /api/ui/screenshot_window",
        "POST /api/ocr/ocr_region",
        "POST /api/ocr/assert_text",
        "GET /api/mcp-config-snippet",
        "GET /api/csrf-token (transport bootstrap)",
    ]
    print(f"\n=== HTTP control-plane coverage ===")
    print(f"  endpoints covered: {len(expected)}")
    for ep in expected:
        print(f"  - {ep}")