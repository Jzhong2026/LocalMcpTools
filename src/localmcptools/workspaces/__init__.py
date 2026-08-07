"""Workspace registry + workspace-scoped helpers.

The registry is the single source of truth for "which directories may
side-effecting tools touch". Every tool that accepts a ``workspace_id``
goes through :func:`registry.resolve` to obtain the canonical root and
verifies any user-supplied path against it.
"""
