"""Execution-layer helpers.

- :mod:`.service` — single chokepoint for tool invocation: it owns
  audit recording, envelope wiring, and exception → error-envelope
  conversion. Every tool published by :mod:`localmcptools.tools`
  dispatches through it so we cannot accidentally skip an audit row
  or leak a Python traceback into the agent context.
- :mod:`.tool_error` — the carrier exception that lets tools raise
  into typed error envelopes without coupling :mod:`tools._errors` to
  :mod:`execution.service`.
"""

from __future__ import annotations

from .service import ToolExecutionService, ToolLogic
from .tool_error import ToolErrorResponse

__all__ = ["ToolErrorResponse", "ToolExecutionService", "ToolLogic"]
