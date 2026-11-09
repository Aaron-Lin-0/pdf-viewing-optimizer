import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handwriting import HEADER, _render, batch_segments, batching_contexts, remove_duplicate_layer, rewrite_handwriting, verify_handwriting
from optimize_pdf import optimize
import optimize_pdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject
from unittest.mock import patch
from PIL import ImageChops, ImageStat
from viewing_copy import build_candidate, page_delta, verify_pdfjs_page


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


def make_crossing_masked_fixture(path, type3_fallback=False):
    w = PdfWriter()
    p = w.add_blank_page(width=200, height=200)
    state = DictionaryObject({NameObject(k): v for k, v in {
        '/AIS': BooleanObject(False), '/BM': NameObject('/Normal'), '/CA': NumberObject(1),
        '/LC': NumberObject(2), '/Type': NameObject('/ExtGState'), '/ca': NumberObject(1)}.items()})
    form = DecodedStreamObject()
    form.update({
        NameObject('/Type'): NameObject('/XObject'),
        NameObject('/Subtype'): NameObject('/Form'),
        NameObject('/BBox'): ArrayObject([NumberObject(x) for x in [0, 0, 200, 200]]),
        NameObject('/Resources'): DictionaryObject({
            NameObject('/ExtGState'): DictionaryObject({NameObject('/GS'): state})
        })
    })
    form.set_data(HEADER + b'10 w 20 20 m 80 80 l S\n10 w 20 80 m 80 20 l S\n')
    layer = w._add_object(form)

    mask_form = DecodedStreamObject()
    mask_form.update({
        NameObject('/Type'): NameObject('/XObject'),
        NameObject('/Subtype'): NameObject('/Form'),
        NameObject('/BBox'): ArrayObject([NumberObject(x) for x in [0, 0, 200, 200]]),
        NameObject('/Resources'): DictionaryObject(),
        NameObject('/Group'): DictionaryObject({
            NameObject('/S'): NameObject('/Transparency'),
            NameObject('/CS'): NameObject('/DeviceGray'),
            NameObject('/I'): BooleanObject(True)
        })
    })
    mask_form.set_data(b'0.5 g 0 0 200 200 re f\n')
    mask_ref = w._add_object(mask_form)

    masked_gs = DictionaryObject({
        NameObject('/Type'): NameObject('/ExtGState'),
        NameObject('/SMask'): DictionaryObject({
            NameObject('/S'): NameObject('/Luminosity'),
            NameObject('/G'): mask_ref
        })
    })
    p[NameObject('/Resources')] = DictionaryObject({
        NameObject('/XObject'): DictionaryObject({NameObject('/FFT0'): layer}),
        NameObject('/ExtGState'): DictionaryObject({NameObject('/Masked'): masked_gs})
    })
    content = DecodedStreamObject()
    if type3_fallback:
        p.mediabox.upper_right = (450, 200)
        glyph = DecodedStreamObject()
        glyph.set_data(b'200 0 d0 q /Masked gs /FFT0 Do Q')
        font = DictionaryObject({NameObject(key): value for key, value in {
            '/Type': NameObject('/Font'), '/Subtype': NameObject('/Type3'),
            '/FontBBox': form['/BBox'],
            '/FontMatrix': ArrayObject([NumberObject(x) for x in (1, 0, 0, 1, 0, 0)]),
            '/FirstChar': NumberObject(65), '/LastChar': NumberObject(65),
            '/Widths': ArrayObject([NumberObject(200)]),
            '/CharProcs': DictionaryObject({NameObject('/A'): w._add_object(glyph)}),
            '/Encoding': DictionaryObject({NameObject('/Differences'):
                                            ArrayObject([NumberObject(65), NameObject('/A')])}),
        }.items()})
        p['/Resources'][NameObject('/Font')] = DictionaryObject({NameObject('/T3'): w._add_object(font)})
        content.set_data(b'q /FFT0 Do Q BT /T3 1 Tf 200 0 Td (A) Tj ET')
    else:
        content.set_data(b'q /Masked gs /FFT0 Do Q\n')
    p[NameObject('/Contents')] = w._add_object(content)
    w.write(path)


