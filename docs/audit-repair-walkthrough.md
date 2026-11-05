# Implementation walkthrough: four remaining audit repairs

Prepared against commit `70618a3` (2026-11-03). This is implementation guidance
for P15 in [the roadmap](roadmap.md), not a separate roadmap. The four numbers
here refer to the **second audit**, not the original six-finding audit.

## Task for the implementing agent

Fix the four cases below, in order, with the smallest complete changes. Add a
regression that fails before each fix and passes afterward. Preserve existing
acceptance thresholds, CLI/API behavior, originals, output collision checks and
checkpoint identity checks. Do not add dependencies, optimizer modes or a framework.
Do not start unrelated P13/P14 speed work.

The objective is smaller and efficiently rendered PDFs without lost content.
These repairs protect that objective; they do not themselves promise better
compression or faster rendering. Reports-only rejection remains a valid result.

## 0. Establish the starting point

1. Read `AGENTS.md`, `docs/design.md`, the P15 section of `docs/roadmap.md`, and
   `docs/testing.md`. Production policy comes from `docs/design.md`.
2. Run `git status --short` and `git log -1 --oneline`. Do not discard another
   person's edits. If HEAD differs from the commit above, locate the functions by
   name and confirm each bug still exists before applying the suggested change.
3. Check actual comparison processes and their run records before editing
   executable files. A stale `running` record alone does not prove an active run.
   Do not modify executable files used by an active comparison.
4. Use the root `.venv/Scripts/python.exe` and root `node_modules`. Never edit
   `dist/`, installed skills or third-party packages as the implementation.
5. Use generated PDFs only. Previous permission to inspect personal PDFs does not
   authorize optimizing them. Keep local evidence under ignored `audit-output/`.

Expected maintained changes:

| File | Purpose |
|---|---|
| `navigation.py` | Trailer precedence and signature rejection |
| `batch_workflow.py` | Preserve saved retry state when a job is skipped |
| `viewing_copy_gui.py` | Prevent setup actions during conflicting work |
| `tests/test_navigation.py` | Incremental trailers and inline signature widgets |
| `tests/test_viewing_copy_gui.py` | Stop/close recovery and keyboard race |
| `tests/test_quick_lossless.py` | Shared direct-profile signature rejection, if not covered by the new navigation test |
| `docs/roadmap.md`, `docs/testing.md` | Actual completion status and added coverage |

Other files should change only if tracing a caller demonstrates a concrete need.
Before editing, find callers with `rg` for `reader_for`, `validate_input`,
`navigation_fingerprint`, `_record`, `_skip_ready`, and `check_setup`.

## 1. Keep the newest custom trailer entries

### Why the current code fails

`_PreservingReader._process_xref_stream` assigns a new
`preserved_xref_trailer` dictionary every time it sees an xref stream. The pinned
pypdf reader traverses revisions newest-first. An older stream therefore replaces
the newer values before `reader_for` copies them into the public trailer.
Merely accumulating a side dictionary is insufficient for mixed classic/stream
revisions: an older classic trailer can already populate `self.trailer` before
the side dictionary is copied back.

### Make this change

1. In `_process_xref_stream`, insert each permitted entry directly into
   `self.trailer` using `setdefault` **while that revision is being processed**.
   Keep the existing exclusions: `/Type`, `/W`, `/Index`, `/Length`, `/Filter`,
   `/DecodeParms`. Keep indirect references as references.
2. Still call `super()._process_xref_stream(stream)`.
3. Remove the now-unused side-dictionary assignment and the post-read copying
   loop in `reader_for`. Keep encryption handling and layer-reference repair.

The intended replacement body is:

```python
for key, value in stream.items():
    if key not in {'/Type', '/W', '/Index', '/Length', '/Filter', '/DecodeParms'}:
        self.trailer.setdefault(key, value)
return super()._process_xref_stream(stream)
```

Do not use `update`: an older revision must never replace a newer value. Do not
relax the direct profile's separate rejection of incremental inputs. Do not copy
or rewrite all of pypdf's parser to solve this.

### Add the regression first

In `tests/test_navigation.py`, reuse the existing blank-PDF and bundled-qpdf
fixture pattern. The following test helper appends a minimal generated revision;
it is test code, not a production PDF writer:

