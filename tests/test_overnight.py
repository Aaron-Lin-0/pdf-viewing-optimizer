from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from handwriting import HEADER
from lossless_verify import verify_lossless
from navigation import reader_for
from pdf_runtime import RunBudget, TimeBudgetExceeded, filehash
from test_handwriting import make_boox, make_crossing_masked_fixture
from tools.approximate_paths import make_candidate, paths, reduce_points, verify_subsequence, verify_chains


class ApproximatePathTests(unittest.TestCase):
    def test_joined_segments_reduce_real_saved_path_and_preserve_zero_control(self):
        for tolerance in (0,.025):
            with self.subTest(tolerance=tolerance),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                body=(b'1 w 10 10 m 20 10.001 l S\n1 w 20 10.001 m 30 10.002 l S\n'
                      b'1 w 30 10.002 m 40 10 l S\n1 w 50 50 m 50 50 l S\n')
                source=self.fixture(root,body)
                from pdf_runtime import build_qpdf_candidate
                packed=root/'packed.pdf'
                build_qpdf_candidate(source,packed,strict=True)
                result=make_candidate(packed,root/'candidate.pdf',root,tolerance,'joined-rdp')
                self.assertTrue(result['restoration_proof']['complete'])
                edit=result['edits'][0]
                self.assertEqual(edit['paint_calls_before'],4)
                self.assertEqual(edit['paint_calls_after'],2)
                self.assertEqual(edit['after_points'],6 if tolerance==0 else 4)
                with reader_for(root/'candidate.pdf') as reader:
                    data=reader.pages[0]['/Resources']['/XObject']['/FFT0'].get_data()
                    self.assertIn(b'50 50 m 50 50 l S',data)
                self.assertFalse(list(root.glob('path-proof-*')))

    def test_joining_preserves_width_changes_gaps_and_dots(self):
        from tools.approximate_paths import chain_runs
        data=(b'1 w 0 0 m 1 0 l S\n1 w 1 0 m 2 0 l S\n'
              b'2 w 2 0 m 3 0 l S\n2 w 4 0 m 5 0 l S\n'
              b'2 w 5 0 m 5 0 l S\n2 w 5 0 m 6 0 l S\n')
        runs=paths(HEADER+data)
        joined=chain_runs(runs)
        self.assertEqual([(r['first'],r['last']) for r in joined],[(0,2),(2,3),(3,4),(4,5),(5,6)])
        spelled_dot=paths(HEADER+b'1 w 0 0 m 1 0 l S\n1 w 1 0 m 1.0 0.0 l S\n1 w 1.0 0.0 m 2 0 l S\n')
        self.assertEqual(len(chain_runs(spelled_dot)),3)
        unsafe=paths(HEADER+b'1 w 0 0 m 6 0 l S\n')
        with self.assertRaisesRegex(ValueError,'width change'):
            verify_chains(runs,unsafe,[(0,len(runs))],[1])
        with self.assertRaisesRegex(ValueError,'omitted'):
            verify_chains(runs,paths(HEADER+b'1 w 0 0 m 2 0 l S\n'),[(0,2)],[1])

    def test_repeated_loop_vertices_are_not_smoothed_away(self):
        points=[(0,0),(1,0),(2,0),(2,1),(1,1),(1,0),(3,0)]
        self.assertEqual(reduce_points(points,10,'rdp'),list(range(len(points))))

    def test_exact_decimal_clone_does_not_round_untouched_metadata(self):
        from lossless_verify import strict_parsing, strict_reader
        from test_quick_lossless import write_raw
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.pdf';output=root/'copy.pdf'
            number=b'0.12345678901234567890123456789'
            write_raw(source,number)
            with strict_parsing(),strict_reader(source) as reader,closing(PdfWriter(reader,full=True)) as writer:
                with output.open('xb') as stream:writer.write(stream)
            verify_lossless(source,output)
            self.assertIn(number,output.read_bytes())

    def test_storage_neutral_staging_keeps_logical_orphan_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root,b'1 w 10 10 m 20 10 l S\n')
            with reader_for(source) as reader,closing(PdfWriter(reader,full=True)) as writer:
                orphan=DecodedStreamObject();orphan.set_data(b'private orphan payload')
                writer._add_object(orphan)
                modified=root/'with-orphan.pdf'
                with modified.open('xb') as stream:writer.write(stream)
            result=make_candidate(modified,root/'candidate.pdf',root,0,'joined-rdp')
            self.assertTrue(result['restoration_proof']['complete'])
            verify_lossless(modified,root/'candidate.pdf')

    def test_joined_reconstruction_still_skips_inherited_transparency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'masked.pdf';make_crossing_masked_fixture(source)
            result=make_candidate(source,root/'candidate.pdf',root,.05,'joined-rdp')
            self.assertFalse(result['edits'])
            verify_lossless(source,root/'candidate.pdf')

    def fixture(self, root, data, transform=b''):
        seed, source = root/'seed.pdf', root/'source.pdf'
        make_boox(seed)
        with reader_for(seed) as reader, closing(PdfWriter(clone_from=reader)) as writer:
            form = writer.pages[0]['/Resources']['/XObject']['/FFT0']
            form.set_data(HEADER + data)
            content = DecodedStreamObject()
            content.set_data(b'q ' + transform + b'/FFT0 Do Q\n')
            writer.pages[0][NameObject('/Contents')] = writer._add_object(content)
            writer.add_attachment('control.bin', b'non-target attachment')
            writer.add_outline_item('Preserved target', 0)
            with source.open('xb') as stream:
                writer.write(stream)
        return source

    def test_noop_saved_pdf_and_tiny_marks_remain_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.fixture(root, b'1 w 10 10 m 20 10 l 30 10 l 40 10 l S\n'
                                  b'1 w 50 50 m 50 50 l S\n')
            digest = filehash(source)
            output = root/'zero.pdf'
            result = make_candidate(source, output, root, 0, 'rdp')
            self.assertFalse(result['edits'])
            verify_lossless(source, output)
            from PIL import ImageChops
            from viewing_copy import render
            with render(source, 0) as a, render(output, 0) as b:
                self.assertIsNone(ImageChops.difference(a, b).getbbox())
            self.assertEqual(filehash(source), digest)

    def test_actual_saved_pdf_reduction_and_independent_bound(self):
        for algorithm in ('rdp', 'midpoint'):
            with self.subTest(algorithm=algorithm), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = self.fixture(root, b'1 w 10 10 m 20 10.001 l 30 10.002 l 40 10 l S\n'
                                      b'1 w 50 50 m 50 50 l S\n')
                result = make_candidate(source, root/'candidate.pdf', root, .025, algorithm)
                self.assertEqual(len(result['edits']), 1)
                edit = result['edits'][0]
                self.assertEqual((edit['before_points'], edit['after_points']), (6, 4))
                self.assertLessEqual(max(edit['verified_max_deviation_local']), .1)
                with reader_for(root/'candidate.pdf') as candidate:
                    data = candidate.pages[0]['/Resources']['/XObject']['/FFT0'].get_data()
                    self.assertIn(b'50 50 m 50 50 l S', data)
                    self.assertNotIn(b'20 10.001 l', data)

    def test_inherited_mask_and_shear_do_not_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.pdf'
            make_crossing_masked_fixture(source)
            result = make_candidate(source, root/'candidate.pdf', root, .05, 'rdp')
            self.assertFalse(result['edits'])
            verify_lossless(source, root/'candidate.pdf')
        for transform in (b'1 0 1 1 0 0 cm ', b'2 0 0 1 0 0 cm ', b'[2 2] 0 d '):
            with self.subTest(transform=transform), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = self.fixture(root, b'1 w 10 10 m 20 10 l 30 10 l 40 10 l S\n', transform)
                result = make_candidate(source, root/'candidate.pdf', root, .05, 'rdp')
                self.assertFalse(result['edits'])

    def test_uniform_transform_scales_physical_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.fixture(root, b'1 w 10 10 m 20 10.08 l 30 10.08 l 40 10 l S\n',
                                  b'10 0 0 10 0 0 cm ')
            result = make_candidate(source, root/'candidate.pdf', root, .025, 'rdp')
            self.assertFalse(result['edits'])

    def test_loops_cusps_and_separate_strokes_are_protected(self):
        points = [(0,0),(1,0),(1,1),(0,0)]
        self.assertEqual(reduce_points(points, 1, 'rdp'), [0,1,2,3])
        points = [(0,0),(1,0),(1,1),(2,1)]
        self.assertEqual(reduce_points(points, 1, 'rdp'), [0,1,2,3])
        for data in (b'1 w 1 1 m 2 2 l S\n1 w 2 2 m 3 3 l S\n', b'1 w 1 1 m 2 2 l h S\n'):
            parsed = paths(HEADER + data)
            self.assertTrue(parsed is None or all(len(run['points']) == 2 for run in parsed))

    def test_independent_checker_rejects_wrong_saved_path(self):
        before = paths(HEADER + b'1 w 0 0 m 1 0.2 l 2 0 l 3 0 l S\n')[0]
        after = paths(HEADER + b'1 w 0 0 m 3 0 l S\n')[0]
        with self.assertRaisesRegex(ValueError, 'bound'):
            verify_subsequence(before, after, .1)
        changed = paths(HEADER + b'2 w 0 0 m 3 0 l S\n')[0]
        with self.assertRaisesRegex(ValueError, 'width'):
            verify_subsequence(before, changed, 1)

    def test_bad_reducer_is_rejected_after_saving_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.fixture(root, b'1 w 10 10 m 20 11 l 30 11 l 40 10 l S\n')
            with patch('tools.approximate_paths.reduce_points', return_value=[0,3]):
                with self.assertRaisesRegex(ValueError, 'bound'):
                    make_candidate(source, root/'candidate.pdf', root, .01, 'rdp')

    def test_rejects_unsupported_and_nonfinite_grammar(self):
        for body in (b'0 w 0 0 m 1 1 l S', b'1 w 0 0 m 1 1 c S', b'1 w 0 0 m 1 1 l S %comment'):
            self.assertIsNone(paths(HEADER + body))

    def test_output_collision_keeps_existing_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.fixture(root, b'1 w 10 10 m 20 10 l S\n')
            output = root/'candidate.pdf'
            output.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                make_candidate(source, output, root, .01, 'rdp')
            self.assertEqual(output.read_bytes(), b'keep')


