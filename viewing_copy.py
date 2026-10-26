"""Build, verify and benchmark optional vector viewing copies."""
import gc
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import os
import re
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
import time
import sys
import multiprocessing
from PIL import ImageChops, ImageFilter, ImageStat
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
import pypdfium2 as pdfium
from navigation import navigation_fingerprint, reader_for, validate_input
from annotation_ink import flatten_ink, consolidate_ink, verify_flattened, verify_consolidated
from geometry import page_signature
from tools.evidence_cache import EvidenceCache


CHECKPOINT_SCHEMA = 1
CHECKPOINT_CODE = ('viewing_copy.py','handwriting.py','annotation_ink.py','geometry.py',
                   'navigation.py','browser_check.py','pdfjs_benchmark.mjs','browser_harness.html',
                   'verification_worker.py','tools/evidence_cache.py')


class TimeBudgetExceeded(RuntimeError):
    pass


class DiskBudgetExceeded(RuntimeError):
    pass


class RunBudget:
    def __init__(self, seconds=None, bytes_limit=None, checkpoint=None):
        self.started=time.monotonic();self.seconds=seconds;self.bytes_limit=bytes_limit
        self.checkpoint=Path(checkpoint) if checkpoint else None

    def check(self, stage, paths=()):
        if self.seconds is not None and time.monotonic()-self.started>=self.seconds:
            raise TimeBudgetExceeded(f'Time budget exhausted before {stage}.')
        if self.bytes_limit is not None:
            roots=[self.checkpoint,*map(Path,paths)] if self.checkpoint else list(map(Path,paths))
            total=sum(path.stat().st_size for root in roots if root and root.exists()
                      for path in ([root] if root.is_file() else root.rglob('*')) if path.is_file())
            if total>self.bytes_limit:raise DiskBudgetExceeded(f'Disk budget exhausted before {stage}.')

    def timeout(self, maximum):
        if self.seconds is None:return maximum
        return max(.001,min(maximum,self.seconds-(time.monotonic()-self.started)))


def _version(distribution):
    try:return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:return 'unavailable'


def _code_hash():
    digest=hashlib.sha256();root=Path(__file__).resolve().parent
    for name in CHECKPOINT_CODE:
        data=(root/name).read_bytes();digest.update(name.encode());digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def _context(operation,source_hash,candidate_hash,renderer,renderer_version,details):
    return {'operation':operation,'source_sha256':source_hash,'candidate_sha256':candidate_hash,
            'page':None,'crop':None,'renderer':renderer,'renderer_version':renderer_version,
            'geometry':None,'resolution':details,'background':None,'thresholds':None,
            'metric_version':f'production-{CHECKPOINT_SCHEMA}'}


def _run(command,maximum,budget,stage,**kwargs):
    timeout=budget.timeout(maximum) if budget else maximum
    budget_limited=budget is not None and timeout<maximum
    try:return subprocess.run(command,timeout=timeout,**kwargs)
    except subprocess.TimeoutExpired as exc:
        if budget_limited:raise TimeBudgetExceeded(f'Time budget exhausted during {stage}.') from exc
        raise ValueError(f'{stage} timed out after {maximum:g} seconds.') from exc


def _store_candidate(cache,context,path):
    payload=cache.store_file(context,path)
    digest=filehash(payload)
    if filehash(path)!=digest:
        Path(path).unlink();shutil.copyfile(payload,path)
    return digest


def filehash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def render(path,index,scale=2,crop=None,print_mode=False):
    with pdfium.PdfDocument(path) as doc:
        doc.init_forms();page=doc[index]
        try:
            kwargs={'scale':scale,'draw_annots':True}
            if crop:kwargs['crop']=crop
            if print_mode:kwargs['optimize_mode']='print'
            bitmap=page.render(**kwargs)
            try:return bitmap.to_pil().convert('RGB')
            finally:bitmap.close()
        finally:page.close()


def logical_cpu_count():
    return os.cpu_count() or 1


def default_verification_workers():
    return min(2, logical_cpu_count())


def verification_resources(requested=None):
    logical = logical_cpu_count()
    effective = default_verification_workers() if requested is None else requested
    if not isinstance(effective, int) or isinstance(effective, bool) or not 1 <= effective <= logical:
        raise ValueError(f'Verification workers must be an integer from 1 through {logical}.')
    return {'requested_workers': requested, 'effective_workers': effective,
            'logical_cpu_count': logical, 'estimated_ram_bytes': (2 + 3 * effective) * 1024**3}


class VerificationSession:
    def __init__(self, source, directory, workers, budget=None):
        from verification_worker import compare_page, render_source
        self.source = Path(source).absolute();self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.reader = reader_for(self.source);self.budget = budget
        self._render_source = render_source;self._compare_page = compare_page
        self.pool = ProcessPoolExecutor(max_workers=workers,
                                        mp_context=multiprocessing.get_context('spawn'))
        self.signatures = {};self.stats = {'source_renders_created': 0,
                                           'source_renders_reused': 0,
                                           'source_signatures_created': 0,
                                           'source_signatures_reused': 0}

    def signature(self, index, normalization=False):
        key = index, bool(normalization)
        if key in self.signatures:
            self.stats['source_signatures_reused'] += 1
            return self.signatures[key]
        value = page_signature(self.reader, index, normalization)
        self.signatures[key] = value;self.stats['source_signatures_created'] += 1
        return value

    def _source_path(self, index, scale=2, crop=None, print_mode=False):
        settings = json.dumps([index, scale, crop, print_mode], separators=(',', ':'))
        return self.directory / (hashlib.sha256(settings.encode()).hexdigest() + '.png')

    def compare(self, candidate, pages, allow_edges=False, scale=2, crop=None, print_mode=False):
        source_paths = [self._source_path(index, scale, crop, print_mode) for index in pages]
        missing = [(index, path) for index, path in zip(pages, source_paths) if not path.is_file()]
        futures = [self.pool.submit(self._render_source,
                   (str(self.source), index, str(path), scale, crop, print_mode))
                   for index, path in missing]
        try:
            for future in futures:
                future.result(timeout=self.budget.timeout(300) if self.budget else 300)
                self.stats['source_renders_created'] += 1
                if self.budget:self.budget.check('source rendering reuse', (self.directory,))
            self.stats['source_renders_reused'] += len(pages) - len(missing)
            tasks = [(str(path), str(candidate), index, allow_edges, scale, crop, print_mode)
                     for index, path in zip(pages, source_paths)]
            results = [self.pool.submit(self._compare_page, task) for task in tasks]
            ordered = [future.result(timeout=self.budget.timeout(300) if self.budget else 300)
                       for future in results]
            if len(ordered) != len(pages) or [row.get('page') for row in ordered] != [i + 1 for i in pages]:
                raise ValueError('Verification worker returned missing or unordered page evidence.')
            return ordered
        except BaseException:
            self.close(force=True)
            raise

    def close(self, force=False):
        pool = getattr(self, 'pool', None)
        if pool is not None:
            self.pool = None
            if force:
                for process in (getattr(pool, '_processes', None) or {}).values():
                    if process.is_alive():process.terminate()
            pool.shutdown(wait=True, cancel_futures=True)
        stream=getattr(getattr(self,'reader',None),'stream',None)
        if stream and hasattr(stream,'close'):stream.close()
        self.reader = None
        gc.collect()

    def __enter__(self):return self
    def __exit__(self, exc_type, _exc, _tb):self.close(force=exc_type is not None)
    def __del__(self):self.close(force=True)


