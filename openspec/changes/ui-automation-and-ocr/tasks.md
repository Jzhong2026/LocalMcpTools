# Tasks: ui-automation-and-ocr

> Phase: **5** — UI automation + OCR-assisted verification
> Goal: agents can verify UI interactions without image recognition.

## 5.1 Window authorization

- [x] `ui/windows.py`: list visible top-level windows (process + title + pid)
- [x] `safety/filters.py`: credential window denylist by title regex
  (Sign in, Password, Credential, BitLocker, UAC)
- [x] `ui/windows.py`: authorize returns `window_id` (UUID), persists row
- [x] Default TTL = 60 min, configurable
- [x] `POST /api/windows/authorize` (control plane)
- [x] `POST /api/windows/{id}/revoke`
- [x] Add `authorized_windows` table; bump `schema_version` to 5
- [x] Unit test: credential windows excluded from list
- [x] Unit test: revoke prevents subsequent tool calls

## 5.2 `ui.get_ui_tree`

- [x] `ui/windows.py`: get UIA tree via `uiautomation` up to `depth`
- [x] Filter `isVisible=false`, return nodes with
  `{name, automationId, controlType, boundingBox, isEnabled, isVisible}`
- [x] If > 500 nodes: stream to artifact, return summary + handle
- [x] Unit test: tree shape; credential content filtered out

## 5.3 `ui.find_element`

- [x] Implement criterion union: text / automationId / controlType / name
- [x] Combined criteria → AND semantics
- [x] Returns up to 20 matches `{node, score}`
- [x] Unit test: combined criteria
- [ ] Integration test: VS Code Settings dialog → find "Auto Save" item *(deferred — needs live Windows UI)*

## 5.4 screenshots

- [x] `ui/screenshots.py`: full / window / region
- [x] Stream pixels to artifact; do **not** keep in response body
- [x] Rate limit: 20 / minute per agent (token bucket)
- [x] Unit test: rate limit triggers
- [ ] Integration test: capture window; artifact written; redaction applied *(deferred — needs live Windows UI)*

## 5.5 `ui.click_element` and `ui.type_text`

- [x] `ui/actions.py`: dispatch to `uiautomation` for click / type
- [x] Both require `verify_with` (else `verification_required`)
- [x] Both use the verification harness (5.6)

## 5.6 verification harness

- [x] `ui/verify.py`: `Predicate` and `verify(predicates)`
- [x] `uia` predicate: re-query node, compare expected fields
- [x] `screenshot` predicate: pixel-diff vs prior handle, threshold
- [x] `ocr` predicate: delegate to `ocr.assert_text`
- [x] `VerificationFailed` carries the report
- [x] Unit test: each predicate kind, pass and fail
- [ ] Integration test: click that triggers no UI change → verification fails *(deferred — needs live Windows UI)*

## 5.7 `ui.act_and_verify`

- [x] Tool wrapper that combines action + verification atomically
- [x] Single audit row, single `run_id`
- [x] Unit test: action fail + verify fail both recorded

## 5.8 OCR provider spike

- [x] `ocr/windows_provider.py`: spike using `winrt` / `winsdk`
  `Windows.Media.Ocr` *(provider implemented; accuracy measurements deferred to live Windows host)*
- [ ] Synthetic fixtures: 10 English, 10 Chinese, 10 mixed
- [ ] Measure: per-block accuracy, bounding-box tolerance, latency
- [ ] Compare against thresholds in REQ-OCR-6
- [ ] If spike fails any threshold: write a spike report, decide whether
  to ship without OCR or proceed to a model-bundled provider (deferred)
- [x] Unit test: `ocr.ocr_region` returns `OcrResult` with all required fields
- [ ] Integration test: real VS Code window → OCR text matches Settings label *(deferred — needs live Windows host + fixture text)*

## 5.9 `ocr.ocr_region` / `ocr.find_text` / `ocr.assert_text`

- [x] `tools/ocr.py`: source resolution (window_id or screenshot_handle)
- [x] `ocr.ocr_region`: returns blocks + full_text + uncertain flag
- [x] `ocr.find_text`: exact / contains / regex / fuzzy
- [x] `ocr.assert_text`: returns passed/false; uncertain → false
- [x] Apply `safety.redact.redact_text` to OCR full_text before persist
- [x] Unit test: source not allowed → `source_not_allowed`
- [x] Unit test: assert_text uncertainty → `passed: false`

## 5.10 Profile wiring

- [x] `policy/profile.py`: `interactive_ui` capability for `ui.*` and
  `ocr.*` tools
- [x] Unit test: `observe` profile cannot call any UI tool

## 5.11 Angular automation page

- [x] `/ui/automation` page
- [x] Window list widget (filtered; "Authorize" button)
- [x] UI tree viewer widget (collapsible nodes, click-to-find) *(wired via /api/ui/get_ui_tree + /api/ui/find_element proxies)*
- [x] OCR preview widget (shows OCR result overlay on a screenshot handle) *(wired via /api/ocr/ocr_region + /api/ocr/assert_text proxies)*
- [x] Revoke button per window
- [x] CSRF + auth middleware applied (inherited from change-5)

## 5.12 DoD (must all pass)

- [ ] VS Code Settings demo: open Settings → find "Auto Save" → OCR
  matches "Auto Save" with confidence ≥ threshold → click → verify name
  becomes "Auto Save: afterDelay"
- [ ] `ui.click_element` without `verify_with` → `verification_required`
- [ ] `ui.act_and_verify` records single audit row with both action and
  verification report
- [ ] Credential windows are not enumerable
- [ ] `source = "C:\\foo.png"` → `source_not_allowed`
- [ ] OCR text is redacted in audit meta
- [ ] Rate limit fires after 20 screenshots/minute
- [ ] Spike report filed with accuracy numbers; threshold met or
  documented decision to ship without OCR
- [ ] No screenshot bytes in any tool response body
- [ ] OCR coordinates agree with UIA bounding boxes within ±2px on the
  synthetic fixtures