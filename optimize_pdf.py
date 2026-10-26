"""Remove duplicate PDF objects and verify every page before saving a new copy.

Usage: python optimize_pdf.py INPUT.pdf [OUTPUT.pdf]
Requires: pypdf >= 6.10, pypdfium2 >= 5.14, Pillow
"""

import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
from navigation import navigation_fingerprint, reader_for, validate_input

try:
    from pypdf import PdfReader, PdfWriter
    import pypdfium2 as pdfium
except ImportError as exc:
    raise SystemExit(
        "Missing PDF tools. Install them with:\n"
        'python -m pip install "pypdf>=6.10" "pypdfium2>=5.14" Pillow'
    ) from exc


def page_fingerprint(document, index, scale=2):
    page = document[index]
    try:
        bitmap = page.render(scale=scale, draw_annots=True)
        try:
            with bitmap.to_pil().convert("RGB") as image:
                return image.size, hashlib.sha256(image.tobytes()).digest()
        finally:
            bitmap.close()
    finally:
        page.close()


def verify_pages(original, candidate):
    # shortcut: compares pixels at 144 DPI, use more renderers/resolutions for prepress work.
    with pdfium.PdfDocument(original) as before, pdfium.PdfDocument(candidate) as after:
        if len(before) != len(after):
            raise ValueError("Page count changed; optimized copy rejected.")
        count = len(before)
    for index in range(count):
        # Reopening releases PDFium's accumulated resource caches on large BOOX exports.
        with pdfium.PdfDocument(original) as before, pdfium.PdfDocument(candidate) as after:
            before.init_forms()
            after.init_forms()
            if page_fingerprint(before, index) != page_fingerprint(after, index):
                raise ValueError(f"Page {index + 1} changed visually; optimized copy rejected.")
        print(f"Verified page {index + 1}/{count}: identical", flush=True)
    return count


