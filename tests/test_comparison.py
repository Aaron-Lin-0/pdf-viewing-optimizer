"""Generated calibration and safety checks for the separate comparison tools."""
from argparse import Namespace as argparse_namespace
import importlib.util
import json
from pathlib import Path
import subprocess
from shutil import which as shutil_which
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import compare_optimizers as comparison
import firefox_measure
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from reportlab.pdfgen import canvas

HAS_METRICS = all(importlib.util.find_spec(x) for x in ('numpy', 'scipy', 'skimage', 'matplotlib'))


def fixture(path, text='Math α + β = 3.14', pages=1, attachment=True, links=True):
    raw = path.with_suffix('.raw.pdf')
    c = canvas.Canvas(str(raw), pagesize=(144, 144))
    for i in range(pages):
        c.drawString(8, 120, text)
        c.setLineWidth(2)
        c.circle(60, 60, 12)
        c.line(20, 20, 90, 30)
        c.circle(100, 30, .5, fill=1)
        c.showPage()
    c.save()
    w = PdfWriter(clone_from=raw)
    w.add_outline_item('Page one', 0)
    if attachment:
        w.add_attachment('calibration.txt', b'attachment bytes')
    if links:
        w.add_annotation(0, Link(rect=(1, 1, 20, 20), url='https://example.org/'))
    with path.open('xb') as stream:
        w.write(stream)


