"""Classification of a single failed or slow tool run.

The classifier takes the audit row + its (possibly truncated) log and
returns a stable :class:`Classification` enum plus a one-line
``summary``. The agent uses the classification to decide which
``next_actions`` from :mod:`.next_actions` to attach and to power
``diagnostics.explain_failure``.

The classifier is intentionally **stateless and pure** — no DB
connection, no I/O. It receives an audit-shaped dict and returns a
result. Callers that need to fetch the audit row do so themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Classification(StrEnum):
    """Stable labels for failure modes.

    New values land in their own OpenSpec change so existing UI can
    keep matching on the old strings.
    """

    SUCCESS = "success"
    TIMEOUT = "timeout"
    EXIT_CODE = "exit_code"
    DENIED_BY_RULE = "denied_by_rule"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_DIGEST_MISMATCH = "approval_digest_mismatch"
    VERIFICATION_FAILED = "verification_failed"
    INSUFFICIENT_CAPABILITY = "insufficient_capability"
    UNKNOWN = "unknown"


_CLASSIFICATION_ORDER: tuple[Classification, ...] = (
    Classification.TIMEOUT,
    Classification.DENIED_BY_RULE,
    Classification.APPROVAL_REQUIRED,
    Classification.APPROVAL_EXPIRED,
    Classification.APPROVAL_DIGEST_MISMATCH,
    Classification.VERIFICATION_FAILED,
    Classification.INSUFFICIENT_CAPABILITY,
    Classification.EXIT_CODE,
)


_SUMMARIES: dict[Classification, str] = {
    Classification.SUCCESS: "the call completed successfully",
    Classification.TIMEOUT: "the call exceeded its timeout",
    Classification.EXIT_CODE: "the underlying command exited non-zero",
    Classification.DENIED_BY_RULE: "the safety rules denied the call",
    Classification.APPROVAL_REQUIRED: "human approval was required but missing",
    Classification.APPROVAL_EXPIRED: "the approval had already expired",
    Classification.APPROVAL_DIGEST_MISMATCH: "the approval was bound to a different action",
    Classification.VERIFICATION_FAILED: "a UI verification check failed",
    Classification.INSUFFICIENT_CAPABILITY: "the workspace profile cannot perform this action",
    Classification.UNKNOWN: "the failure mode could not be classified",
}


@dataclass(frozen=True)
class ClassificationResult:
    """The output of :func:`classify`."""

    classification: Classification
    summary: str
    rule_id: str | None = None
    exit_code: int | None = None


def classify(run: dict[str, Any], log_lines: list[str] | None = None) -> ClassificationResult:
    """Classify one audit row.

    ``run`` is the row dict from the ``calls`` table. ``log_lines`` is
    optional — used only as a fallback when the row doesn't carry
    enough metadata.
    """
    ok = bool(run.get("ok"))
    error_code = str(run.get("error_code") or "")
    status = str(run.get("status") or "")
    blocked_by = run.get("blocked_by")
    rule_id: str | None = None
    if isinstance(blocked_by, str) and blocked_by.startswith("rule:"):
        rule_id = blocked_by.split(":", 1)[1]
    elif isinstance(blocked_by, str) and blocked_by:
        rule_id = blocked_by

    for label in _CLASSIFICATION_ORDER:
        # An empty match for both error_code and status means "no signal" —
        # skip rather than emit a false positive on a label whose lookup
        # function returns "" by default.
        expected_code = _error_code_for(label)
        expected_status = _status_for(label)
        if (expected_code and error_code == expected_code) or (
            expected_status and status == expected_status
        ):
            return ClassificationResult(
                classification=label,
                summary=_SUMMARIES[label],
                rule_id=rule_id if label is Classification.DENIED_BY_RULE else None,
            )

    # Heuristic: a timed-out status set by the runner.
    if status in ("timeout", "timed_out"):
        return ClassificationResult(
            classification=Classification.TIMEOUT,
            summary=_SUMMARIES[Classification.TIMEOUT],
        )

    if ok:
        return ClassificationResult(
            classification=Classification.SUCCESS,
            summary=_SUMMARIES[Classification.SUCCESS],
        )

    # Fallback: non-zero exit code with no error_code marker.
    exit_code = run.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return ClassificationResult(
            classification=Classification.EXIT_CODE,
            summary=_SUMMARIES[Classification.EXIT_CODE],
            exit_code=exit_code,
        )

    return ClassificationResult(
        classification=Classification.UNKNOWN,
        summary=_SUMMARIES[Classification.UNKNOWN],
    )


def _error_code_for(label: Classification) -> str:
    return {
        Classification.TIMEOUT: "timed_out",
        Classification.DENIED_BY_RULE: "denied_by_rule",
        Classification.APPROVAL_REQUIRED: "approval_required",
        Classification.APPROVAL_EXPIRED: "approval_expired",
        Classification.APPROVAL_DIGEST_MISMATCH: "approval_digest_mismatch",
        Classification.VERIFICATION_FAILED: "verification_failed",
        Classification.INSUFFICIENT_CAPABILITY: "insufficient_capability",
    }.get(label, "")


def _status_for(label: Classification) -> str:
    return {
        Classification.TIMEOUT: "timeout",
        Classification.APPROVAL_EXPIRED: "approval_expired",
    }.get(label, "")


__all__ = ["Classification", "ClassificationResult", "classify"]