def optimize(source, destination, compact_structure=False, fast_handwriting=False, remove_invisible_text=False,
             inline_handwriting=False, viewing_copy=False, expected_source_sha256=None,
             checkpoint=None,time_budget_seconds=None,disk_budget_bytes=None,verification_workers=None):
    if viewing_copy:
        if any((compact_structure, fast_handwriting, remove_invisible_text, inline_handwriting)):
            raise ValueError('Viewing-copy mode selects its own passes; run other modes separately.')
        from viewing_copy import optimize_viewing
        return optimize_viewing(source, destination, expected_source_sha256,checkpoint,
                                time_budget_seconds,disk_budget_bytes,verification_workers)
    if any(value is not None for value in (expected_source_sha256,checkpoint,time_budget_seconds,
                                            disk_budget_bytes,verification_workers)):
        raise ValueError('Expected-source checking, checkpoints, budgets and verification workers are available only in viewing-copy mode.')
    if sum((fast_handwriting, remove_invisible_text, inline_handwriting)) > 1:
        raise ValueError('Run handwriting, OCR removal and inlining modes as separate passes.')
    if compact_structure:
        try:
            import pymupdf
        except ImportError as exc:
            raise ValueError('Structure compression requires: python -m pip install pymupdf') from exc
    source = Path(source).expanduser().absolute()
    destination = Path(destination).expanduser().absolute()
    if not source.is_file():
        raise ValueError("Input must be a PDF file.")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Output already exists or is the original. Choose a new filename.")
    if not destination.parent.is_dir():
        raise ValueError("The output folder does not exist.")

    with source.open("rb") as input_file, tempfile.TemporaryDirectory(
        prefix="pdf-dedup-", dir=destination.parent
    ) as temporary:
        reader = reader_for(input_file)
        validate_input(reader)

        original_size = source.stat().st_size
        page_count = len(reader.pages)
        print(f"Reading {page_count} pages ({original_size / 1048576:.1f} MiB)...", flush=True)
        writer = PdfWriter(clone_from=reader)
        if fast_handwriting:
            from handwriting import rewrite_handwriting
            rewrite_handwriting(writer)
        if remove_invisible_text:
            from invisible_text import remove_invisible_text as remove_ocr
            remove_ocr(writer)
        if inline_handwriting:
            from handwriting import inline_handwriting as inline_forms
            inline_forms(writer)
        print("Merging identical objects and removing unreferenced objects...", flush=True)
        writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
        candidate = Path(temporary) / "candidate.pdf"
        writer.write(candidate)
        writer.close()

        if remove_invisible_text or inline_handwriting:
            # A fresh root clone drops entire unreachable resource graphs, including descendants.
            rebuilt = Path(temporary) / 'reachable.pdf'
            with candidate.open('rb') as intermediate:
                reachable = PdfWriter(clone_from=PdfReader(intermediate))
                reachable.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
                reachable.write(rebuilt)
                reachable.close()
            candidate = rebuilt

        if compact_structure:
            compacted = Path(temporary) / "compacted.pdf"
            print("Compressing PDF structure while preserving drawing commands...", flush=True)
            with pymupdf.open(candidate) as document:
                document.save(compacted, garbage=4, deflate=True, use_objstms=1, clean=False)
            candidate = compacted

        with candidate.open('rb') as navigation_file:
            if navigation_fingerprint(reader) != navigation_fingerprint(PdfReader(navigation_file)):
                raise ValueError('Table of contents, named destinations or attachments changed; candidate rejected.')
        print('Verified table of contents, named destinations and embedded attachments.', flush=True)

        print("Checking every page at 144 DPI, including annotations...", flush=True)
        checker = verify_pages
        if fast_handwriting:
            from handwriting import verify_handwriting
            checker = verify_handwriting
        if checker(source, candidate) != page_count:
            raise ValueError("Page count changed during processing.")

        # Exclusive creation prevents replacing another file, even if it appeared during verification.
        with destination.open("xb") as output_file:
            try:
                with candidate.open("rb") as verified_file:
                    shutil.copyfileobj(verified_file, output_file)
                output_file.flush()
            except BaseException:
                output_file.close()
                destination.unlink()
                raise

    optimized_size = destination.stat().st_size
    reduction = 100 * (1 - optimized_size / original_size)
    print(f"Saved: {destination}")
    print(f"Size: {original_size / 1048576:.1f} -> {optimized_size / 1048576:.1f} MiB ({reduction:.1f}% smaller)")
    if fast_handwriting:
        print(f"All {page_count} pages passed the shading check. Exact segments preserved; edge shading may differ.")
    else:
        print(f"All {page_count} pages rendered identically. Original preserved.")
    return original_size, optimized_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--compact-structure", action="store_true",
                        help="Also compress PDF structure with PyMuPDF; retain vector handwriting.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--fast-handwriting", action="store_true",
                        help="Batch BOOX strokes and remove duplicate layer draws; permits minor edge shading changes.")
    modes.add_argument('--remove-invisible-text', action='store_true',
                       help='Remove isolated BOOX OCR blocks and unused fonts; loses their search/copy text.')
    modes.add_argument('--inline-handwriting', action='store_true',
                       help='Consolidate supported BOOX handwriting Forms, preserving drawing bytes and clipping.')
    modes.add_argument('--viewing-copy', action='store_true',
                       help='Select a verified faster vector viewing copy; converts static BOOX ink annotations.')
    parser.add_argument('--expected-source-sha256',
                        help='Fail without publication unless the viewing-copy input matches this SHA256.')
    parser.add_argument('--checkpoint',type=Path,
                        help='Resume exact completed viewing-copy stages from this adjacent directory.')
    parser.add_argument('--time-budget-seconds',type=float,
                        help='Stop this viewing-copy run when its elapsed-time budget is exhausted.')
    parser.add_argument('--disk-budget-bytes',type=int,
                        help='Stop this viewing-copy run when checkpoint and working files exceed this budget.')
    parser.add_argument('--verification-workers',type=int,
                        help='Viewing-copy PDF verification processes (default: 2, limited by logical CPUs).')
    args = parser.parse_args()
    suffix = '.viewing.pdf' if args.viewing_copy else ('.inlined.pdf' if args.inline_handwriting else ('.no-ocr.pdf' if args.remove_invisible_text else '.optimized.pdf'))
    destination = args.output or args.input.with_name(args.input.stem + suffix)
    try:
        optimize(args.input, destination, compact_structure=args.compact_structure,
                 fast_handwriting=args.fast_handwriting,
                 remove_invisible_text=args.remove_invisible_text,
                 inline_handwriting=args.inline_handwriting, viewing_copy=args.viewing_copy,
                 expected_source_sha256=args.expected_source_sha256,checkpoint=args.checkpoint,
                 time_budget_seconds=args.time_budget_seconds,disk_budget_bytes=args.disk_budget_bytes,
                 verification_workers=args.verification_workers)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        from viewing_copy import TimeBudgetExceeded,DiskBudgetExceeded
        if isinstance(exc,TimeBudgetExceeded):return 2
        if isinstance(exc,DiskBudgetExceeded):return 3
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
