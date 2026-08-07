# Design: ui-automation-and-ocr

## Module layout (additions)

```text
src/localmcptools/
├── ui/
│   ├── __init__.py
│   ├── windows.py             # enumerate + filter + authorize
│   ├── actions.py             # click / type + verification harness
│   ├── screenshots.py         # full / window / region; streaming to artifact
│   └── verify.py              # uia / screenshot predicates
├── ocr/
│   ├── __init__.py
│   ├── provider.py            # OcrProvider protocol + registry
│   ├── windows_provider.py    # Windows.Media.Ocr via winrt OR pywinrt
│   └── result.py              # OcrBlock / OcrResult dataclasses
├── safety/
│   └── filters.py             # credential window denylist
└── tools/
    ├── ui.py                  # ui.get_ui_tree / find_element / click_element /
    │                          # ui.type_text / screenshot_* / act_and_verify
    └── ocr.py                 # ocr.ocr_region / find_text / assert_text

ui/src/app/features/automation/
├── automation.component.ts/html/css
└── widgets/
    ├── window-list.widget.ts
    ├── ui-tree-viewer.widget.ts
    └── ocr-preview.widget.ts

tests/
├── unit/
│   ├── test_windows_authorize.py
│   ├── test_credential_filter.py
│   ├── test_verify_predicates.py
│   └── test_ocr_provider.py
└── integration/
    ├── test_ui_get_tree.py
    ├── test_click_with_verification.py
    └── test_ocr_synthetic.py
```

## Window authorization data model

```sql
CREATE TABLE IF NOT EXISTS authorized_windows (
    window_id       TEXT PRIMARY KEY,
    window_title    TEXT NOT NULL,
    process_name    TEXT NOT NULL,
    pid             INTEGER NOT NULL,
    authorized_at   INTEGER NOT NULL,
    authorized_by   TEXT NOT NULL,         -- 'user' | 'auto'
    expires_at      INTEGER NOT NULL,
    revoked         INTEGER NOT NULL DEFAULT 0
);

-- schema_version -> 4 here
```

## OcrProvider interface

```python
# ocr/provider.py
from typing import Protocol
from dataclasses import dataclass
from PIL import Image

@dataclass
class OcrBlock:
    text: str
    confidence: float         # 0..1
    bounding_box: dict        # {x, y, width, height}
    line_index: int

@dataclass
class OcrResult:
    blocks: list[OcrBlock]
    full_text: str
    preprocessing: list[str]  # what we did before OCR
    provider_name: str
    provider_version: str
    languages: list[str]

class OcrProvider(Protocol):
    name: str
    version: str
    supported_languages: list[str]

    def ocr_image(self, image: Image.Image,
                  languages: list[str]) -> OcrResult: ...

# Registry: get_provider() -> the configured provider (single for now)
```

## Action + verify harness

```python
# ui/verify.py
class VerificationFailed(Exception): ...

@dataclass
class Predicate:
    kind: str            # 'uia' | 'screenshot' | 'ocr'
    spec: dict           # kind-specific

def verify(predicates: list[Predicate]) -> VerificationReport:
    report = VerificationReport(results=[])
    for p in predicates:
        if p.kind == 'uia':
            report.append(_check_uia(p.spec))
        elif p.kind == 'screenshot':
            report.append(_check_screenshot_diff(p.spec))
        elif p.kind == 'ocr':
            report.append(_check_ocr_text(p.spec))
    if not all(r.passed for r in report):
        raise VerificationFailed(report)
    return report
```

Audit row records the full Predicate set so future inspection can replay
the verification.

## Verification predicates (kind-specific)

### `uia`
```jsonc
{
  "kind": "uia",
  "node_ref": "<from find_element>",
  "expect": { "name": "Clicked", "isEnabled": true }
}
```

### `screenshot`
```jsonc
{
  "kind": "screenshot",
  "compare_to_handle": "<previous screenshot>",
  "diff_threshold": 0.02          // fraction of changed pixels
}
```

### `ocr`
```jsonc
{
  "kind": "ocr",
  "expected": "Auto Save: afterDelay",
  "match": "exact",
  "min_confidence": 0.7
}
```

## Source validation (REQ-OCR-5)

```python
ALLOWED_SOURCES = {"window_id", "screenshot_handle"}

def _resolve_source(source: str) -> tuple[Image.Image, dict]:
    if source.startswith("win:"):
        wid = source[4:]
        win = windows.get_authorized(wid)        # raises WindowNotAuthorized
        img = screenshots.capture_window(win)
        return img, {"source": "window_id", "window_id": wid}
    if source.startswith("art:"):
        art = artifacts.lookup(source)
        return _read_image(art.path), {"source": "screenshot_handle", "handle": source}
    raise SourceNotAllowed(source)
```

## Redaction in OCR pipeline

OCR text is post-processed through `safety.redact.redact_text` (from
change-2) before being persisted as an artifact. The OCR result stored in
`meta.ocr_full_text` is the **redacted** version.

## Config additions

```jsonc
{
  "ui": {
    "automation": {
      "enabled": false,
      "default_window_authorization_minutes": 60,
      "screenshot_rate_per_minute": 20,
      "ocr_min_confidence": 0.6,
      "ocr_supported_languages": ["en-US", "zh-Hans"]
    }
  },
  "ocr": {
    "provider": "windows_system",    // "windows_system" | "<future provider>"
    "fallback_provider": null
  }
}
```

## New dependencies

| Package | Why |
|---|---|
| `uiautomation` | Windows UI Automation traversal (already in plan) |
| `Pillow` | Image loading for OCR + screenshots |
| `winrt` or `winsdk` | Windows Runtime OCR access (TBD during spike) |

Lock after spike + integration test pass.

## Out-of-scope reminders

- No web DOM automation (no DevTools Protocol) in this change.
- No cross-window actions.
- No OCR on free-form paths.
- No bundled OCR model — first attempt uses Windows system OCR. If the
  spike fails the accuracy thresholds, ship without OCR (degrade to
  UIA-only verification) and revisit as a follow-up.