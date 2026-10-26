import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from qualify_planner import assess_viewing_decision, execute


class QualificationTests(unittest.TestCase):
    def test_missing_timings_leave_alternatives_unassessed(self):
        decision = assess_viewing_decision({'objective': 'viewing'}, {}, set(), [{'id': 'qpdf/flate'}])
        self.assertEqual(decision['status'], 'unassessed')
        self.assertEqual(decision['missed_alternatives'][0]['classification'], 'unassessed')

    def test_records_misses_and_false_safe_recommendations(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            source_hash = 'a'*64
            plan = {'schema': 1, 'objective': 'viewing', 'source': {'sha256': source_hash},
                    'selected': ['qpdf/flate'],
                    'candidates': [{'id': 'qpdf/flate', 'reason': 'selected'},
                                   {'id': 'qpdf/flate-linearized', 'reason': 'network objective not requested'}]}
            outputs = [
                {'tool': 'source', 'profile': 'original', 'status': 'usable',
                 'recommendation': 'Strict preservation candidate', 'bytes': 100},
                {'tool': 'qpdf', 'profile': 'flate', 'status': 'usable',
                 'recommendation': 'Strict preservation candidate', 'bytes': 80},
                {'tool': 'qpdf', 'profile': 'flate-linearized', 'status': 'usable',
                 'recommendation': 'Strict preservation candidate', 'bytes': 70},
                {'tool': 'boox-pdf', 'profile': 'vector-6', 'status': 'usable',
                 'recommendation': 'Feature-loss diagnostic', 'bytes': 20},
            ]
            for row, median in zip(outputs, (1000, 800, 600, 200)):
                row['firefox_summary'] = {'first_visit/p1/None': {
                    'resolved': True, 'median_ms': median}}
            results = {'status': 'complete', 'documents': [{'source_sha256': source_hash,
                'pages': 2, 'outputs': outputs,
                'nondominated': {'first_visit/p1/None': {'0': ['qpdf/flate-linearized']}}}]}
            for name in ('generated', 'authorized'):
                (root/f'{name}-plan.json').write_text(json.dumps(plan))
                (root/f'{name}-results.json').write_text(json.dumps(results))
            review_plan = dict(plan, selected=['repository/viewing-copy', 'qpdf/flate'])
            review = {'status': 'complete', 'source': {'sha256': source_hash, 'bytes': 100},
                      'page_count': 2, 'timings': {
                          'source/original': {'first_visit': {'complete_repetitions': 3,
                              'required_repetitions': 3, 'median_total_ms': 1000}},
                          'repository/inline': {'first_visit': {'complete_repetitions': 3,
                              'required_repetitions': 3, 'median_total_ms': 700}},
                          'qpdf/flate': {'first_visit': {'complete_repetitions': 3,
                              'required_repetitions': 3, 'median_total_ms': 800}}},
                      'candidates': {
                          'repository/inline': {'artifact': {'bytes': 70},
                              'functions': {'text': {'status': 'pass'}},
                              'appearance': {'all_pages_144_complete': True, 'exact_pages_144': 2,
                                  'detail_576': {'metrics': {'status': 'pass', 'exact': True}}}},
                          'qpdf/flate': {'artifact': {'bytes': 80},
                              'functions': {'text': {'status': 'pass'}},
                              'appearance': {'all_pages_144_complete': True, 'exact_pages_144': 2,
                                  'detail_576': {'metrics': {'status': 'pass', 'exact': True}}}}}}
            (root/'review-plan.json').write_text(json.dumps(review_plan))
            (root/'review.json').write_text(json.dumps(review))
            manifest = {'cases': [
                {'name': 'generated', 'kind': 'generated', 'plan': 'generated-plan.json', 'results': 'generated-results.json'},
                {'name': 'authorized', 'kind': 'authorized', 'plan': 'authorized-plan.json', 'results': 'authorized-results.json'},
                {'name': 'targeted', 'kind': 'authorized', 'plan': 'review-plan.json', 'review': 'review.json'}]}
            path = root/'manifest.json'; path.write_text(json.dumps(manifest))

            qualification = execute(path, root/'qualification')

            self.assertEqual(qualification['status'], 'complete-with-findings')
            self.assertFalse(qualification['false_safe_recommendations'])
            self.assertEqual({item['id'] for item in qualification['missed_useful_candidates']},
                             {'qpdf/flate-linearized'})
            self.assertEqual(qualification['cases'][0]['exhaustive_feature_loss_diagnostics'],
                             ['boox-pdf/vector-6'])
            self.assertEqual(qualification['cases'][2]['status'], 'complete')
            self.assertEqual(qualification['cases'][2]['selected_verified'],
                             ['repository/viewing-copy', 'qpdf/flate'])
            self.assertEqual(qualification['cases'][2]['decision_quality']['status'], 'complete')
            self.assertEqual(qualification['decision_quality']['status'], 'complete-with-regret')
            self.assertEqual(qualification['decision_quality']['material_regret_operations'],
                             ['first_visit/p1/None', 'first_visit/p1/None'])
            with self.assertRaisesRegex(FileExistsError, 'exist'):
                execute(path, root/'qualification')

            unsafe = dict(plan, selected=['boox-pdf/vector-6'])
            (root/'authorized-plan.json').write_text(json.dumps(unsafe))
            failed = execute(path, root/'failed')
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(failed['false_safe_recommendations'], ['boox-pdf/vector-6'])


if __name__ == '__main__':
    unittest.main()
