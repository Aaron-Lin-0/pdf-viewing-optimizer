import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

SCRIPT = Path(__file__).resolve().parents[1] / "optimize_pdf.py"
spec = importlib.util.spec_from_file_location("optimize_pdf", SCRIPT)
optimizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(optimizer)


def make_pdf(path, color="1 0 0", encrypted=False):
    writer = PdfWriter()
    for _ in range(2):
        page = writer.add_blank_page(width=200, height=200)
        stream = DecodedStreamObject()
        stream.set_data((f"{color} rg 20 30 80 100 re f\n" + "% duplicate padding\n" * 100).encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test")
    writer.write(path)


class OptimizerTests(unittest.TestCase):
    def test_default_viewing_copy_uses_a_dated_artifact_folder(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_pdf(source)
            with patch.object(sys,'argv',['optimize_pdf.py',str(source),'--viewing-copy']),\
                 patch.object(optimizer,'optimize') as run:
                self.assertEqual(optimizer.main(),0)
            destination=run.call_args.args[1]
            self.assertEqual(destination.name,'source.optimized.pdf')
            self.assertRegex(destination.parent.name,r'^source\.optimized-\d{4}-\d{2}-\d{2}$')
            self.assertTrue(destination.parent.is_dir())

    def test_verification_workers_are_viewing_copy_only(self):
        with self.assertRaisesRegex(ValueError,'viewing-copy mode'):
            optimizer.optimize('unused.pdf','unused-output.pdf',verification_workers=2)
        with self.assertRaisesRegex(ValueError,'viewing-copy mode'):
            optimizer.optimize('unused.pdf','unused-output.pdf',parallel_candidates=True)
        with self.assertRaisesRegex(ValueError,'viewing-copy mode'):
            optimizer.optimize('unused.pdf','unused-output.pdf',viewing_optimizer='qpdf')

    def test_structure_compression_keeps_exact_commands(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder) / "source.pdf", Path(folder) / "output.pdf"
            make_pdf(source)
            original = source.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                optimizer.optimize(source, output, compact_structure=True)
            before, after = PdfReader(source), PdfReader(output)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(len(before.pages), len(after.pages))
            for a, b in zip(before.pages, after.pages):
                self.assertEqual(a.get_contents().get_data(), b.get_contents().get_data())
            self.assertTrue(after.xref_objStm)

    def test_duplicate_content_merges_without_changing_pages_or_original(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder) / "source.pdf", Path(folder) / "output.pdf"
            make_pdf(source)
            original = source.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                old_size, new_size = optimizer.optimize(source, output)
            self.assertLess(new_size, old_size)
            self.assertEqual(source.read_bytes(), original)
            result = PdfReader(output)
            self.assertEqual(len(result.pages), 2)
            self.assertEqual(result.pages[0].raw_get("/Contents"), result.pages[1].raw_get("/Contents"))

    def test_visual_difference_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, changed = Path(folder) / "source.pdf", Path(folder) / "changed.pdf"
            make_pdf(source)
            make_pdf(changed, color="0 0 1")
            with self.assertRaisesRegex(ValueError, "changed visually"):
                optimizer.verify_pages(source, changed)

    def test_strip_fingerprint_bounds_memory_and_detects_changes(self):
        import pypdfium2 as pdfium
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, changed = Path(folder) / "source.pdf", Path(folder) / "changed.pdf"
            make_pdf(source)
            make_pdf(changed, color="0 0 1")
            with pdfium.PdfDocument(source) as before, pdfium.PdfDocument(changed) as after:
                whole = optimizer.page_fingerprint(before, 0)
                strips = optimizer.page_fingerprint(before, 0, strip_pixels=1000)
                self.assertEqual(whole[0], strips[0])
                self.assertEqual(strips, optimizer.page_fingerprint(before, 0, strip_pixels=1000))
                self.assertNotEqual(strips, optimizer.page_fingerprint(after, 0, strip_pixels=1000))

    def test_damaged_input_is_rejected_with_a_clear_message(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder) / "source.pdf", Path(folder) / "output.pdf"
            make_pdf(source)
            source.write_bytes(source.read_bytes()[:400])
            with self.assertRaisesRegex(ValueError, "damaged or incomplete"):
                optimizer.optimize(source, output)
            self.assertFalse(output.exists())

    def test_existing_output_and_original_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder) / "source.pdf", Path(folder) / "output.pdf"
            make_pdf(source)
            output.write_bytes(b"keep me")
            for target in [source, output]:
                with self.assertRaises(FileExistsError):
                    optimizer.optimize(source, target)
            self.assertEqual(output.read_bytes(), b"keep me")

    def test_encrypted_input_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder) / "source.pdf", Path(folder) / "output.pdf"
            make_pdf(source, encrypted=True)
            with self.assertRaisesRegex(ValueError, "Encrypted"):
                optimizer.optimize(source, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
