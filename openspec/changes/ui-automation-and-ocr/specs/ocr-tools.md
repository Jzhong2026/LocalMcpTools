# Spec: `ocr.*` — OCR as evidence, not authority

## ADDED Requirements

### REQ-OCR-1: provider interface

```python
class OcrProvider(Protocol):
    name: str
    version: str
    supported_languages: list[str]

    def ocr_image(self, image: PIL.Image,
                  languages: list[str]) -> list[OcrBlock]: ...
```

OCR providers write their `{name, version, languages, params}` to every
artifact's audit meta. Misrecognition is reproducible.

### REQ-OCR-2: `ocr.ocr_region`

#### Scenario: OCR on an authorized window

- **Given** `window_id` is authorized
- **When** the agent calls
  `ocr.ocr_region({source: window_id, region?, languages=["zh-Hans","en"]})`
- **Then** the response is
  `data.blocks: [{text, confidence, boundingBox:{x,y,width,height}, lineIndex}]`,
  `data.full_text`, `data.uncertain: bool`, `data.preprocessing: [...]`

#### Scenario: OCR on a screenshot artifact

- **Given** a `screenshot_handle` from a prior `ui.screenshot_*` call
- **When** `ocr.ocr_region({source: screenshot_handle, ...})`
- **Then** the same shape is returned

#### Scenario: low confidence

- **Given** any block has `confidence < threshold` (default 0.6)
- **Then** `data.uncertain = true`
- **And** no call downstream can declare "verification passed" using only
  this OCR result

### REQ-OCR-3: `ocr.find_text`

#### Scenario: exact match

- **When** `query = "Submit"`, `match = "exact"`
- **Then** returns all blocks with text === "Submit", with coordinates

#### Scenario: contains

- **When** `match = "contains"`
- **Then** all blocks containing `query` as substring

#### Scenario: regex

- **When** `match = "regex"`
- **Then** all blocks whose text matches; patterns compiled safely
  (max length, timeout)

#### Scenario: fuzzy

- **When** `fuzzy = true`
- **Then** blocks with similarity > 0.7 to `query` (configurable threshold)

### REQ-OCR-4: `ocr.assert_text`

#### Scenario: assertion passes

- **When** `expected = "Auto Save: afterDelay"`, `match = "exact"`
- **Then** if a block exists with that text and confidence ≥ threshold,
  return `{passed: true, actual_text, matches, min_confidence, evidence_handle}`

#### Scenario: assertion fails

- **Then** `{passed: false, ...}` — **never** an exception

#### Scenario: uncertainty blocks assertion

- **Given** `uncertain = true` (any reason)
- **Then** `passed = false` and `data.reason = "uncertain"`

### REQ-OCR-5: rules

The following are mandatory regardless of implementation:

- OCR accepts only `window_id` or `screenshot_handle` as `source`. Any
  attempt to pass a free-form file path returns `error.code = "source_not_allowed"`.
- OCR text is treated as sensitive — it passes through the redactor before
  any artifact persist (same rules as `change-2`).
- `ocr_region` requires `interactive_ui` profile. **Observation** of OCR
  on an authorized window is still gated.

### REQ-OCR-6: provider selection

The first spike lands a `WindowsOcrProvider` based on Windows Runtime
`Windows.Media.Ocr` (no model bundled). Verification:

- English accuracy on synthetic fixture ≥ 95%
- Chinese accuracy on synthetic fixture ≥ 90% (when `zh-Hans` installed)
- Mixed Chinese/English/digits accuracy ≥ 85%
- Bounding box coordinate accuracy ±2px
- Offline operation (no network)

If spike fails any threshold, the change documents the failure and the
project either ships without OCR (degrading verification to UIA-only) or
adds a model-bundled provider as a follow-up change.

## Standard error codes (additions)

| Code | Meaning |
|---|---|
| `source_not_allowed` | `source` is neither window_id nor screenshot_handle |
| `ocr_provider_unavailable` | Selected provider not installed / unsupported |