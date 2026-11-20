# Testing

This is the current verification guide. Development priorities and milestone
exit criteria live in the [roadmap](roadmap.md#current-work-queue).
Comparison-specific dependencies, calibration and native-viewer smoke coverage
are documented in [comparison.md](comparison.md#validation).

Install `requirements-dev.txt` and the Node packages before running tests. Tests generate PDFs in temporary directories; no private notes or downloaded fixtures are required.
Install `requirements-comparison.txt` to run the calibration tests. Discovery
skips them when NumPy, SciPy or scikit-image is unavailable.

```powershell
python -m ruff check .
python -m unittest discover -s tests -p 'test_*.py'
```

`ruff.toml` limits linting to definite errors and likely bugs; it does not enforce a code style.

The unit/regression suite covers geometry, navigation mutations, annotation conversion, shared resources, transparency, malformed references, output collisions, interrupted writes, signatures, dependency failures, memory ownership and candidate selection. One test decodes a JPEG2000 image and checks its colored pixels, preventing two blank renderings from passing as equivalent.

`test_bitmap_study.py` separately covers the opt-in bitmap contract: fitted/rotated
pages, fractional physical page sizes, image payloads, retained attachments and
local bookmarks, rejected bookmark chains, search, annotations, encryption and
signature fields, source identity, output collisions, interrupted publication,
per-page and cumulative image limits, reported progress and recovery settings.
The CLI regression requires qpdf-clean input with a missing XObject on the page
and in a nested Form to fail before publication. Exact compatibility runners do not qualify this lossy mode.
Generated malformed-xref controls require a complete graph proof before repair;
unrecognized warnings and failed graph proofs cannot publish. A Letter/300-DPI
control checks tiny dots, minus/fraction bars, superscripts and loops, with a
deliberately deleted-dot counterpart that must differ.
Human small-mark review and matched-scale viewing measurements remain separate
local evidence; image checks cannot certify handwritten mathematical meaning.

## Bitmap study copy release acceptance

Complete the hardening checks in the [current work queue](roadmap.md#current-work-queue)
before qualifying the isolated bitmap package. Run the release checks against the
proposed shipping source. The [bitmap contract](design.md#experimental-bitmap-study-contract-bitmap-study-1)
defines the supported subset. For the native Windows pass, extract to a short
path and launch `Create Viewing Copies.cmd`. Use two fresh supported generated
PDFs and one with an annotation that preflight must reject.

1. Review **Source** paper at **450 DPI**. Check the paths, settings, declared
   feature losses and rejected input. Cancel and confirm zero output artifacts.
2. Review again and run. Confirm the settings stay locked during planning and
   processing and the two supported jobs finish sequentially. Check each PDF,
   JSON and Markdown set, declared paper dimensions, DPI and source/output hashes.
3. Repeat with **Letter** at **300 DPI** and verify the previous outputs remain
   intact. Keep the source revisions and local evidence under ignored
   `audit-output/`.
4. Review the saved equation-sheet pair at study and print scale, including
   decimals, signs, superscripts and fraction bars. Run the all-page and detail
   checks on an independently authorized supported holdout. If claiming a viewing
   gain for the release build, time that build at matched content scale.
5. Run CI on the isolated shipping changes and verify the package and extracted
   runtime against the same source. Record deferred native or holdout checks as
   pending until completed.


