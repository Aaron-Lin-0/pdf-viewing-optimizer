"""Event-clock measurements of Firefox's bundled PDF viewer, never a PDF.js harness."""
import contextlib
import os
from pathlib import Path
import shutil
import statistics
import tempfile
import threading
import time


def discover(override=None):
    choices = [override] if override else [shutil.which('firefox'),
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Mozilla Firefox', 'firefox.exe'),
        '/Applications/Firefox.app/Contents/MacOS/firefox', '/usr/bin/firefox']
    return next((str(Path(p).resolve()) for p in choices if p and Path(p).is_file()), None)


def kill_owned(processes):
    import psutil
    for process in reversed(processes):
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            process.terminate()
    _, alive = psutil.wait_procs(processes, timeout=3)
    for process in alive:
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            process.kill()
    psutil.wait_procs(alive, timeout=3)


@contextlib.contextmanager
def session(binary, directory):
    import psutil
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    directory = Path(directory).resolve()
    options = Options()
    options.binary_location = binary
    options.add_argument('-headless')
    options.enable_bidi = True
    # Fixed device scale and rendering preferences across all measurements.
    for key, value in {'pdfjs.disabled': False, 'layout.css.devPixelsPerPx': '1.0',
                       'browser.shell.checkDefaultBrowser': False,
                       'toolkit.telemetry.enabled': False,
                       'datareporting.healthreport.uploadEnabled': False,
                       'pdfjs.enableScripting': False}.items():
        options.set_preference(key, value)
    driver = None
    owned = {}
    stop = threading.Event()
    samples = []
    with tempfile.TemporaryDirectory(prefix='firefox-', dir=directory) as profile:
        options.add_argument('-no-remote')
        service = Service(executable_path=shutil.which('geckodriver'),
                          log_output=str(Path(directory) / 'geckodriver.log'),
                          service_args=['--log', 'error', '--profile-root', profile],
                          env={**os.environ, 'TEMP': str(directory), 'TMP': str(directory)})
        def sample():
            while not stop.wait(.05):
                try:
                    if getattr(service, 'process', None) is None:
                        continue
                    root = psutil.Process(service.process.pid)
                    tree = [root] + root.children(recursive=True)
                    rss = 0
                    for process in tree:
                        owned[process.pid] = process
                        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                            rss += process.memory_info().rss
                    samples.append({'elapsed_s': time.monotonic() - start, 'rss_bytes': rss})
                except psutil.NoSuchProcess:
                    break
        start = time.monotonic()
        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        try:
            driver = webdriver.Firefox(options=options, service=service)
            driver.command_executor.client_config.timeout = 310
            driver.set_window_size(1440, 1000)
            driver.set_page_load_timeout(300)
            driver.set_script_timeout(300)
            driver.script.add_preload_script(function_declaration=PRELOAD)
            yield driver, samples
        finally:
            if driver:
                with contextlib.suppress(Exception):
                    driver.quit()
            stop.set()
            if thread:
                thread.join(3)
            kill_owned(list(owned.values()))
            service.stop()


# Viewer initialization is discovered at document creation. A late hook is rejected
# by smoke rather than inventing a navigation time from WebDriver polling.
PRELOAD = r"""() => {
  const install=()=>{
    const app=window.PDFViewerApplication;
    if(!app?.eventBus){setTimeout(install,0);return;}
    const tracker=window.__comparison={events:[],pages:{},hookedAt:performance.now()};
    for(const name of ['pagerender','pagerendered','textlayerrendered','annotationlayerrendered']) {
      app.eventBus.on(name,e=>{
        const at=performance.now();
        const detail=!!e.source?.pageView;
        tracker.events.push({name,page:e.pageNumber,at,detail,error:e.error?String(e.error):null});
        if(name==='pagerender' && !detail)tracker.pages[e.pageNumber]={};
        const p=tracker.pages[e.pageNumber] ||= {};
        // Detail canvases reuse the page's completed text/annotation layers.
        if(name==='pagerender' && detail){p.detailrender=at;delete p.pagerendered;}
        else p[name]=at;
        if(e.error)p.error=String(e.error);
      });
    }
  };
  install();
}"""