class OvernightLifecycleTests(unittest.TestCase):
    def test_required_lossy_candidate_blocks_all_viewing_measurements(self):
        from tools.benchmark_overnight import document_run,hypothesis
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)/'source.pdf';make_boox(source)
            tasks=MagicMock()
            inventory={'sample_pages':{'selected_samples':[1]},'source':{'page_count':1},
                       'opportunity_report':{'first_supported_subset':{'supported':None}}}
            def run(request,*args):
                if request['action']=='inventory':return inventory
                if request['action']=='generate':return {'error':'preflight failed'}
                raise AssertionError('Viewing work started despite failed required lossy preflight')
            tasks.run.side_effect=run
            name='joined-rdp-0.025'
            settings={'recipes':[name],'required_recipes':[name],'optimizer_repeats':1,'repeats':5,
                      'page_limit':3,'firefox':None,'hypotheses':{name:hypothesis(name)}}
            state={'inputs':[{'original':str(source),'snapshot':str(source)}],'documents':[]}
            document_run(source,tasks,settings,state)
            self.assertEqual(state['documents'][0]['preflight_blocked'],[name])
            self.assertFalse(any(c['timings'] for c in state['documents'][0]['candidates']))

    def test_trial_hypotheses_and_balanced_order(self):
        from tools.benchmark_overnight import RECIPES, hypothesis, ordered
        for recipe in RECIPES:
            self.assertEqual(set(hypothesis(recipe)), {'baseline', 'expect', 'falsifier'})
        rows = [1,2,3,4]
        self.assertEqual(ordered(rows, 0), rows)
        self.assertEqual(ordered(rows, 1), [1,4,3,2])
        self.assertEqual(rows, [1,2,3,4])

    def test_completed_tasks_revalidate_pdf_and_reuse_without_launch(self):
        from tools.benchmark_overnight import Tasks, save_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = Tasks(root, RunBudget(60), lambda _: None)
            def execute(command, folder, name, timeout):
                (folder/'candidate.pdf').write_bytes(b'pdf evidence')
                save_json(folder/'result.json', {'status': 'generated'})
                return {'status': 'completed', 'exit_code': 0}
            with patch('tools.benchmark_overnight.run_process', side_effect=execute) as launch:
                self.assertEqual(task.run({'action': 'test'})['status'], 'generated')
                self.assertEqual(task.run({'action': 'test'})['status'], 'generated')
                self.assertEqual(launch.call_count, 1)
                next(root.rglob('candidate.pdf')).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    task.run({'action': 'test'})

    def test_failed_task_keeps_logs_and_next_trial_runs(self):
        from tools.benchmark_overnight import Tasks, save_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = Tasks(root, RunBudget(60), lambda _: None)
            def execute(command, folder, name, timeout):
                request = json.loads((folder/'request.json').read_text())
                if request['action'] == 'bad':
                    return {'status': 'timeout'}
                save_json(folder/'result.json', {'ok': True})
                return {'status': 'completed', 'exit_code': 0}
            with patch('tools.benchmark_overnight.run_process', side_effect=execute):
                self.assertIn('error', task.run({'action': 'bad'}))
                self.assertTrue(task.run({'action': 'good'})['ok'])
                (root/'STOP').touch()
                with self.assertRaises(TimeBudgetExceeded):
                    task.run({'action': 'later'})


