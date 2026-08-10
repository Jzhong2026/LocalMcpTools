"""``ocr.*`` MCP tools — read-only OCR over authorised windows or
artifact handles. All three tools require only the ``observe`` profile.

- :func:`ocr_ocr_region` — run OCR over a window / screenshot.
- :func:`ocr_find_text` — search the OCR output for a query.
- :func:`ocr_assert_text` — assert a query appears; uncertainty forces
  ``passed: false`` per REQ-OCR-6.

OCR text is redacted via :func:`safety.redact.redact` before
persisting, so a credential window that has its text OCR'd is still
treated as sensitive output.
"""

from __future__ import annotations

from typing import Any

from ..ui import ocr as ocr_module


def _resolve_source(args: dict[str, Any]) -> dict[str, Any]:
    """Map tool args to the ``source`` dict the OCR module expects."""
    if "window_id" in args:
        return {"window_id": str(args["window_id"])}
    if "screenshot_handle" in args:
        return {"screenshot_handle": str(args["screenshot_handle"])}
    return {}


def ocr_ocr_region(args: dict[str, Any]) -> Any:
    """Run OCR over ``window_id`` or ``screenshot_handle``."""
    source = _resolve_source(args)
    if not source:
        return {
            "error": {
                "code": "invalid_args",
                "message": "window_id or screenshot_handle is required",
            }
        }
    region = args.get("region")
    if isinstance(region, dict):
        # Region cropping is a future enhancement; the stub pass-through
        # keeps the contract stable.
        pass
    result = ocr_module.ocr_region(source=source)
    return result


def ocr_find_text(args: dict[str, Any]) -> Any:
    source = _resolve_source(args)
    if not source:
        return {
            "error": {
                "code": "invalid_args",
                "message": "window_id or screenshot_handle is required",
            }
        }
    query = args.get("query")
    if not isinstance(query, str) or not query:
        return {
            "error": {"code": "invalid_args", "message": "query is required"}
        }
    return ocr_module.ocr_find_text(
        query=query,
        source=source,
        match=str(args.get("match", "contains")),
        fuzzy=bool(args.get("fuzzy", False)),
    )


def ocr_assert_text(args: dict[str, Any]) -> Any:
    source = _resolve_source(args)
    if not source:
        return {
            "error": {
                "code": "invalid_args",
                "message": "window_id or screenshot_handle is required",
            }
        }
    expected = args.get("expected")
    if not isinstance(expected, str) or not expected:
        return {
            "error": {"code": "invalid_args", "message": "expected is required"}
        }
    return ocr_module.ocr_assert_text(
        source=source,
        expected=expected,
        match=str(args.get("match", "contains")),
    )


__all__ = ["ocr_assert_text", "ocr_find_text", "ocr_ocr_region"]