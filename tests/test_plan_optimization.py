import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from plan_optimization import build_plan


def characterization(partial=False, transparency=0):
    return {'schema': 2, 'source_sha256': 'a' * 64, 'bytes': 10_000_000, 'page_count': 2,
            'coverage': {'pages_total': 2, 'pages_complete': 1 if partial else 2,
                         'pages_partial': 1 if partial else 0, 'text': 'complete',
                         'navigation': 'complete', 'document_features': 'complete'},
            'document': {'images': {'calls': 3}, 'text': {'pages_with_text': 2},
                         'forms': {'present': False, 'xfa': False}, 'layers': {'present': True},
                         'attachments': [], 'links': {'count': 1},
                         'structural_tags': {'present': False}},
            'pages': [
                {'content': {'form_calls': 4, 'expanded_operations': 100,
                             'same_width_adjacent_segments': 5,
                             'transparency_state_uses': transparency, 'transparency_groups': 0},
                 'annotations': {'eligible_leading_prefix': 0}},
                {'content': {'form_calls': 2, 'expanded_operations': 50,
                             'same_width_adjacent_segments': 3,
                             'transparency_state_uses': 0, 'transparency_groups': 0},
                 'annotations': {'eligible_leading_prefix': 1}},
            ]}


class PlannerTests(unittest.TestCase):
    def test_bounded_explained_plan_and_experimental_guard(self):
        config = {'tools': {'qpdf': {'command': [sys.executable], 'version': 'fixture',
                                     'version_args': ['--version'], 'help_args': ['--help']}},
                  'imports': []}
        plan = build_plan(characterization(), config, allow_experimental=True)
        self.assertEqual(plan['selected'], ['repository/viewing-copy', 'qpdf/flate',
                                            'repository/fast-handwriting'])
        self.assertLessEqual(sum(item['decision'] == 'run' and item['lane'] == 'conservative'
                                 for item in plan['candidates']), 2)
        self.assertLessEqual(sum(item['decision'] == 'run' and item['lane'] == 'experimental'
                                 for item in plan['candidates']), 1)
        self.assertTrue(all(item['expected_storage_effect']['estimate'] == 'uncalibrated'
                            for item in plan['candidates']))
        repository = next(item for item in plan['candidates'] if item['id'] == 'repository/viewing-copy')
        self.assertTrue(repository['provenance']['prefix_hashes'])
        qpdf = next(item for item in plan['candidates'] if item['id'] == 'qpdf/flate')
        self.assertEqual(qpdf['provenance']['declared_version'], 'fixture')
        for item in plan['candidates']:
            self.assertIn(item['decision'], {'run', 'skip', 'unavailable'})
            self.assertTrue(item['reason'])
            self.assertIn('preservation_risks', item)
            self.assertIn('prerequisites', item)
        mupdf = next(item for item in plan['candidates'] if item['id'] == 'mupdf/compress-packed')
        self.assertEqual(mupdf['decision'], 'skip')
        boox = next(item for item in plan['candidates'] if item['id'] == 'boox-pdf/vector-6')
        self.assertNotEqual(boox['decision'], 'run')

    def test_partial_characterization_and_transparency_block_candidates(self):
        plan = build_plan(characterization(partial=True, transparency=1), {'tools': {}, 'imports': []},
                          allow_experimental=True)
        self.assertEqual(plan['selected'], [])
        self.assertFalse(any(item['decision'] == 'run' for item in plan['candidates']))
        self.assertFalse(any('_applicable' in item for item in plan['candidates']))
        fast = next(item for item in plan['candidates'] if item['id'] == 'repository/fast-handwriting')
        self.assertEqual(fast['decision'], 'skip')
        self.assertIn('partial', fast['reason'].lower())
        qpdf = next(item for item in plan['candidates'] if item['id'] == 'qpdf/flate')
        self.assertEqual(qpdf['decision'], 'skip')

        complete = build_plan(characterization(transparency=1), {'tools': {}, 'imports': []})
        qpdf = next(item for item in complete['candidates'] if item['id'] == 'qpdf/flate')
        self.assertEqual(qpdf['decision'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