# Install before changing page/zoom; completion timestamps are taken inside Firefox.
MEASURE = r"""
const pageNumber=arguments[0], scale=arguments[1], done=arguments[arguments.length-1];
const app=window.PDFViewerApplication;
if (!app?.pdfViewer || !app?.eventBus) { done({error:'Native viewer interface unavailable'}); return; }
const viewer=app.pdfViewer, bus=app.eventBus, start=performance.now();
const tracker=window.__comparison;
if(!tracker){done({error:'Native viewer preload instrumentation unavailable'});return;}
const events=[], handlers=[]; let finished=false;
const target=viewer.getPageView(pageNumber-1);
const zoomChange=scale!==null && String(viewer.currentScaleValue)!==scale;
let cached=!zoomChange && target?.renderingState===3 && ![1,2].includes(target?.detailView?.renderingState);
const timeout=setTimeout(()=>finish({error:'Page/layers readiness timed out',state:tracker.pages[pageNumber]}),290000);
function finish(result) {
  if(finished)return; finished=true; clearTimeout(timeout);
  for(const [name,fn] of handlers)bus.off(name,fn);
  done({...result,page:pageNumber,scale,events});
}
function ready() {
  const p=viewer.getPageView(pageNumber-1);
  if(!p || p.renderingState!==3 || !p.canvas)return;
  // These interfaces are probed by the smoke fixture, including nonempty layers.
  const text=p.textLayer, annotation=p.annotationLayer, state=tracker.pages[pageNumber];
  if(state?.error){finish({error:state.error});return;}
  if(!state?.pagerendered || !state.textlayerrendered || !state.annotationlayerrendered)return;
  if(!text?.div || text.div.hidden)return;
  const end=performance.now();
  const completedAt=Math.max(state.pagerendered,state.textlayerrendered,state.annotationlayerrendered);
  finish({ms:cached?end-start:completedAt-start, completedAt,cached,
    textReady:true,annotationReady:true,textItems:text.div.textContent.length,
    annotationItems:annotation?.div?.childElementCount ?? 0});
}
for(const name of ['pagerendered','textlayerrendered','annotationlayerrendered']) {
  const fn=e=>{if(e.pageNumber===pageNumber){events.push({name,at:performance.now()});if(e.error)finish({error:String(e.error)});else ready();}};
  handlers.push([name,fn]);bus.on(name,fn);
}
viewer.currentPageNumber=pageNumber;
if(scale!==null)viewer.currentScaleValue=scale;
viewer.scrollPageIntoView({pageNumber});
ready();
"""


def page_measure(driver, page, scale=None):
    if scale is not None:
        return zoom_measure(driver, page, scale)
    result = driver.execute_async_script(MEASURE, page, scale)
    if result.get('error'):
        raise RuntimeError(f"{result['error']} (page={page}, scale={scale}, state={result.get('state')}, events={result.get('events')})")
    # Cached readiness is an observation, not an invented positive rendering time.
    result['resolved'] = result['ms'] > 0
    return result


SETTLE = r"""
const page=arguments[0],done=arguments[arguments.length-1];let stable=0,finished=false;
const timer=setTimeout(()=>{finished=true;done({error:'Zoom preparation did not settle'});},290000);
function tick(){
 if(finished)return;
 const p=PDFViewerApplication.pdfViewer.getPageView(page-1);
 const ready=p?.renderingState===3 && ![1,2].includes(p.detailView?.renderingState) && p.textLayer?.div && !p.textLayer.div.hidden;
 stable=ready?stable+1:0;
 if(stable===2){finished=true;clearTimeout(timer);done({ready:true});return;}
 requestAnimationFrame(tick);
}requestAnimationFrame(tick);
"""


ZOOM = r"""
const page=arguments[0],scale=arguments[1],done=arguments[arguments.length-1];
const a=window.PDFViewerApplication,v=a.pdfViewer,bus=a.eventBus,start=performance.now();
const pending=new Set(),events=[],handlers=[];let completed=0,last=null,stable=0,finished=false;
function finish(extra){
 if(finished)return;finished=true;clearTimeout(timer);
 for(const [name,fn] of handlers)bus.off(name,fn);
 done({page,scale,ms:last===null?null:last-start,actual_canvas_completions:completed,events,...extra});
}
const timer=setTimeout(()=>finish({error:'Actual zoom redraw/layers did not complete'}),290000);
const requested=()=>String(v.currentScaleValue)===scale || (scale!=='page-width' && Number(v.currentScaleValue)===Number(scale));
if(requested()){finish({ms:0,cached:true,redraw_requested:false});return;}
for(const name of ['pagerender','pagerendered','textlayerrendered','annotationlayerrendered']){
 const fn=e=>{
  if(e.pageNumber!==page)return;
  const at=e.timestamp ?? performance.now();
  events.push({name,at,cssTransform:!!e.cssTransform,detail:!!e.isDetailView || !!e.source?.pageView});
  if(e.error){finish({error:String(e.error)});return;}
  if(name==='pagerender')pending.add(e.source);
  if(name==='pagerendered' && !e.cssTransform){pending.delete(e.source);last=Math.max(last??at,at);completed++;}
  if(name==='textlayerrendered' || name==='annotationlayerrendered')last=Math.max(last??at,at);
 };
 bus.on(name,fn);handlers.push([name,fn]);
}
function tick(){
 if(finished)return;
 const p=v.getPageView(page-1),state=window.__comparison.pages[page];
 if(state?.error){finish({error:state.error});return;}
 const ready=completed>0 && pending.size===0 && p?.renderingState===3 &&
  ![1,2].includes(p.detailView?.renderingState) && p.textLayer?.div && !p.textLayer.div.hidden &&
  state?.textlayerrendered && state?.annotationlayerrendered && requested();
 stable=ready?stable+1:0;
 if(stable===2){finish({cached:false,redraw_requested:true,textReady:true,annotationReady:true,
   textItems:p.textLayer.div.textContent.length,annotationItems:p.annotationLayer?.div?.childElementCount??0});return;}
 requestAnimationFrame(tick);
}
v.currentScaleValue=scale;
requestAnimationFrame(tick);
"""