```python
import re
import struct

def append_xref_revision(source, target, private_value):
    raw = source.read_bytes()
    with reader_for(source) as reader:
        number = int(reader.trailer['/Size'])
        root = reader.trailer.raw_get('/Root')
        root_ref = f'{root.idnum} {root.generation} R'
    previous = int(re.findall(rb'startxref\s+(\d+)', raw)[-1])
    offset = len(raw)
    entry = struct.pack('>BIH', 1, offset, 0)
    # private_value is a fixed test literal without PDF string delimiters.
    header = (
        f'{number} 0 obj\n<< /Type /XRef /Size {number + 1} '
        f'/Root {root_ref} /Prev {previous} /W [1 4 2] '
        f'/Index [{number} 1] /Length 7 '
        f'/PrivateRevision ({private_value}) >>\nstream\n'
    ).encode('ascii')
    tail = f'\nendstream\nendobj\nstartxref\n{offset}\n%%EOF\n'.encode('ascii')
    with target.open('xb') as output:
        output.write(raw + header + entry + tail)
```

Use unique paths in a temporary directory. Cover these assertions:

- Create a blank classic PDF, convert it to an xref-stream PDF with
  `build_qpdf_candidate(..., strict=True)`, then append `new-value` with the helper.
  `reader_for(result).trailer['/PrivateRevision']` must equal `new-value`.
- Append another revision with `newest-value`. The newest value must win.
- Add `/PrivateRevision (old-value)` to the base classic trailer using the
  existing test's trailer-insertion technique. Append a stream revision with
  `new-value`; the stream value must win over the older classic value.
- Confirm the generated fixture passes bundled `qpdf --check`; a malformed
  fixture is not evidence for the fix.
- Run legacy `optimize(source, fresh_output)` on the incremental fixture.
  Writer cloning currently omits this custom data, so expect preservation
  rejection, no output, and unchanged source bytes. Do not demand publication.
- Retain the existing single-revision classic/stream trailer tests and ordinary
  bookmark/attachment tests.

## 2. Restore inline signature-widget rejection

### Why the current code fails

The new `_current_objects` scan sees standalone indirect objects but does not
inspect a signature dictionary stored directly inside `/Annots`. The previous
validator explicitly inspected annotations and their `/Parent` chains. Replacing
that logic with the inventory scan created the regression.

### Make this change

Keep the current bounded inventory scan and `is_signature_object`. Restore the
previous catalog-field and page-annotation/parent checks in `validate_input`.
Use `git show 6dde916:navigation.py` to retrieve that small existing implementation.
Reuse `is_signature_object` when inspecting dictionaries. Keep the parent-chain
visited set so malformed cycles cannot loop forever. Keep encryption and empty
document rejection. A parse failure must not become permission to publish.

Do not remove the inventory scan: it is what catches unreferenced and compressed
signature objects. Do not create a second validation implementation in the CLI
or GUI; the shared function must protect their callers.

### Add the regression first

Create a 20-by-20 blank page and assign `/Annots` to an `ArrayObject` containing a
**direct** `DictionaryObject` with `/Subtype /Widget`, `/FT /Sig`, `/T (Signature)`
and `/Rect [0 0 0 0]`. Do not call `writer._add_object` for this dictionary: doing
so would test the already-working indirect case instead of this bug.

Assert that:

- `validate_input(reader_for(source))` raises a signature-related `ValueError`.
- Legacy `optimize(source, output)` rejects it without publishing or changing the
  source. At the audited commit, this call incorrectly publishes a copy.
- `validate_quick_input(source)` rejects it too, proving the direct profile uses
  the repaired shared validation. Do not launch renderers for this rejection test.
- A direct parent carrying `/FT /Sig` is rejected; a parent cycle terminates.
- Existing orphan-signature, object-stream-signature, encrypted-input and ordinary
  unsigned-input tests still pass.

## 3. Preserve checkpoint recovery when queued retries are skipped

### Why the current code fails

`cancel()` now changes only the displayed job state. However, `_skip_ready()`
calls `_record(..., 'skipped', ...)`, which overwrites an existing `job.json`.
`_recover_job` intentionally does not recover `skipped`, so the next selection
allocates a new folder and loses access to completed work.

### Make this change

In `_record`, retain the in-memory status update and `result` event, but do not
persist `skipped` over a saved record. Put `status != 'skipped'` around the existing
save condition, with parentheses preserving its current `or` grouping. This
protects both stop and close without duplicating their logic.

Do not add every skipped or failed job to `RECOVERABLE_STATUSES`. Do not delete
checkpoints. Keep validation of source/profile/optimizer, output collisions,
completed reports and process ownership unchanged.

