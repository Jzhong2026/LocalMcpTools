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
            # Prefer Win32 PrintWindow (with SW_RESTORE so minimized windows
            # are captured too), falling back to ImageGrab on failure. This
            # matches _capture_image so both artifact and file paths behave
            # the same for minimized / GPU-composited windows.
            picture = _capture_image(mode="window", hwnd=hwnd)
            if isinstance(picture, dict):
                return picture
            image = picture
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
        # binary=True is mandatory here: the default write() path decodes
        # its payload as UTF-8 before redacting, which replaces every
        # non-text byte with U+FFFD and leaves an artifact that no image
        # decoder can open. See artifacts.write()'s docstring.
        handle = artifacts.write(encoded, sensitive=True, binary=True)
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


def _declare_gdi(user32: Any, gdi32: Any) -> None:
    """Give every Win32 handle-returning call an explicit 64-bit signature.

    Without ``restype``, ctypes defaults to ``c_int``. On 64-bit Windows
    an ``HDC`` / ``HBITMAP`` is pointer-sized, so the 32-bit return value
    gets sign-extended into a *negative* Python int, and the next call
    raises ``ArgumentError: int too long to convert`` — e.g. a perfectly
    valid ``GetWindowDC`` result of ``0xFFFF8000_xxxxxxxx`` becomes
    ``-2147483648 + n``. The whole capture then died inside a bare
    ``except Exception: return None``, so every window reported as
    "no bytes" and OCR was dead on every host.
    """
    import ctypes
    from ctypes import wintypes

    user32.GetWindowDC.restype = wintypes.HDC
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.IsIconic.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.RedrawWindow.restype = wintypes.BOOL
    user32.RedrawWindow.argtypes = [
        wintypes.HWND,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.c_uint,
    ]
    user32.UpdateWindow.restype = wintypes.BOOL
    user32.UpdateWindow.argtypes = [wintypes.HWND]

    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.GetDIBits.restype = ctypes.c_uint
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]


def capture_window_bytes(*, hwnd: int) -> bytes | None:
    """Capture a window's bytes directly, for in-process OCR consumers.

    NOT exposed as a tool — only callable from within the LocalMcpTools
    process. Used by :mod:`.ocr` to feed a window to the OCR provider
    without an artifact round-trip.

    Uses Win32 ``PrintWindow`` (PW_RENDERFULLCONTENT) instead of
    ``ImageGrab.grab`` so that GPU-composited windows (Electron / VS Code /
    hardware-accelerated UIs) are captured correctly instead of coming back
    as a black bitmap.

    Unlike :func:`_capture_image` this has no ``ImageGrab`` fallback: it
    is the OCR feed, and OCR needs a single deterministic pixel source.
    That also means every Win32/GDI handle is declared explicitly — see
    :func:`_declare_gdi` for why a missing ``restype`` is fatal here.
    """
    if os.name != "nt":
        return None
    hdc_screen = None
    hdc_mem = None
    hbm = None
    old_obj = None
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
        _declare_gdi(user32, gdi32)

        bbox = _window_bbox(hwnd)
        if bbox is None:
            return None
        left, top, right, bottom = bbox
        width = max(1, right - left)
        height = max(1, bottom - top)

        # Ensure the window is restored (not minimized -> offscreen -32000 coords).
        # A minimized window's GetWindowRect returns the taskbar-button rectangle
        # (e.g. 160x28), so after restoring we re-read the rect to get the real
        # restored size and force a repaint before PrintWindow reads the DC.
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.2)
        rbbox = _window_bbox(hwnd)
        if rbbox is not None:
            left, top, right, bottom = rbbox
            width = max(1, right - left)
            height = max(1, bottom - top)
        user32.RedrawWindow(
            hwnd, None, None, 0x0001 | 0x0100
        )  # RDW_INVALIDATE | RDW_UPDATENOW
        user32.UpdateWindow(hwnd)

        hdc_screen = user32.GetWindowDC(hwnd)
        if not hdc_screen:
            _log.warning("GetWindowDC returned NULL for hwnd=%s", hwnd)
            return None
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            _log.warning("CreateCompatibleDC failed for hwnd=%s", hwnd)
            return None
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        if not hbm:
            _log.warning("CreateCompatibleBitmap failed for hwnd=%s (%sx%s)", hwnd, width, height)
            return None
        old_obj = gdi32.SelectObject(hdc_mem, hbm)

        PW_RENDERFULLCONTENT = 2
        if not user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT):
            # Fallback: try without the full-content flag
            if not user32.PrintWindow(hwnd, hdc_mem, 0):
                _log.warning("PrintWindow failed for hwnd=%s", hwnd)
                return None

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        buf = ctypes.create_string_buffer(width * height * 4)
        scanlines = gdi32.GetDIBits(hdc_mem, hbm, 0, height, buf, ctypes.byref(bmi), 0)
        if scanlines != height:
            _log.warning(
                "GetDIBits returned %s of %s scanlines for hwnd=%s",
                scanlines,
                height,
                hwnd,
            )

        image = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1)
        image = image.convert("RGB")

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001
        # This used to be a bare `return None`, which made every distinct
        # failure look like "this window has no pixels" — the reason a
        # total OCR outage was indistinguishable from an idle desktop.
        _log.warning("window capture failed for hwnd=%s: %s: %s", hwnd, type(exc).__name__, exc)
        return None
    finally:
        # Restore the stock object and release every GDI handle, including
        # on the early-return paths above.
        try:
            import ctypes as _ctypes

            user32 = _ctypes.windll.user32
            gdi32 = _ctypes.windll.gdi32
            _declare_gdi(user32, gdi32)
            if hdc_mem and old_obj:
                gdi32.SelectObject(hdc_mem, old_obj)
            if hbm:
                gdi32.DeleteObject(hbm)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            if hdc_screen:
                user32.ReleaseDC(hwnd, hdc_screen)
        except Exception:  # noqa: BLE001 — cleanup must never mask the result
            pass


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