"""The index-warning compatibility exception must not admit other repairs."""
import json
import logging
from pathlib import Path
import re
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import NameObject, TextStringObject

from lossless_verify import UnsupportedPDF, validate_quick_input
from pdf_runtime import build_qpdf_candidate, filehash
from tools.normalize_boox import normalize_index_warning, validate_direct_input


class IndexNormalizationTests(unittest.TestCase):
    def fixture(self, folder, warning=True):
        path = folder / 'source.pdf'
        with PdfWriter() as writer:
            writer.add_blank_page(100, 100)
            writer.add_metadata({'/Producer': 'NeoPdf'})
            writer.write(path)
        if warning:
            data = re.sub(rb'xref\n0 (\d+)\n0000000000 65535 f \n',
                          lambda m: b'xref\n1 ' + str(int(m[1])-1).encode() + b'\n',
                          path.read_bytes())
            path.write_bytes(data)
        return path

    def test_exact_warning_passes_without_changing_default_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self.fixture(root); digest = filehash(source)
            with self.assertRaisesRegex(UnsupportedPDF, 'zero-indexed'):
                validate_quick_input(source)
            self.assertEqual(validate_direct_input(source), (1, True))
            result = normalize_index_warning(source, root/'out.pdf')
            self.assertTrue(result['preservation']['complete'])
            self.assertEqual(validate_quick_input(root/'out.pdf'), 1)
            self.assertEqual(filehash(source), digest)
            with self.assertRaises(FileExistsError):
                normalize_index_warning(source, root/'out.pdf')

    def test_no_warning_and_other_warning_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self.fixture(root, False)
            with self.assertRaisesRegex(UnsupportedPDF, 'warning is required'):
                normalize_index_warning(source, root/'out.pdf')
            with patch('tools.normalize_boox.strict_reader', side_effect=lambda _: logging.getLogger('pypdf._reader').warning('Other repair')):
                with self.assertRaisesRegex(UnsupportedPDF, 'Other repair'):
                    normalize_index_warning(source, root/'out.pdf')
            self.assertFalse((root/'out.pdf').exists())

    def test_direct_reviews_and_verifies_incremental_export_against_original(self):
        from batch_workflow import BatchController, _save_job, confirmation_text, plan_batch, validate_result
        from test_overnight import BooxPreprocessingTests

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            source = BooxPreprocessingTests().incremental(root)
            digest = filehash(source)
            job, = plan_batch([source], profile='quick')
            self.assertEqual(job.status, 'ready', job.error)
            self.assertTrue(job.current_revision)
            self.assertIn('snapshot of the current revision', confirmation_text([job]))
            self.assertEqual(plan_batch([source], optimizer='qpdf', profile='quick')[0].status, 'rejected')
            self.assertFalse(job.output.exists())

            result, = BatchController([job]).run()
            self.assertEqual(result.status, 'optimized', result.error)
            report_path = result.output.with_suffix('.json')
            report = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(report['source_sha256'], digest)
            self.assertEqual(report['source_preparation']['source_sha256'], digest)
            self.assertTrue(report['source_preparation']['preservation']['complete'])
            self.assertTrue(report['verification']['exact'])
            self.assertTrue(all(row['exact'] for row in report['pdfjs_all_pages']))
            self.assertTrue(report['high_resolution_and_print']['detail']['exact'])
            self.assertEqual(filehash(source), digest)
            self.assertEqual(filehash(result.output), report['output_sha256'])
            _save_job(replace(result,status='running'))
            self.assertEqual(plan_batch([source], profile='quick')[0].status, 'optimized')

            report.pop('source_preparation')
            report_path.write_text(json.dumps(report), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'verified current-revision preparation'):
                validate_result(job, recovering=True)

    def test_failed_preparation_stops_before_building_the_viewing_copy(self):
        from quick_lossless import optimize_quick
        from test_overnight import BooxPreprocessingTests

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            source = BooxPreprocessingTests().incremental(root)
            output = root/'out.pdf'
            with patch('tools.normalize_boox.check_qpdf', side_effect=ValueError('qpdf reported unfamiliar warnings')), \
                 patch('quick_lossless.direct_candidate') as transform:
                optimize_quick(source, output)
            transform.assert_not_called()
            self.assertFalse(output.exists())
            report = json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'], 'no improvement')
            self.assertIn('unfamiliar warnings', report['reason'])

    def test_warning_does_not_hide_a_signature(self):
        from pypdf.generic import DictionaryObject

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'signed.pdf'
            with PdfWriter() as writer:
                writer.add_blank_page(100, 100)
                writer.add_metadata({'/Producer': 'NeoPdf'})
                writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Sig')}))
                writer.write(source)
            data = re.sub(rb'xref\n0 (\d+)\n0000000000 65535 f \n',
                          lambda match: b'xref\n1 ' + str(int(match[1])-1).encode() + b'\n',source.read_bytes())
            source.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'signatures'):
                validate_direct_input(source)

    def test_incremental_history_remains_blocking(self):
        from test_overnight import BooxPreprocessingTests
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = BooxPreprocessingTests().incremental(root)
            with self.assertRaisesRegex(UnsupportedPDF, 'incremental'):
                normalize_index_warning(source, root/'out.pdf')
            self.assertFalse((root/'out.pdf').exists())

    def test_mutation_never_publishes_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self.fixture(root)
            def changed(_source, destination, **kwargs):
                with PdfWriter() as writer:
                    writer.add_blank_page(100,100)
                    writer._root_object[NameObject('/LostOriginal')] = TextStringObject('damage')
                    writer.write(destination)
            with patch('tools.normalize_boox.build_qpdf_candidate', side_effect=changed):
                with self.assertRaises(ValueError):
                    normalize_index_warning(source, root/'out.pdf')
            self.assertEqual(list(root.iterdir()), [source])

    def test_source_change_blocks_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self.fixture(root)
            def changed(source, destination, **kwargs):
                build_qpdf_candidate(source, destination, **kwargs)
                with source.open('ab') as stream:
                    stream.write(b'\n')
            with patch('tools.normalize_boox.build_qpdf_candidate', side_effect=changed):
                with self.assertRaisesRegex(ValueError, 'Original changed'):
                    normalize_index_warning(source, root/'out.pdf')
            self.assertEqual(list(root.iterdir()), [source])
