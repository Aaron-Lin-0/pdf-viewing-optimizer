import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handwriting import HEADER, inline_handwriting
from optimize_pdf import optimize
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

BODY = b'2 w\n10 10 m 15 15 l\n15 15 m 20 10 l\n20 10 m 10 10 l\n30 30 m 30 30 l\nS\n3 w\n40 40 m 90 40 l\nS\n'
IDENTITY = b'q 1 0 0 1 0 0 cm /F0 Do Q\n'
TRANSFORMED = b'q 0.5 0 0 0.5 20 20 cm /F0 Do Q\n'


def fixture():
    w = PdfWriter()
    state = DictionaryObject({NameObject(k): v for k, v in {
        '/AIS': BooleanObject(False), '/BM': NameObject('/Normal'), '/CA': NumberObject(1),
        '/LC': NumberObject(2), '/Type': NameObject('/ExtGState'), '/ca': NumberObject(1)}.items()})
    leaf = DecodedStreamObject()
    leaf.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                 NameObject('/BBox'): ArrayObject([NumberObject(x) for x in [0, 0, 60, 60]]),
                 NameObject('/Resources'): DictionaryObject({NameObject('/ExtGState'): DictionaryObject({NameObject('/GS'): state})})})
    leaf.set_data(HEADER + BODY)
    leaf_ref = w._add_object(leaf)
    resources = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/F0'): leaf_ref}),
                                  NameObject('/ExtGState'): DictionaryObject({NameObject('/InlinePen'): state})})
    parents = []
    for data in [IDENTITY + TRANSFORMED + IDENTITY, TRANSFORMED]:
        parent = DecodedStreamObject()
        parent.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                       NameObject('/BBox'): ArrayObject([NumberObject(x) for x in [0, 0, 100, 100]]),
                       NameObject('/Resources'): resources})
        parent.set_data(data)
        parents.append(parent)
        page = w.add_blank_page(width=100, height=100)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Layer'): w._add_object(parent)})})
        content = DecodedStreamObject(); content.set_data(b'q /Layer Do Q\n1 0 0 rg 80 80 10 10 re f\n')
        page[NameObject('/Contents')] = w._add_object(content)
    return w, parents, leaf


class InlineTests(unittest.TestCase):
    def test_mixed_calls_shared_resources_collision_and_order(self):
        w, parents, leaf = fixture()
        other_resources = parents[1]['/Resources']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(inline_handwriting(w), (2, 1))
        data = parents[0].get_data()
        self.assertEqual(data.count(BODY), 2)
        self.assertLess(data.index(BODY), data.index(TRANSFORMED))
        self.assertLess(data.index(TRANSFORMED), data.rindex(BODY))
        self.assertEqual(data.count(b'/InlinePen1 gs'), 2)
        self.assertIn('/F0', parents[0]['/Resources']['/XObject'])
        self.assertIs(parents[1]['/Resources'], other_resources)
        self.assertNotIn('/InlinePen1', other_resources['/ExtGState'])
        self.assertEqual(parents[1].get_data(), TRANSFORMED)
        self.assertEqual(leaf.get_data(), HEADER + BODY)

    def test_full_pipeline_clipping_loops_dots_state_and_reloaded_bytes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder)/'input.pdf', Path(folder)/'out.pdf'
            w, parents, leaf = fixture(); w.write(source)
            original = source.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()): optimize(source, output, inline_handwriting=True)
            self.assertEqual(source.read_bytes(), original)
            reader = PdfReader(output)
            data = reader.pages[0]['/Resources']['/XObject']['/Layer'].get_data()
            self.assertEqual(data.count(BODY), 2)
            self.assertEqual(data.count(b'0 0 m 60 0 l 60 60 l 0 60 l h W n'), 2)
            self.assertIn(TRANSFORMED, data)
            self.assertEqual(reader.pages[0].get_contents().get_data(), w.pages[0].get_contents().get_data())

    def test_prunes_only_unneeded_references(self):
        w, parents, leaf = fixture(); parents[0].set_data(IDENTITY)
        with contextlib.redirect_stdout(io.StringIO()): inline_handwriting(w)
        self.assertNotIn('/F0', parents[0]['/Resources']['/XObject'])
        self.assertIn('/F0', parents[1]['/Resources']['/XObject'])

    def test_unsupported_leafs_and_parent_commands_untouched(self):
        for kind in ('group', 'alpha', 'matrix', 'parent'):
            w, parents, leaf = fixture()
            if kind == 'group': leaf[NameObject('/Group')] = DictionaryObject()
            if kind == 'alpha': leaf['/Resources']['/ExtGState']['/GS'][NameObject('/CA')] = NumberObject(0)
            if kind == 'matrix': leaf[NameObject('/Matrix')] = ArrayObject([NumberObject(x) for x in [2,0,0,2,0,0]])
            if kind == 'parent': parents[0].set_data(b'0.5 g\n' + IDENTITY)
            expected = parents[0].get_data()
            with self.assertRaisesRegex(ValueError, 'No supported'): inline_handwriting(w)
            self.assertEqual(parents[0].get_data(), expected)

    def test_indirect_resources(self):
        w, parents, leaf = fixture()
        resource = parents[0]['/Resources']
        resource[NameObject('/XObject')] = w._add_object(resource['/XObject'])
        resource[NameObject('/ExtGState')] = w._add_object(resource['/ExtGState'])
        parents[0][NameObject('/Resources')] = w._add_object(resource)
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(inline_handwriting(w), (2, 1))

    def test_visual_mismatch_is_not_published(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder)/'input.pdf', Path(folder)/'out.pdf'
            fixture()[0].write(source)
            def broken(w):
                layer = w.pages[0]['/Resources']['/XObject']['/Layer']
                layer.set_data(b'0 0 100 100 re f\n')
            with patch('handwriting.inline_handwriting', side_effect=broken):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'changed visually'):
                    optimize(source, output, inline_handwriting=True)
            self.assertFalse(output.exists())

    def test_modes_are_separate(self):
        for kwargs in ({'fast_handwriting':True}, {'remove_invisible_text':True}):
            with self.assertRaisesRegex(ValueError, 'separate passes'):
                optimize('unused', 'unused', inline_handwriting=True, **kwargs)

    def test_structure_compression_remains_compatible(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder)/'input.pdf', Path(folder)/'out.pdf'
            fixture()[0].write(source)
            with contextlib.redirect_stdout(io.StringIO()):
                optimize(source, output, inline_handwriting=True, compact_structure=True)
            data=PdfReader(output).pages[0]['/Resources']['/XObject']['/Layer'].get_data()
            self.assertEqual(data.count(BODY), 2)

    def test_original_unbatched_segments_are_inlined_without_batching(self):
        w,parents,leaf=fixture()
        body=b'2 w 10 10 m 15 15 l S\n2 w 15 15 m 20 10 l S\n'
        leaf.set_data(HEADER+body)
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(inline_handwriting(w),(2,1))
        self.assertEqual(parents[0].get_data().count(body),2)
        self.assertEqual(leaf.get_data(),HEADER+body)


if __name__ == '__main__': unittest.main()
