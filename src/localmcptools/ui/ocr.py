"""OCR provider interface + Windows provider + text-match helpers.

Three surfaces:

- :class:`OcrProvider` — abstract interface every OCR backend
  implements. The provider takes a PIL image and returns a list of
  ``OcrBlock`` records with bounding boxes + confidence.

- :func:`get_provider` — returns the platform's best provider. On
  Windows it tries ``WindowsOcrProvider`` (using
  ``Windows.Media.Ocr`` via ``winsdk``); on failure / non-Windows it
  falls back to :class:`StubProvider` which emits
  ``uncertain=True, blocks=[], full_text=""`` for every call. This
  keeps the agent's contract identical across platforms; the spike
  report can later record actual accuracy numbers once a real
  provider is wired in.

- :func:`ocr_region` / :func:`ocr_find_text` / :func:`ocr_assert_text` —
  the three tool-level helpers. They accept a source (``window_id``
  or ``screenshot_handle``), run the provider, and shape the result
  for the agent.

Every OCR full_text string passes through :func:`safety.redact.redact`
before persisting, per REQ-OCR-6.
"""

from __future__ import annotations

import io
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from ..safety.redact import redact
from .screens import capture, capture_window_bytes

_log = logging.getLogger(__name__)


# --- Provider interface ---------------------------------------------------


@dataclass
class OcrBlock:
    text: str
    confidence: float
    bounding_box: dict[str, int]


@dataclass
class OcrResult:
    blocks: list[OcrBlock]
    full_text: str
    uncertain: bool
    source_handle: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocks": [
                {
                    "text": b.text,
                    "confidence": b.confidence,
                    "bounding_box": b.bounding_box,
                }
                for b in self.blocks
            ],
            "full_text": self.full_text,
            "uncertain": self.uncertain,
            "source_handle": self.source_handle,
        }


class OcrProvider(Protocol):
    name: str

    def ocr_image(self, image_bytes: bytes) -> OcrResult: ...


# --- Stub fallback --------------------------------------------------------


class StubProvider:
    """Provider that always reports ``uncertain=True``.

    Used when no real provider is available (no ``winsdk`` on the host,
    or running on Linux/macOS). Returning uncertain keeps the agent's
    contract honest: it can never claim "passed" on OCR it didn't
    actually do.
    """

    name = "stub"

    def ocr_image(self, image_bytes: bytes) -> OcrResult:
        return OcrResult(blocks=[], full_text="", uncertain=True)


# --- Windows provider -----------------------------------------------------


class WindowsOcrProvider:
    """OCR via ``Windows.Media.Ocr`` (Windows 10+).

    Implementation notes:

    - ``winsdk`` ships the WinRT projections; we project
      ``OcrEngine`` + ``SoftwareBitmap`` and feed PNG bytes through
      ``BitmapDecoder``.
    - Languages default to ``en-US`` + ``zh-Hans-CN`` per REQ-OCR-1
      (mixed CJK/English Windows hosts).
    - The provider marks ``uncertain=True`` whenever confidence falls
      below 0.5 (per the implementation plan's pre-flight check).

    Any failure during initialisation falls back to :class:`StubProvider`
    so the public surface stays the same.
    """

    name = "windows_media_ocr"

    def __init__(self, languages: tuple[str, ...] = ("en-US", "zh-Hans-CN")) -> None:
        self._languages = languages
        self._engine = None
        self._available = False
        self._init_error: str | None = None
        try:
            import winsdk  # type: ignore[import-untyped]  # noqa: F401
            import winsdk.windows.media.ocr as win_ocr  # type: ignore[import-untyped]
            import winsdk.windows.globalization as win_glob  # type: ignore[import-untyped]
            import winsdk.windows.graphics.imaging as win_img  # type: ignore[import-untyped]
            import winsdk.windows.storage.streams as win_streams  # type: ignore[import-untyped]

            languages_list = []
            for lang in self._languages:
                try:
                    languages_list.append(win_ocr.OcrEngine.available_recognizer_languages)
                except Exception:  # noqa: BLE001
                    pass
            # The winsdk API is verbose; keep this in a helper so the
            # contract above stays clean.
            self._engine = _build_engine(win_ocr, win_glob, win_img, win_streams, languages)
            self._available = True
        except Exception as exc:  # noqa: BLE001 — winsdk may not be present
            self._init_error = str(exc)
            _log.debug("WindowsOcrProvider unavailable: %s", exc)
            self._available = False

    def ocr_image(self, image_bytes: bytes) -> OcrResult:
        if not self._available or self._engine is None:
            return OcrResult(blocks=[], full_text="", uncertain=True)
        try:
            return self._engine(image_bytes)
        except Exception as exc:  # noqa: BLE001
            _log.debug("WindowsOcrProvider ocr_image failed: %s", exc)
            return OcrResult(blocks=[], full_text="", uncertain=True)


