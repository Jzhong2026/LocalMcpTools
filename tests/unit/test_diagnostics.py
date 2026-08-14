"""Tests for the diagnostics module + tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.diagnostics import Classification, advice_for, classify
from localmcptools.diagnostics.classify import ClassificationResult
from localmcptools.tools.diagnostics import diagnostics_collect, diagnostics_explain_failure


def test_classify_timeout() -> None:
    result = classify({"ok": 0, "error_code": "timed_out", "status": "failed"})
    assert result.classification is Classification.TIMEOUT


def test_classify_exit_code_fallback() -> None:
    result = classify({"ok": 0, "error_code": "internal_error", "exit_code": 2})
    assert result.classification is Classification.EXIT_CODE
    assert result.exit_code == 2


def test_classify_denied_by_rule_extracts_rule_id() -> None:
    result = classify(
        {"ok": 0, "error_code": "denied_by_rule", "blocked_by": "rule:block-format-volume"}
    )
    assert result.classification is Classification.DENIED_BY_RULE
    assert result.rule_id == "block-format-volume"


def test_classify_success_returns_summary() -> None:
    result = classify({"ok": 1})
    assert result.classification is Classification.SUCCESS
    assert "successful" in result.summary.lower()


def test_advice_for_format_substitutes_rule_id() -> None:
    result = ClassificationResult(
        classification=Classification.DENIED_BY_RULE,
        summary="denied",
        rule_id="block-format-volume",
    )
    items = advice_for(result)
    # At least one advice string mentions the rule id.
    assert any("block-format-volume" in item for item in items)


def test_advice_for_unknown_has_fallback() -> None:
    assert advice_for(Classification.UNKNOWN)


def test_diagnostics_explain_failure_handles_missing_run() -> None:
    out = diagnostics_explain_failure({"run_id": "no-such-id"})
    assert out["classification"] == "unknown"
    assert out["next_actions"]


def test_diagnostics_explain_failure_with_synthetic_row() -> None:
    row = {
        "id": "call-1",
        "tool": "shell.run_command",
        "status": "failed",
        "ok": 0,
        "error_code": "denied_by_rule",
        "blocked_by": "rule:block-privilege-escalation",
        "log_path": None,
        "workspace_id": None,
    }
    out = diagnostics_explain_failure({"row": row})
    assert out["classification"] == "denied_by_rule"
    assert out["rule_id"] == "block-privilege-escalation"
    assert any("block-privilege-escalation" in item for item in out["next_actions"])


def test_diagnostics_collect_returns_summary_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point data dir at tmp so the audit DB doesn't pollute the user's
    # %APPDATA%. No calls are actually recorded here.
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    out = diagnostics_collect({"depth": "summary"})
    assert "sections" in out
    assert "runtime" in out["sections"]
    assert "next_actions" in out
    # The ports + problems sections are always present.
    assert "ports" in out["sections"]
    assert "problems" in out["sections"]


def test_diagnostics_collect_rejects_invalid_depth() -> None:
    out = diagnostics_collect({"depth": "huge"})
    assert out["error"]["code"] == "invalid_args"


def test_diagnostics_collect_full_depth_returns_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    out = diagnostics_collect({"depth": "full"})
    assert "handles" in out
    # handles is a dict keyed by section name.
    for section in ("git", "runtime", "problems", "ports", "recent_failures"):
        assert section in out["handles"]
