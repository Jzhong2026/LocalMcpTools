# Change: ui-automation-and-ocr

> Covers: `docs/implementation-plan.md` **Phase 5 — UI automation + OCR-assisted verification**

## Intent

Provide a way for an agent to **observe and act inside an authorized
window** without relying on image recognition (which most agents are bad at
and which is brittle to theme / DPI / scaling changes).

OCR is added as a **second source of evidence**: it can confirm the
rendered text on screen, but **never** replaces the structural data from
UI Automation or DOM/Accessibility trees.

After this change, an agent with `interactive_ui` profile can:

- Get a structured UI tree for an authorized window
- Find elements by text / automationId / controlType / name
- Click and type (each action separately approved)
- Take screenshots as evidence, optionally OCR'd
- Use `act_and_verify` to require a UIA / DOM / OCR cross-check after every
  action — single "click success" is not enough.

## Scope

### In scope

- `ui.get_ui_tree` — UIA tree (Windows native) or DOM/Accessibility tree
  (web, via DevTools Protocol in future work — for now, native only)
- `ui.find_element` — multi-condition element lookup
- `ui.click_element` and `ui.type_text` — actions (each requires approval)
- `ui.screenshot_full_screen` / `ui.screenshot_window` / `ui.screenshot_region`
- `ui.act_and_verify` — action + at least one verification assertion
- `ocr.ocr_region` — OCR on an authorized window or screenshot artifact
- `ocr.find_text` — find a string with exact / fuzzy / regex match
- `ocr.assert_text` — assert rendered text contains / equals / matches
- Provider interface (`OcrProvider`) with one concrete implementation
  after the spike (Windows system OCR or a model-bundled one)
- UI surface `/ui/automation` for browsing UI trees + granting approvals
- Audit fields: `window_id`, `screenshot_handle`

### Out of scope

- Any non-authorized window (browser cookies, system settings, credential
  dialogs — explicitly filtered out)
- Cross-window actions (click something to bring another window forward)
- Web DOM scraping (no DevTools Protocol integration in this change; web
  automation tools land in a follow-up if needed)
- OCR on arbitrary file paths (OCR is restricted to authorized sources)

## Approach

1. **Authorization is per-window.** A `window_id` is an opaque handle that
   the user has explicitly authorized in the UI. The tool never accepts a
   process name + title; only the handle.
2. **`interactive_ui` profile gates `click_element` and `type_text`.** Read
   tools (`get_ui_tree`, `find_element`, screenshots, OCR) are also gated
   to prevent silent exfiltration of screen content.
3. **Verification is mandatory.** Any mutating action must end in an
   `act_and_verify` or carry an explicit `verify_with` parameter that names
   which evidence (UIA / DOM / OCR) is required before the action is
   considered successful.
4. **OCR provider is pluggable.** The first spike verifies Windows system
   OCR (English) and decides whether to bundle a model. The provider
   records its name + version in every audit row for reproducibility.
5. **Sensitive content is filtered.** Known credential windows (named like
   "Sign in", "Password", "Credential") are excluded from `get_ui_tree` /
   OCR by default, with an allowlist override per workspace.

## Why this matters later

- This change unlocks the "verify UI without image recognition" path that
  the requirements doc calls out as a core capability.
- The `OcrProvider` interface is reused by any later OCR-as-evidence tool
  (e.g. for log screenshot analysis).

## Affected components

| Component | Notes |
|---|---|
| `ui/` (new module) | `windows.py`, `actions.py`, `screenshots.py` |
| `ocr/` (new module) | `provider.py` (interface), `windows_provider.py` (spike) |
| `tools/ui.py` | new — UI tools |
| `tools/ocr.py` | new — OCR tools |
| `safety/filters.py` | new — credential window filter |
| `policy/profile.py` | add `interactive_ui` capability checks |
| `ui/src/app/features/automation/` | Angular component |
| `control_api.py` | `POST /api/windows/authorize` etc. |

## Open follow-up (NOT in this change)

- Web DOM automation (via Chrome DevTools Protocol) — separate change.
- Mobile platform UI automation — separate change if ever needed.

## Key non-regression

- OCR is verification, not authority. It cannot prove a button is enabled,
  a checkbox is checked, an icon is the right icon, or colors/layout are
  correct.
- A `click_element` without a verification step **must** return
  `verification_required`, not `success`.