class HandwritingTests(unittest.TestCase):
    def test_type3_glyph_fallback_protects_shared_form_from_batching(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = (Path(folder) / name for name in ('source.pdf', 'output.pdf'))
            make_crossing_masked_fixture(source, type3_fallback=True)
            original = source.read_bytes()
            # The glyph really paints the masked stroke in the right half.
            self.assertLess(_render(source, 0).crop((400, 0, 800, 400)).getextrema()[0][0], 255)
            with self.assertRaisesRegex(ValueError, 'No supported BOOX'):
                optimize(source, output, fast_handwriting=True)
            with self.assertRaisesRegex(ValueError, 'Type 3 glyph resource fallback'):
                build_candidate(source, output, 'batched')
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), original)

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

    def test_batching_contexts_contain_unrelated_transparency_and_shared_unsafe_calls(self):
        from handwriting import batching_contexts, rewrite_handwriting
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_boox(source)
            writer=PdfWriter(clone_from=PdfReader(source));page=writer.pages[0]
            form=page['/Resources']['/XObject']['/FFT0'];original=form.get_data()
            unrelated=writer.add_blank_page(width=200,height=200)
            unrelated[NameObject('/Group')]=DictionaryObject({NameObject('/S'):NameObject('/Transparency')})
            contexts=batching_contexts(writer)
            self.assertEqual(contexts[id(form)],set())
            changed=rewrite_handwriting(writer,{key for key,value in contexts.items() if not value})
            self.assertEqual(changed[:3],(1,4,1))
            self.assertNotEqual(form.get_data(),original)
            form.set_data(original)
            unrelated[NameObject('/Resources')]=page['/Resources']
            stream=DecodedStreamObject();stream.set_data(b'/FFT0 Do')
            unrelated[NameObject('/Contents')]=writer._add_object(stream)
            contexts=batching_contexts(writer)
            self.assertEqual(contexts[id(form)],{'transparency group'})
            with self.assertRaisesRegex(ValueError,'nothing changed'):
                rewrite_handwriting(writer,{key for key,value in contexts.items() if not value})
            self.assertEqual(form.get_data(),original)

    def test_batching_inherited_transparency_and_graphics_stack(self):
        from handwriting import batching_contexts
        from pypdf.generic import FloatObject
        cases=[('/SMask',DictionaryObject()),('/BM',NameObject('/Multiply')),
               ('/CA',FloatObject(.5)),('/ca',FloatObject(.5)),('/OP',BooleanObject(True))]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_boox(source)
            for key,value in cases:
                with self.subTest(key=key):
                    writer=PdfWriter(clone_from=PdfReader(source));page=writer.pages[0]
                    form=page['/Resources']['/XObject']['/FFT0']
                    state=page['/Resources']['/ExtGState']['/G'];state[NameObject(key)]=value
                    content=DecodedStreamObject();content.set_data(b'q /G gs /FFT0 Do Q /FFT0 Do')
                    page[NameObject('/Contents')]=writer._add_object(content)
                    self.assertIn('inherited '+key,batching_contexts(writer)[id(form)])
                    content.set_data(b'q /G gs Q /FFT0 Do')
                    self.assertEqual(batching_contexts(writer)[id(form)],set())

    def test_batching_uses_child_invocation_state_after_parent_resets_alpha(self):
        from handwriting import batching_contexts
        from pypdf.generic import FloatObject
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_boox(source)
            writer=PdfWriter(clone_from=PdfReader(source));page=writer.pages[0]
            leaf=page['/Resources']['/XObject']['/FFT0']
            parent=DecodedStreamObject()
            parent.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Form'),
                NameObject('/BBox'):leaf['/BBox'],NameObject('/Resources'):DictionaryObject({
                    NameObject('/ExtGState'):DictionaryObject({NameObject('/Reset'):DictionaryObject({
                        NameObject('/CA'):NumberObject(1),NameObject('/ca'):NumberObject(1)})}),
                    NameObject('/XObject'):DictionaryObject({NameObject('/Leaf'):leaf.indirect_reference})})})
            parent.set_data(b'/Reset gs /Leaf Do')
            page['/Resources']['/XObject'][NameObject('/Parent')]=writer._add_object(parent)
            page['/Resources']['/ExtGState']['/G'][NameObject('/CA')]=FloatObject(.5)
            content=DecodedStreamObject();content.set_data(b'/G gs /Parent Do')
            page[NameObject('/Contents')]=writer._add_object(content)
            contexts=batching_contexts(writer)
            self.assertIn('inherited /CA',contexts[id(parent)])
            self.assertEqual(contexts[id(leaf)],set())
            parent[NameObject('/Group')]=DictionaryObject({NameObject('/S'):NameObject('/Transparency')})
            self.assertIn('transparency group',batching_contexts(writer)[id(leaf)])
            del parent['/Group']
            page['/Resources']['/ExtGState']['/G'][NameObject('/SMask')]=DictionaryObject()
            self.assertIn('inherited /SMask',batching_contexts(writer)[id(leaf)])

    def test_batching_protects_mask_and_annotation_resources_and_rejects_cycles(self):
        from handwriting import batching_contexts
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_boox(source)
            writer=PdfWriter(clone_from=PdfReader(source));page=writer.pages[0]
            form=page['/Resources']['/XObject']['/FFT0']
            page['/Resources']['/ExtGState']['/G'][NameObject('/SMask')]=DictionaryObject({
                NameObject('/G'):form.indirect_reference})
            self.assertIn('indirect paint resource /ExtGState',batching_contexts(writer)[id(form)])
            del page['/Resources']['/ExtGState']['/G']['/SMask']
            page[NameObject('/Annots')]=ArrayObject([writer._add_object(DictionaryObject({
                NameObject('/AP'):DictionaryObject({NameObject('/N'):form.indirect_reference})}))])
            self.assertIn('annotation appearance',batching_contexts(writer)[id(form)])
            del page['/Annots']
            form['/Resources'][NameObject('/XObject')]=DictionaryObject({NameObject('/Self'):form.indirect_reference})
            form.set_data(b'/Self Do')
            with self.assertRaisesRegex(ValueError,'cycle'):batching_contexts(writer)

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

    def test_crossing_stroke_inherited_mask_legacy_guard_flaw_and_repaired_rejection(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder) / 'source.pdf'
            make_crossing_masked_fixture(source)
            before_bytes = source.read_bytes()

            # 1. Establish that legacy whole-page peak/mean verification misses interior damage.
            batched_pdf = Path(folder) / 'batched_manual.pdf'
            w = PdfWriter(clone_from=PdfReader(source))
            fft0 = w.pages[0]['/Resources']['/XObject']['/FFT0']
            fft0.set_data(HEADER + b'10 w\n20 20 m 80 80 l\n20 80 m 80 20 l\nS\n')
            w.write(batched_pdf)

            with _render(source, 0) as a, _render(batched_pdf, 0) as b:
                with ImageChops.difference(a, b) as difference:
                    peak = max(high for low, high in difference.getextrema())
                    mean = sum(ImageStat.Stat(difference).mean) / 3

            # Differences exist at the crossing intersection.
            self.assertGreater(peak, 0)
            # Legacy peak/mean thresholds pass because whole-page averages dilute the interior damage.
            self.assertLessEqual(peak, 96)
            self.assertLessEqual(mean, 1.5)
            self.assertEqual(verify_handwriting(source, batched_pdf), 1)

            # Both PDFium and PDF.js detect the interior difference outside the edge band.
            with self.assertRaisesRegex(ValueError, 'edge band'):
                page_delta(source, batched_pdf, 0, allow_edges=True)
            with self.assertRaises(ValueError):
                verify_pdfjs_page(source, batched_pdf, 1, allow_edges=True)

            # 2. The scoped proof reports inherited /SMask.
            writer = PdfWriter(clone_from=PdfReader(source))
            contexts = batching_contexts(writer)
            fft0_form = writer.pages[0]['/Resources']['/XObject']['/FFT0']
            self.assertIn('inherited /SMask', contexts[id(fft0_form)])

            # 3. Viewing-copy batching rejects the unsafe Form.
            with self.assertRaisesRegex(ValueError, 'no proven opaque Form invocation'):
                build_candidate(source, Path(folder) / 'batched_candidate.pdf', 'batched')

            # 4. Repaired legacy API caller rejects the job before publication; source untouched.
            api_out = Path(folder) / 'api_out.pdf'
            with self.assertRaisesRegex(ValueError, 'nothing changed'):
                optimize(source, api_out, fast_handwriting=True)
            self.assertFalse(api_out.exists())
            self.assertEqual(source.read_bytes(), before_bytes)

            # 5. Repaired legacy CLI caller exits with failure; source untouched.
            cli_out = Path(folder) / 'cli_out.pdf'
            with patch.object(sys, 'argv', ['optimize_pdf.py', str(source), str(cli_out), '--fast-handwriting']):
                code = optimize_pdf.main()
            self.assertEqual(code, 1)
            self.assertFalse(cli_out.exists())
            self.assertEqual(source.read_bytes(), before_bytes)

    def test_unrelated_safe_form_remains_eligible_in_mixed_document(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder) / 'mixed.pdf'
            out = Path(folder) / 'mixed_out.pdf'

            # Build a 2-page document:
            # Page 1: unsafe /FFT0 under /Masked (crossing strokes)
            # Page 2: safe /FFT1 (opaque BOOX strokes, no /Masked)
            w = PdfWriter()
            p1 = w.add_blank_page(width=200, height=200)
            pen_state = DictionaryObject({NameObject(k): v for k, v in {
                '/AIS': BooleanObject(False), '/BM': NameObject('/Normal'), '/CA': NumberObject(1),
                '/LC': NumberObject(2), '/Type': NameObject('/ExtGState'), '/ca': NumberObject(1)}.items()})

            form0 = DecodedStreamObject()
            form0.update({
                NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Form'),
                NameObject('/BBox'): ArrayObject([NumberObject(x) for x in [0, 0, 200, 200]]),
                NameObject('/Resources'): DictionaryObject({
                    NameObject('/ExtGState'): DictionaryObject({NameObject('/GS'): pen_state})
                })
            })
            form0_body = HEADER + b'10 w 20 20 m 80 80 l S\n10 w 20 80 m 80 20 l S\n'
            form0.set_data(form0_body)
            f0_ref = w._add_object(form0)

            mask_form = DecodedStreamObject()
            mask_form.update({
                NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                NameObject('/BBox'): ArrayObject([NumberObject(x) for x in [0, 0, 200, 200]]),
                NameObject('/Resources'): DictionaryObject(),
                NameObject('/Group'): DictionaryObject({
                    NameObject('/S'): NameObject('/Transparency'),
                    NameObject('/CS'): NameObject('/DeviceGray'),
                    NameObject('/I'): BooleanObject(True)
                })
            })
            mask_form.set_data(b'0.5 g 0 0 200 200 re f\n')
            mask_ref = w._add_object(mask_form)

            masked_gs = DictionaryObject({
                NameObject('/Type'): NameObject('/ExtGState'),
                NameObject('/SMask'): DictionaryObject({
                    NameObject('/S'): NameObject('/Luminosity'),
                    NameObject('/G'): mask_ref
                })
            })
            p1[NameObject('/Resources')] = DictionaryObject({
                NameObject('/XObject'): DictionaryObject({NameObject('/FFT0'): f0_ref}),
                NameObject('/ExtGState'): DictionaryObject({NameObject('/Masked'): masked_gs})
            })
            c1 = DecodedStreamObject()
            c1.set_data(b'q /Masked gs /FFT0 Do Q\n')
            p1[NameObject('/Contents')] = w._add_object(c1)

            # Page 2: safe /FFT1
            p2 = w.add_blank_page(width=200, height=200)
            form1 = DecodedStreamObject()
            form1.update({
                NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Form'),
                NameObject('/BBox'): ArrayObject([NumberObject(x) for x in [0, 0, 200, 200]]),
                NameObject('/Resources'): DictionaryObject({
                    NameObject('/ExtGState'): DictionaryObject({NameObject('/GS'): pen_state})
                })
            })
            form1_body = (HEADER + b'2 w 20 20 m 25 25 l S\n2 w 25 25 m 30 20 l S\n'
                          b'2 w 30 20 m 20 20 l S\n2 w 50 50 m 50 50 l S\n')
            form1.set_data(form1_body)
            f1_ref = w._add_object(form1)
            p2[NameObject('/Resources')] = DictionaryObject({
                NameObject('/XObject'): DictionaryObject({NameObject('/FFT1'): f1_ref})
            })
            c2 = DecodedStreamObject()
            c2.set_data(b'q /FFT1 Do Q\n')
            p2[NameObject('/Contents')] = w._add_object(c2)

            w.write(source)
            before_bytes = source.read_bytes()

            with contextlib.redirect_stdout(io.StringIO()):
                optimize(source, out, fast_handwriting=True)

            self.assertEqual(source.read_bytes(), before_bytes)
            self.assertTrue(out.exists())

            # Read back output: Page 1 /FFT0 is untouched; Page 2 /FFT1 is batched.
            reader = PdfReader(out)
            out_form0 = reader.pages[0]['/Resources']['/XObject']['/FFT0']
            self.assertEqual(out_form0.get_data(), form0_body)

            out_form1 = reader.pages[1]['/Resources']['/XObject']['/FFT1']
            self.assertNotEqual(out_form1.get_data(), form1_body)
            self.assertIn(b'2 w\n20 20 m 25 25 l\n25 25 m 30 20 l\n30 20 m 20 20 l\n50 50 m 50 50 l\nS\n', out_form1.get_data())

    def test_conservative_duplicate_layer_handling_rejects_unsafe_contexts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / 'source.pdf'
            make_boox(path)

            # 1. Page with /Group rejects duplicate layer removal.
            w = PdfWriter(clone_from=PdfReader(path))
            w.pages[0][NameObject('/Group')] = DictionaryObject({
                NameObject('/S'): NameObject('/Transparency')
            })
            self.assertFalse(remove_duplicate_layer(w.pages[0], w))

            # 2. Inherited /SMask on /FFT0 prevents duplicate layer removal via rewrite_handwriting.
            w2 = PdfWriter(clone_from=PdfReader(path))
            mask_form = DecodedStreamObject()
            mask_form.update({
                NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                NameObject('/BBox'): ArrayObject([NumberObject(x) for x in [0, 0, 200, 200]]),
                NameObject('/Resources'): DictionaryObject(),
                NameObject('/Group'): DictionaryObject({NameObject('/S'): NameObject('/Transparency')})
            })
            mask_form.set_data(b'0.5 g 0 0 200 200 re f\n')
            m_ref = w2._add_object(mask_form)
            masked_gs = DictionaryObject({
                NameObject('/Type'): NameObject('/ExtGState'),
                NameObject('/SMask'): DictionaryObject({NameObject('/S'): NameObject('/Luminosity'), NameObject('/G'): m_ref})
            })
            w2.pages[0]['/Resources']['/ExtGState'][NameObject('/Masked')] = masked_gs
            # Prepend /Masked gs to contents
            old_c = w2.pages[0].get_contents().get_data()
            w2.pages[0][NameObject('/Contents')] = w2._add_object(DecodedStreamObject())
            w2.pages[0]['/Contents'].set_data(b'/Masked gs\n' + old_c)

            # rewrite_handwriting reuses scoped proof and does not remove duplicate layer when /FFT0 is unsafe
            contexts = batching_contexts(w2)
            fft0 = w2.pages[0]['/Resources']['/XObject']['/FFT0']
            self.assertIn('inherited /SMask', contexts[id(fft0)])
            with self.assertRaisesRegex(ValueError, 'nothing changed'):
                rewrite_handwriting(w2)

    def test_exact_inlining_distinct_from_stroke_merging_under_transparency(self):
        from tests.test_inline_handwriting import fixture
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            w, parents, leaf = fixture()
            source = Path(folder) / 'inlining.pdf'
            out = Path(folder) / 'inlined.pdf'
            # Add an inherited transparency state to page
            w.pages[0]['/Resources'].setdefault(NameObject('/ExtGState'), DictionaryObject())[NameObject('/Half')] = DictionaryObject({
                NameObject('/ca'): FloatObject(0.5)
            })
            c = w.pages[0].get_contents().get_data()
            w.pages[0][NameObject('/Contents')] = w._add_object(DecodedStreamObject())
            w.pages[0]['/Contents'].set_data(b'q /Half gs Q\n' + c)
            w.write(source)

            # Exact inlining succeeds and preserves exact drawing commands without stroke merging
            with contextlib.redirect_stdout(io.StringIO()):
                optimize(source, out, inline_handwriting=True)
            self.assertTrue(out.exists())
            reader = PdfReader(out)
            # The Form call in parent container was inlined
            layer_data = reader.pages[0]['/Resources']['/XObject']['/Layer'].get_data()
            self.assertIn(b'h W n', layer_data)


if __name__ == '__main__':
    unittest.main()