def zoom_measure(driver, page, scale):
    """Require a real canvas/detail redraw; CSS previews cannot complete a zoom."""
    page_measure(driver, page)
    prepared = driver.execute_async_script(SETTLE, page)
    if prepared.get('error'):
        raise RuntimeError(prepared['error'])
    result = driver.execute_async_script(ZOOM, page, scale)
    if result.get('error'):
        raise RuntimeError(f"{result['error']} (page={page}, scale={scale}, events={result.get('events')})")
    result['resolved'] = bool(result.get('actual_canvas_completions') and result['ms'] > 0)
    result['timing_basis'] = 'zoom request through actual canvas/detail completion and layer readiness; CSS previews excluded; stable-frame validation excluded from timing'
    return result


def open_pdf(driver, path, page_number=1):
    from selenium.webdriver.support.ui import WebDriverWait
    driver.get(Path(path).resolve().as_uri() + f'#page={page_number}&zoom=page-width')
    WebDriverWait(driver, 30).until(lambda d: d.execute_script(
        'return !!window.PDFViewerApplication?.pdfDocument'))
    result = page_measure(driver, page_number)
    timing = driver.execute_script("""
const a=window.PDFViewerApplication;
return {pdfjs:a.pdfjsLib?.version ?? globalThis.pdfjsLib?.version ?? null,
 tracker:window.__comparison,
 marks:performance.getEntriesByType('mark').map(m=>({name:m.name,startTime:m.startTime})),
 devicePixelRatio,viewport:[innerWidth,innerHeight],userAgent:navigator.userAgent};
""")
    page = (timing.get('tracker') or {}).get('pages', {}).get(str(page_number), {})
    completions = [page.get(k) for k in ('pagerendered', 'textlayerrendered', 'annotationlayerrendered')]
    if not all(completions):
        raise RuntimeError('Native navigation completion events unavailable')
    result['navigation_ms'] = max(completions)
    result['navigation_status'] = 'event clock relative to viewer navigation origin'
    result['ms'] = result['navigation_ms']
    result['resolved'] = result['ms'] > 0
    return result, timing


