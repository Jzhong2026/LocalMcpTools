"""Diagnostics aggregator for the agent.

Three modules:

- :mod:`.classify` — maps an audit row + its log lines into a
  :class:`Classification` (timeout / exit_code / denied_by_rule / ...).
- :mod:`.next_actions` — per-classification advice strings the agent
  can act on without re-asking the user.
- :mod:`.aggregate` — the ``diagnostics.collect`` aggregator that fans
  out to runtime, git, problems, ports, and recent failures.
"""

from __future__ import annotations

from .classify import Classification, classify
from .next_actions import advice_for

__all__ = ["Classification", "advice_for", "classify"]