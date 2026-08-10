# Spike: Windows OCR provider

> **Status:** scaffolded, awaits Windows accuracy measurements.

## What this spike is

`openspec/changes/ui-automation-and-ocr` section 5.8 requires a
spike against the Windows OCR provider to verify accuracy on
mixed CJK / English UI text before we commit to shipping it as the
default. The threshold table comes from REQ-OCR-6:

| Metric | Threshold |
|---|---|
| Per-block accuracy (English) | ≥ 95 % |
| Per-block accuracy (Chinese) | ≥ 90 % |
| Per-block accuracy (mixed)   | ≥ 90 % |
| Bounding-box vs UIA          | ±2 px |
| Latency (per region)         | ≤ 250 ms |

## What this change ships

- `localmcptools/ui/ocr.py` defines a pluggable `OcrProvider`
  protocol; the Windows implementation goes through `winsdk` →
  `Windows.Media.Ocr.OcrEngine.try_create_from_language()`.
- The provider falls back to a stub that returns `uncertain=True`
  on hosts without `winsdk` or outside Windows. The agent's contract
  stays identical across platforms — uncertainty forces
  `passed: false` on every `ocr.assert_text` call.
- `OcrResult.full_text` is redacted through `safety.redact` before
  being persisted (Bearer tokens / passwords / PATs in OCR output
  cannot leak to the audit log).

## How to run the spike (manually, on a Windows host)

```powershell
# Install the OCR binding.
pip install "winsdk>=1.0"

# Run the synthetic-fixture script (lives in scripts/, not yet written).
python scripts/spike_ocr.py --languages en-US,zh-Hans-CN
```

## What's NOT yet measured

- Real-block accuracy: needs fixtures of known text rendered into
  PNGs. 30 fixtures (10 EN / 10 ZH / 10 mixed) are TODO.
- Bounding-box tolerance: needs UIA tree + OCR overlay from a live
  VS Code window. The integration test is TODO.
- Latency: needs a real Windows desktop. Per-region numbers are
  TODO.

## Decision rule

If the spike shows **any** threshold below the table above, fall
back to (a) no OCR shipped and a stub provider, or (b) a model-bundled
provider. We do NOT lower the accuracy bar to ship Windows OCR by
default.

Until the spike runs, the default provider on Windows is the stub;
agents see `uncertain: true` for every OCR call. The MCP tools still
work end-to-end against the stub — `ocr.find_text` and
`ocr.assert_text` return `passed: false` correctly, the agent never
lies to the user, and the audit row still carries the redacted
output.