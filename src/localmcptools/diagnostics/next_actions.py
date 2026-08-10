"""Per-classification advice strings.

Each :class:`Classification` maps to a small list of stable
``next_actions`` strings the agent can act on. The strings are kept
short and stable so the UI can also localise them.
"""

from __future__ import annotations

from typing import Any

from .classify import Classification, ClassificationResult

_ADVICE: dict[Classification, list[str]] = {
    Classification.SUCCESS: [],
    Classification.TIMEOUT: [
        "raise timeout_ms and retry",
        "split the work into smaller commands",
        "use process.start_dev_server for long-running work",
    ],
    Classification.EXIT_CODE: [
        "open file:line from key_evidence in fs.tail_log_file",
        "re-run with --verbose or extra logging",
    ],
    Classification.DENIED_BY_RULE: [
        "review the safety rule that matched ({rule_id})",
        "request human approval at the UI for {rule_id}",
        "rewrite the command so it does not match {rule_id}",
    ],
    Classification.APPROVAL_REQUIRED: [
        "approve the pending approval_id in the UI",
        "call again with the approval_id returned in the response",
    ],
    Classification.APPROVAL_EXPIRED: [
        "request a new approval — the old one is past its 10 minute TTL",
    ],
    Classification.APPROVAL_DIGEST_MISMATCH: [
        "the action's arguments changed after the approval was issued",
        "request a new approval for the new arguments",
    ],
    Classification.VERIFICATION_FAILED: [
        "open the verification report in the UI",
        "narrow the verification predicate",
    ],
    Classification.INSUFFICIENT_CAPABILITY: [
        "ask the operator to grant the workspace_exec profile",
    ],
    Classification.UNKNOWN: [
        "provide log_handle manually",
        "consult audit.sqlite for the full row",
    ],
}


def advice_for(result: ClassificationResult | Classification) -> list[str]:
    """Return the next-action list for a classification result.

    Strings may include ``{rule_id}`` placeholders for the denied-by-
    rule case; callers should format them as needed.
    """
    label = result.classification if isinstance(result, ClassificationResult) else result
    items = list(_ADVICE.get(label, _ADVICE[Classification.UNKNOWN]))
    if isinstance(result, ClassificationResult) and result.rule_id:
        items = [item.format(rule_id=result.rule_id) for item in items]
    return items


__all__ = ["advice_for"]