### Add the regression first

Extend `tests/test_viewing_copy_gui.py`, reusing `make_pdf`, `_save_job`,
`replace`, `plan_batch`, and `BatchController`:

1. Generate a source and call `plan_batch` to get its first job.
2. Save that job with status `timeout` (also run the case with `budget exhausted`).
3. Reselect it; assert the planned output is still the first output.
4. For the close case, create a controller and call `request_close()` before launch.
5. For the stop case, create a fresh controller, call `request_stop()`, then `run()`.
6. Assert the in-memory job is skipped, zero children launched, and the persisted
   record still contains its recoverable state.
7. Reselect again; assert status `ready` and the **same output/checkpoint paths**.
8. Keep the existing review-cancellation, two-job stop-after-current, source-change,
   collision and profile-isolation tests passing. No six-hour wait is needed.

## 4. Make the setup shortcut obey the busy state

### Why the current code fails

The setup button is disabled during planning, but the root Alt+S binding calls
`check_setup()` directly. That increments `task_token`, invalidating the pending
planning result and silently losing the selected batch.

### Make this change

1. Before `check_setup` changes any state or increments the token, return if the
   window is closed, an optimizer is running, or the setup button is disabled.
   Reuse existing state; no new scheduler or cancellation system is needed.
2. Route the root Alt+S action through the enabled setup button, or explicitly
   check its disabled state before invoking setup. Keep the callback guard too.
3. Preserve startup `after_idle` setup and setup triggered by a genuine profile
   change. Keep the review dialog's separate Alt+S "Start" binding intact.
4. Keep all widget access on the Tk thread and all slow work in existing workers.

### Add the regression first

Extend the existing `test_slow_planning_does_not_block_tk_events_and_close_is_safe`
pattern; use an event to hold the planning worker, not arbitrary long sleeps:

1. Stub startup diagnostics to succeed. Select a generated PDF and block planning.
2. Verify the setup button is disabled. Invoke the **root keyboard binding**;
   invoking the disabled button alone would miss the bug.
3. Assert no additional diagnostics call started. Release planning and pump Tk
   events until the original job reaches the review callback exactly once.
4. Stub review to cancel; assert zero optimizer launches and restored controls.
5. Keep existing startup, profile-change, slow-setup, safe-close and stale-result
   tests passing. Do not remove task-token checks to make the lost result reappear.

## 5. Verification commands and completion rules

Run from the repository root. Use a fresh writable temp directory; this avoids
the system-temp permission failures seen in the earlier audit:

```powershell
$repairRun = Join-Path (Get-Location) ('audit-output\repair-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $repairRun | Out-Null
$env:TEMP = $repairRun
$env:TMP = $repairRun
```

After each relevant fix:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_navigation.py'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_quick_lossless.py'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_viewing_copy_gui.py'
```

After integration:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe tests/run_quick_compatibility.py fixtures --output "$repairRun\quick"
.\.venv\Scripts\python.exe tests/run_quick_compatibility.py render --output "$repairRun\quick"
.\.venv\Scripts\python.exe tests/run_quick_compatibility.py browser --output "$repairRun\quick"
.\.venv\Scripts\python.exe tests/run_compatibility.py fixtures --output "$repairRun\thorough"
.\.venv\Scripts\python.exe tests/run_compatibility.py browser --output "$repairRun\thorough"
```

Run these sequentially. If a command fails, diagnose it before continuing; do not
overwrite its reports on retry. Use a fresh directory when the fixture runner
requires it. Native-browser failures remain explicit, never bypassed. Finish
the two-file Windows picker/review/start/stop smoke described in `docs/testing.md`.

Before declaring release-ready, build a fresh destination with
`tools/build_release.py --output dist/NEW_UNUSED_NAME` using the same interpreter;
verify packaged bytes and run the documented clean-package fixture smoke. Preserve
existing releases. Do not commit or push unless the user requests it.

Update P15 with the actual outcomes. The starting audit had 210 tests, one skip,
and passing Ruff; do not copy that count as evidence for the modified code. Report:

- The four repaired behaviors and their new regression names.
- Actual focused/full test results and any skips.
- Generated rendering/browser and GUI checks completed or still blocked.
- Package validation performed, or explicitly not performed.
- Confirmation that no original PDFs, existing outputs or unrelated files changed.

Stop when the four regressions are fixed and required checks are accounted for.
Do not claim maximum compression, universal visual equivalence or faster viewing
from these repairs alone.
