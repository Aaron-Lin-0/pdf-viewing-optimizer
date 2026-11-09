"""Source-only browser scheduling and exact trace comparison."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from browser_check import _TRACE_KEYS, check_browser, probe_browser


def trace():
    return {'ok':True, **{key:[] for key in _TRACE_KEYS}}


class BrowserProbeTests(unittest.TestCase):
    def test_source_probe_profile_does_not_require_writing_beside_source(self):
        with patch.dict('os.environ',{'PDF_BROWSER':sys.executable}), \
             patch('browser_check.profile_directory',side_effect=RuntimeError('profile boundary')) as profile:
            with self.assertRaisesRegex(RuntimeError,'profile boundary'):
                probe_browser(Path('read-only-originals')/'source.pdf',[1],30)
        profile.assert_called_once_with(Path(tempfile.gettempdir()))

    def test_probe_runs_only_source(self):
        source=trace()
        with patch('browser_check._browser_results',return_value={'source':source}) as run:
            self.assertIs(probe_browser('a.pdf',[1],30),source)
        run.assert_called_once_with('a.pdf','a.pdf',[1],30,('source',))

    def test_reused_source_runs_only_candidate_and_still_compares_every_trace(self):
        source=trace()
        for key in _TRACE_KEYS:
            candidate=trace()
            candidate[key]=['changed']
            with self.subTest(key=key), patch('browser_check._browser_results',return_value={'candidate':candidate}) as run:
                with self.assertRaisesRegex(ValueError,key):
                    check_browser('a.pdf','b.pdf',[1],30,source_result=source)
                run.assert_called_once_with('a.pdf','b.pdf',[1],30,('candidate',))
        with patch('browser_check._browser_results',return_value={'candidate':trace()}):
            result=check_browser('a.pdf','b.pdf',[1],30,source_result=source)
            self.assertIs(result['results']['source'],source)

    def test_default_still_runs_both_and_failed_source_is_rejected(self):
        with patch('browser_check._browser_results',return_value={'source':trace(),'candidate':trace()}) as run:
            check_browser('a.pdf','b.pdf',[1],30)
        run.assert_called_once_with('a.pdf','b.pdf',[1],30,('source','candidate'))
        with patch('browser_check._browser_results',return_value={'candidate':trace()}):
            with self.assertRaisesRegex(ValueError,'incomplete or failed'):
                check_browser('a.pdf','b.pdf',source_result={'ok':False})