def visual_delta(a,b,allow_edges=False):
    if a.size!=b.size:raise ValueError('Rendered page dimensions changed.')
    with ImageChops.difference(a,b) as delta:
        peak=max(hi for lo,hi in delta.getextrema())
        mean=sum(ImageStat.Stat(delta).mean)/3
        if not peak:return {'exact':True,'peak':0,'mean':0}
        if not allow_edges or peak>96 or mean>1.5:
            raise ValueError(f'Appearance changed beyond guard: peak={peak}, mean={mean:.6f}.')
        ink=a.convert('L').point(lambda p:255 if p<250 else 0)
        outer=ink.filter(ImageFilter.MaxFilter(3));inner=ink.filter(ImageFilter.MinFilter(3))
        band=ImageChops.subtract(outer,inner)
        channels=delta.split();changed=ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2]).point(lambda p:255 if p else 0)
        outside=ImageChops.multiply(changed,ImageChops.invert(band))
        if outside.getbbox():raise ValueError('Pixel changes extend beyond the one-pixel ink-edge band.')
        return {'exact':False,'peak':peak,'mean':mean}


def detail_crop(source,pages):
    best=None
    for page in pages:
        with render(source,page-1) as image:
            ink=image.convert('L').point(lambda p:255 if p<250 else 0)
            edges=ImageChops.subtract(ink.filter(ImageFilter.MaxFilter(3)),ink.filter(ImageFilter.MinFilter(3)))
            width,height=image.size;side=min(192,width,height)
            for y in sorted(set(range(0,height-side+1,side))|{height-side}):
                for x in sorted(set(range(0,width-side+1,side))|{width-side}):
                    with edges.crop((x,y,x+side,y+side)) as tile:score=sum(ImageStat.Stat(tile).sum)
                    if best is None or score>best[0]:best=(score,page,(x/2,(height-y-side)/2,(width-x-side)/2,y/2))
    if not best:raise ValueError('No page available for high-resolution acceptance.')
    return best[1],best[2]


def verify_candidate(source,candidate,manifest,allow_edges=False,full=True,session=None):
    before,after=(session.reader if session else reader_for(source)),reader_for(candidate)
    if before.is_encrypted or len(before.pages)!=len(after.pages):raise ValueError('Protection/page count changed.')
    excluded={int(k):v for k,v in manifest.get('converted',{}).items()}
    if navigation_fingerprint(before,excluded)!=navigation_fingerprint(after):
        raise ValueError('Navigation, retained annotations or catalog data changed; candidate rejected.')
    if manifest.get('consolidated'):verify_consolidated(after,manifest)
    else:verify_flattened(after,manifest.get('evidence',[]))
    pages=list(range(len(before.pages))) if full else sorted(set([0,min(6,len(before.pages)-1),min(28,len(before.pages)-1)]))
    exact=True;deltas=[]
    for index in pages:
        a,b=before.pages[index],after.pages[index]
        if index in excluded:
            commands=manifest.get('page_commands',{})
            expected=commands.get(index,commands.get(str(index)))
            if expected and hashlib.sha256(b.get_contents().get_data()).hexdigest()!=expected:
                raise ValueError('Converted page placement or drawing order changed.')
            content=a.get_contents();old=content.get_data() if content is not None else b''
            if not b.get_contents().get_data().startswith(b'q\n'+old+b'\nQ\n'):
                raise ValueError('Original page commands changed during annotation conversion.')
            names=[e['target'] for e in manifest['evidence'] if e['page']==index]
            if manifest.get('consolidated'):names.extend(c['name'] for c in manifest['consolidated'] if c['page']==index)
            old_sig=session.signature(index) if session else page_signature(before,index)
            new_sig=page_signature(after,index,excluded_names=names)
            if old_sig[1:]!=new_sig[1:]:raise ValueError('Original drawing/text/image resources changed.')
        else:
            old_sig=session.signature(index,allow_edges) if session else page_signature(before,index,allow_edges)
            if old_sig!=page_signature(after,index,allow_edges):
                raise ValueError(f'Page {index+1}: saved handwriting/resource geometry changed.')
        if not session:
            with render(source,index) as old,render(candidate,index) as new:
                delta=visual_delta(old,new,allow_edges)
            exact &= delta['exact'];deltas.append({'page':index+1,**delta})
        if not session:print(f'Verified vector content/navigation/appearance: {index+1}/{len(before.pages)}',flush=True)
    if session:
        deltas=session.compare(candidate,pages,allow_edges)
        exact=all(delta['exact'] for delta in deltas)
        for index in pages:
            print(f'Verified vector content/navigation/appearance: {index+1}/{len(before.pages)}',flush=True)
    missing=before.missing_optional_content
    if not session:del before
    del after;gc.collect()
    return {'exact':exact,'pages':deltas,'normalized_missing_OC':missing,'full':full,
            'checks':{'navigation':True,'ordered_geometry_and_resources':True,'pdfium_144_dpi':True}}


def runtime_node():
    explicit=os.environ.get('PDF_NODE')
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    requested=explicit or (str(bundled) if bundled.is_file() else 'node')
    node=shutil.which(requested) or (requested if Path(requested).is_file() else None)
    if not node:raise ValueError('Node.js is missing. Run Setup Dependencies.cmd, then try again.')
    return str(Path(node).resolve())


