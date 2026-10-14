import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handwriting import HEADER, batch_segments, remove_duplicate_layer, verify_handwriting
from optimize_pdf import optimize
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject


def make_boox(path, alpha=1, unsafe_prefix=False):
    w = PdfWriter()
    p = w.add_blank_page(width=200, height=200)
    state = DictionaryObject({NameObject(k): v for k, v in {
        '/AIS': BooleanObject(False), '/BM': NameObject('/Normal'), '/CA': NumberObject(1),
        '/LC': NumberObject(2), '/Type': NameObject('/ExtGState'), '/ca': NumberObject(1)}.items()})
    form = DecodedStreamObject()
    form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                 NameObject('/BBox'): ArrayObject([NumberObject(x) for x in [0, 0, 200, 200]]),
                 NameObject('/Resources'): DictionaryObject({NameObject('/ExtGState'): DictionaryObject({NameObject('/GS'):state})})})
    # Closed small loop plus a zero-length round-capped dot: neither may disappear.
    form.set_data(HEADER + b'2 w 20 20 m 25 25 l S\n2 w 25 25 m 30 20 l S\n'
                  b'2 w 30 20 m 20 20 l S\n2 w 50 50 m 50 50 l S\n')
    layer = w._add_object(form)
    gs = DictionaryObject({NameObject('/BM'):NameObject('/Normal'), NameObject('/CA'):NumberObject(alpha), NameObject('/ca'):NumberObject(alpha)})
    p[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/FFT0'):layer}),
        NameObject('/ExtGState'):DictionaryObject({NameObject('/G'):gs})})
    content = DecodedStreamObject()
    content.set_data((b'2 w\n' if unsafe_prefix else b'') + b'q 1 0 0 1 0 0 cm /FFT0 Do Q\nq\n'
        b'0 0 0 RG 0 0 0 rg 1 w 0 J 0 j\n/G gs q /FFT0 Do Q\nQ\n')
    p[NameObject('/Contents')] = w._add_object(content)
    w.write(path)


class HandwritingTests(unittest.TestCase):
    def test_editing_array_preserves_other_pages_shared_streams(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder)/'source.pdf'
            make_boox(path)
            w = PdfWriter(clone_from=PdfReader(path)); first=w.pages[0]
            data=first.get_contents().get_data(); split=data.index(b'Q\n')+2
            shared=DecodedStreamObject(); shared.set_data(data[:split].rstrip(b'\n'))
            shared_ref=w._add_object(shared)
            tail=DecodedStreamObject(); tail.set_data(data[split:])
            first[NameObject('/Contents')]=ArrayObject([shared_ref,w._add_object(tail)])
            second=w.add_blank_page(width=200,height=200)
            second[NameObject('/Resources')]=first['/Resources']
            second[NameObject('/Contents')]=ArrayObject([shared_ref])
            expected=second.get_contents().get_data()
            self.assertTrue(remove_duplicate_layer(first,w))
            self.assertEqual(second.get_contents().get_data(),expected)

    def test_width_changes_disjoint_segments_and_dots_preserved(self):
        data = HEADER + b'1 w 0 0 m 1 1 l S\n1 w 2 2 m 3 3 l S\n2 w 4 4 m 4 4 l S\n'
        result, count, groups = batch_segments(data)
        self.assertEqual((count, groups), (3, 2))
        self.assertEqual(result, HEADER + b'1 w\n0 0 m 1 1 l\n2 2 m 3 3 l\nS\n2 w\n4 4 m 4 4 l\nS\n')

    def test_unknown_operations_are_untouched(self):
        data = HEADER + b'1 w 0 0 m 1 1 l S\n0.5 g\n'
        self.assertEqual(batch_segments(data), (data, 0, 0))

    def test_nondefault_state_does_not_remove_layer(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / 'source.pdf'
            for kwargs in ({'alpha':0}, {'unsafe_prefix':True}):
                make_boox(path, **kwargs)
                w = PdfWriter(clone_from=PdfReader(path))
                original = w.pages[0].get_contents().get_data()
                self.assertFalse(remove_duplicate_layer(w.pages[0], w))
                self.assertEqual(w.pages[0].get_contents().get_data(), original)

    def test_small_loop_and_dot_survive_full_optimization(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, out = Path(folder)/'source.pdf', Path(folder)/'out.pdf'
            make_boox(source)
            before = source.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                optimize(source, out, fast_handwriting=True)
            self.assertEqual(source.read_bytes(), before)
            reader = PdfReader(out)
            self.assertEqual(reader.pages[0].get_contents().get_data().count(b'/FFT0 Do'), 1)

    def test_missing_handwriting_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, empty = Path(folder)/'source.pdf', Path(folder)/'empty.pdf'
            make_boox(source)
            w = PdfWriter(); w.add_blank_page(width=200, height=200); w.write(empty)
            with self.assertRaisesRegex(ValueError, 'candidate rejected'):
                verify_handwriting(source, empty)


if __name__ == '__main__':
    unittest.main()
