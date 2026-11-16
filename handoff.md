# Project handoff

Start with [AGENTS.md](AGENTS.md) and the ordered [roadmap](docs/roadmap.md).
Current behavior is defined in [design](docs/design.md), not historical plans.

- [BOOX conversion](docs/boox-note-handoff.md): setup, widths, bookmark preservation and limits.
- [Renderer source](vendor/boox-renderer/README.md): pinned public source and maintained patch.
- [Testing](docs/testing.md): generated regressions and explicit private-input checks.
- [Comparison](docs/comparison.md): research measurements, separate from production acceptance.

Bookmark publication failures, notebook page order, normalized IDs, depth limits
and configured single-canvas bounds have regression coverage. The GUI supports mixed PDF/note batches with an existing converter executable;
conversion remains experimental. P18-D independent tablet fidelity remains open;
P13/P14 outstanding qualification remains recorded in the roadmap.

Private run records and machine-specific handoffs belong under ignored
`audit-output/`; they are not portable evidence or release deliverables.


## Mixed GUI integration

Choose files accepts PDFs and BOOX notes together. The BOOX panel supplies the
converter path, pen mode/width/scale, bookmarks and optional Direct optimization.
PDF settings apply only to PDFs. Strict note jobs bind reviewed source/renderer
hashes and validate their own report contract before showing converted results.
Note checkpoints are separate schema 3; incomplete jobs restart conversion and
prior note conversion evidence is not reused. See the BOOX guide for setup.

Local validation: 327 tests completed successfully with three skips; lint and
package-byte verification passed. Native mixed-selection/review and a complete
mixed GUI batch passed. All controls fit at 900x780 and 760x650. The renderer is
still supplied externally; P18-D tablet fidelity remains open. No GUI changes
were committed or pushed during this implementation.