def node_identity(node,budget=None):
    version=_run([node,'--version'],30,budget,'Node.js version check',capture_output=True,text=True,
                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if version.returncode:raise ValueError('Node.js version check failed: '+version.stderr[-500:])
    return {'path':node,'version':version.stdout.strip(),'sha256':filehash(node)}


def qpdf_identity(budget=None):
    requested=os.environ.get('PDF_QPDF') or 'qpdf'
    executable=shutil.which(requested)
    if not executable:raise ValueError('qpdf is unavailable; install it or set PDF_QPDF.')
    version=_run([executable,'--version'],30,budget,'qpdf version check',capture_output=True,text=True,
                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if version.returncode:raise ValueError('qpdf version preflight failed: '+version.stderr[-500:])
    executable_hash=filehash(executable) if Path(executable).is_file() else hashlib.sha256(executable.encode()).hexdigest()
    return executable,version.stdout.strip().splitlines()[0],executable_hash


def build_qpdf_candidate(source,destination,budget=None,identity=None):
    executable,version_text,_=identity or qpdf_identity(budget)
    command=[executable,str(source),'--recompress-flate','--compression-level=9',
             '--object-streams=generate','--stream-data=compress',str(destination)]
    child=_run(command,1800,budget,'qpdf candidate generation',capture_output=True,text=True,
               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if child.returncode not in (0,3):raise ValueError('qpdf failed: '+child.stderr[-1500:])
    if not Path(destination).is_file():raise ValueError('qpdf produced no output.')
    return version_text


def benchmark_worker(path,page,mode,width=1404,budget=None,node=None):
    command=[node or runtime_node(),str(Path(__file__).with_name('pdfjs_benchmark.mjs')),str(Path(path).absolute()),str(page),mode,str(width)]
    child=_run(command,300,budget,'PDF.js benchmark',capture_output=True,text=True,
               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if child.returncode:raise ValueError('PDF.js benchmark failed: '+child.stderr[-1500:])
    return json.loads(child.stdout.strip().splitlines()[-1])


def benchmark_pages(source):
    reader=reader_for(source);cache={};active=set()
    def cost(stream):
        stream=stream.get_object()
        key=id(stream)
        if key in cache:return cache[key][1]
        if key in active:raise ValueError('Cyclic drawing graph in density measurement.')
        active.add(key)
        data=stream.get_data();total=len(data)
        resources=stream.get('/Resources',{})
        if hasattr(resources,'get_object'):resources=resources.get_object()
        objects=resources.get('/XObject',{})
        if hasattr(objects,'get_object'):objects=objects.get_object()
        for name in re.findall(rb'/([^\s/]+)\s+Do\b',data):
            child=objects.get('/'+name.decode('latin1'))
            if child is not None:
                child=child.get_object()
                if child.get('/Subtype')=='/Form':total+=cost(child)
                elif child.get('/Subtype')=='/Image':
                    total+=int(child.get('/Width',0))*int(child.get('/Height',0))
        active.remove(key);cache[key]=(stream,total)
        if hasattr(stream,'decoded_self'):stream.decoded_self=None
        return total
    density=[]
    for page in reader.pages:
        content=page.get_contents()
        if content is not None:
            content[NameObject('/Resources')]=page.get('/Resources',{})
        value=cost(content) if content is not None else 0
        for ref in (page['/Annots'] if '/Annots' in page else []):
            appearance=ref.get_object().get('/AP',{}).get('/N')
            if appearance is not None and hasattr(appearance.get_object(),'get_data'):value+=cost(appearance)
        density.append(value)
    count=len(density)
    samples=[1,3,6] if count==6 else [1,min(7,count),min(29,count)]
    return sorted(set(samples+[density.index(max(density))+1]))


def benchmarks(paths,pages,runs=3,results=None,checkpoint=None,budget=None,node=None):
    results=[] if results is None else results
    def measure(path,page,mode,run):
        if any(Path(r['file'])==Path(path).absolute() and r['page']==page and r['mode']==mode and r['run']==run+1 for r in results):return
        if budget:budget.check('timing',paths)
        record=benchmark_worker(path,page,mode,budget=budget,node=node);record['run']=run+1;results.append(record)
        if checkpoint:checkpoint(results)
    for page in pages:
        for run in range(runs):
            for path in (paths if run%2==0 else list(reversed(paths))):
                for mode in ['render','prepare']:
                    measure(path,page,mode,run)
                print(f'Benchmarked {Path(path).name}, page {page}, run {run+1}/{runs}',flush=True)
    # Re-run the complete comparison if three samples vary substantially.
    if runs==3:
        values={}
        for r in results:
            if r['mode']=='render':values.setdefault((r['file'],r['page']),[]).append(r['cold']['cpu'])
        if any(max(v)>min(v)*1.25 for v in values.values()):
            for page in pages:
                for run in [3,4]:
                    for path in (paths if run%2==0 else list(reversed(paths))):
                        for mode in ['render','prepare']:
                            measure(path,page,mode,run)
                        print(f'Noise check: {Path(path).name}, page {page}, run {run+1}/5',flush=True)
    return results


def verify_pdfjs_page(source,candidate,page,allow_edges=False,budget=None,node=None):
    command=[node or runtime_node(),'--expose-gc',str(Path(__file__).with_name('pdfjs_benchmark.mjs')),str(source),str(page),'compare','1404',str(candidate)]
    if allow_edges:command.append('edges')
    child=_run(command,300,budget,f'PDF.js page {page} acceptance',capture_output=True,text=True,
               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if child.returncode:raise ValueError(f'PDF.js page {page} changed: '+child.stdout[-500:]+child.stderr[-500:])
    return json.loads(child.stdout.strip().splitlines()[-1])


def verify_pdfjs(source,candidate,count,allow_edges=False,budget=None,node=None,workers=None):
    workers=min(count,verification_resources(workers)['effective_workers'])
    groups=[list(range(start,count+1,workers)) for start in range(1,workers+1)]
    processes=[]
    def run_group(child,pages):
        if budget:budget.check('final PDF.js acceptance',(source,candidate))
        try:
            output,error=child.communicate(''.join(f'{page}\n' for page in pages),
                                           timeout=budget.timeout(300*len(pages)) if budget else 300*len(pages))
        except subprocess.TimeoutExpired as exc:
            child.terminate();child.wait()
            raise ValueError('PDF.js verification worker timed out.') from exc
        if child.returncode:raise ValueError('PDF.js verification worker failed: '+error[-1500:])
        if budget:budget.check('final PDF.js acceptance',(source,candidate))
        rows=[]
        for line in output.splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if 'page' in row:rows.append(row)
        if len(rows)!=len(pages):raise ValueError('PDF.js verification worker returned a missing result.')
        for row in rows:
            if row.get('error') or not row.get('ok'):
                raise ValueError(f"PDF.js page {row.get('page')} changed: {row.get('error','appearance guard failed')}")
        return rows
    try:
        for _group in groups:
            command=[node or runtime_node(),'--expose-gc',str(Path(__file__).with_name('pdfjs_benchmark.mjs')),
                     str(source),str(candidate),'compare-worker','1404']
            if allow_edges:command.append('edges')
            processes.append(subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE,text=True,
                                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        pool=ThreadPoolExecutor(max_workers=workers)
        futures=[pool.submit(run_group,child,group) for child,group in zip(processes,groups)]
        try:records=[row for future in futures for row in future.result()]
        except BaseException:
            for child in processes:
                if child.poll() is None:child.terminate()
            pool.shutdown(wait=True,cancel_futures=True)
            raise
        pool.shutdown(wait=True)
    except BaseException:
        for child in processes:
            if child.poll() is None:child.terminate()
        for child in processes:child.wait()
        raise
    records.sort(key=lambda row:row['page'])
    if [row['page'] for row in records]!=list(range(1,count+1)):
        raise ValueError('PDF.js verification results were missing or duplicated.')
    for page in range(1,count+1):print(f'PDF.js appearance verified: {page}/{count}',flush=True)
    return records


def medians(results,path):
    records=[r for r in results if Path(r['file'])==Path(path).absolute() and r['mode']=='render']
    values={}
    for page in sorted({r['page'] for r in records}):
        rows=[r for r in records if r['page']==page]
        values[page]={key:statistics.median(r[stage][metric] for r in rows)
                      for key,stage,metric in [('cpu','cold','cpu'),('cold','cold','wall'),('cached','cached','wall')]}
    return values


def choose_candidate(source,candidates,results):
    baseline=medians(results,source);base_cpu=sum(v['cpu'] for v in baseline.values())
    eligible=[]
    for candidate in candidates:
        measured=medians(results,candidate['path'])
        if not measured or set(measured)!=set(baseline):continue
        regression=any(measured[p][key]>baseline[p][key]+max(baseline[p][key]*.1,.05)
                       for p in baseline for key in ['cold','cached'])
        cpu=sum(v['cpu'] for v in measured.values());speedup=1-cpu/base_cpu if base_cpu else 0
        candidate.update({'median':measured,'cpu_reduction':speedup,'regression':regression,
                          'cpu_resolution_limited':not base_cpu})
        if not regression and (speedup>=.1 or (candidate['exact'] and Path(candidate['path']).stat().st_size<Path(source).stat().st_size and cpu<=base_cpu*1.05)):
            eligible.append(candidate)
    if not eligible:return None
    best=min(sum(v['cpu'] for v in c['median'].values()) for c in eligible)
    close=[c for c in eligible if sum(v['cpu'] for v in c['median'].values())<=best*1.05]
    return min(close,key=lambda c:(not c['exact'],Path(c['path']).stat().st_size))


def build_candidate(source,destination,kind):
    from handwriting import inline_handwriting,rewrite_handwriting
    reader=reader_for(source)
    writer=PdfWriter(clone_from=reader)
    original_pages=[dict(page) for page in writer.pages]
    manifest={'converted':{},'evidence':[],'count':0,'kind':kind}
    if kind in {'annotations','consolidated','combined'}:
        manifest.update(flatten_ink(writer))
        if not manifest['count']:raise ValueError('No eligible leading BOOX ink annotations.')
        if kind in {'consolidated','combined'}:consolidate_ink(writer,manifest)
        if kind=='combined':inline_handwriting(writer)
    elif kind in {'inline','batched'}:
        if kind=='batched':
            # A local pen state does not clear an inherited soft mask.
            pending=list(writer._objects);seen=set()
            while pending:
                obj=pending.pop()
                if not isinstance(obj,(dict,list)) or id(obj) in seen:continue
                seen.add(id(obj))
                if isinstance(obj,dict):
                    if ('/Group' in obj or obj.get('/CA',1)!=1 or obj.get('/ca',1)!=1 or
                        obj.get('/SMask','/None')!='/None' or obj.get('/BM','/Normal')!='/Normal'):
                        raise ValueError('Batching skipped: transparency requires retaining separate paint operations.')
                    pending.extend(obj.values())
                else:pending.extend(obj)
            del pending,seen,obj
            rewrite_handwriting(writer)
        inline_handwriting(writer)
    baseline_navigation=navigation_fingerprint(reader,{int(k):v for k,v in manifest.get('converted',{}).items()})
    unmerged=Path(destination).with_suffix('.unmerged.pdf')
    writer.write(unmerged)
    if manifest.get('converted'):
        restored=[]
        for index in list(manifest['converted']):
            try:
                with render(source,index) as a,render(unmerged,index) as b:visual_delta(a,b,True)
                verify_pdfjs_page(source,unmerged,index+1,True)
            except ValueError as exc:
                writer.pages[index].clear();writer.pages[index].update(original_pages[index])
                restored.append(index)
                manifest['skipped'].append({'page':index+1,'reason':'Original annotations restored: '+str(exc)})
        if restored:
            for index in restored:manifest['converted'].pop(index,None)
            manifest['evidence']=[e for e in manifest['evidence'] if e['page'] not in restored]
            manifest['consolidated']=[e for e in manifest.get('consolidated',[]) if e['page'] not in restored]
            manifest['count']=sum(map(len,manifest['converted'].values()))
            if not manifest['count']:raise ValueError('No annotation conversion passed appearance checks.')
            writer.write(unmerged)
            baseline_navigation=navigation_fingerprint(reader,{int(k):v for k,v in manifest['converted'].items()})
    # Page snapshots retain their writer (and its source reader) through indirect references.
    original_pages.clear()
    writer.compress_identical_objects(remove_duplicates=True,remove_unreferenced=True)
    # Navigation verification rejects merges of independently interactive identities.
    first=Path(destination).with_suffix('.first.pdf')
    writer.write(first);writer.close();del writer,reader;gc.collect()
    selected=first
    merged_reader=unmerged_reader=None
    try:
        merged_reader=reader_for(first)
        if navigation_fingerprint(merged_reader)!=baseline_navigation:raise ValueError('Protected navigation changed.')
        unmerged_reader=reader_for(unmerged)
        for index in range(len(merged_reader.pages)):
            if page_signature(unmerged_reader,index)!=page_signature(merged_reader,index):raise ValueError('Deduplication altered drawing resources.')
        if manifest.get('consolidated'):verify_consolidated(merged_reader,manifest)
        else:verify_flattened(merged_reader,manifest.get('evidence',[]))
    except (ValueError,AttributeError,KeyError):
        selected=unmerged
        manifest['deduplication_skipped']='Merging altered protected navigation/identities.'
    finally:
        merged_reader=unmerged_reader=None
        gc.collect()
    with selected.open('rb') as stream:
        rebuilt=PdfWriter(clone_from=PdfReader(stream));rebuilt.write(destination);rebuilt.close()
    del rebuilt;gc.collect()
    if navigation_fingerprint(reader_for(destination))!=baseline_navigation:
        raise ValueError('Deduplication changed protected navigation/identities; candidate rejected.')
    first.unlink()
    unmerged.unlink()
    return manifest


def report_text(report):
    lines=['# Vector viewing-copy report','',f"Status: {report['status']}",f"Classification: {report.get('classification','no qualifying improvement')}",f"Original: {report['source']}",f"Original bytes: {report.get('source_bytes','see source file')}",f"Original SHA256: {report['source_sha256']}",'','OCR/search text retained. If ink annotations are converted, only the viewing copy loses their individual annotation editing. Originals remain unchanged.','', '| Candidate | Bytes | Exact tested appearance | CPU reduction | Regression |','| --- | ---: | --- | ---: | --- |']
    resources=report.get('verification_resources',{});reuse=report.get('source_evidence_reuse',{})
    lines[7:7]=['',f"Verification workers: requested={resources.get('requested_workers','default')}, effective={resources.get('effective_workers','unavailable')}",
                f"Conservative verification RAM estimate: {resources.get('estimated_ram_bytes',0)/1024**3:.1f} GiB (planning estimate, not a measured peak or limit).",
                f"Source evidence: {reuse.get('source_renders_created',0)} renders created, {reuse.get('source_renders_reused',0)} reused; {reuse.get('source_signatures_created',0)} signatures created, {reuse.get('source_signatures_reused',0)} reused."]
    for c in report.get('candidates',[]):
        reduction='unresolved (timer resolution)' if c.get('cpu_resolution_limited') else f"{c.get('cpu_reduction',0)*100:.1f}%"
        lines.append(f"| {c['kind']} | {c['bytes']} | {c.get('exact')} | {reduction} | {c.get('regression','unmeasured')} |")
    lines.extend(['','Rejected/skipped passes:']+[f"- {r['kind']}: {r['reason']}" for r in report.get('rejected',[])])
    rows=report.get('measurements',[])
    if rows:
        labels={str(Path(report['source']).absolute()):'original'}
        for c in report.get('candidates',[]):
            for row in rows:
                if Path(row['file']).name==c.get('benchmark_file',Path(c.get('path','')).name):labels[row['file']]=c['kind']
        lines.extend(['','Median fresh-process measurements (seconds; sampled RSS in MiB):','',
          '| Version / page | Load | Operator preparation | Cold render wall / CPU | Cached drawing | Cold wall range | Operators / annotations | RSS |',
          '| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |'])
        for filename in dict.fromkeys(r['file'] for r in rows):
            for page in sorted({r['page'] for r in rows if r['file']==filename}):
                rr=[r for r in rows if r['file']==filename and r['page']==page and r['mode']=='render']
                pp=[r for r in rows if r['file']==filename and r['page']==page and r['mode']=='prepare']
                if not rr or not pp:continue
                median=lambda records,stage,key:statistics.median(r[stage][key] for r in records)
                lines.append(f"| {labels.get(filename,Path(filename).stem)} / {page} | {median(rr,'load','wall'):.3f} | {median(pp,'prepare','wall'):.3f} | {median(rr,'cold','wall'):.3f} / {median(rr,'cold','cpu'):.3f} | {median(rr,'cached','wall'):.3f} | {min(r['cold']['wall'] for r in rr):.3f}–{max(r['cold']['wall'] for r in rr):.3f} | {pp[0]['operators']} / {pp[0]['annotations']['count']} | {statistics.median(r['rss'] for r in rr)/1048576:.1f} |")
    lines.extend(['','Benchmarks separate operator preparation, cold rendering, and cached drawing. They are not summed. Node CPU/RSS and raw individual runs are in the JSON. Browser acceptance is recorded separately.'])
    if report.get('stages'):
        lines.extend(['','Invocation stage wall times (include cache hits):']+
                     [f"- {row['stage']}: {row['wall_seconds']:.3f}s ({row['outcome']})" for row in report['stages']])
    return '\n'.join(lines)+'\n'


def optimize_viewing(source,destination,expected_source_sha256=None,checkpoint=None,
                     time_budget_seconds=None,disk_budget_bytes=None,verification_workers=None):
    try:import pymupdf
    except ImportError as exc:raise ValueError('PyMuPDF is missing. Run Setup Dependencies.cmd, then try again.') from exc
    source,destination=Path(source).expanduser().absolute(),Path(destination).expanduser().absolute()
    resources=verification_resources(verification_workers)
    if time_budget_seconds is not None and time_budget_seconds<=0:raise ValueError('Time budget must be positive.')
    if disk_budget_bytes is not None and disk_budget_bytes<=0:raise ValueError('Disk budget must be positive.')
    if not source.is_file():raise ValueError('Input must be a PDF file.')
    targets=[destination,destination.with_suffix('.json'),destination.with_suffix('.md')]
    if any(p.exists() or p.is_symlink() for p in targets):raise FileExistsError('Output/report already exists. Choose a new filename.')
    if not destination.parent.is_dir():raise ValueError('The output folder does not exist.')
    source_bytes=source.stat().st_size;source_hash=filehash(source)
    if expected_source_sha256 is not None:
        if not re.fullmatch(r'[0-9a-fA-F]{64}',expected_source_sha256):
            raise ValueError('Expected source SHA256 must be 64 hexadecimal characters.')
        if source_hash != expected_source_sha256.lower():
            raise ValueError('Original does not match the reviewed source hash.')
    node=runtime_node()
    checkpoint=Path(checkpoint).expanduser().absolute() if checkpoint else destination.with_suffix('.checkpoint')
    if checkpoint.is_symlink():raise ValueError('Saved-progress folder is a shortcut. Rename or remove it, then try again.')
    if checkpoint.exists() and not checkpoint.is_dir():raise FileExistsError('Checkpoint path is not a directory.')
    if checkpoint.parent.resolve()!=destination.parent.resolve():raise ValueError('Checkpoint must stay beside the reviewed output.')
    budget=RunBudget(time_budget_seconds,disk_budget_bytes,checkpoint)
    budget.check('renderer preflight')
    print('Stage: renderer preflight',flush=True)
    node_runtime=node_identity(node,budget)
    benchmark_worker(source,1,'preflight',budget=budget,node=node)
    if filehash(source)!=source_hash:raise ValueError('Original changed during renderer preflight.')
    code_hash=_code_hash()
    run_identity={'schema':CHECKPOINT_SCHEMA,'source_sha256':source_hash,'code_sha256':code_hash,
                  'python':sys.version.split()[0],'pypdf':_version('pypdf'),
                  'pypdfium2':_version('pypdfium2'),'pymupdf':_version('pymupdf')}
    run_key=hashlib.sha256(json.dumps(run_identity,sort_keys=True).encode()).hexdigest()[:16]
    checkpoint=checkpoint/run_key
    checkpoint.mkdir(parents=True,exist_ok=True)
    identity_path=checkpoint/'context.json'
    if not identity_path.exists():identity_path.write_text(json.dumps(run_identity,sort_keys=True),encoding='utf-8')
    cache=EvidenceCache(checkpoint/'operations')
    budget.checkpoint=checkpoint
    stage_name=None;stage_started=None;stage_records=[]
    def finish_stage(outcome='complete'):
        nonlocal stage_name,stage_started
        if stage_name is None:return
        elapsed=time.monotonic()-stage_started
        stage_records.append({'stage':stage_name,'outcome':outcome,'wall_seconds':elapsed})
        temporary=checkpoint/'.state.tmp'
        temporary.write_text(json.dumps({'schema':CHECKPOINT_SCHEMA,'status':'running','stage':stage_name,
                                         'stage_outcome':outcome,'stage_elapsed_seconds':elapsed,
                                         'source_sha256':source_hash}),encoding='utf-8')
        os.replace(temporary,checkpoint/'state.json')
        stage_name=stage_started=None
    def stage(name,paths=()):
        nonlocal stage_name,stage_started
        finish_stage()
        budget.check(name,paths)
        stage_name=name;stage_started=time.monotonic()
        print(f'Stage: {name}',flush=True)
        temporary=checkpoint/'.state.tmp'
        temporary.write_text(json.dumps({'schema':CHECKPOINT_SCHEMA,'status':'running','stage':name,
                                         'stage_started':time.time(),'source_sha256':source_hash}),encoding='utf-8')
        os.replace(temporary,checkpoint/'state.json')
    reader=reader_for(source)
    validate_input(reader)
    root=reader.trailer['/Root']
    if '/AcroForm' in root and '/XFA' in root['/AcroForm']:
        raise ValueError('XFA form behavior cannot be verified in viewing-copy mode; retain the original.')
    has_ink=any(ref.get_object().get('/Name')=='/#ONYX-STROKE' or '/onyxtag' in ref.get_object()
                for page in reader.pages for ref in (page['/Annots'] if '/Annots' in page else []))
    count=len(reader.pages)
    del reader;gc.collect()
    pages=benchmark_pages(source)
    report={'status':'no improvement','source':str(source),'source_bytes':source_bytes,'source_sha256':source_hash,'pages':count,'benchmark_pages':pages,'candidates':[],'rejected':[],
            'checkpoint':{'schema':CHECKPOINT_SCHEMA,'path':str(checkpoint),'resumed':False},
            'budgets':{'time_seconds':time_budget_seconds,'disk_bytes':disk_budget_bytes},
            'verification_resources':resources,'stage_wall_seconds':{},
            'source_evidence_reuse':{'source_renders_created':0,'source_renders_reused':0,
                                     'source_signatures_created':0,'source_signatures_reused':0}}
    with tempfile.TemporaryDirectory(prefix='pdf-viewing-',dir=destination.parent) as temporary:
        valid=[];verification_session=None
        evidence_stats={key:0 for key in report['source_evidence_reuse']}
        def session():
            nonlocal verification_session
            if verification_session is None:
                verification_session=VerificationSession(source,Path(temporary)/'source-evidence',
                                                         resources['effective_workers'],budget)
            return verification_session
        def retire_session(force=False):
            nonlocal verification_session
            if verification_session is None:return
            for key,value in verification_session.stats.items():evidence_stats[key]+=value
            verification_session.close(force=force);verification_session=None
        def failure_budget(name,paths):
            try:budget.check(name,paths)
            except (TimeBudgetExceeded,DiskBudgetExceeded):
                retire_session(True)
                raise
        kinds=['dedup']+(['annotations','consolidated','combined'] if has_ink else [])+['inline','batched']
        if not has_ink:report['rejected'].append({'kind':'annotation conversion','reason':'No BOOX handwriting annotations detected.'})
        for kind in kinds:
            path=Path(temporary)/(kind+'.pdf')
            try:
                stage(f'candidate generation: {kind}',(Path(temporary),))
                recipe=hashlib.sha256(json.dumps({'kind':kind,'code':code_hash},sort_keys=True).encode()).hexdigest()
                file_context=_context('production-candidate-file',source_hash,recipe,'repository',code_hash,{'kind':kind})
                meta_context=_context('production-candidate-complete',source_hash,recipe,'repository',code_hash,{'kind':kind})
                metadata=cache.load_json(meta_context)
                reused=metadata is not None and cache.materialize(file_context,path)
                if reused and filehash(path)==metadata.get('candidate_sha256'):
                    manifest=metadata['manifest'];report['checkpoint']['resumed']=True
                    print(f'Reused completed candidate: {kind}',flush=True)
                else:
                    path.unlink(missing_ok=True)
                    print(f'Preparing candidate: {kind}',flush=True)
                    manifest=build_candidate(source,path,kind)
                    candidate_hash=_store_candidate(cache,file_context,path)
                    cache.store_json(meta_context,{'candidate_sha256':candidate_hash,'manifest':manifest})
                gc.collect()
                stage(f'preservation verification: {kind}',(Path(temporary),))
                candidate_hash=filehash(path);allow_edges=kind in {'batched','annotations','consolidated','combined'}
                verify_context=_context('production-preservation',source_hash,candidate_hash,'pdfium',_version('pypdfium2'),
                                        {'kind':kind,'allow_edges':allow_edges,'manifest':hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest(),'code':code_hash})
                verification=cache.load_json(verify_context)
                if verification is None:
                    print(f'Checking candidate: {kind}',flush=True)
                    verification=verify_candidate(source,path,manifest,allow_edges,full=True,session=session())
                    cache.store_json(verify_context,verification)
                else:
                    report['checkpoint']['resumed']=True
                    print(f'Reused completed preservation check: {kind}',flush=True)
                item={'kind':kind,'path':str(path),'bytes':path.stat().st_size,'exact':verification['exact'],'manifest':manifest,'verification':verification}
                valid.append(item)
            except Exception as exc:
                finish_stage('interrupted' if isinstance(exc,(TimeBudgetExceeded,DiskBudgetExceeded)) else 'failed')
                if verification_session and verification_session.pool is None:retire_session(True)
                failure_budget(f'candidate generation or verification: {kind}',(Path(temporary),))
                if isinstance(exc,(TimeBudgetExceeded,DiskBudgetExceeded)):
                    retire_session(True)
                    raise
                report['rejected'].append({'kind':kind,'reason':str(exc)})
                print(f'Skipped {kind}: {exc}',flush=True)
            finally:gc.collect()
        # Compression-only candidate keeps the original document graph and stream contents.
        packing_inputs=[(source,{'kind':'packed-original','manifest':{'converted':{},'evidence':[]},'exact':True})]+[(Path(c['path']),c) for c in valid]
        for path,item in packing_inputs:
            packed=Path(temporary)/(item['kind']+'-packed.pdf')
            try:
                stage(f'candidate generation: {item["kind"]}-packed',(Path(temporary),))
                input_hash=filehash(path)
                recipe=hashlib.sha256(json.dumps({'kind':item['kind']+'-packed','input':input_hash,
                                                   'pymupdf':_version('pymupdf')},sort_keys=True).encode()).hexdigest()
                file_context=_context('production-candidate-file',source_hash,recipe,'pymupdf',_version('pymupdf'),{'kind':item['kind']+'-packed'})
                meta_context=_context('production-candidate-complete',source_hash,recipe,'pymupdf',_version('pymupdf'),{'kind':item['kind']+'-packed'})
                metadata=cache.load_json(meta_context)
                reused=metadata is not None and cache.materialize(file_context,packed)
                if not reused or filehash(packed)!=metadata.get('candidate_sha256'):
                    packed.unlink(missing_ok=True)
                    with pymupdf.open(path) as document:document.save(packed,garbage=0,deflate=True,use_objstms=1,clean=False,no_new_id=True)
                    candidate_hash=_store_candidate(cache,file_context,packed)
                    cache.store_json(meta_context,{'candidate_sha256':candidate_hash})
                else:report['checkpoint']['resumed']=True
                if packed.stat().st_size>=path.stat().st_size:continue
                stage(f'preservation verification: {item["kind"]}-packed',(Path(temporary),))
                packed_hash=filehash(packed)
                verify_context=_context('production-preservation',source_hash,packed_hash,'pdfium',_version('pypdfium2'),
                                        {'kind':item['kind']+'-packed','allow_edges':not item['exact'],'code':code_hash})
                check=cache.load_json(verify_context)
                if check is None:
                    check=verify_candidate(source,packed,item['manifest'],not item['exact'],full=True,session=session())
                    cache.store_json(verify_context,check)
                else:report['checkpoint']['resumed']=True
                valid.append({**item,'kind':item['kind']+'-packed','path':str(packed),'bytes':packed.stat().st_size,'exact':check['exact'],'verification':check})
            except Exception as exc:
                finish_stage('interrupted' if isinstance(exc,(TimeBudgetExceeded,DiskBudgetExceeded)) else 'failed')
                if verification_session and verification_session.pool is None:retire_session(True)
                failure_budget(f'candidate generation or verification: {item["kind"]}-packed',(Path(temporary),))
                if isinstance(exc,(TimeBudgetExceeded,DiskBudgetExceeded)):
                    retire_session(True)
                    raise
                report['rejected'].append({'kind':item['kind']+'-packed','reason':str(exc)})
        qpdf=Path(temporary)/'qpdf-flate.pdf'
        try:
            stage('candidate generation: qpdf-flate',(Path(temporary),))
            identity=qpdf_identity(budget);executable,version,executable_hash=identity
            recipe=hashlib.sha256(json.dumps({'kind':'qpdf-flate','executable':executable_hash,'version':version},sort_keys=True).encode()).hexdigest()
            file_context=_context('production-candidate-file',source_hash,recipe,'qpdf',version,{'kind':'qpdf-flate','tool_sha256':executable_hash})
            meta_context=_context('production-candidate-complete',source_hash,recipe,'qpdf',version,{'kind':'qpdf-flate','tool_sha256':executable_hash})
            metadata=cache.load_json(meta_context)
            reused=metadata is not None and cache.materialize(file_context,qpdf)
            if not reused or filehash(qpdf)!=metadata.get('candidate_sha256'):
                qpdf.unlink(missing_ok=True)
                build_qpdf_candidate(source,qpdf,budget,identity)
                candidate_hash=_store_candidate(cache,file_context,qpdf)
                cache.store_json(meta_context,{'candidate_sha256':candidate_hash})
            else:report['checkpoint']['resumed']=True
            stage('preservation verification: qpdf-flate',(Path(temporary),))
            qpdf_hash=filehash(qpdf)
            verify_context=_context('production-preservation',source_hash,qpdf_hash,'pdfium',_version('pypdfium2'),
                                    {'kind':'qpdf-flate','allow_edges':False,'code':code_hash})
            check=cache.load_json(verify_context)
            if check is None:
                check=verify_candidate(source,qpdf,{'converted':{},'evidence':[]},full=True,session=session())
                cache.store_json(verify_context,check)
            else:report['checkpoint']['resumed']=True
            valid.append({'kind':'qpdf-flate','path':str(qpdf),'bytes':qpdf.stat().st_size,
                          'exact':check['exact'],'manifest':{'converted':{},'evidence':[]},
                          'verification':check,'tool_version':version})
        except Exception as exc:
            finish_stage('interrupted' if isinstance(exc,(TimeBudgetExceeded,DiskBudgetExceeded)) else 'failed')
            if verification_session and verification_session.pool is None:retire_session(True)
            failure_budget('candidate generation or verification: qpdf-flate',(Path(temporary),))
            if isinstance(exc,(TimeBudgetExceeded,DiskBudgetExceeded)):
                retire_session(True)
                raise
            report['rejected'].append({'kind':'qpdf-flate','reason':str(exc)})
        if verification_session:
            retire_session()
        report['source_evidence_reuse']=evidence_stats
        if valid:
            stage('balanced performance timing',(Path(temporary),))
            timing_paths=[source]+[Path(c['path']) for c in valid]
            timing_hash=hashlib.sha256(json.dumps([(c['kind'],filehash(c['path'])) for c in valid],sort_keys=True).encode()).hexdigest()
            lock=Path(__file__).with_name('package-lock.json')
            node_context={'pages':pages,'runs':3,'order':[c['kind'] for c in valid],
                          'node':node_runtime,'package_lock':filehash(lock) if lock.is_file() else None,'code':code_hash}
            timing_context=_context('production-timing',source_hash,timing_hash,'pdfjs-node',node_runtime['version'],node_context)
            measured=cache.load_json(timing_context)
            if measured is None:
                measured=benchmarks(timing_paths,pages,budget=budget,node=node)
                roles={str(Path(source).absolute()):'source'} | {str(Path(c['path']).absolute()):c['kind'] for c in valid}
                cache.store_json(timing_context,[{**row,'file':roles[row['file']]} for row in measured])
            else:
                current={'source':str(Path(source).absolute())} | {c['kind']:str(Path(c['path']).absolute()) for c in valid}
                measured=[{**row,'file':current[row['file']]} for row in measured]
                report['checkpoint']['resumed']=True
            report['measurements']=measured
            chosen=choose_candidate(source,valid,measured)
            report['candidates']=[{k:v for k,v in c.items() if k not in {'path','manifest'}} | {'benchmark_file':Path(c['path']).name,'converted_annotations':c['manifest'].get('count',0)} for c in valid]
            while chosen:
                try:
                    stage(f'final acceptance: {chosen["kind"]}',(Path(temporary),))
                    acceptance={}
                    acceptance['pdfjs_all_pages']=verify_pdfjs(source,chosen['path'],count,not chosen['exact'],
                                                               budget,node,resources['effective_workers'])
                    for page in pages:
                        budget.check('high-resolution and print acceptance',(Path(temporary),))
                        with render(source,page-1,4) as a,render(chosen['path'],page-1,4) as b:visual_delta(a,b,not chosen['exact'])
                        with render(source,page-1,2,print_mode=True) as a,render(chosen['path'],page-1,2,print_mode=True) as b:visual_delta(a,b,not chosen['exact'])
                    acceptance['high_dpi_and_print_pages']=pages
                    detail_page,crop=detail_crop(source,pages)
                    budget.check('detail-crop acceptance',(Path(temporary),))
                    with render(source,detail_page-1,8,crop) as a,render(chosen['path'],detail_page-1,8,crop) as b:delta=visual_delta(a,b,not chosen['exact'])
                    acceptance['detail_crop']={'page':detail_page,'crop':crop,'dpi':576,**delta}
                    from browser_check import check_browser
                    budget.check('browser acceptance',(Path(temporary),))
                    acceptance['browser']=check_browser(source,chosen['path'],pages,budget.timeout(1200))
                    report.update(acceptance)
                    break
                except Exception as exc:
                    finish_stage('interrupted' if isinstance(exc,(TimeBudgetExceeded,DiskBudgetExceeded)) else 'failed')
                    budget.check(f'final acceptance: {chosen["kind"]}',(Path(temporary),))
                    if isinstance(exc,(TimeBudgetExceeded,DiskBudgetExceeded)):raise
                    report['rejected'].append({'kind':chosen['kind'],'reason':'Final renderer/browser acceptance: '+str(exc)})
                    valid.remove(chosen)
                    chosen=choose_candidate(source,valid,measured)
            if chosen:
                if filehash(source)!=report['source_sha256']:raise ValueError('Original changed during processing.')
                report.update({'status':'optimized','classification':'faster' if chosen['cpu_reduction']>=.1 else 'storage-only','selected':chosen['kind'],'output':str(destination),'output_sha256':filehash(chosen['path'])})
                report['selected_transformations']={k:chosen['manifest'][k] for k in
                    ['kind','count','converted','skipped','deduplication_skipped'] if k in chosen['manifest']}
        if filehash(source)!=report['source_sha256']:raise ValueError('Original changed during processing.')
        stage('publication',(Path(temporary),))
        report['checkpoint']['stats']=dict(cache.stats)
        created=[]
        try:
            if report['status']=='optimized':
                if expected_source_sha256 and filehash(source)!=expected_source_sha256.lower():raise ValueError('Original changed after review.')
                with destination.open('xb') as out:
                    created.append(destination)
                    with Path(chosen['path']).open('rb') as inp:shutil.copyfileobj(inp,out)
                if filehash(destination)!=report['output_sha256']:raise ValueError('Published copy failed its checksum; output removed.')
            finish_stage()
            report['stages']=stage_records
            report['stage_wall_seconds']={row['stage']:row['wall_seconds'] for row in stage_records}
            for suffix,body in [('.json',json.dumps(report,indent=2)),('.md',report_text(report))]:
                if expected_source_sha256 and filehash(source)!=expected_source_sha256.lower():raise ValueError('Original changed after review.')
                target=destination.with_suffix(suffix)
                with target.open('x',encoding='utf-8') as stream:
                    created.append(target);stream.write(body)
        except BaseException:
            for target in created:target.unlink(missing_ok=True)
            raise
    state_path=checkpoint/'state.json';temporary_state=checkpoint/'.state.tmp'
    temporary_state.write_text(json.dumps({'schema':CHECKPOINT_SCHEMA,'status':report['status'],
                                           'stage':'complete','stage_started':time.time(),
                                           'source_sha256':source_hash}),encoding='utf-8')
    os.replace(temporary_state,state_path)
    print(f"Viewing-copy result: {report['status']}",flush=True)
    return source.stat().st_size,destination.stat().st_size if destination.exists() else source.stat().st_size
