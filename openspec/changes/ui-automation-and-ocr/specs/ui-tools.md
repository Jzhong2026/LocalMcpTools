# Spec: `ui.*` — UI automation tools

## ADDED Requirements

### REQ-UI-T-1: Window authorization

#### Scenario: user authorizes a window

- **Given** the SPA `/ui/automation` page
- **When** the user clicks "Authorize window" on a row
- **Then** the server issues a `window_id` and persists `{window_id,
  window_title, process_name, pid, authorized_at, authorized_by}` to
  `authorized_windows` table
- **And** the `window_id` is opaque and time-limited (default 1 hour)

#### Scenario: tool call with unauthorized window

- **Given** a `window_id` not in `authorized_windows`
- **When** any UI tool is called
- **Then** `error.code = "window_not_authorized"`

#### Scenario: credential windows are filtered

- **Given** the SPA's "Authorize window" list
- **Then** windows whose title matches a denylist (e.g. contains "Sign in",
  "Password", "Credential", "BitLocker", "UAC") are **excluded**
- **And** the user cannot bypass via allowlist

### REQ-UI-T-2: `ui.get_ui_tree`

#### Scenario: tree for an authorized window

- **Given** a valid `window_id`
- **When** the agent calls `ui.get_ui_tree({window_id, depth=4})`
- **Then** the response is a tree of nodes
  `[{name, automationId, controlType, boundingBox: {x,y,width,height},
  isEnabled, isVisible, children: [...]}]`
- **And** nodes with `isVisible=false` are excluded
- **And** the tree is clipped to `depth`

#### Scenario: large tree streaming

- **Given** the tree has > 500 nodes
- **Then** `data` contains a `handle` to a JSON artifact; the in-memory
  payload is a summary with the first 100 nodes + the handle

### REQ-UI-T-3: `ui.find_element`

#### Scenario: by text

- **Given** `criterion = {text: "Submit"}`
- **Then** returns up to 20 matches `{node, score}` where `score` is
  the Levenshtein-like similarity
- **And** `data.matched = true | false`

#### Scenario: by automationId / controlType / name

- **Given** `criterion = {automationId: "btnSubmit"}` or
  `{controlType: "Button"}` or `{name: "Submit"}`
- **Then** the response is the same shape

#### Scenario: combined criteria

- **When** `criterion = {controlType: "Button", name: "Submit"}`
- **Then** results match **all** fields

### REQ-UI-T-4: `ui.click_element` and `ui.type_text`

#### Scenario: click with verification required

- **Given** an element ref returned by `find_element`
- **When** the agent calls `ui.click_element({node_ref})`
- **And** does not pass `verify_with`
- **Then** the response is `error.code = "verification_required"` with
  `next_actions: ["use ui.act_and_verify", "specify verify_with"]`

#### Scenario: click with UIA verification

- **When** `verify_with = {kind: "uia", predicate: "node.name == 'Clicked'"}`
- **Then** the action runs and the verification evaluates
- **And** if the predicate fails → `error.code = "verification_failed"`
- **And** if the predicate matches → success

#### Scenario: type into a text input

- **Given** an element ref for an editable text field
- **When** `ui.type_text({node_ref, text, verify_with?})` is called
- **Then** characters are typed; verification is the same as click

### REQ-UI-T-5: `ui.act_and_verify`

#### Scenario: combined action + verification

- **When** the agent calls
  `ui.act_and_verify({action: "click", node_ref, verify_with: ...})`
- **Then** the action and verification are atomic from the audit's
  perspective (single row, single `run_id`)

#### Scenario: success criteria require all listed evidence kinds

- **Given** `verify_with: [{kind:"uia",...}, {kind:"screenshot",...}]`
- **Then** all must pass; if any fails, the action is considered failed
  even if `click` itself returned success

### REQ-UI-T-6: screenshots

#### Scenario: full-screen

- **Then** `meta.screenshot_handle` is set; data contains only a
  `width, height` summary (never the pixels in the response body)

#### Scenario: window-scoped

- **Given** `window_id`
- **Then** the screenshot is clipped to the window's bounding box

#### Scenario: region

- **Given** `region = {x, y, width, height}`
- **Then** the screenshot is clipped to that region

#### Scenario: rate limit

- **Given** more than 20 screenshots / minute from one agent
- **Then** `error.code = "rate_limited"` (suggests using `output.search`
  on the previous handle)

## Standard error codes (additions)

| Code | Meaning |
|---|---|
| `window_not_authorized` | `window_id` not in `authorized_windows` |
| `verification_required` | Action tool called without `verify_with` |
| `verification_failed` | Predicate / assertion did not hold |
| `element_not_found` | `find_element` returned 0 matches |
| `rate_limited` | Too many screenshots / OCR calls in window |