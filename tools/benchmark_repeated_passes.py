"""Measure up to three published Direct passes, outside normal optimization."""
import argparse
import json
import math
from pathlib import Path
from statistics import median
import subprocess
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_runtime import filehash
from tools.compare_optimizers import independent_checks
from viewing_copy import benchmark_pages, benchmark_worker, node_identity, runtime_node
from viewing_copy import verify_candidate, verify_pdfjs


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, default=str)


def reduction(before, after):
    return 100 * (before - after) / before if before else None


def measure(versions, pages, node, log):
    samples = [[] for _ in versions]
    for page in pages:
        for repeat in range(3):
            order = range(len(versions))
            if repeat % 2:
                order = reversed(order)
            for index in order:
                record = benchmark_worker(versions[index], page, "render", node=node)
                record.update(pass_number=index, run=repeat + 1)
                samples[index].append(record)
                print(json.dumps(record), file=log, flush=True)
    results = []
    for records in samples:
        result = {}
        for field in ("load", "cold", "cached", "ready"):
            for unit in ("wall", "cpu"):
                values = sorted(record[field][unit] * 1000 for record in records)
                result[f"{field}_{unit}_ms"] = {
                    "median": median(values),
                    "p95": values[math.ceil(.95 * len(values)) - 1],
                    "min": values[0],
                    "max": values[-1],
                }
        results.append(result)
    return results


def benchmark(source, folder, node):
    folder.mkdir()
    versions = [source]
    hashes = [filehash(source)]
    rows = [{"pass": 0, "bytes": source.stat().st_size, "sha256": hashes[0]}]
    attempts = []
    for number in range(1, 4):
        print(f"{source.name}: optimization pass {number}", flush=True)
        output = folder / f"pass-{number}.pdf"
        command = [sys.executable, str(ROOT / "optimize_pdf.py"),
                   str(versions[-1]), str(output), "--viewing-copy", "--viewing-profile", "quick",
                   "--expected-source-sha256", hashes[-1], "--time-budget-seconds", "1200"]
        with output.with_suffix(".log").open("x", encoding="utf-8") as log:
            started = monotonic()
            child = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1260)
            elapsed = monotonic() - started
        child.check_returncode()
        report_path = output.with_suffix(".json")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        state_path = output.with_suffix(".checkpoint") / "quick" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state["stage"] != "complete" or state["status"] != report["status"]
                or state["report_sha256"] != filehash(report_path)
                or state["source_sha256"] != hashes[-1] or report["source_sha256"] != hashes[-1]):
            raise ValueError("Incomplete or mismatched optimizer evidence.")
        attempts.append({"pass": number, "status": report["status"],
                         "classification": report["classification"], "reason": report.get("reason"),
                         "runtime_s": elapsed, "candidate_bytes": report.get("candidate_bytes"),
                         "report_sha256": state["report_sha256"]})
        if report["status"] == "no improvement" and not output.exists():
            break
        digest = filehash(output)
        if (report["status"] != "optimized" or digest != report["output_sha256"]
                or not report["verification"]["complete"]):
            raise ValueError("Publication or preservation evidence did not match the output.")
        versions.append(output)
        hashes.append(digest)
        size = output.stat().st_size
        rows.append({"pass": number, "bytes": size, "sha256": digest, "runtime_s": elapsed,
                     "size_vs_original_pct": reduction(rows[0]["bytes"], size),
                     "size_vs_previous_pct": reduction(rows[-1]["bytes"], size)})
        print(f"{source.name}: checking pass {number} against original", flush=True)
        integrity = independent_checks(source, output)
        # Pass 1 already has all-page exact comparisons with the original in its production report.
        # Later passes repeat the strict comparisons directly against the original.
        if number > 1:
            integrity["original_pdfium"] = verify_candidate(source, output, {})
            integrity["original_pdfjs"] = verify_pdfjs(source, output, report["pages"], workers=1)
        write_json(folder / f"integrity-{number}.json", integrity)
        rows[-1]["independent_checks"] = {key: value["status"] for key, value in integrity.items()
                                          if "status" in value}

    count = report["pages"]
    pages = list(range(1, count + 1)) if count <= 6 else sorted(
        {1, (count + 1) // 2, count, *benchmark_pages(source)})
    print(f"{source.name}: timing pages {pages}", flush=True)
    with (folder / "timings.jsonl").open("x", encoding="utf-8") as log:
        timing = measure(versions, pages, node, log)
    for index, row in enumerate(rows):
        row["timing"] = timing[index]
        if index:
            row["timing_change_pct"] = {
                key: {stat: {"vs_original": reduction(timing[0][key][stat], value[stat]),
                             "vs_previous": reduction(timing[index - 1][key][stat], value[stat])}
                      for stat in ("median", "p95")}
                for key, value in timing[index].items()
            }
    if [filehash(path) for path in versions] != hashes:
        raise ValueError("An original or published PDF changed during measurement.")
    write_json(folder / "result.json", {"status": "complete", "source": str(source), "pages": count,
                                       "timed_pages": pages, "attempts": attempts, "versions": rows})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New run directory")
    parser.add_argument("--fixtures", action="store_true", help="Generate compatibility inputs")
    parser.add_argument("sources", nargs="*", type=Path)
    args = parser.parse_args()
    sources = [path.resolve(strict=True) for path in args.sources]
    if not sources and not args.fixtures:
        parser.error("Provide PDF sources or --fixtures.")
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    node = runtime_node()
    write_json(output / "method.json", {
        "node": node_identity(node), "pdfjs": benchmark_worker(ROOT / "unused.pdf", 1, "preflight", node=node),
        "harness_sha256": filehash(Path(__file__)), "profile": "quick/adaptive",
        "runs": 3, "pdfjs_width": 1404, "p95": "nearest rank, pooled sampled page runs",
        "load": "fresh PDF.js process, OS cache uncontrolled, document promise wait",
        "ready": "file read through first completed draw, excludes Node startup",
        "cached": "second draw in the same PDF.js document",
    })
    if args.fixtures:
        sys.path.insert(0, str(ROOT / "tests"))
        from test_pdf_compatibility import compatibility_fixture, interactive_fixture, mixed_transparency_fixture

        inputs = output / "generated"
        inputs.mkdir()
        for build in (mixed_transparency_fixture, interactive_fixture, compatibility_fixture):
            source = inputs / f"{build.__name__}.pdf"
            build(source)
            sources.append(source)
    for index, source in enumerate(sources, 1):
        benchmark(source, output / f"{index:02d}-{source.stem}", node)


if __name__ == "__main__":
    main()
