"""Internal carrier exception shared by tool bodies and the chokepoint.

Lives in its own leaf module so neither :mod:`localmcptools.tools._errors`
(nor its callers) nor :mod:`localmcptools.execution.service` need to
import each other to define or catch this class.
"""

from __future__ import annotations

from ..tools._common import ToolResponse

__all__ = ["ToolErrorResponse"]


class ToolErrorResponse(Exception):
    """Internal carrier: lets a tool raise into a typed envelope.

    :func:`localmcptools.tools._errors.fail` constructs these; the
    chokepoint in :class:`ToolExecutionService` catches them, refreshes
    the meta fields, and writes the audit row.
    """

    def __init__(self, response: ToolResponse) -> None:
        super().__init__(response.error.message if response.error else "<error>")
        self.response = response
