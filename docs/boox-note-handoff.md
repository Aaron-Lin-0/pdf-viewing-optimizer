# BOOX note conversion

The experimental command-line adapter converts supported modern `geo_layout`
`.note` archives to static vector PDFs, then optionally applies Direct optimization.
It is separate from the PDF picker and does not change existing PDF modes.

## Setup and use

Install the normal Python/Node dependencies. Build the pinned patched renderer
using [the renderer source instructions](../vendor/boox-renderer/README.md), then
set `BOOX_NOTES_RENDERER` or pass `--renderer`. A generic upstream executable is
not a substitute for the maintained patch.

```powershell
.\.venv\Scripts\python.exe note_to_pdf.py "notebook.note" --inspect-only
.\.venv\Scripts\python.exe note_to_pdf.py "notebook.note" "viewing.pdf"
.\.venv\Scripts\python.exe note_to_pdf.py "notebook.note" "thin.pdf" --fixed-stroke-width 0.10mm
.\.venv\Scripts\python.exe note_to_pdf.py "notebook.note" "scaled.pdf" --stroke-scale 0.5
```

Outputs and sidecar reports must not already exist. Originals are retained.
`--page N` selects a one-based page. `--skip-optimize` keeps the conversion stage.

## Widths and navigation

Upstream rendering remains the default. Fixed widths and linear scaling explicitly
change appearance. `density-scaled` is an experimental density/pressure model;
`device-native` is a compatibility alias, not a calibrated fidelity claim.
Units are points by default, mm = 72/25.4 points and px = 0.24 points under the
assumed 300-DPI convention. Fixed width overrides scale, which overrides mode.

Bookmarks use notebook-scoped normalized UUIDs and renderer page order, including
blank pages. Single-page exports remap retained bookmarks to destination zero.
Cycles are repaired with warnings; conflicting normalized IDs and depth above
64 levels reject conversion. Malformed TOC or injection failures block publication.
Only explicit `--no-toc` bypasses bookmark processing. Invalid destinations are
reported and dropped. Saved outlines are reopened and verified before acceptance.

Custom single-canvas bounds cover configured pen envelopes with a 24-point margin.
Upstream bounds and fixed per-page dimensions retain their existing behavior.

## Limits and verification

Strict mode rejects unsupported pens, missing content, hidden layers and active
unrepresented templates. It retains raw stroke points without decimation but
uses inferred brush models: this does not establish exact BOOX device rendering.
The source archive is limited to 50,000 entries and 500 MiB expanded data.

Direct verification compares the optimized PDF against converter output. Independent
all-page fidelity to a tablet export remains open under P18-D. Earlier private
size/appearance measurements do not guarantee savings or faster viewing elsewhere.

Tests use generated fixtures. `PDF_NOTE_TEST_INPUT` explicitly opts into private
preflight/TOC checks; it is skipped when unset and fails for an invalid path.
Keep private PDFs, reports and local handoffs under ignored `audit-output/`.
See [design](design.md), [testing](testing.md) and the ordered [roadmap](roadmap.md).
