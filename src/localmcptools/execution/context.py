"""Per-invocation execution dependencies supplied by the chokepoint."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .concurrency import ConcurrencyGate

_GATE: ContextVar[ConcurrencyGate | None] = ContextVar("localmcptools_execution_gate", default=None)


@contextmanager
def use_gate(gate: ConcurrencyGate) -> Iterator[None]:
    token = _GATE.set(gate)
    try:
        yield
    finally:
        _GATE.reset(token)


def current_gate() -> ConcurrencyGate:
    gate = _GATE.get()
    if gate is None:
        raise RuntimeError("controlled execution must be invoked through ToolExecutionService")
    return gate


__all__ = ["current_gate", "use_gate"]
