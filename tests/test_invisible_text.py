import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from invisible_text import remove_invisible_text
from optimize_pdf import optimize
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

OCR = b'q 0 0 0 rg 0 0 0 RG BT 1 0 0 1 20 30 Tm /Hidden 12 Tf 3 Tr <48656c6c6f> Tj ET Q\n'
VISIBLE = b'q BT /Visible 12 Tf 1 0 0 1 20 60 Tm (Visible) Tj ET Q\n'
DRAWING = b'0 0 0 RG 2 w 10 10 m 80 80 l S\n'


def make_writer():
    w = PdfWriter()
    resources = DictionaryObject({NameObject('/Font'): DictionaryObject()})
    unused = DecodedStreamObject(); unused.set_data(b'unused hidden font payload' * 100)
    for name in ['Hidden', 'Visible']:
        f = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                              NameObject('/BaseFont'): NameObject('/Helvetica')})
        if name == 'Hidden': f[NameObject('/Unused')] = w._add_object(unused)
        resources['/Font'][NameObject('/'+name)] = w._add_object(f)
    for data in [DRAWING + OCR + VISIBLE, DRAWING + b'q BT /Hidden 12 Tf 1 0 0 1 20 60 Tm (Keep) Tj ET Q\n']:
        p=w.add_blank_page(width=100,height=100)
        p[NameObject('/Resources')]=resources
        stream=DecodedStreamObject();stream.set_data(data)
        p[NameObject('/Contents')]=w._add_object(stream)
    return w


class OCRTests(unittest.TestCase):
    def test_indirect_resources_are_supported(self):
        w=make_writer();p=w.pages[0]
        p[NameObject('/Resources')]=w._add_object(p['/Resources'])
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(remove_invisible_text(w),(1,1))
        self.assertIn('/Visible',p['/Resources']['/Font'])

    def test_visual_failure_does_not_publish_output(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'source.pdf',Path(folder)/'out.pdf'
            make_writer().write(source)
            def change_drawing(w):
                s=DecodedStreamObject();s.set_data(b'1 0 0 rg 10 10 80 80 re f\n')
                w.pages[0][NameObject('/Contents')]=w._add_object(s)
            with patch('invisible_text.remove_invisible_text',side_effect=change_drawing):
                with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'changed visually'):
                    optimize(source,out,remove_invisible_text=True)
            self.assertFalse(out.exists())

    def test_signatures_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'source.pdf',Path(folder)/'out.pdf'
            w=make_writer();w.root_object[NameObject('/Perms')]=DictionaryObject();w.write(source)
            with self.assertRaisesRegex(ValueError,'signatures'):
                optimize(source,out,remove_invisible_text=True)
            self.assertFalse(out.exists())

    def test_removes_only_isolated_invisible_scope_and_protects_shared_resources(self):
        w=make_writer();second=w.pages[1].get_contents().get_data()
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(remove_invisible_text(w),(1,1))
        self.assertEqual(w.pages[0].get_contents().get_data(), DRAWING+b'\n'+VISIBLE)
        self.assertEqual(w.pages[1].get_contents().get_data(),second)
        self.assertNotIn('/Hidden',w.pages[0]['/Resources']['/Font'])
        self.assertIn('/Hidden',w.pages[1]['/Resources']['/Font'])

    def test_unrecognized_or_clipping_text_is_preserved(self):
        for changed in [OCR.replace(b'3 Tr',b'7 Tr'), OCR.replace(b'Tj ET',b'Tj 1 w ET')]:
            w=make_writer();s=DecodedStreamObject();s.set_data(changed)
            w.pages[0][NameObject('/Contents')]=w._add_object(s)
            with self.assertRaisesRegex(ValueError,'No supported'):remove_invisible_text(w)
            self.assertEqual(w.pages[0].get_contents().get_data(),changed)

    def test_rebuild_drops_unreachable_font_payload_and_keeps_visible_text(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'source.pdf',Path(folder)/'out.pdf'
            w=make_writer()
            w.pages[1][NameObject('/Resources')]=DictionaryObject()
            w.pages[1][NameObject('/Contents')]=w._add_object(DecodedStreamObject())
            w.write(source);original=source.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):optimize(source,out,remove_invisible_text=True)
            self.assertEqual(source.read_bytes(),original)
            self.assertNotIn(b'unused hidden font payload',out.read_bytes())
            self.assertIn('Visible',PdfReader(out).pages[0].extract_text())
            self.assertNotIn('Hello',PdfReader(out).pages[0].extract_text())

    def test_annotations_retain_font_resources(self):
        w=make_writer();w.pages[0][NameObject('/Annots')]=ArrayObject([w._add_object(DictionaryObject({
            NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Text'),
            NameObject('/Rect'):ArrayObject([NumberObject(x) for x in [0,0,10,10]])}))])
        with contextlib.redirect_stdout(io.StringIO()):remove_invisible_text(w)
        self.assertIn('/Hidden',w.pages[0]['/Resources']['/Font'])
        self.assertEqual(len(w.pages[0]['/Annots']),1)

    def test_modes_cannot_be_combined(self):
        with self.assertRaisesRegex(ValueError,'separate pass'):
            optimize('unused','unused',fast_handwriting=True,remove_invisible_text=True)


if __name__=='__main__':unittest.main()
