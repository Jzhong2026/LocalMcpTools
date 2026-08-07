"""Tests for the tool response envelope.

Covers the spike DoD bullets:

- :class:`ToolMeta`, :class:`ToolError`, :class:`ToolResponse` exist and
  match the design.md shapes exactly.
- Every standard error code serialises to JSON without leaking Python
  internals (no ``repr``, no ``Exception`` tracebacks, no ``pydantic``
  objects).
"""

from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from localmcptools.tools._common import (
    STANDARD_ERROR_CODES,
    ToolError,
    ToolMeta,
    ToolResponse,
)


def _sample_meta() -> ToolMeta:
    return ToolMeta(
        tool="workspace.inspect",
        duration_ms=12,
        audit_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
    )


def test_meta_required_fields() -> None:
    m = _sample_meta()
    assert m.tool == "workspace.inspect"
    assert m.duration_ms == 12
    assert m.audit_id
    assert m.run_id
    # Optional defaults.
    assert m.log_path is None
    assert m.output_handle is None
    assert m.next_actions == []


def test_meta_rejects_negative_duration() -> None:
    with pytest.raises(ValidationError):
        ToolMeta(
            tool="x",
            duration_ms=-1,
            audit_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
        )


def test_meta_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ToolMeta(
            tool="x",
            duration_ms=0,
            audit_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            not_a_field="nope",
        )


def test_error_minimum_fields() -> None:
    e = ToolError(code="internal_error", message="boom")
    assert e.code == "internal_error"
    assert e.message == "boom"
    assert e.suggestion is None
    assert e.approval_id is None


def test_response_ok_shape() -> None:
    meta = _sample_meta()
    r = ToolResponse.ok_response(data={"pid": 1234, "build": "spike-0"}, meta=meta)
    assert r.ok is True
    assert r.data == {"pid": 1234, "build": "spike-0"}
    assert r.error is None
    # JSON round-trip preserves shape.
    blob = r.model_dump_json()
    parsed = json.loads(blob)
    assert parsed["ok"] is True
    assert parsed["data"] == {"pid": 1234, "build": "spike-0"}
    assert parsed["meta"]["tool"] == "workspace.inspect"


def test_response_error_shape() -> None:
    meta = _sample_meta()
    r = ToolResponse.error_response(
        code="approval_required",
        message="needs user approval",
        meta=meta,
        suggestion="ask user for approval",
        approval_id=None,
    )
    assert r.ok is False
    assert r.data is None
    assert r.error is not None
    assert r.error.code == "approval_required"
    assert r.error.suggestion == "ask user for approval"
    blob = r.model_dump_json()
    parsed = json.loads(blob)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "approval_required"


def test_error_response_rejects_unknown_code() -> None:
    meta = _sample_meta()
    with pytest.raises(ValueError, match="unknown error code"):
        ToolResponse.error_response(
            code="some_new_code",  # not in registry
            message="...",
            meta=meta,
        )


@pytest.mark.parametrize("code", list(STANDARD_ERROR_CODES))
def test_every_standard_code_serialises_cleanly(code: str) -> None:
    """All four spike error codes must round-trip via JSON without leaking Python objects."""
    meta = _sample_meta()
    r = ToolResponse.error_response(code=code, message=f"failure: {code}", meta=meta)
    blob = r.model_dump_json()
    parsed = json.loads(blob)

    # Shape: top-level keys.
    assert set(parsed.keys()) == {"ok", "data", "meta", "error"}
    assert parsed["ok"] is False
    assert parsed["data"] is None
    assert parsed["error"]["code"] == code
    assert isinstance(parsed["error"]["message"], str)
    assert isinstance(parsed["meta"], dict)

    # No Python repr / traceback / class names anywhere.
    raw = blob.lower()
    for leak in ("traceback", "exception", "pydantic", "<built-in", "builtins"):
        assert leak not in raw, f"{code}: serialized body contains {leak!r}"


def test_error_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ToolError(code="internal_error", message="x", not_a_field=1)


def test_response_forbids_extra_fields() -> None:
    meta = _sample_meta()
    with pytest.raises(ValidationError):
        ToolResponse(ok=True, data=1, meta=meta, error=None, extra="x")


def test_standard_codes_are_unique() -> None:
    assert len(STANDARD_ERROR_CODES) == len(set(STANDARD_ERROR_CODES))


def test_meta_next_actions_default_is_independent_per_instance() -> None:
    """The Field(default_factory=[]) must give each instance its own list."""
    m1 = _sample_meta()
    m2 = _sample_meta()
    m1.next_actions.append("show_audit")
    assert m2.next_actions == []