def measure(path, binary, directory, pages=None, exploratory=False, progress=None):
    progress = progress or (lambda message: None)
    observations = []
    metadata = {}
    with session(binary, directory) as (driver, memory):
        progress('Firefox: navigation to first page')
        first, metadata = open_pdf(driver, path)
        count = driver.execute_script('return PDFViewerApplication.pdfDocument.numPages')
        metadata.update(firefox=driver.capabilities.get('browserVersion'),
                        geckodriver=driver.capabilities.get('moz:geckodriverVersion'),
                        binary=str(binary), headless=True,
                        rendering_preferences={'device_scale': 1, 'zoom': 'page-width',
                                               'pdf_scripting': False, 'acceleration': 'Firefox defaults'})
        observations.append(dict(first, operation='navigation'))
        for page in range(1, count + 1):
            progress(f'Firefox: first visit, page {page}/{count}')
            row = page_measure(driver, page)
            events = driver.execute_script('return window.__comparison.pages[arguments[0]]', page)
            if not events or not events.get('pagerender'):
                raise RuntimeError('Native first-visit render-start event unavailable')
            row['ms'] = max(events[k] for k in ('pagerendered', 'textlayerrendered', 'annotationlayerrendered')) - events['pagerender']
            row['resolved'] = row['ms'] > 0
            row['timing_basis'] = 'native first draw through layer readiness; may be prefetched before navigation request'
            observations.append(dict(row, operation='first_visit'))
        if not exploratory:
            for direction, sequence in [('backward', range(count, 0, -1)), ('forward', range(1, count + 1))]:
                for page in sequence:
                    progress(f'Firefox: cached {direction}, page {page}/{count}')
                    observations.append(dict(page_measure(driver, page), operation='revisit_' + direction))
            for page in pages or [1]:
                if page <= count:
                    progress(f'Firefox: prepare zoom at 200%, page {page}/{count}')
                    zoom_measure(driver, page, '2')
                    for scale in ['page-width', '1', '2']:
                        progress(f'Firefox: zoom {scale}, page {page}/{count}')
                        observations.append(dict(zoom_measure(driver, page, scale), operation='zoom'))
        progress('Firefox: collect process metadata and close completed session')
        import psutil
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            tree = [psutil.Process(driver.service.process.pid)]
            tree += tree[0].children(recursive=True)
            metadata['owned_commands'] = [p.cmdline() for p in tree]
        memory_copy = list(memory)
    if not exploratory:
        for page in pages or [1]:
            if page > count:
                continue
            progress(f'Firefox: launch fresh session for page {page}/{count}')
            with session(binary, directory) as (driver, memory):
                progress(f'Firefox: fresh-session page {page}/{count}')
                first, _ = open_pdf(driver, path, page)
                observations.append(dict(first, operation='fresh_session'))
                memory_copy.extend(memory)
    return {'status': 'available', 'metadata': metadata, 'observations': observations,
            'memory_samples': memory_copy, 'memory_note': 'Sampled owned-tree RSS, not peak memory; includes driver.',
            'cache_note': 'Fresh sessions do not clear operating-system disk caches.'}


def smoke(binary, directory, progress=None):
    if not binary:
        return {'status': 'unavailable', 'error': 'Firefox not found'}
    try:
        from reportlab.pdfgen import canvas
        from pypdf import PdfReader, PdfWriter
        from pypdf.annotations import Text
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            root = Path(tmp)
            raw = root / 'raw.pdf'
            c = canvas.Canvas(str(raw))
            for i in range(3):
                c.drawString(50, 700, f'Native viewer smoke page {i+1}')
                c.showPage()
            c.save()
            writer = PdfWriter(clone_from=PdfReader(raw))
            for i in range(3):
                writer.add_annotation(i, Text(rect=(50, 600, 80, 630), text='Smoke annotation'))
            fixture = root / 'smoke.pdf'
            with fixture.open('xb') as stream:
                writer.write(stream)
            result = measure(fixture, binary, directory, [1, 3], progress=progress)
            rendered = [x for x in result['observations'] if x['events']]
            if not rendered or not all(x['textReady'] and x['annotationReady'] for x in rendered):
                raise RuntimeError('Smoke test did not observe native render/layer completion')
            if not any(x['annotationItems'] for x in result['observations']):
                raise RuntimeError('Smoke annotation layer is empty')
            if not all(x['textItems'] > 0 for x in result['observations'] if x['operation'] == 'first_visit'):
                raise RuntimeError('Smoke text layer is empty')
            if not any(x['events'] for x in result['observations'] if x['operation'] == 'zoom'):
                raise RuntimeError('Smoke did not observe zoom rendering')
            if not all(x.get('actual_canvas_completions', 0)>0 and x['resolved']
                       for x in result['observations'] if x['operation'] == 'zoom'):
                raise RuntimeError('Smoke zoom did not observe actual canvas completion')
            if not any(x['cached'] for x in result['observations'] if x['operation'].startswith('revisit')):
                raise RuntimeError('Smoke did not detect an already-rendered cached page')
            if not result['metadata'].get('pdfjs'):
                raise RuntimeError('Bundled PDF.js version unavailable')
            return result
    except Exception as exc:
        return {'status': 'unavailable', 'error': f'{type(exc).__name__}: {exc}'}


def summarize(runs):
    series = {}
    for run in runs:
        for row in run.get('observations', []):
            key = f"{row['operation']}/p{row['page']}/{row.get('scale')}"
            series.setdefault(key, []).append(row['ms'])
    return {key: {'raw_ms': values, 'median_ms': statistics.median(values),
                  'range_ms': [min(values), max(values)],
                  'resolved': min(values) > 0}
            for key, values in series.items()}


def needs_more(summaries):
    for summary in summaries:
        for row in summary.values():
            median = row['median_ms']
            if median <= 0 or (row['range_ms'][1] - row['range_ms'][0]) / median > .25:
                return True
    for key in set().union(*(s.keys() for s in summaries)):
        values = sorted(s[key]['median_ms'] for s in summaries if key in s and s[key]['median_ms'] > 0)
        if any(b / a <= 1.1 for a, b in zip(values, values[1:])):
            return True
    return False