# One dedicated thread so the blocking WinRT OCR call never lands on the
# MCP server's event loop, and never spawns a thread per screenshot.
_OCR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lmcp-ocr")


def _build_engine(win_ocr: Any, win_glob: Any, win_img: Any, win_streams: Any, languages: tuple[str, ...]):
    """Helper that wires up the Windows OCR engine.

    Lives outside :meth:`WindowsOcrProvider.__init__` so the import
    surface stays narrow and the test mocks don't have to patch every
    ``winsdk`` submodule.
    """
    # This function is intentionally a thin shim around the winsdk
    # projection. It returns a callable ``(bytes) -> OcrResult`` so the
    # provider's contract is decoupled from any particular winsdk
    # version.
    def _run(image_bytes: bytes) -> OcrResult:
        import asyncio

        async def _do() -> OcrResult:
            # Resolve a recognizer for the first available language.
            engine = None
            for lang in languages:
                try:
                    language = win_glob.Language(lang)
                    engine = win_ocr.OcrEngine.try_create_from_language(language)
                    if engine is not None:
                        break
                except Exception:  # noqa: BLE001
                    continue
            if engine is None:
                engine = win_ocr.OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                return OcrResult(blocks=[], full_text="", uncertain=True)

            stream = win_streams.InMemoryRandomAccessStream()
            writer = win_streams.DataWriter(stream)
            writer.write_bytes(image_bytes)
            await writer.store_async()
            await writer.flush_async()
            stream.seek(0)
            decoder = await win_img.BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            result = await engine.recognize_async(bitmap)
            blocks: list[OcrBlock] = []
            for line in result.lines:
                confidence = float(getattr(line, "confidence", 1.0)) if hasattr(line, "confidence") else 1.0
                text = line.text
                try:
                    rect = line.bounding_rect
                    box = {
                        "x": int(rect.x),
                        "y": int(rect.y),
                        "width": int(rect.width),
                        "height": int(rect.height),
                    }
                except Exception:  # noqa: BLE001
                    box = {"x": 0, "y": 0, "width": 0, "height": 0}
                blocks.append(OcrBlock(text=text, confidence=confidence, bounding_box=box))
            full = "\n".join(block.text for block in blocks)
            uncertain = any(b.confidence < 0.5 for b in blocks)
            return OcrResult(blocks=blocks, full_text=full, uncertain=uncertain)

        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # No loop on this thread — drive the coroutine directly.
                return asyncio.run(_do())
            # A loop is already running here, which is the normal case:
            # the MCP server is async and tool bodies execute on its event
            # loop thread. ``loop.run_until_complete`` on a *second* loop
            # raises ``Cannot run the event loop while another loop is
            # running``, the broad handler below swallowed it, and every
            # ``ocr.*`` call returned an empty result that looked like
            # "the screen contains no text". WinRT objects are created
            # inside ``_do``, so running the whole thing on a dedicated
            # worker thread keeps them on one thread from birth to death.
            return _OCR_EXECUTOR.submit(lambda: asyncio.run(_do())).result()
        except Exception as exc:  # noqa: BLE001
            # A swallowed OCR failure is indistinguishable from a blank
            # screen, so it is a warning, not a debug line.
            _log.warning("OCR run failed: %s: %s", type(exc).__name__, exc)
            return OcrResult(blocks=[], full_text="", uncertain=True)

    return _run


# --- Provider singleton ---------------------------------------------------


_PROVIDER: OcrProvider | None = None


