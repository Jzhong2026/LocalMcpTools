"""Screenshot capture + per-agent rate limiting (REQ-UI-4).

Two surfaces:

- :func:`capture` — snapshot the desktop / window / region to an
  artifact; never return bytes inline.
- :class:`TokenBucket` — simple per-agent rate limiter. The default
  cap is 20 screenshots / minute per the OpenSpec REQ-UI-4 contract.

Screenshots are persisted via :func:`artifacts.write` with
``sensitive=True`` so the ACL is tightened to the current user. The
artifact handle is the only thing returned to the agent.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from ..persistence import artifacts

_log = logging.getLogger(__name__)

# Default cap per OpenSpec REQ-UI-4.
DEFAULT_RATE_PER_MINUTE = 20


@dataclass
class TokenBucket:
    """Per-key sliding-window counter.

    The bucket keeps a deque of timestamps (one per recent shot) and
    evicts anything older than the window. Concurrency is enforced with
    a lock so multiple HTTP requests from the same agent don't slip
    past the cap.
    """

    rate_per_minute: int = DEFAULT_RATE_PER_MINUTE
    window_seconds: float = 60.0
    _buckets: dict[str, deque[float]] = None  # type: ignore[assignment]
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._buckets = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """Return True if a new screenshot is allowed; False otherwise.

        The check consumes one token on success.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.rate_per_minute:
                return False
            bucket.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


# Module-level singleton so every caller sees the same cap.
SCREENSHOT_BUCKET = TokenBucket()


def capture(
    *,
    mode: str = "window",
    hwnd: int | None = None,
    region: dict[str, int] | None = None,
    rate_key: str = "default",
) -> dict[str, Any]:
    """Capture a screenshot and persist it.

    ``mode`` is one of:

    - ``"full"`` — the entire virtual desktop.
    - ``"window"`` — the window under ``hwnd`` (required for this mode).
    - ``"region"`` — the rectangle in ``region`` (``x/y/width/height``).

    Returns ``{handle, width, height, mode, taken_at}`` or
    ``{error: ..., code: ...}`` on failure. Bytes are NEVER returned
    inline.
    """
    if not SCREENSHOT_BUCKET.check(rate_key):
        return {
            "error": "rate_limit",
            "code": "rate_limit_exceeded",
            "message": f"screenshot rate limit ({DEFAULT_RATE_PER_MINUTE}/min) hit for {rate_key!r}",
            "next_actions": ["wait 60 seconds before retrying"],
        }
    if os.name != "nt":
        return {"error": "platform_unsupported"}

    try:
        from PIL import ImageGrab  # type: ignore[import-untyped]
    except ImportError:
        return {"error": "pillow_not_installed"}

    try:
        if mode == "window":
            if hwnd is None:
                return {"error": "hwnd_required"}
            bbox = _window_bbox(hwnd)
            if bbox is None:
                return {"error": "window_not_found"}
            image = ImageGrab.grab(bbox=bbox)
        elif mode == "region":
            if region is None:
                return {"error": "region_required"}
            bbox = (
                int(region["x"]),
                int(region["y"]),
                int(region["x"]) + int(region["width"]),
                int(region["y"]) + int(region["height"]),
            )
            image = ImageGrab.grab(bbox=bbox)
        elif mode == "full":
            image = ImageGrab.grab()
        else:
            return {"error": "invalid_mode", "code": "invalid_args"}
    except Exception as exc:  # noqa: BLE001 — ImageGrab raises on locked desktops
        _log.debug("screenshot capture failed: %s", exc)
        return {"error": "capture_failed", "message": str(exc)}

    try:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        encoded = buffer.getvalue()
        handle = artifacts.write(encoded, sensitive=True)
    except Exception as exc:  # noqa: BLE001
        _log.debug("screenshot artifact write failed: %s", exc)
        return {"error": "artifact_failed", "message": str(exc)}

    return {
        "handle": handle,
        "width": int(image.width),
        "height": int(image.height),
        "mode": mode,
        "taken_at": int(time.time() * 1000),
    }


def capture_window_bytes(*, hwnd: int) -> bytes | None:
    """Capture a window's bytes directly, for in-process OCR consumers.

    NOT exposed as a tool — only callable from within the LocalMcpTools
    process. Used by :mod:`.ocr` to feed a window to the OCR provider
    without an artifact round-trip.
    """
    if os.name != "nt":
        return None
    try:
        from PIL import ImageGrab  # type: ignore[import-untyped]

        bbox = _window_bbox(hwnd)
        if bbox is None:
            return None
        image = ImageGrab.grab(bbox=bbox)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _window_bbox(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return the screen-space rectangle for ``hwnd`` or None."""
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)) == 0:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "DEFAULT_RATE_PER_MINUTE",
    "SCREENSHOT_BUCKET",
    "TokenBucket",
    "capture",
    "capture_window_bytes",
]