class BooxPreprocessingTests(unittest.TestCase):
    def incremental(self, root, producer='NeoPdf', *, seed=None):
        import io
        import re
        from pypdf.generic import DictionaryObject, TextStringObject
        from test_quick_lossless import rich_fixture
        source = root/'incremental.pdf'
        if seed is None:
            seed = root/'seed.pdf'
            rich_fixture(seed)
        data = seed.read_bytes()
        previous = int(re.search(rb'startxref\s+(\d+)\s+%%EOF\s*$', data)[1])
        with reader_for(seed) as reader:
            number = reader.trailer.raw_get('/Info').idnum
            catalog = reader.trailer.raw_get('/Root').idnum
            size = reader.trailer['/Size']
            info = DictionaryObject(reader.trailer['/Info'])
            info[NameObject('/Producer')] = TextStringObject(producer)
            body = io.BytesIO()
            info.write_to_stream(body)
        offset = len(data)
        data += f'{number} 0 obj\n'.encode() + body.getvalue() + b'\nendobj\n'
        xref = len(data)
        data += (f'xref\n{number} 1\n{offset:010d} 00000 n \ntrailer\n'
                 f'<< /Size {size} /Root {catalog} 0 R /Info {number} 0 R /Prev {previous} >>\n'
                 f'startxref\n{xref}\n%%EOF\n').encode()
        with source.open('xb') as stream:
            stream.write(data)
        return source

    def test_actual_incremental_pdf_is_verified_without_changing_default_policy(self):
        from tools.normalize_boox import normalize
        from lossless_verify import validate_quick_input, UnsupportedPDF
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.incremental(root)
            digest = filehash(source)
            with self.assertRaises(UnsupportedPDF):
                validate_quick_input(source)
            result = normalize(source, root/'normalized.pdf')
            self.assertTrue(result['preservation']['complete'])
            from tools.benchmark_overnight import normalized_inventory
            inventory = normalized_inventory(result)
            self.assertEqual(inventory['source']['page_count'], 1)
            self.assertEqual(inventory['sample_pages']['selected_samples'], [1])
            self.assertIsNone(inventory['opportunity_report']['first_supported_subset']['supported'])
            self.assertEqual(validate_quick_input(root/'normalized.pdf'), 1)
            self.assertEqual(filehash(source), digest)
            self.assertIn('not retained', result['history'])

    def test_other_producer_is_rejected_before_copy(self):
        from tools.normalize_boox import normalize
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.incremental(root, 'Different exporter')
            with self.assertRaisesRegex(ValueError, 'restricted'):
                normalize(source, root/'normalized.pdf')
            self.assertFalse((root/'normalized.pdf').exists())

    def test_signature_and_changed_content_fail_closed(self):
        from tools.normalize_boox import normalize
        from pypdf.generic import DictionaryObject, TextStringObject
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.incremental(root)
            def changed(_source, destination, **kwargs):
                with reader_for(_source) as reader, closing(PdfWriter(clone_from=reader)) as writer:
                    writer._root_object[NameObject('/UnexpectedChange')] = TextStringObject('damage')
                    with destination.open('xb') as stream:
                        writer.write(stream)
            with patch('tools.normalize_boox.build_qpdf_candidate', side_effect=changed):
                with self.assertRaises(ValueError):
                    normalize(source, root/'mutated.pdf')
            with reader_for(source) as reader, closing(PdfWriter(clone_from=reader)) as writer:
                writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Sig')}))
                signed = root/'signed.pdf'
                with signed.open('xb') as stream:
                    writer.write(stream)
            with self.assertRaisesRegex(ValueError, 'signature'):
                normalize(signed, root/'refused.pdf')
            self.assertFalse((root/'refused.pdf').exists())


if __name__ == '__main__':
    unittest.main()