def get_provider() -> OcrProvider:
    """Return the platform's best OCR provider, lazily constructed."""
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER
    if os.name == "nt":
        provider = WindowsOcrProvider()
        if provider._available:  # type: ignore[attr-defined]
            _PROVIDER = provider
            return _PROVIDER
    _PROVIDER = StubProvider()
    return _PROVIDER


def reset_provider() -> None:
    """Test hook: drop the singleton so :func:`get_provider` rebuilds."""
    global _PROVIDER
    _PROVIDER = None


# --- Tool-level helpers ----------------------------------------------------


def _error(code: str, message: str, *, suggestion: str | None = None) -> dict[str, Any]:
    """Build the module-level ``error`` payload shape callers expect."""
    payload: dict[str, Any] = {"code": code, "message": message}
    if suggestion:
        payload["suggestion"] = suggestion
    return {"error": payload}


def _opt(value: Any) -> str | None:
    """Return a non-blank source value, ignoring ``None`` / ``"None"``."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text == "None":
        return None
    return text


# Magic-byte prefixes for the image containers ``BitmapDecoder`` accepts.
# Sniffing here means a caller who points ``screenshot_handle`` at a text
# artifact (say, one from ``output.tail``) gets a named error instead of
# an opaque ``HRESULT -2003292336 The component cannot be found`` from
# deep inside WinRT.
_IMAGE_MAGICS: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"BM",  # BMP
    b"GIF87a",  # GIF
    b"GIF89a",  # GIF
    b"II*\x00",  # TIFF, little-endian
    b"MM\x00*",  # TIFF, big-endian
)


def _looks_like_image(data: bytes) -> bool:
    """True when ``data`` starts with a known image container signature."""
    return any(data.startswith(magic) for magic in _IMAGE_MAGICS)


def _resolve_bytes(
    source: dict[str, Any],
) -> tuple[bytes | None, str | None, dict[str, Any] | None]:
    """Resolve ``source`` to ``(image_bytes, artifact_handle, error)``.

    Accepts exactly one of:

    - ``{"window_id": "..."}`` — capture the authorised window
    - ``{"screenshot_handle": "art://..."}`` — read the artifact

    The third element is ``None`` on success. Failures are *classified*
    rather than collapsed into one sentinel, because they have different
    fixes:

    - neither key carries a usable value → ``source_not_allowed``
    - ``window_id`` is not in the authorisation registry → ``window_not_authorized``
    - ``screenshot_handle`` is not a known artifact → ``artifact_not_found``
    - ``screenshot_handle`` is a real artifact holding text, not pixels → ``invalid_args``
    - source is valid but pixels could not be produced → ``capture_failed``

    Collapsing all of these into ``(None, None)`` used to make a dead GDI
    pipeline report "source must be a window_id or screenshot_handle",
    sending the caller off to re-check arguments that were already right.
    """
    window_id = _opt(source.get("window_id"))
    handle = _opt(source.get("screenshot_handle"))

    if window_id is not None:
        from .windows import lookup  # local — avoid cycle

        row = lookup(window_id=window_id)
        if row is None:
            return (
                None,
                None,
                _error(
                    "window_not_authorized",
                    f"window_id {window_id!r} is not authorised",
                    suggestion="call ui_authorize_window first",
                ),
            )
        image_bytes = capture_window_bytes(hwnd=row.hwnd)
        if image_bytes is None:
            return (
                None,
                None,
                _error(
                    "capture_failed",
                    f"could not capture pixels from authorised window hwnd={row.hwnd}",
                    suggestion="bring the window to the foreground and retry",
                ),
            )
        return image_bytes, None, None

    if handle is not None:
        from ..persistence import artifacts

        try:
            path = artifacts.lookup(handle)
        except artifacts.ArtifactNotFound:
            return (
                None,
                None,
                _error(
                    "artifact_not_found",
                    f"screenshot_handle {handle!r} is not a known artifact",
                    suggestion="pass the handle returned by ui_screenshot_window",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return (
                None,
                None,
                _error(
                    "internal_error",
                    f"failed to resolve screenshot_handle {handle!r}: "
                    f"{type(exc).__name__}: {exc}",
                ),
            )
        if path is None:
            return (
                None,
                None,
                _error(
                    "artifact_not_found",
                    f"screenshot_handle {handle!r} is not a known artifact",
                    suggestion="pass the handle returned by ui_screenshot_window",
                ),
            )
        try:
            with open(path.path, "rb") as fh:  # type: ignore[attr-defined]
                raw = fh.read()
        except OSError as exc:
            return (
                None,
                None,
                _error(
                    "capture_failed",
                    f"could not read artifact {handle!r}: {exc}",
                    suggestion="re-capture the window and retry",
                ),
            )
        if not _looks_like_image(raw):
            head = raw[:8].hex(" ") or "<empty>"
            return (
                None,
                None,
                _error(
                    "invalid_args",
                    f"screenshot_handle {handle!r} is a known artifact but "
                    f"does not contain an image (first bytes: {head})",
                    suggestion=(
                        "pass the handle returned by ui_screenshot_window, "
                        "not a text artifact from output.*"
                    ),
                ),
            )
        return raw, handle, None

    return (
        None,
        None,
        _error("source_not_allowed", "source must be a window_id or screenshot_handle"),
    )


def ocr_region(*, source: dict[str, Any]) -> dict[str, Any]:
    """Run OCR over ``source`` and return the structured result."""
    image_bytes, handle, error = _resolve_bytes(source)
    if image_bytes is None:
        return error or _error("internal_error", "ocr produced no image bytes")
    provider = get_provider()
    result = provider.ocr_image(image_bytes)
    redacted_text, _ = redact(result.full_text)
    result.full_text = redacted_text
    if handle:
        result.source_handle = handle
    return result.to_dict()


def ocr_find_text(
    *,
    query: str,
    source: dict[str, Any],
    match: str = "contains",
    fuzzy: bool = False,
) -> dict[str, Any]:
    """Search OCR output for ``query``. Returns matched blocks + matched flag."""
    region = ocr_region(source=source)
    if "error" in region:
        return region
    if region.get("uncertain") and not fuzzy:
        return {
            "matched": False,
            "uncertain": True,
            "matches": [],
            "next_actions": ["retry with fuzzy=true or improve image quality"],
        }
    pattern = _compile(query, match)
    matches: list[dict[str, Any]] = []
    for block in region.get("blocks", []):
        text = block["text"]
        if pattern is None:
            continue
        if pattern.search(text):
            matches.append(
                {
                    "text": text,
                    "confidence": block["confidence"],
                    "bounding_box": block["bounding_box"],
                }
            )
    return {
        "matched": bool(matches),
        "uncertain": bool(region.get("uncertain")),
        "matches": matches,
        "source_handle": region.get("source_handle"),
    }


def ocr_assert_text(
    *,
    source: dict[str, Any],
    expected: str,
    match: str = "contains",
) -> dict[str, Any]:
    """Assert that ``expected`` appears in the OCR output.

    Returns ``{passed, actual_text, matches, min_confidence,
    evidence_handle, uncertain}``. Uncertainty forces
    ``passed: False`` per REQ-OCR-6.
    """
    region = ocr_region(source=source)
    if "error" in region:
        return {
            "passed": False,
            "actual_text": None,
            "matches": [],
            "min_confidence": None,
            "evidence_handle": None,
            "uncertain": False,
            "error": region["error"],
        }
    pattern = _compile(expected, match)
    full_text = region.get("full_text") or ""
    matches: list[dict[str, Any]] = []
    for block in region.get("blocks", []):
        if pattern is not None and pattern.search(block["text"]):
            matches.append(block)
    min_confidence = (
        min((b["confidence"] for b in matches), default=None)
    )
    uncertain = bool(region.get("uncertain"))
    passed = bool(matches) and not uncertain
    return {
        "passed": passed,
        "actual_text": full_text,
        "matches": matches,
        "min_confidence": min_confidence,
        "evidence_handle": region.get("source_handle"),
        "uncertain": uncertain,
    }


def _compile(query: str, match: str):
    if match == "regex":
        try:
            return re.compile(query)
        except re.error:
            return None
    if match == "exact":
        return re.compile(rf"^{re.escape(query)}$")
    # contains is the default; match anything containing the literal.
    return re.compile(re.escape(query))


__all__ = [
    "OcrBlock",
    "OcrProvider",
    "OcrResult",
    "StubProvider",
    "WindowsOcrProvider",
    "get_provider",
    "ocr_assert_text",
    "ocr_find_text",
    "ocr_region",
    "reset_provider",
]