@unittest.skipUnless(importlib.util.find_spec('psutil'), 'Install requirements-comparison.txt for execution tests')
class ComparisonSafetyTests(unittest.TestCase):
    def test_render_cache_hit_and_eviction_rerender(self):
        from evidence_cache import EvidenceCache
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder); source = root/'source.pdf'
            writer = PdfWriter(); writer.add_blank_page(width=20, height=20); writer.write(source)
            cache = EvidenceCache(root/'cache')
            context = {'operation': 'render', 'source_sha256': 'a'*64,
                       'candidate_sha256': 'a'*64, 'page': 1, 'crop': None,
                       'renderer': 'pdfium', 'renderer_version': 'fixture',
                       'geometry': [0, 0, 20, 20], 'resolution': {'dpi': 144},
                       'background': 'white', 'thresholds': [200, 225, 250],
                       'metric_version': 'fixture'}
            first = root/'first'; first.mkdir()
            with comparison.rendered(source, 1, 2, first, 'page', cache=cache,
                                     cache_context=context) as image:
                first_pixels = image.tobytes()
            second = root/'second'; second.mkdir()
            with patch.object(comparison, 'run_process', side_effect=AssertionError('rerendered')):
                with comparison.rendered(source, 1, 2, second, 'page', cache=cache,
                                         cache_context=context) as image:
                    self.assertEqual(image.tobytes(), first_pixels)
            payload = next((root/'cache'/cache.key(context)).glob('payload.*'))
            payload.unlink()
            third = root/'third'; third.mkdir()
            with patch.object(comparison, 'run_process', side_effect=AssertionError('rerendered')), \
                 self.assertRaisesRegex(AssertionError, 'rerendered'):
                comparison.rendered(source, 1, 2, third, 'page', cache=cache,
                                    cache_context=context)
            self.assertGreaterEqual(cache.stats['hits'], 1)
            self.assertGreaterEqual(cache.stats['invalid'], 1)

    def test_firefox_cache_requires_exact_candidate_and_order(self):
        from evidence_cache import EvidenceCache
        metadata = {'firefox': '1', 'geckodriver': '2', 'pdfjs': '3',
                    'userAgent': 'fixture', 'viewport': [100, 100], 'devicePixelRatio': 1,
                    'rendering_preferences': {'device_scale': 1}}
        base = comparison.firefox_cache_context('a'*64, 'b'*64, 2, {1, 2}, 1,
                                                ['source/original', 'qpdf/flate'], 0, metadata)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            cache = EvidenceCache(folder)
            measurement = {'observations': [{'ms': 0, 'resolved': False}]}
            cache.store_json(base, measurement)
            self.assertEqual(cache.load_json(base), measurement)
            changed = dict(base, candidate_sha256='c'*64)
            self.assertIsNone(cache.load_json(changed))
            reordered = dict(base, order=list(reversed(base['order'])))
            self.assertIsNone(cache.load_json(reordered))
            self.assertFalse(cache.load_json(base)['observations'][0]['resolved'])

    def test_command_arrays_only(self):
        for value in ('python x.py', [], ['cmd.exe', '/c'], ['tool.cmd'], ['node', 'tool.sh']):
            with self.assertRaises(ValueError):
                comparison.command_prefix(value)
        self.assertEqual(comparison.command_prefix([sys.executable, 'tool.py']), [sys.executable, 'tool.py'])

    def test_import_hash_required_and_duplicates_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'config.json'
            p.write_text(json.dumps({'imports': [{'tool': 'acrobat'}]}))
            with self.assertRaises(ValueError):
                comparison.load_config(p)
            entry = dict(tool='acrobat', profile='conservative', source_sha256='0'*64,
                         path='output.pdf', settings='No downsampling', version='local licensed installation')
            p.write_text(json.dumps({'imports': [entry, entry]}))
            with self.assertRaises(ValueError):
                comparison.load_config(p)

    def test_source_checkout_version_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'config.json'
            spec = {'command': [sys.executable, 'pdfsimpler.py'], 'version': 'pinned-commit',
                    'version_command': ['git', '-C', 'checkout', 'rev-parse', 'HEAD'], 'help_args': ['--help']}
            path.write_text(json.dumps({'tools': {'boox-pdf': spec}}))
            self.assertEqual(comparison.load_config(path)['tools']['boox-pdf'], spec)
            spec['version_args'] = ['--version']
            path.write_text(json.dumps({'tools': {'boox-pdf': spec}}))
            with self.assertRaises(ValueError):
                comparison.load_config(path)
            del spec['version_args']
            spec['version_command'] = ['cmd.exe', '/c', 'echo pinned-commit']
            path.write_text(json.dumps({'tools': {'boox-pdf': spec}}))
            with self.assertRaises(ValueError):
                comparison.load_config(path)

    def test_process_success_failure_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = comparison.run_process([sys.executable, '-c', 'print(123)'], root, 'good')
            self.assertEqual(good['exit_code'], 0)
            bad = comparison.run_process([sys.executable, '-c', 'raise SystemExit(4)'], root, 'bad')
            self.assertEqual(bad['exit_code'], 4)
            slow = comparison.run_process([sys.executable, '-c', 'import time;time.sleep(60)'], root, 'slow', .2)
            self.assertEqual(slow['status'], 'timeout')
            self.assertTrue((root/'slow.json').exists())
            with self.assertRaises(FileExistsError):
                comparison.save_json(root/'slow.json', {})

    def test_interrupted_record(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(comparison.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                comparison.run_process([sys.executable, '-c', 'import time;time.sleep(60)'], Path(tmp), 'interrupt')
            self.assertEqual(json.loads((Path(tmp)/'interrupt.json').read_text())['status'], 'interrupted')

    def test_timeout_cleans_owned_descendant(self):
        import psutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root/'parent.py'
            script.write_text("import pathlib,subprocess,sys,time\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\npathlib.Path('child.pid').write_text(str(p.pid))\ntime.sleep(60)\n")
            record = comparison.run_process([sys.executable, str(script)], root, 'tree', 2)
            self.assertEqual(record['status'], 'timeout')
            pid = int((root/'child.pid').read_text())
            self.assertFalse(psutil.pid_exists(pid))

    def test_every_profile_has_entry_or_import(self):
        self.assertEqual(sum(map(len, comparison.PROFILES.values())), 33)
        for tool, profiles in comparison.PROFILES.items():
            for profile in profiles:
                if tool not in {'acrobat', 'boox-note'}:
                    command, _ = comparison.commands(tool, profile, 'input.pdf', 'output.pdf')
                    self.assertTrue(command)
                    self.assertFalse(any(x in command for x in ('--force-ocr', '--normalize-content', '--optimize-images')))

    def test_original_and_feature_loss_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root/'a.pdf', root/'b.pdf'
            fixture(a, pages=2)
            same = comparison.independent_checks(a, a)
            self.assertTrue(all(x['status'] == 'pass' for x in same.values()), same)
            fixture(b, text='', attachment=False, links=False)
            changed = comparison.independent_checks(a, b)
            for name in ('page_geometry', 'text', 'attachments'):
                self.assertEqual(changed[name]['status'], 'confirmed difference', changed[name])
            self.assertNotEqual(changed['links']['status'], 'pass')

    def test_equivalent_rewrite_and_order_do_not_prove_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root/'a.pdf', root/'b.pdf'
            fixture(a, text='abc')
            fixture(b, text='cba')
            checks = comparison.independent_checks(a, b)
            self.assertEqual(checks['text']['status'], 'unproven')
            self.assertEqual(checks['attachments']['status'], 'pass')

    def test_no_recommendation_without_checks(self):
        self.assertEqual(comparison.classification({'status': 'usable'}), 'Unproven')
        row = {'status': 'usable', 'checks': {'text': {'status': 'confirmed difference'}}}
        self.assertEqual(comparison.classification(row), 'Feature-loss diagnostic')

    def test_firefox_failure_explicit_and_variability(self):
        self.assertEqual(firefox_measure.smoke(None, ROOT)['status'], 'unavailable')
        self.assertTrue(firefox_measure.needs_more([{'a': {'median_ms': 1, 'range_ms': [0, 2]}}]))
        self.assertTrue(firefox_measure.needs_more([{'a': {'median_ms': 100, 'range_ms': [99, 101]}},
                                                   {'a': {'median_ms': 109, 'range_ms': [108, 110]}}]))
        self.assertFalse(firefox_measure.needs_more([{'a': {'median_ms': 100, 'range_ms': [99, 101]}}]))

    @unittest.skipUnless(importlib.util.find_spec('selenium'), 'Install comparison dependencies')
    def test_fresh_navigation_opens_requested_page_directly(self):
        driver = MagicMock()
        driver.execute_script.return_value = {'tracker': {'pages': {'7': {
            'pagerender': 200, 'pagerendered': 230, 'textlayerrendered': 235, 'annotationlayerrendered': 240}}}}
        with patch.object(firefox_measure, 'page_measure', return_value={'page': 7, 'ms': 0, 'resolved': False}) as measured:
            result, _ = firefox_measure.open_pdf(driver, ROOT/'generated.pdf', 7)
        self.assertTrue(driver.get.call_args.args[0].endswith('#page=7&zoom=page-width'))
        measured.assert_called_once_with(driver, 7)
        self.assertEqual(result['navigation_ms'], 240)
        self.assertTrue(result['resolved'])

    def test_full_run_failure_isolation_import_identity_and_output_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, imported = root/'source.pdf', root/'imported.pdf'
            fixture(source)
            fixture(imported, text='diagnostic')
            digest = comparison.filehash(source)
            config = root/'config.json'
            fake = root/'fake.py'
            fake.write_text("import pathlib,shutil,sys\nif '--linearize' in sys.argv: shutil.copyfile(sys.argv[1],sys.argv[-1])\nelse: pathlib.Path(sys.argv[-1]).write_bytes(b'not a PDF')\n")
            config.write_text(json.dumps({'tools': {'qpdf': {
                'command': [sys.executable, str(fake)], 'version': 'fixture-1',
                'version_args': ['--version'], 'help_args': ['--help']}}, 'imports': [{
                'tool': 'acrobat', 'profile': 'conservative', 'source_sha256': '0'*64,
                'path': str(imported), 'settings': 'synthetic test', 'version': 'fixture'}]}))
            pre = {'dependencies': {}, 'tools': {'qpdf': {
                'version_pinned': True, 'help_probe': {'status': 'completed'},
                'help_text': '--recompress-flate --compression-level --object-streams --stream-data --linearize'}}, 'environment': {'node': 'node'},
                   'firefox': {'status': 'unavailable', 'error': 'test fixture'}}
            args = argparse_namespace(input=[source], output=root/'run', tools_config=config,
                                      firefox=None, preflight_only=False)
            def fake_analysis(source, output, directory, node, checkpoint, result, progress=None, cache=None):
                page = {'page': 1, 'test_evidence': 'x'*4096}
                result.update(pages=[page], regions=[])
                checkpoint({'kind': 'page-analysis', 'page': page})
                for i in range(3):
                    region = {'id': f'p1-{i}', 'page': 1}
                    checkpoint({'kind': 'detail-analysis', 'region': region})
            with patch.object(comparison, 'preflight', return_value=pre), \
                 patch.object(comparison, 'PROFILES', {'qpdf': ['flate', 'flate-linearized'], 'acrobat': ['conservative']}), \
                 patch.object(comparison, 'analyze', side_effect=fake_analysis):
                state = comparison.execute(args)
                self.assertEqual(state['status'], 'complete')
                rows = state['documents'][0]['outputs']
                self.assertEqual(len(rows), 4)
                self.assertEqual(rows[1]['status'], 'failed')
                self.assertEqual(rows[2]['status'], 'usable')
                self.assertIn('does not match', rows[3]['reason'])
                self.assertTrue(state['documents'][0]['source_integrity'])
                self.assertEqual(comparison.filehash(source), digest)
                self.assertTrue((args.output/'results.json').exists())
                records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(args.output.glob('record-*.json'))]
                self.assertTrue(all(r['schema'] == 2 and 'documents' not in r for r in records))
                self.assertEqual([r['sequence'] for r in records], list(range(1, len(records)+1)))
                pages = [r for r in records if r['kind'] == 'page-analysis']
                self.assertEqual(len(pages), 2)
                self.assertTrue(all(r['document'] == 'document-001' and r['page']['test_evidence'] == 'x'*4096 for r in pages))
                details = [r for r in records if r['kind'] == 'detail-analysis']
                self.assertEqual(len(details), 6)
                self.assertTrue(all('page' not in r and 'summary' not in r for r in details))
                self.assertTrue(any(r['kind'] == 'output' and r['metadata']['status'] == 'failed' for r in records))
                with self.assertRaises(FileExistsError):
                    comparison.execute(args)

    def test_native_event_clock_and_cached_readiness_without_polling(self):
        if not shutil_which('node'):
            self.skipTest('Node required')
        # This checks the adapter logic, not a substitute for the native smoke test.
        script = r'''
const assert=require('node:assert/strict');
const measure=new Function(SCRIPT);
let clock=100, result;
global.performance={now:()=>clock};
global.window={};
const handlers={};
const bus={on:(n,f)=>(handlers[n] ||= []).push(f),off:(n,f)=>handlers[n]=handlers[n].filter(x=>x!==f)};
const page={renderingState:0,canvas:{},textLayer:{div:{hidden:false,textContent:'text'}},annotationLayer:{div:{childElementCount:1}}};
const viewer={currentScaleValue:'page-width',getPageView:()=>page,scrollPageIntoView:()=>{
  if(page.renderingState===3)return;
  for(const name of ['pagerendered','textlayerrendered','annotationlayerrendered']){
    clock+=5;window.__comparison.pages[1][name]=clock;
    if(name==='annotationlayerrendered')page.renderingState=3;
    for(const fn of [...(handlers[name] || [])])fn({pageNumber:1});
  }
}};
window.PDFViewerApplication={pdfViewer:viewer,eventBus:bus};
window.__comparison={pages:{1:{}}};
measure(1,null,r=>result=r);
assert.equal(result.ms,15);assert.equal(result.cached,false);
clock=200;measure(1,null,r=>result=r);
assert.equal(result.cached,true);assert.equal(result.ms,0);
delete window.__comparison;measure(1,null,r=>result=r);
assert.match(result.error,/preload instrumentation unavailable/);
'''.replace('SCRIPT', json.dumps(firefox_measure.MEASURE))
        result = subprocess.run([shutil_which('node'), '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_detail_canvas_retains_finished_layer_readiness(self):
        if not shutil_which('node'):
            self.skipTest('Node required')
        script = r'''
const assert=require('node:assert/strict');
let clock=100;
global.performance={now:()=>clock};
const handlers={};
global.window={PDFViewerApplication:{eventBus:{on:(name,fn)=>handlers[name]=fn}}};
const install=eval('('+PRELOAD+')');install();
const main={id:1},detail={pageView:main};
const emit=(name,at,source=main)=>{clock=at;handlers[name]({pageNumber:1,source});};
emit('pagerender',100);emit('pagerendered',200);
emit('textlayerrendered',300);emit('annotationlayerrendered',400);
emit('pagerender',500,detail);
const state=window.__comparison.pages[1];
assert.equal(state.pagerender,100);
assert.equal(state.textlayerrendered,300);
assert.equal(state.annotationlayerrendered,400);
assert.equal(state.pagerendered,undefined);
emit('pagerendered',600,detail);
assert.equal(state.pagerendered,600);
emit('pagerender',700);
assert.equal(window.__comparison.pages[1].textlayerrendered,undefined);
assert.equal(window.__comparison.pages[1].annotationlayerrendered,undefined);
'''.replace('PRELOAD', json.dumps(firefox_measure.PRELOAD))
        result = subprocess.run([shutil_which('node'), '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_zoom_ignores_css_preview_and_waits_for_detail_and_layers(self):
        driver = MagicMock()
        with patch.object(firefox_measure, 'zoom_measure', return_value={'resolved': False}) as zoom:
            self.assertEqual(firefox_measure.page_measure(driver, 3, '2'), {'resolved': False})
            zoom.assert_called_once_with(driver, 3, '2')
        if not shutil_which('node'):
            self.skipTest('Node required')
        script = r'''
const assert=require('node:assert/strict'),measure=new Function(SCRIPT);
let clock=100,result,frames=[],timers=[],scale='page-width';
global.performance={now:()=>clock};
global.requestAnimationFrame=f=>frames.push(f);
global.setTimeout=f=>(timers.push(f),timers.length);
global.clearTimeout=()=>{};
const handlers={},bus={on:(n,f)=>(handlers[n]||=[]).push(f),off:(n,f)=>handlers[n]=handlers[n].filter(x=>x!==f)};
const page={renderingState:3,detailView:{renderingState:3},textLayer:{div:{hidden:false,textContent:'text'}},annotationLayer:{div:{childElementCount:1}}};
const state={textlayerrendered:90,annotationlayerrendered:90};
const viewer={getPageView:()=>page,get currentScaleValue(){return scale},set currentScaleValue(v){scale=v;emit('pagerendered',110,{cssTransform:true});}};
global.window={PDFViewerApplication:{pdfViewer:viewer,eventBus:bus},__comparison:{pages:{1:state}}};
function emit(name,at,extra={}){clock=at;for(const fn of [...(handlers[name]||[])])fn({pageNumber:1,timestamp:at,source:page,...extra});}
function frame(at){clock=at;const fs=frames;frames=[];for(const f of fs)f();}
measure(1,'1',r=>result=r);
frame(120);assert.equal(result,undefined,'CSS-only preview must not complete zoom');
const detail={pageView:page};page.detailView.renderingState=1;
emit('pagerender',130,{source:detail});frame(140);assert.equal(result,undefined);
page.detailView.renderingState=3;emit('pagerendered',160,{source:detail});
emit('textlayerrendered',170);emit('annotationlayerrendered',175);
frame(200);assert.equal(result,undefined);frame(250);
assert.equal(result.ms,75,'Use browser events, not the validation-frame clock');
assert.equal(result.actual_canvas_completions,1);assert.equal(result.textReady,true);
assert.ok(Object.values(handlers).every(x=>x.length===0),'Listeners cleaned up');
result=undefined;measure(1,'1',r=>result=r);
assert.equal(result.ms,0);assert.equal(result.redraw_requested,false);
result=undefined;measure(1,'1.0',r=>result=r);assert.equal(result.redraw_requested,false);
result=undefined;measure(1,'2',r=>result=r);frame(300);
assert.equal(result,undefined);timers.at(-1)();assert.match(result.error,/did not complete/);
result=undefined;measure(1,'page-width',r=>result=r);
emit('pagerendered',350,{error:'render failed'});assert.match(result.error,/render failed/);
'''.replace('SCRIPT', json.dumps(firefox_measure.ZOOM))
        result = subprocess.run([shutil_which('node'), '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_progress_file_includes_early_stages_and_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'progress.log'
            progress = comparison.Progress()
            with patch('builtins.print'):
                progress('Preflight started')
                progress.attach(path)
                progress('qpdf/flate: optimizing')
                with patch.object(progress.stop, 'wait', side_effect=[False, True]):
                    progress._heartbeat()
            progress.stream.close()
            text = path.read_text(encoding='utf-8')
            self.assertIn('Preflight started', text)
            self.assertIn('Waiting: qpdf/flate: optimizing', text)
            self.assertIn('elapsed', text)
            with self.assertRaises(FileExistsError):
                comparison.Progress().attach(path)

    def test_missing_ocr_and_changed_math(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, after = root/'before.pdf', root/'after.pdf'
            for path, text in [(before, 'x² = 3.14'), (after, 'x³ = 3.14')]:
                c = canvas.Canvas(str(path))
                layer = c.beginText(40, 700)
                layer.setTextRenderMode(3)
                layer.textLine(text)
                c.drawText(layer)
                c.showPage()
                c.save()
            checks = comparison.independent_checks(before, after)
            self.assertEqual(checks['text']['status'], 'confirmed difference')
            self.assertEqual(checks['page_geometry']['status'], 'pass')


@unittest.skipUnless(HAS_METRICS, 'Install requirements-comparison.txt for calibration tests')
class InkCalibrationTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from PIL import Image, ImageDraw
        self.np, self.Image, self.Draw = np, Image, ImageDraw
        self.blank = Image.new('RGB', (128, 128), 'white')
        self.original = self.blank.copy()
        d = ImageDraw.Draw(self.original)
        d.ellipse((20, 20, 60, 60), outline='black', width=3)
        d.line((20, 90, 60, 90), fill='black', width=3)
        d.point((100, 90), fill='black')

    def tearDown(self):
        self.original.close()
        self.blank.close()

    def metrics(self, after):
        from comparison_metrics import image_metrics
        return image_metrics(self.original, after, (12.8, 12.8))

    def test_original_blank_and_complete_loss(self):
        m = self.metrics(self.original)
        self.assertTrue(m['exact'])
        for t in m['thresholds'].values():
            self.assertEqual(t['stroke_displacement_mm']['max'], 0)
            self.assertEqual(t['thickness_change_mm']['p05'], 0)
            self.assertEqual(t['source_width_mm'], t['output_width_mm'])
            self.assertIsNotNone(t['source_width_mm']['p05'])
            self.assertEqual(t['thickness_absolute_change_mm']['max'], 0)
            self.assertEqual(t['tolerances_mm']['0']['missing_fraction'], 0)
            self.assertEqual(t['matched_skeleton_coverage']['source']['fraction'], 1)
            self.assertGreaterEqual(t['source_features']['dot_candidates'], 1)
            self.assertGreaterEqual(t['source_features']['fraction_bar_candidates'], 1)
            self.assertGreaterEqual(t['source_features']['loop_components'], 1)
        lost = self.metrics(self.blank)
        for t in lost['thresholds'].values():
            self.assertEqual(t['tolerances_mm']['0.1']['missing_fraction'], 1)
            self.assertIsNone(t['stroke_displacement_mm']['median'])
        from comparison_metrics import image_metrics
        empty = image_metrics(self.blank, self.blank, (12.8, 12.8))
        self.assertIsNone(empty['thresholds']['225']['tolerances_mm']['0']['missing_fraction'])

    def test_missing_dot_and_small_local_damage(self):
        from comparison_metrics import flagged_regions
        after = self.original.copy()
        self.Draw.Draw(after).point((100, 90), fill='white')
        m = self.metrics(after)
        self.assertGreater(m['whole_page_ssim'], .99)
        self.assertTrue(m['thresholds']['225']['potential_missing_components'])
        self.assertLess(m['thresholds']['225']['matched_skeleton_coverage']['source']['fraction'], 1)
        self.assertTrue(flagged_regions(self.original, after))
        after.close()

    def test_translation_recovered_within_pixel(self):
        after = self.blank.copy()
        after.paste(self.original, (3, 0))
        m = self.metrics(after)['thresholds']['225']
        self.assertAlmostEqual(m['stroke_displacement_mm']['max'], .3, delta=.1)
        after.close()

    def test_thickness_and_loop_break(self):
        after = self.original.copy()
        d = self.Draw.Draw(after)
        d.rectangle((38, 18, 44, 25), fill='white')
        m = self.metrics(after)
        self.assertTrue(m['thresholds']['225']['potential_topology_change'])
        after.close()
        from PIL import ImageFilter
        after = self.original.filter(ImageFilter.MinFilter(3))
        m = self.metrics(after)['thresholds']['225']
        self.assertAlmostEqual(m['thickness_absolute_change_mm']['p95'], .2, delta=.1)
        self.assertIsNotNone(m['thickness_change_mm']['p05'])
        self.assertIsNotNone(m['thickness_change_mm']['p95'])
        self.assertGreater(m['output_width_mm']['median'], m['source_width_mm']['median'])
        after.close()

    def test_added_merge_faded_colored_and_thresholds(self):
        after = self.original.copy()
        d = self.Draw.Draw(after)
        d.line((40, 60, 40, 90), fill='black', width=2)
        d.point((110, 110), fill='red')
        m = self.metrics(after)
        self.assertGreater(m['thresholds']['225']['tolerances_mm']['0']['added_fraction'], 0)
        self.assertTrue(m['thresholds']['225']['potential_added_components'])
        self.assertTrue(m['thresholds']['225']['potential_topology_change'])
        after.close()
        a = self.np.array(self.original)
        a[a < 255] = 230
        after = self.Image.fromarray(a)
        m = self.metrics(after)
        self.assertTrue(m['threshold_sensitive'])
        self.assertGreater(m['rgb_foreground_absolute_difference']['max'], 0)
        after.close()

    def test_resolution_scaling_and_raster_artifacts(self):
        from comparison_metrics import image_metrics
        from PIL import ImageFilter
        fine = self.original.resize((256, 256), resample=self.Image.Resampling.NEAREST)
        shifted = self.Image.new('RGB', fine.size, 'white')
        shifted.paste(fine, (6, 0))
        m = image_metrics(fine, shifted, (12.8, 12.8))
        self.assertAlmostEqual(m['thresholds']['225']['stroke_displacement_mm']['max'], .3, delta=.05)
        with self.original.filter(ImageFilter.GaussianBlur(.5)) as blurred:
            self.assertFalse(self.metrics(blurred)['exact'])
        import io
        compressed = io.BytesIO()
        self.original.save(compressed, format='JPEG', quality=40)
        compressed.seek(0)
        with self.Image.open(compressed) as jpeg:
            self.assertFalse(self.metrics(jpeg)['exact'])
        fine.close()
        shifted.close()

    def test_plot_axes_and_nondominated_records(self):
        from comparison_metrics import image_metrics
        original_metrics = self.metrics(self.original)
        changed_metrics = self.metrics(self.blank)
        timing = lambda value: {'first_visit/p1/None': {'median_ms': value, 'range_ms': [value, value], 'resolved': True}}
        state = {'documents': [{'id': 'generated', 'outputs': [
            {'tool': 'source', 'profile': 'original', 'status': 'usable',
             'firefox_summary': timing(100), 'appearance': {'pages': [{'pdfium_144': original_metrics}]}},
            {'tool': 'test', 'profile': 'lost', 'status': 'usable',
             'firefox_summary': timing(50), 'appearance': {'pages': [{'pdfium_144': changed_metrics}]}}
        ]}]}
        with tempfile.TemporaryDirectory() as tmp:
            comparison.plots(Path(tmp), state)
            self.assertTrue(list(Path(tmp).glob('*.png')))
            doc = state['documents'][0]
            self.assertTrue(doc['nondominated'])
            # Missing ink has a value of one; absent skeleton distances stay null.
            self.assertEqual(doc['outputs'][1]['tradeoffs']['first_visit/p1/None']['quality_axes'][:2], [1, None])


if __name__ == '__main__':
    unittest.main()
