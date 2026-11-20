import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter, PdfReader
from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject, DictionaryObject

from bitmap_study import optimize_bitmap, preflight, settings


class BitmapStudyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root/'source.pdf'
        self.output = self.root/'study.pdf'

    def fixture(self, *, rotate=False, text=False, annotation=False):
        with PdfWriter() as writer:
            page = writer.add_blank_page(72, 96)
            stream = DecodedStreamObject()
            stream.set_data(b'0 0 0 RG 1 w 5 5 m 65 90 l S 10 15 2 2 re f\n')
            page[NameObject('/Contents')] = writer._add_object(stream)
            if rotate: page.rotate(90)
            if text:
                page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
                    NameObject('/F1'): writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                    NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))})})
                stream.set_data(b'BT /F1 12 Tf 10 20 Td (search) Tj ET')
            if annotation:
                from pypdf.annotations import Text
                writer.add_annotation(0, Text(rect=(1, 1, 20, 20), text='keep comment'))
            writer.add_metadata({'/Title': 'Generated study control'})
            writer.add_attachment('control.txt', b'attachment data')
            writer.add_outline_item('Page one', 0)
            writer.set_page_label(0, 0, style='/D', prefix='Study ', start=1)
            writer.write(self.source)

    def test_saved_images_features_and_batch_identity(self):
        self.fixture()
        from batch_workflow import Job, validate_result
        from pdf_runtime import filehash
        before = filehash(self.source)
        optimize_bitmap(self.source, self.output, paper='source')
        report = json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['pages'][0]['pixels'], [300, 400])
        self.assertTrue(report['verification']['complete'])
        self.assertEqual(filehash(self.source), before)
        with PdfReader(self.output) as reader:
            self.assertEqual(reader.metadata.title, 'Generated study control')
            self.assertEqual(reader.attachments['control.txt'], [b'attachment data'])
            self.assertEqual(reader.outline[0].title, 'Page one')
            self.assertEqual(reader.page_labels, ['Study 1'])
        job = Job(self.source, self.output, before, 'ready', profile='bitmap', study_paper='source')
        self.assertEqual(validate_result(job)[0], 'optimized')
        from dataclasses import replace
        with self.assertRaisesRegex(RuntimeError, 'settings'):
            validate_result(replace(job, study_dpi=450))

    def test_rotated_letter_fit(self):
        self.fixture(rotate=True)
        optimize_bitmap(self.source, self.output)
        report = json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['pages'][0]['pixels'], [2550, 1913])
        with PdfReader(self.output) as reader:
            self.assertEqual(list(reader.pages[0].mediabox), [0, 0, 612, 792])
            self.assertEqual(reader.pages[0].rotation, 0)

    def test_source_paper_preserves_fractional_physical_dimensions(self):
        from pypdf.generic import NumberObject
        with PdfWriter() as writer:
            page = writer.add_blank_page(72.123456, 96)
            page[NameObject('/UserUnit')] = NumberObject(2)
            page.rotate(90)
            writer.write(self.source)
        optimize_bitmap(self.source, self.output, paper='source')
        report = json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['pages'][0]['pixels'], [800, 602])
        with PdfReader(self.output) as reader:
            page = reader.pages[0]
            self.assertEqual(page.user_unit, 1)
            self.assertEqual(page.mediabox.width, 192)
            self.assertAlmostEqual(page.mediabox.height, 144.246912, places=4)
            self.assertEqual(list(map(float, page.mediabox))[2:], report['pages'][0]['paper_points'])

    def test_whole_page_bookmark_with_chained_action_is_rejected(self):
        from pypdf.generic import TextStringObject
        with PdfWriter() as writer:
            writer.add_blank_page(72, 96)
            bookmark = writer.add_outline_item('Page one', 0).get_object()
            bookmark['/A'][NameObject('/Next')] = DictionaryObject({
                NameObject('/S'): NameObject('/URI'), NameObject('/URI'): TextStringObject('https://example.invalid/')})
            writer.write(self.source)
        with self.assertRaisesRegex(ValueError, 'bookmark actions'):
            preflight(self.source)
        with self.assertRaisesRegex(ValueError, 'bookmark actions'):
            optimize_bitmap(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_encryption_and_signature_fields_fail_preflight_and_publication(self):
        for protected in ('Encrypted', 'signatures'):
            with self.subTest(protected=protected), PdfWriter() as writer:
                writer.add_blank_page(72, 96)
                if protected == 'Encrypted':
                    writer.encrypt('secret')
                else:
                    writer._root_object[NameObject('/AcroForm')] = DictionaryObject({
                        NameObject('/Fields'): ArrayObject([
                            writer._add_object(DictionaryObject({NameObject('/FT'): NameObject('/Sig')}))])})
                writer.write(self.source)
                with self.assertRaisesRegex(ValueError, protected):
                    preflight(self.source)
                with self.assertRaises(ValueError):
                    optimize_bitmap(self.source, self.output)
                self.assertFalse(any(self.output.with_suffix(suffix).exists() for suffix in ('.pdf', '.json', '.md')))

    def test_search_and_annotations_declined(self):
        for option in ('text', 'annotation'):
            with self.subTest(option=option):
                self.fixture(**{option: True})
                with self.assertRaises(ValueError): preflight(self.source)
                self.assertFalse(self.output.exists())

    def test_collision_and_failed_publication(self):
        self.fixture()
        self.output.with_suffix('.md').write_text('existing')
        with self.assertRaises(FileExistsError): optimize_bitmap(self.source, self.output)
        self.assertEqual(self.output.with_suffix('.md').read_text(), 'existing')
        self.output.with_suffix('.md').unlink()
        with patch('bitmap_study.shutil.copyfileobj', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): optimize_bitmap(self.source, self.output, paper='source')
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix('.json').exists())


    def test_cli_rejects_undefined_xobject_in_page_and_nested_form(self):
        import subprocess
        import sys
        from pypdf.generic import NumberObject
        from pdf_runtime import check_qpdf, qpdf_identity

        for in_form in (False, True):
            with self.subTest(in_form=in_form), PdfWriter() as writer:
                page = writer.add_blank_page(72, 96)
                content = DecodedStreamObject()
                body = b'0 0 12 12 re f /MissingMark Do'
                if in_form:
                    form = DecodedStreamObject()
                    form.set_data(body)
                    form.update({NameObject('/Type'): NameObject('/XObject'),
                                 NameObject('/Subtype'): NameObject('/Form'),
                                 NameObject('/BBox'): ArrayObject([NumberObject(n) for n in (0, 0, 72, 96)]),
                                 NameObject('/Resources'): DictionaryObject()})
                    page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({
                        NameObject('/Form'): writer._add_object(form)})})
                    body = b'/Form Do'
                content.set_data(body)
                page[NameObject('/Contents')] = writer._add_object(content)
                writer.write(self.source)
                original = self.source.read_bytes()
                check_qpdf(self.source, qpdf_identity())
                with self.assertRaisesRegex(ValueError, '/XObject /MissingMark'):
                    preflight(self.source)
                result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'optimize_pdf.py'),
                                         str(self.source), str(self.output), '--viewing-copy',
                                         '--viewing-profile', 'bitmap', '--study-paper', 'source'],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('/XObject /MissingMark', result.stderr)
                self.assertNotIn('Study pages rasterized', result.stdout)
                self.assertEqual(self.source.read_bytes(), original)
                self.assertFalse(any(self.output.with_suffix(ext).exists() for ext in ('.pdf', '.json', '.md')))

    def test_cumulative_image_budget_and_completed_page_progress(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from viewing_copy_gui import stage_progress, verified_page_fraction

        def make(count):
            with PdfWriter() as writer:
                for _ in range(count):
                    writer.add_blank_page(72, 72)
                writer.write(self.source)

        single_page_log = StringIO()
        with patch('bitmap_study.MAX_ENCODED_IMAGE_BYTES', 200):
            make(1)
            with redirect_stdout(single_page_log):
                optimize_bitmap(self.source, self.root/'single.pdf', paper='source')
            make(2)
            over_budget_log = StringIO()
            with redirect_stdout(over_budget_log), self.assertRaisesRegex(ValueError, 'total encoded image limit'):
                optimize_bitmap(self.source, self.output, paper='source')
        self.assertFalse(any(self.output.with_suffix(ext).exists() for ext in ('.pdf', '.json', '.md')))
        self.assertIn('Study pages rasterized: 1/2', over_budget_log.getvalue())
        self.assertNotIn('Study pages rasterized: 2/2', over_budget_log.getvalue())
        self.assertNotIn('Stage: study saved verification', over_budget_log.getvalue())

        progress = single_page_log.getvalue().splitlines()
        milestones = ['Stage: study rasterization', 'Study pages rasterized: 1/1',
                      'Stage: study packing', 'Stage: study saved verification',
                      'Study pages verified: 1/1', 'Stage: publication']
        self.assertEqual([line for line in progress if line in milestones], milestones)
        self.assertEqual(verified_page_fraction('Study pages rasterized: 1/2'), .5)
        self.assertEqual(verified_page_fraction('Study pages verified: 1/1'), 1)
        _, raster_end = stage_progress('study rasterization', 10)
        verification_start, verification_end = stage_progress('study saved verification', raster_end)
        publication_start, publication_end = stage_progress('publication', verification_end)
        self.assertTrue(raster_end < verification_start < verification_end < publication_start < publication_end < 100)

    def test_settings_and_source_hash(self):
        self.fixture()
        with self.assertRaises(ValueError): settings('letter', 100)
        with self.assertRaisesRegex(ValueError, 'hash'):
            optimize_bitmap(self.source, self.output, '0'*64)

    def test_budgets_and_batch_recovery_settings(self):
        self.fixture()
        from pdf_runtime import DiskBudgetExceeded
        with self.assertRaises(DiskBudgetExceeded):
            optimize_bitmap(self.source, self.output, paper='source', disk_budget_bytes=1)
        self.assertFalse(self.output.exists())
        from batch_workflow import plan_batch, _save_job, confirmation_text
        job = plan_batch([self.source], profile='bitmap', study_paper='source', study_dpi=450)[0]
        self.assertEqual(job.status, 'ready', job.error)
        _save_job(job)
        self.assertEqual(plan_batch([self.source], profile='bitmap', study_paper='source', study_dpi=450)[0].output, job.output)
        self.assertNotEqual(plan_batch([self.source], profile='bitmap')[0].output, job.output)
        self.assertIn('450 DPI', confirmation_text([job]))
        self.assertIn('Mathematical meaning requires human review', confirmation_text([job]))

    def test_known_xref_wrapper_requires_complete_graph_proof(self):
        data = bytearray(b'%PDF-1.5\n')
        bodies = [b'<< /Type /Catalog /Pages 2 0 R >>',
                  b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                  b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 96] /Resources << >> >>']
        offsets = []
        for index, body in enumerate(bodies, 1):
            offsets.append(len(data))
            data.extend(f'{index} 0 obj\n'.encode()+body+b'\nendobj\n')
        xref = len(data)
        rows = b'\0\0\0\0\0\xff\xff'+b''.join(b'\1'+offset.to_bytes(4, 'big')+b'\0\0' for offset in offsets)
        data.extend(f'4 0 obj\n<< /Type /XRef /Size 5 /Root 1 0 R /W [1 4 2] /Index [0 4] /Length {len(rows)} >>\nstream\n'.encode()+rows+b'\nendstream\n')
        data.extend(f'startxref\n{xref}\n%%EOF\n'.encode())
        self.source.write_bytes(data)
        optimize_bitmap(self.source, self.output, paper='source')
        report = json.loads(self.output.with_suffix('.json').read_text())
        self.assertTrue(report['source_preparation']['preservation']['complete'])
        self.output.unlink(); self.output.with_suffix('.json').unlink(); self.output.with_suffix('.md').unlink()
        with patch('bitmap_study.verify_xref_snapshot', side_effect=ValueError('graph changed')):
            with self.assertRaisesRegex(ValueError, 'graph changed'):
                optimize_bitmap(self.source, self.output, paper='source')
        self.assertFalse(self.output.exists())
        from bitmap_study import prepare_source, verify_xref_snapshot
        from pdf_runtime import RunBudget, qpdf_identity
        from lossless_verify import strict_reader
        budget = RunBudget()
        snapshot, _ = prepare_source(self.source, self.root, qpdf_identity(), budget)
        data = snapshot.read_bytes().replace(b'[0 0 72 96]', b'[0 0 73 96]')
        self.assertNotEqual(data, snapshot.read_bytes())
        snapshot.write_bytes(data)
        with strict_reader(self.source) as reader:
            with self.assertRaisesRegex(ValueError, 'object bytes changed'):
                verify_xref_snapshot(self.source, snapshot, reader, 4, reader._startxref, budget)

    def test_small_marks_survive_letter_300dpi_and_deleted_dot_control_differs(self):
        from PIL import Image
        commands = b'q 1 J 0.8 w 100 100 m 100 100 l S Q\n150 100 m 160 100 l S\n200 100 m 212 100 l S\n250 104 m 253 107 l 256 104 l S\n300 100 m 305 100 l 305 105 l 300 105 l h S\n'
        def make(body):
            with PdfWriter() as writer:
                page = writer.add_blank_page(1404, 1872)
                content = DecodedStreamObject(); content.set_data(body)
                page[NameObject('/Contents')] = writer._add_object(content)
                writer.write(self.source)
        make(commands)
        optimize_bitmap(self.source, self.output)
        with PdfReader(self.output) as reader:
            obj = reader.pages[0]['/Resources']['/XObject']['/StudyImage']
            with Image.frombytes('L', (obj['/Width'], obj['/Height']), obj.get_data()) as image:
                for x in (100, 155, 206, 253, 302):
                    center = (round(x*2475/1404), round((1872-102)*3300/1872))
                    with image.crop((center[0]-10, center[1]-12, center[0]+10, center[1]+12)) as crop:
                        self.assertLess(crop.getextrema()[0], 250)
                reference = obj.get_data()
        make(commands.replace(b'100 100 m 100 100 l S', b''))
        damaged = self.root/'damaged.pdf'
        optimize_bitmap(self.source, damaged)
        with PdfReader(damaged) as reader:
            self.assertNotEqual(reference, reader.pages[0]['/Resources']['/XObject']['/StudyImage'].get_data())

    def test_unrecognized_syntax_warning_is_never_repaired(self):
        from subprocess import CompletedProcess
        self.fixture()
        with patch('bitmap_study._run', return_value=CompletedProcess([], 3, '', 'WARNING: other damage')):
            with self.assertRaisesRegex(ValueError, 'Unresolved source syntax'):
                optimize_bitmap(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_real_batch_child_receives_reviewed_settings(self):
        self.fixture()
        from batch_workflow import BatchController, plan_batch
        job = plan_batch([self.source], profile='bitmap', study_paper='source', study_dpi=450)[0]
        controller = BatchController([job])
        controller.run()
        self.assertEqual(controller.jobs[0].status, 'optimized', controller.jobs[0].error)
        report = json.loads(job.output.with_suffix('.json').read_text())
        self.assertEqual(report['settings'], {'paper': 'source', 'dpi': 450})

    def test_source_race_and_hidden_layers_never_publish(self):
        from bitmap_study import filehash
        self.fixture()
        reads = 0
        def changed(path):
            nonlocal reads
            if Path(path) == self.source:
                reads += 1
                if reads > 1: return '0'*64
            return filehash(path)
        with patch('bitmap_study.filehash', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Original changed'):
                optimize_bitmap(self.source, self.output, paper='source')
        self.assertFalse(self.output.exists())
        with PdfWriter() as writer:
            writer.add_blank_page(72, 96)
            writer._root_object[NameObject('/OCProperties')] = DictionaryObject({
                NameObject('/D'): DictionaryObject({NameObject('/BaseState'): NameObject('/OFF')})})
            writer.write(self.source)
        with self.assertRaisesRegex(ValueError, 'Hidden'):
            preflight(self.source)

    def test_color_is_retained(self):
        self.fixture()
        # The replacement has equal byte length, retaining the generated source index.
        self.source.write_bytes(self.source.read_bytes().replace(b'0 0 0 RG', b'0 0 1 RG'))
        optimize_bitmap(self.source, self.output, paper='source')
        report = json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['pages'][0]['color'], 'RGB')
        from PIL import Image
        with PdfReader(self.output) as reader:
            obj = reader.pages[0]['/Resources']['/XObject']['/StudyImage']
            with Image.frombytes('RGB', (obj['/Width'], obj['/Height']), obj.get_data()) as image:
                self.assertTrue(any(b > r+20 for r, g, b in image.get_flattened_data()))

    def test_indirect_visible_layer_list_is_supported(self):
        from pypdf.generic import ArrayObject, TextStringObject
        with PdfWriter() as writer:
            writer.add_blank_page(72, 96)
            group = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/OCG'),
                NameObject('/Name'): TextStringObject('Visible'), NameObject('/Usage'): DictionaryObject({
                    NameObject('/CreatorInfo'): DictionaryObject({NameObject('/Creator'): TextStringObject('Generated')})})}))
            writer._root_object[NameObject('/OCProperties')] = DictionaryObject({
                NameObject('/D'): DictionaryObject(), NameObject('/OCGs'): writer._add_object(ArrayObject([group]))})
            writer.write(self.source)
        self.assertEqual(preflight(self.source)['flattened_layers'], 1)
        optimize_bitmap(self.source, self.output, paper='source')

    def test_inherited_crop_and_rotation_do_not_clip_output(self):
        from pypdf.generic import ArrayObject, NumberObject
        with PdfWriter() as writer:
            writer.add_blank_page(72, 96)
            parent = writer._pages.get_object()
            parent[NameObject('/CropBox')] = ArrayObject([NumberObject(n) for n in (0, 0, 36, 96)])
            parent[NameObject('/Rotate')] = NumberObject(90)
            writer.write(self.source)
        optimize_bitmap(self.source, self.output, paper='source')
        with PdfReader(self.output) as reader:
            self.assertEqual(list(reader.pages[0].mediabox), [0, 0, 96, 36])
            self.assertEqual(reader.pages[0].cropbox, reader.pages[0].mediabox)
            self.assertEqual(reader.pages[0].rotation, 0)

    def test_original_resource_cycles_are_removed(self):
        from navigation import _current_objects
        with PdfWriter() as writer:
            page = writer.add_blank_page(72, 96)
            form = DecodedStreamObject(); form.set_data(b'')
            form[NameObject('/Subtype')] = NameObject('/Form')
            ref = writer._add_object(form)
            resources = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Cycle'): ref})})
            form[NameObject('/Resources')] = resources
            page[NameObject('/Resources')] = resources
            writer.write(self.source)
        optimize_bitmap(self.source, self.output, paper='source')
        with PdfReader(self.output) as reader:
            self.assertFalse(any(isinstance(obj, dict) and obj.get('/Subtype') == '/Form' for obj in _current_objects(reader)))


if __name__ == '__main__':
    unittest.main()

