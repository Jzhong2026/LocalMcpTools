"""Entry point for ``python -m localmcptools``.

Thin shim around :func:`localmcptools.cli.main`. Kept separate so that
``pyproject.toml``'s ``[project.scripts]`` can also re-export the same
function without two divergent code paths.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())