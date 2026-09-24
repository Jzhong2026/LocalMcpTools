"""``ocr.*`` MCP tools — read-only OCR over authorised windows or
artifact handles. All three tools require only the ``observe`` profile.

- :func:`ocr_ocr_region` — run OCR over a window / screenshot.
- :func:`ocr_find_text` — search the OCR output for a query.
- :func:`ocr_assert_text` — assert a query appears; uncertainty forces
  ``passed: false`` per REQ-OCR-6.

OCR text is redacted via :func:`safety.redact.redact` before
persisting, so a credential window that has its text OCR'd is still
treated as sensitive output.

Every failure path here goes through :func:`_errors.fail` so it lands
in the envelope's top-level ``error`` with ``ok: false``. The
:mod:`localmcptools.ui.ocr` module reports failures as an ``error`` key
nested inside its own result dict; returning that dict straight out of a
tool body would wrap it as ``ok: true`` with the error buried in
``data``. Any caller that only checks ``ok`` — an E2E script, an agent
loop — would then read a hard OCR failure as a success. :func:`_raise`
is the single place that translation happens.
"""

from __future__ import annotations

from typing import Any

from ..ui import ocr as ocr_module
from ._common import STANDARD_ERROR_CODES
from ._errors import fail


def _opt_str(value: Any) -> str | None:
    """Return a non-blank string, or ``None`` when absent / unusable."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _opt_bool(value: Any) -> bool:
    """Coerce a flag that may have arrived as a string.

    ``bool("false")`` is ``True``, so a client that stringifies every
    field would silently turn ``fuzzy`` on — which changes the answer
    (uncertain text starts matching) rather than just the encoding.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_source(args: dict[str, Any], *, tool: str) -> dict[str, Any]:
    """Map tool args to the ``source`` dict the OCR module expects.

    The chokepoint synthesises each tool wrapper with *every* declared
    parameter, defaulting the ones the agent omitted to ``None``. Testing
    ``"window_id" in args`` therefore always succeeded — even when the
    caller supplied only ``screenshot_handle`` — and the resulting
    ``{"window_id": "None"}`` never resolved, so all three ``ocr.*``
    tools failed with ``source must be a window_id or
    screenshot_handle`` no matter which argument the client sent.

    Resolve on a *non-blank value* instead, and reject the ambiguous
    both-at-once case rather than silently preferring one.
    """
    window_id = _opt_str(args.get("window_id"))
    handle = _opt_str(args.get("screenshot_handle"))
    if window_id is not None and handle is not None:
        fail(
            code="invalid_args",
            message="pass exactly one of window_id or screenshot_handle, not both",
            tool=tool,
            audit_id="pending",
            run_id="pending",
        )
    if window_id is not None:
        return {"window_id": window_id}
    if handle is not None:
        return {"screenshot_handle": handle}
    fail(
        code="invalid_args",
        message="window_id or screenshot_handle is required",
        tool=tool,
        audit_id="pending",
        run_id="pending",
    )
    raise AssertionError("unreachable — fail() always raises")


def _raise(result: dict[str, Any], *, tool: str) -> None:
    """Bubble a module-level ``error`` up as a real ``ok: false``.

    A no-op when the result carries no error, so callers only need to
    invoke it once after the module call.
    """
    error = result.get("error")
    if not error:
        return
    if isinstance(error, str):
        error = {"code": "internal_error", "message": error}
    code = str(error.get("code") or "internal_error")
    if code not in STANDARD_ERROR_CODES:
        # Never emit a code the envelope registry doesn't know about.
        code = "internal_error"
    message = str(error.get("message") or "ocr failed")
    fail(
        code=code,
        message=message,
        tool=tool,
        audit_id="pending",
        run_id="pending",
        suggestion=str(error.get("suggestion") or "") or None,
    )
    raise AssertionError("unreachable — fail() always raises")


def ocr_ocr_region(args: dict[str, Any]) -> Any:
    """Run OCR over ``window_id`` or ``screenshot_handle``."""
    tool = "ocr.ocr_region"
    source = _resolve_source(args, tool=tool)
    result = ocr_module.ocr_region(source=source)
    _raise(result, tool=tool)
    return result


def ocr_find_text(args: dict[str, Any]) -> Any:
    tool = "ocr.find_text"
    source = _resolve_source(args, tool=tool)
    query = args.get("query")
    if not isinstance(query, str) or not query:
        fail(
            code="invalid_args",
            message="query is required",
            tool=tool,
            audit_id="pending",
            run_id="pending",
        )
    result = ocr_module.ocr_find_text(
        query=query,
        source=source,
        match=str(args.get("match") or "contains"),
        fuzzy=_opt_bool(args.get("fuzzy")),
    )
    _raise(result, tool=tool)
    return result


def ocr_assert_text(args: dict[str, Any]) -> Any:
    tool = "ocr.assert_text"
    source = _resolve_source(args, tool=tool)
    expected = args.get("expected")
    if not isinstance(expected, str) or not expected:
        fail(
            code="invalid_args",
            message="expected is required",
            tool=tool,
            audit_id="pending",
            run_id="pending",
        )
    result = ocr_module.ocr_assert_text(
        source=source,
        expected=expected,
        match=str(args.get("match") or "contains"),
    )
    _raise(result, tool=tool)
    return result


__all__ = ["ocr_assert_text", "ocr_find_text", "ocr_ocr_region"]
