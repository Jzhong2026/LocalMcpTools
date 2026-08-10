"""Verification harness — REQ-UI-5.

Three predicate kinds:

- :class:`UIAPredicate` — re-query a UI element and compare fields.
- :class:`ScreenshotPredicate` — pixel-diff vs a previous screenshot
  handle, threshold for tolerance.
- :class:`OCRPredicate` — delegate to ``ocr.assert_text`` and assert
  the substring / regex / exact match.

All predicates share a common :meth:`Predicate.check` API so the
:func:`verify` orchestrator can run them sequentially and emit a single
report. The first failure aborts the rest (matching the OpenSpec
semantics — a click that triggers no UI change is a failure, not a
"try harder").
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .tree import attach_to_window

_log = logging.getLogger(__name__)


# --- Exceptions -----------------------------------------------------------


class VerificationFailed(AssertionError):
    """A predicate failed; carries the report in :attr:`report`."""

    def __init__(self, report: dict[str, Any]):
        super().__init__(report.get("summary", "verification failed"))
        self.report = report


# --- Predicate protocol ---------------------------------------------------


class Predicate(Protocol):
    """Predicate contract.

    Implementations must expose a :meth:`check` returning a
    :class:`PredicateResult`. They may attach arbitrary context to the
    result for the orchestrator.
    """

    name: str

    def check(self) -> "PredicateResult": ...


@dataclass
class PredicateResult:
    """Outcome of one predicate check."""

    passed: bool
    detail: str
    context: dict[str, Any] = field(default_factory=dict)


# --- UIA predicate --------------------------------------------------------


@dataclass
class UIAPredicate:
    """Re-query a UI element and verify expected fields.

    ``criterion`` is the same dict shape :func:`find.find_element`
    accepts (text / automationId / controlType / name). ``expected``
    maps field name → expected value (substring for strings, exact for
    ``automationId`` / ``controlType``).
    """

    window_id: str
    criterion: dict[str, Any]
    expected: dict[str, Any] = field(default_factory=dict)
    name: str = "uia"

    def check(self) -> PredicateResult:
        from .find import find_element

        matches = find_element(
            window_id=self.window_id,
            text=self.criterion.get("text"),
            automation_id=self.criterion.get("automationId"),
            control_type=self.criterion.get("controlType"),
            name=self.criterion.get("name"),
        )
        if not matches:
            return PredicateResult(
                passed=False,
                detail="no element matched the criterion",
                context={"criterion": self.criterion, "matches": []},
            )
        candidate = matches[0]
        failures: list[str] = []
        for key, expected in self.expected.items():
            actual = candidate.get(key, "")
            if key in {"automationId", "controlType"}:
                if str(actual).lower() != str(expected).lower():
                    failures.append(f"{key}: expected {expected!r}, got {actual!r}")
            else:
                if str(expected).lower() not in str(actual).lower():
                    failures.append(f"{key}: expected substring {expected!r}, got {actual!r}")
        if failures:
            return PredicateResult(
                passed=False,
                detail="; ".join(failures),
                context={"criterion": self.criterion, "candidate": candidate},
            )
        return PredicateResult(
            passed=True,
            detail="UIA element matched expectations",
            context={"criterion": self.criterion, "candidate": candidate},
        )


# --- Screenshot predicate -------------------------------------------------


@dataclass
class ScreenshotPredicate:
    """Compare a freshly-captured screenshot to a prior handle.

    Uses :class:`PIL.ImageChops` to compute a diff metric. The
    threshold is the fraction of pixels (0..1) allowed to differ; the
    default 0.02 (2 %) matches the OpenSpec REQ-UI-5 contract.
    """

    window_id: int
    reference_handle: str
    threshold: float = 0.02
    name: str = "screenshot"

    def check(self) -> PredicateResult:
        from .screens import capture
        from ..persistence import artifacts

        fresh = capture(mode="window", hwnd=self.window_id, rate_key=f"verify:{self.window_id}")
        if "error" in fresh:
            return PredicateResult(
                passed=False,
                detail=f"screenshot capture failed: {fresh['error']}",
                context={"error": fresh},
            )
        fresh_handle = fresh["handle"]

        # Read both images; compute a coarse pixel-diff metric.
        try:
            from PIL import Image, ImageChops
            import io

            ref_bytes = artifacts.lookup(self.reference_handle).path  # type: ignore[attr-defined]
            with open(ref_bytes, "rb") as fh:
                ref_image = Image.open(io.BytesIO(fh.read())).convert("RGB")
            with open(artifacts.lookup(fresh_handle).path, "rb") as fh:  # type: ignore[attr-defined]
                new_image = Image.open(io.BytesIO(fh.read())).convert("RGB")
            if ref_image.size != new_image.size:
                return PredicateResult(
                    passed=False,
                    detail="screenshot size mismatch",
                    context={"reference_size": ref_image.size, "fresh_size": new_image.size},
                )
            diff = ImageChops.difference(ref_image, new_image)
            bbox = diff.getbbox()
            if bbox is None:
                return PredicateResult(passed=True, detail="screenshots identical")
            total = ref_image.size[0] * ref_image.size[1]
            # Cheap metric: fraction of pixels that differ in at least
            # one channel. Good enough for a sanity check.
            differing = 0
            for x in range(ref_image.size[0]):
                for y in range(ref_image.size[1]):
                    if diff.getpixel((x, y)) != (0, 0, 0):
                        differing += 1
                        if differing / total > self.threshold:
                            return PredicateResult(
                                passed=False,
                                detail="diff exceeds threshold",
                                context={
                                    "threshold": self.threshold,
                                    "differing_fraction": differing / total,
                                    "fresh_handle": fresh_handle,
                                },
                            )
            return PredicateResult(
                passed=True,
                detail=f"diff within threshold ({differing / total:.4f})",
                context={"fresh_handle": fresh_handle},
            )
        except Exception as exc:  # noqa: BLE001
            return PredicateResult(
                passed=False,
                detail=f"screenshot diff failed: {exc}",
                context={"fresh_handle": fresh_handle},
            )


# --- OCR predicate --------------------------------------------------------


@dataclass
class OCRPredicate:
    """Delegate to ``ocr.assert_text`` (loaded lazily to avoid cycles).

    ``match`` is one of ``"exact" | "contains" | "regex"``. The
    predicate fails when the OCR pass returns ``uncertain=True`` —
    OpenSpec REQ-OCR-6 forbids "passed" verdicts on uncertain input.
    """

    window_id: str
    expected: str
    match: str = "contains"
    name: str = "ocr"

    def check(self) -> PredicateResult:
        from .ocr import ocr_assert_text

        result = ocr_assert_text(
            source={"window_id": self.window_id},
            expected=self.expected,
            match=self.match,
        )
        passed = bool(result.get("passed")) and not bool(result.get("uncertain"))
        detail = (
            "OCR match passed"
            if passed
            else f"OCR match failed: {result.get('actual_text') or '<no text>'}"
        )
        return PredicateResult(
            passed=passed,
            detail=detail,
            context={"result": result},
        )


# --- Orchestrator ---------------------------------------------------------


def verify(
    *,
    predicates: list[Predicate],
) -> dict[str, Any]:
    """Run every predicate in order. First failure aborts.

    Returns ``{passed: bool, predicates: [{name, passed, detail, context}], summary: str}``.
    Raises :class:`VerificationFailed` on the first failure so the
    caller can short-circuit (e.g. emit a single audit row).
    """
    report: list[dict[str, Any]] = []
    for predicate in predicates:
        try:
            result = predicate.check()
        except Exception as exc:  # noqa: BLE001
            result = PredicateResult(passed=False, detail=f"predicate raised: {exc}")
        report.append(
            {
                "name": getattr(predicate, "name", "predicate"),
                "passed": result.passed,
                "detail": result.detail,
                "context": result.context,
            }
        )
        if not result.passed:
            return {
                "passed": False,
                "predicates": report,
                "summary": f"verification failed at predicate {len(report) - 1} ({report[-1]['name']}): {result.detail}",
            }
    return {
        "passed": True,
        "predicates": report,
        "summary": "all predicates passed",
    }


__all__ = [
    "OCRPredicate",
    "Predicate",
    "PredicateResult",
    "ScreenshotPredicate",
    "UIAPredicate",
    "VerificationFailed",
    "verify",
]