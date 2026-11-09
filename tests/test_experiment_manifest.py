"""Tests for P16-B experiment manifest and opportunity reporting."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from pypdf import PdfWriter
from pypdf.generic import (DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject)

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from experiment_manifest import (analyze_drawing_opportunity,
                                 build_experiment_manifest,
                                 filehash, freeze_exact_baseline,
                                 SCHEMA)


def stream(writer, data):
    val = DecodedStreamObject()
    val.set_data(data)
    return writer._add_object(val)


class ExperimentManifestTests(unittest.TestCase):
    def test_eligibility_uses_graphics_state_at_each_paint(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder) / 'source.pdf'
            writer = PdfWriter()
            for suffix in (b'', b' 20 10 m 30 20 l 40 10 l S'):
                page = writer.add_blank_page(width=100, height=100)
                page[NameObject('/Resources')] = DictionaryObject({
                    NameObject('/ExtGState'): DictionaryObject({
                        NameObject('/Fade'): DictionaryObject({NameObject('/CA'): FloatObject(.5)}),
                        NameObject('/Hairline'): DictionaryObject({NameObject('/LW'): NumberObject(0)}),
                    })
                })
                page[NameObject('/Contents')] = stream(
                    writer, b'1 w 1 1 m 2 2 l S '
                    b'q /Hairline gs 3 3 m 4 4 l 5 5 l S Q '
                    b'q /Fade gs 6 6 m 7 7 l 8 8 l S Q '
                    b'q .01 .005 0 .01118034 0 0 cm 6 6 m 7 7 l 8 8 l S Q' + suffix)
            writer.write(source)
            report = analyze_drawing_opportunity(source)
            first, second = report['pages']
            self.assertFalse(first['supported_opportunity'])
            self.assertEqual(first['eligible_points'], 0)
            self.assertTrue(second['supported_opportunity'])
            self.assertEqual(second['eligible_runs'], 1)
            self.assertEqual(second['eligible_points'], 3)
            self.assertEqual(report['first_supported_subset']['eligible_paint_calls'], 1)
            self.assertEqual(report['first_supported_subset']['skip_reasons']['device_dependent_hairlines'], 2)
            self.assertEqual(report['first_supported_subset']['skip_reasons']['unsupported_transforms'], 2)

    def test_baseline_requires_exact_report_with_matching_hashes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, copy, unrelated = (Path(folder) / name for name in ('source.pdf', 'copy.pdf', 'blank.pdf'))
            writer = PdfWriter()
            page = writer.add_blank_page(width=150, height=150)
            page[NameObject('/Contents')] = stream(writer, b'1 1 m 2 2 l S')
            writer.write(source)
            copy.write_bytes(source.read_bytes())
            blank = PdfWriter()
            blank.add_blank_page(width=90, height=150)
            blank.write(unrelated)
            self.assertEqual(freeze_exact_baseline(source, unrelated)['baseline_type'], 'original')
            page = {'page': 1, 'exact': True}
            evidence = {
                'status': 'optimized', 'profile': 'quick', 'optimizer': 'adaptive', 'pages': 1,
                'source_sha256': filehash(source), 'output_sha256': filehash(copy),
                'verification': {'complete': True, 'policy': 'direct-viewing-2', 'full': True, 'exact': True,
                                 'checks': dict.fromkeys(('navigation', 'ordered_geometry_and_resources',
                                                         'pdfium_144_dpi'), True),
                                 'pages': [page]},
                'pdfjs_all_pages': [{**page, 'ok': True}],
                'high_resolution_and_print': {'detail': {'exact': True}},
            }
            report_path = copy.with_suffix('.json')
            report_path.write_text(json.dumps(evidence), encoding='utf-8')
            accepted = freeze_exact_baseline(source, copy)
            self.assertEqual(accepted['baseline_type'], 'best_accepted_exact_copy')
            self.assertEqual(accepted['verification_report_sha256'], filehash(report_path))

            evidence['pdfjs_all_pages'][0]['exact'] = False
            report_path.write_text(json.dumps(evidence), encoding='utf-8')
            self.assertEqual(freeze_exact_baseline(source, copy)['baseline_type'], 'original')
            evidence['pdfjs_all_pages'][0]['exact'] = True
            report_path.write_text(json.dumps(evidence), encoding='utf-8')
            unrelated.with_suffix('.json').write_bytes(report_path.read_bytes())
            self.assertEqual(freeze_exact_baseline(source, unrelated)['baseline_type'], 'original')
            self.assertEqual(freeze_exact_baseline(unrelated, copy)['baseline_type'], 'original')

    def test_pure_vector_constant_width_opportunity_detected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            pdf_path = Path(folder) / 'vector_sample.pdf'
            writer = PdfWriter()
            page = writer.add_blank_page(width=200, height=200)
            # Multi-vertex path: 4 vertices in one stroke
            page[NameObject('/Contents')] = stream(
                writer,
                b'1.5 w 10 10 m 20 30 l 40 50 l 60 70 l S'
            )
            writer.write(pdf_path)

            opp = analyze_drawing_opportunity(pdf_path)
            self.assertTrue(opp['first_supported_subset']['supported'])
            self.assertEqual(opp['first_supported_subset']['status'], 'supported opportunity')
            self.assertGreater(opp['inventory']['multi_vertex_stroked_paths'], 0)
            self.assertGreater(opp['inventory']['point_counts'], 0)
            self.assertEqual(opp['inventory']['local_widths'], ['1.5'])
            self.assertEqual(opp['denominator_tracking']['pages_with_supported_opportunity'], 1)

    def test_raster_only_reports_no_opportunity_before_candidate_rendering(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            pdf_path = Path(folder) / 'raster_only.pdf'
            writer = PdfWriter()
            page = writer.add_blank_page(width=100, height=100)
            image = DecodedStreamObject()
            image.set_data(b'\x80' * (10 * 10 * 3))
            image.update({
                NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Image'),
                NameObject('/Width'): NumberObject(10),
                NameObject('/Height'): NumberObject(10),
                NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
                NameObject('/BitsPerComponent'): NumberObject(8),
            })
            img_ref = writer._add_object(image)
            page[NameObject('/Resources')] = DictionaryObject({
                NameObject('/XObject'): DictionaryObject({NameObject('/Im0'): img_ref})
            })
            page[NameObject('/Contents')] = stream(writer, b'q 10 0 0 10 0 0 cm /Im0 Do Q')
            writer.write(pdf_path)

            opp = analyze_drawing_opportunity(pdf_path)
            self.assertFalse(opp['first_supported_subset']['supported'])
            self.assertEqual(opp['first_supported_subset']['status'], 'no supported opportunity')
            self.assertIn('raster-only', opp['first_supported_subset']['explanation'].lower())
            self.assertEqual(opp['denominator_tracking']['pages_raster_only'], 1)
            self.assertEqual(opp['denominator_tracking']['pages_with_supported_opportunity'], 0)

    def test_hairlines_and_transparency_reported_as_skips(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            pdf_path = Path(folder) / 'skipped_drawing.pdf'
            writer = PdfWriter()
            # Page 1: hairline strokes (0 w)
            p1 = writer.add_blank_page(width=100, height=100)
            p1[NameObject('/Contents')] = stream(writer, b'0 w 10 10 m 90 90 l S')

            # Page 2: transparency active
            p2 = writer.add_blank_page(width=100, height=100)
            fade = writer._add_object(DictionaryObject({NameObject('/ca'): FloatObject(0.5)}))
            p2[NameObject('/Resources')] = DictionaryObject({
                NameObject('/ExtGState'): DictionaryObject({NameObject('/Fade'): fade})
            })
            p2[NameObject('/Contents')] = stream(writer, b'/Fade gs 1 w 10 10 m 50 50 l 90 90 l S')
            writer.write(pdf_path)

            opp = analyze_drawing_opportunity(pdf_path)
            self.assertFalse(opp['first_supported_subset']['supported'])
            self.assertEqual(opp['first_supported_subset']['status'], 'no supported opportunity')
            skips = opp['first_supported_subset']['skip_reasons']
            self.assertGreater(skips['device_dependent_hairlines'], 0)
            self.assertGreater(skips['transparency_or_smask'], 0)

    def test_manifest_structure_and_frozen_treatments(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder) / 'source.pdf'
            writer = PdfWriter()
            p1 = writer.add_blank_page(width=100, height=100)
            p1[NameObject('/Contents')] = stream(writer, b'1 w 5 5 m 10 10 l S')
            p2 = writer.add_blank_page(width=100, height=100)
            p2[NameObject('/Contents')] = stream(
                writer,
                b'0.5 w 1 1 m 2 2 l 3 3 l 4 4 l 5 5 l 6 6 l 7 7 l 8 8 l S'
            )
            writer.write(source)

            manifest = build_experiment_manifest(source)
            self.assertEqual(manifest['schema'], SCHEMA)
            self.assertEqual(len(manifest['treatment_table']), 7)
            treatment_names = [t['name'] for t in manifest['treatment_table']]
            self.assertEqual(treatment_names, [
                'Original',
                'Best accepted exact copy',
                'Existing opacity-qualified batching',
                'Zero-tolerance experimental control',
                'Point reduction alone',
                'Point reduction plus qualified batching',
                'Curve fitting',
            ])
            self.assertEqual(manifest['comparator']['baseline_type'], 'original')
            self.assertEqual(manifest['sample_pages']['page_1'], 1)
            # p2 has 0.5w and 8 vertices, so it is identified as small writing / dense
            self.assertIn(2, manifest['sample_pages']['selected_samples'])
            self.assertEqual(manifest['quality_policy']['tolerance_ladder_mm'], [0.0, 0.01, 0.025, 0.05])
            self.assertEqual(manifest['run_budget']['repetitions'], 3)
            self.assertEqual(manifest['run_budget']['noisy_repetition_extension'], 5)


if __name__ == '__main__':
    unittest.main()
