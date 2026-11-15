# Project handoff

Start with [AGENTS.md](AGENTS.md) and the ordered [roadmap](docs/roadmap.md).
Current behavior is defined in [design](docs/design.md), not historical plans.

- [BOOX conversion](docs/boox-note-handoff.md): setup, widths, bookmark preservation and limits.
- [Renderer source](vendor/boox-renderer/README.md): pinned public source and maintained patch.
- [Testing](docs/testing.md): generated regressions and explicit private-input checks.
- [Comparison](docs/comparison.md): research measurements, separate from production acceptance.

Bookmark publication failures, notebook page order, normalized IDs, depth limits
and configured single-canvas bounds have regression coverage. The converter is
experimental CLI functionality. P18-D independent tablet fidelity remains open;
P13/P14 outstanding qualification remains recorded in the roadmap.

Private run records and machine-specific handoffs belong under ignored
`audit-output/`; they are not portable evidence or release deliverables.
