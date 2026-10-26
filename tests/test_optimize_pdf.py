import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

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
    def test_verification_workers_are_viewing_copy_only(self):
        with self.assertRaisesRegex(ValueError,'viewing-copy mode'):
            optimizer.optimize('unused.pdf','unused-output.pdf',verification_workers=2)

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
