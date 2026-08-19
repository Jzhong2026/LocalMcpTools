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


def _save_image(image: Any, path: str) -> dict[str, Any]:
    """Persist a PIL image to ``path`` as PNG and return a result dict."""
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        image.save(path, format="PNG")
    except Exception as exc:  # noqa: BLE001
        _log.debug("screenshot save failed: %s", exc)
        return {"error": "save_failed", "code": "invalid_path", "message": str(exc)}
    return {
        "path": os.path.abspath(path),
        "width": int(image.width),
        "height": int(image.height),
    }


def _capture_image(
    *,
    mode: str = "window",
    hwnd: int | None = None,
    region: dict[str, int] | None = None,
) -> Any | dict[str, Any]:
    """Produce a PIL image for the requested capture, or an error dict.

    ``window`` mode prefers :func:`capture_window_bytes` (Win32
    ``PrintWindow``) so GPU-composited windows are captured correctly,
    falling back to ``ImageGrab`` if PrintWindow yields nothing.
    """
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
            data = capture_window_bytes(hwnd=hwnd)
            if data:
                from io import BytesIO as _B
                from PIL import Image as _I
                return _I.open(_B(data)).convert("RGB")
            bbox = _window_bbox(hwnd)
            if bbox is None:
                return {"error": "window_not_found"}
            return ImageGrab.grab(bbox=bbox)
        elif mode == "region":
            if region is None:
                return {"error": "region_required"}
            bbox = (
                int(region["x"]),
                int(region["y"]),
                int(region["x"]) + int(region["width"]),
                int(region["y"]) + int(region["height"]),
            )
            return ImageGrab.grab(bbox=bbox)
        elif mode == "full":
            return ImageGrab.grab()
        return {"error": "invalid_mode", "code": "invalid_args"}
    except Exception as exc:  # noqa: BLE001
        _log.debug("screenshot capture failed: %s", exc)
        return {"error": "capture_failed", "message": str(exc)}


def capture_to_file(
    *,
    path: str,
    mode: str = "window",
    hwnd: int | None = None,
    region: dict[str, int] | None = None,
    rate_key: str = "default",
) -> dict[str, Any]:
    """Capture a screenshot and write it directly to ``path`` as a PNG.

    Unlike :func:`capture`, this returns the on-disk file path instead of
    an artifact handle, so the resulting image can be opened / displayed
    directly. Path safety (whitelisting) is the caller's responsibility;
    this function only creates the file.

    Returns ``{path, width, height, mode}`` or an error dict.
    """
    if not SCREENSHOT_BUCKET.check(rate_key):
        return {
            "error": "rate_limit",
            "code": "rate_limit_exceeded",
            "message": f"screenshot rate limit ({DEFAULT_RATE_PER_MINUTE}/min) hit for {rate_key!r}",
            "next_actions": ["wait 60 seconds before retrying"],
        }
    image = _capture_image(mode=mode, hwnd=hwnd, region=region)
    if isinstance(image, dict):
        return image
    result = _save_image(image, path)
    if "error" in result:
        return result
    result["mode"] = mode
    return result


def capture_window_bytes(*, hwnd: int) -> bytes | None:
    """Capture a window's bytes directly, for in-process OCR consumers.

    NOT exposed as a tool — only callable from within the LocalMcpTools
    process. Used by :mod:`.ocr` to feed a window to the OCR provider
    without an artifact round-trip.

    Uses Win32 ``PrintWindow`` (PW_RENDERFULLCONTENT) instead of
    ``ImageGrab.grab`` so that GPU-composited windows (Electron / VS Code /
    hardware-accelerated UIs) are captured correctly instead of coming back
    as a black bitmap.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        from PIL import Image

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint32),
                ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", ctypes.c_uint32 * 3),
            ]

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        bbox = _window_bbox(hwnd)
        if bbox is None:
            return None
        left, top, right, bottom = bbox
        width = max(1, right - left)
        height = max(1, bottom - top)

        # Ensure the window is restored (not minimized -> offscreen -32000 coords)
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)

        hdc_screen = user32.GetWindowDC(hwnd)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        gdi32.SelectObject(hdc_mem, hbm)

        PW_RENDERFULLCONTENT = 2
        if user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT) == 0:
            # Fallback: try without the full-content flag
            if user32.PrintWindow(hwnd, hdc_mem, 0) == 0:
                gdi32.DeleteObject(hbm)
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(hwnd, hdc_screen)
                return None

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        buf = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(hdc_mem, hbm, 0, height, buf, ctypes.byref(bmi), 0)

        image = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1)
        image = image.convert("RGB")

        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_screen)

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
    "capture_to_file",
    "capture_window_bytes",
]