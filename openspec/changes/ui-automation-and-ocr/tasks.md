# Tasks: ui-automation-and-ocr

> Phase: **5** — UI automation + OCR-assisted verification
> Goal: agents can verify UI interactions without image recognition.

## 5.1 Window authorization

- [ ] `ui/windows.py`: list visible top-level windows (process + title + pid)
- [ ] `safety/filters.py`: credential window denylist by title regex
  (Sign in, Password, Credential, BitLocker, UAC)
- [ ] `ui/windows.py`: authorize returns `window_id` (UUID), persists row
- [ ] Default TTL = 60 min, configurable
- [ ] `POST /api/windows/authorize` (control plane)
- [ ] `POST /api/windows/{id}/revoke`
- [ ] Add `authorized_windows` table; bump `schema_version` to 4
- [ ] Unit test: credential windows excluded from list
- [ ] Unit test: revoke prevents subsequent tool calls

## 5.2 `ui.get_ui_tree`

- [ ] `ui/windows.py`: get UIA tree via `uiautomation` up to `depth`
- [ ] Filter `isVisible=false`, return nodes with
  `{name, automationId, controlType, boundingBox, isEnabled, isVisible}`
- [ ] If > 500 nodes: stream to artifact, return summary + handle
- [ ] Unit test: tree shape; credential content filtered out

## 5.3 `ui.find_element`

- [ ] Implement criterion union: text / automationId / controlType / name
- [ ] Combined criteria → AND semantics
- [ ] Returns up to 20 matches `{node, score}`
- [ ] Unit test: combined criteria
- [ ] Integration test: VS Code Settings dialog → find "Auto Save" item

## 5.4 screenshots

- [ ] `ui/screenshots.py`: full / window / region
- [ ] Stream pixels to artifact; do **not** keep in response body
- [ ] Rate limit: 20 / minute per agent (token bucket)
- [ ] Unit test: rate limit triggers
- [ ] Integration test: capture window; artifact written; redaction applied

## 5.5 `ui.click_element` and `ui.type_text`

- [ ] `ui/actions.py`: dispatch to `uiautomation` for click / type
- [ ] Both require `verify_with` (else `verification_required`)
- [ ] Both use the verification harness (5.6)

## 5.6 verification harness

- [ ] `ui/verify.py`: `Predicate` and `verify(predicates)`
- [ ] `uia` predicate: re-query node, compare expected fields
- [ ] `screenshot` predicate: pixel-diff vs prior handle, threshold
- [ ] `ocr` predicate: delegate to `ocr.assert_text`
- [ ] `VerificationFailed` carries the report
- [ ] Unit test: each predicate kind, pass and fail
- [ ] Integration test: click that triggers no UI change → verification fails

## 5.7 `ui.act_and_verify`

- [ ] Tool wrapper that combines action + verification atomically
- [ ] Single audit row, single `run_id`
- [ ] Unit test: action fail + verify fail both recorded

## 5.8 OCR provider spike

- [ ] `ocr/windows_provider.py`: spike using `winrt` / `winsdk`
  `Windows.Media.Ocr`
- [ ] Synthetic fixtures: 10 English, 10 Chinese, 10 mixed
- [ ] Measure: per-block accuracy, bounding-box tolerance, latency
- [ ] Compare against thresholds in REQ-OCR-6
- [ ] If spike fails any threshold: write a spike report, decide whether
  to ship without OCR or proceed to a model-bundled provider (deferred)
- [ ] Unit test: `ocr.ocr_region` returns `OcrResult` with all required fields
- [ ] Integration test: real VS Code window → OCR text matches Settings label

## 5.9 `ocr.ocr_region` / `ocr.find_text` / `ocr.assert_text`

- [ ] `tools/ocr.py`: source resolution (window_id or screenshot_handle)
- [ ] `ocr.ocr_region`: returns blocks + full_text + uncertain flag
- [ ] `ocr.find_text`: exact / contains / regex / fuzzy
- [ ] `ocr.assert_text`: returns passed/false; uncertain → false
- [ ] Apply `safety.redact.redact_text` to OCR full_text before persist
- [ ] Unit test: source not allowed → `source_not_allowed`
- [ ] Unit test: assert_text uncertainty → `passed: false`

## 5.10 Profile wiring

- [ ] `policy/profile.py`: `interactive_ui` capability for `ui.*` and
  `ocr.*` tools
- [ ] Unit test: `observe` profile cannot call any UI tool

## 5.11 Angular automation page

- [ ] `/ui/automation` page
- [ ] Window list widget (filtered; "Authorize" button)
- [ ] UI tree viewer widget (collapsible nodes, click-to-find)
- [ ] OCR preview widget (shows OCR result overlay on a screenshot handle)
- [ ] Revoke button per window
- [ ] CSRF + auth middleware applied (inherited from change-5)

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