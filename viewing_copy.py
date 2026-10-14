"""Build, verify and benchmark optional vector viewing copies."""
import gc
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
from PIL import ImageChops, ImageFilter, ImageStat
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
import pypdfium2 as pdfium
from navigation import navigation_fingerprint, reader_for, validate_input
from annotation_ink import flatten_ink, consolidate_ink, verify_flattened, verify_consolidated
from geometry import page_signature


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


def verify_candidate(source,candidate,manifest,allow_edges=False,full=True):
    before,after=reader_for(source),reader_for(candidate)
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
            old_sig=page_signature(before,index);new_sig=page_signature(after,index,excluded_names=names)
            if old_sig[1:]!=new_sig[1:]:raise ValueError('Original drawing/text/image resources changed.')
        else:
            if page_signature(before,index,allow_edges)!=page_signature(after,index,allow_edges):
                raise ValueError(f'Page {index+1}: saved handwriting/resource geometry changed.')
        with render(source,index) as old,render(candidate,index) as new:
            delta=visual_delta(old,new,allow_edges)
        exact &= delta['exact'];deltas.append({'page':index+1,**delta})
        print(f'Verified vector content/navigation/appearance: {index+1}/{len(before.pages)}',flush=True)
    missing=before.missing_optional_content
    del before,after;gc.collect()
    return {'exact':exact,'pages':deltas,'normalized_missing_OC':missing,'full':full,
            'checks':{'navigation':True,'ordered_geometry_and_resources':True,'pdfium_144_dpi':True}}


def runtime_node():
    explicit=os.environ.get('PDF_NODE')
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    node=explicit or (str(bundled) if bundled.is_file() else shutil.which('node'))
    if not node:raise ValueError('Viewing-copy benchmarking requires Node.js, pdfjs-dist and @napi-rs/canvas.')
    return node


def benchmark_worker(path,page,mode,width=1404):
    command=[runtime_node(),str(Path(__file__).with_name('pdfjs_benchmark.mjs')),str(Path(path).absolute()),str(page),mode,str(width)]
    child=subprocess.run(command,capture_output=True,text=True,timeout=300)
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


def benchmarks(paths,pages,runs=3,results=None,checkpoint=None):
    results=[] if results is None else results
    def measure(path,page,mode,run):
        if any(Path(r['file'])==Path(path).absolute() and r['page']==page and r['mode']==mode and r['run']==run+1 for r in results):return
        record=benchmark_worker(path,page,mode);record['run']=run+1;results.append(record)
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


def verify_pdfjs_page(source,candidate,page,allow_edges=False):
    command=[runtime_node(),'--expose-gc',str(Path(__file__).with_name('pdfjs_benchmark.mjs')),str(source),str(page),'compare','1404',str(candidate)]
    if allow_edges:command.append('edges')
    child=subprocess.run(command,capture_output=True,text=True,timeout=300)
    if child.returncode:raise ValueError(f'PDF.js page {page} changed: '+child.stdout[-500:]+child.stderr[-500:])
    return json.loads(child.stdout.strip().splitlines()[-1])


def verify_pdfjs(source,candidate,count,allow_edges=False):
    records=[]
    for page in range(1,count+1):
        records.append(verify_pdfjs_page(source,candidate,page,allow_edges))
        print(f'PDF.js appearance verified: {page}/{count}',flush=True)
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
    return '\n'.join(lines)+'\n'


def optimize_viewing(source,destination):
    try:import pymupdf
    except ImportError as exc:raise ValueError('Viewing-copy mode requires PyMuPDF: python -m pip install "pymupdf>=1.28"') from exc
    source,destination=Path(source).expanduser().absolute(),Path(destination).expanduser().absolute()
    if not source.is_file():raise ValueError('Input must be a PDF file.')
    targets=[destination,destination.with_suffix('.json'),destination.with_suffix('.md')]
    if any(p.exists() or p.is_symlink() for p in targets):raise FileExistsError('Output/report already exists. Choose a new filename.')
    if not destination.parent.is_dir():raise ValueError('The output folder does not exist.')
    source_bytes=source.stat().st_size;source_hash=filehash(source)
    runtime_node()
    reader=reader_for(source)
    validate_input(reader)
    root=reader.trailer['/Root']
    if '/AcroForm' in root and '/XFA' in root['/AcroForm']:
        raise ValueError('XFA form behavior cannot be verified in viewing-copy mode; retain the original.')
    has_ink=any(ref.get_object().get('/Name')=='/#ONYX-STROKE' or '/onyxtag' in ref.get_object()
                for page in reader.pages for ref in (page['/Annots'] if '/Annots' in page else []))
    benchmark_worker(source,1,'preflight')
    count=len(reader.pages)
    del reader;gc.collect()
    pages=benchmark_pages(source)
    report={'status':'no improvement','source':str(source),'source_bytes':source_bytes,'source_sha256':source_hash,'pages':count,'benchmark_pages':pages,'candidates':[],'rejected':[]}
    with tempfile.TemporaryDirectory(prefix='pdf-viewing-',dir=destination.parent) as temporary:
        valid=[]
        kinds=['dedup']+(['annotations','consolidated','combined'] if has_ink else [])+['inline','batched']
        if not has_ink:report['rejected'].append({'kind':'annotation conversion','reason':'No BOOX handwriting annotations detected.'})
        for kind in kinds:
            path=Path(temporary)/(kind+'.pdf')
            try:
                print(f'Preparing candidate: {kind}',flush=True)
                manifest=build_candidate(source,path,kind)
                gc.collect()
                print(f'Checking candidate: {kind}',flush=True)
                verification=verify_candidate(source,path,manifest,kind in {'batched','annotations','consolidated','combined'},full=True)
                item={'kind':kind,'path':str(path),'bytes':path.stat().st_size,'exact':verification['exact'],'manifest':manifest,'verification':verification}
                valid.append(item)
            except Exception as exc:
                report['rejected'].append({'kind':kind,'reason':str(exc)})
                print(f'Skipped {kind}: {exc}',flush=True)
            finally:gc.collect()
        # Compression-only candidate keeps the original document graph and stream contents.
        packing_inputs=[(source,{'kind':'packed-original','manifest':{'converted':{},'evidence':[]},'exact':True})]+[(Path(c['path']),c) for c in valid]
        for path,item in packing_inputs:
            packed=Path(temporary)/(item['kind']+'-packed.pdf')
            try:
                with pymupdf.open(path) as document:document.save(packed,garbage=0,deflate=True,use_objstms=1,clean=False,no_new_id=True)
                if packed.stat().st_size>=path.stat().st_size:continue
                check=verify_candidate(source,packed,item['manifest'],not item['exact'],full=True)
                valid.append({**item,'kind':item['kind']+'-packed','path':str(packed),'bytes':packed.stat().st_size,'exact':check['exact'],'verification':check})
            except Exception as exc:report['rejected'].append({'kind':item['kind']+'-packed','reason':str(exc)})
        if valid:
            measured=benchmarks([source]+[Path(c['path']) for c in valid],pages)
            report['measurements']=measured
            chosen=choose_candidate(source,valid,measured)
            report['candidates']=[{k:v for k,v in c.items() if k not in {'path','manifest'}} | {'benchmark_file':Path(c['path']).name,'converted_annotations':c['manifest'].get('count',0)} for c in valid]
            while chosen:
                try:
                    acceptance={}
                    acceptance['pdfjs_all_pages']=verify_pdfjs(source,chosen['path'],count,not chosen['exact'])
                    for page in pages:
                        with render(source,page-1,4) as a,render(chosen['path'],page-1,4) as b:visual_delta(a,b,not chosen['exact'])
                        with render(source,page-1,2,print_mode=True) as a,render(chosen['path'],page-1,2,print_mode=True) as b:visual_delta(a,b,not chosen['exact'])
                    acceptance['high_dpi_and_print_pages']=pages
                    detail_page,crop=detail_crop(source,pages)
                    with render(source,detail_page-1,8,crop) as a,render(chosen['path'],detail_page-1,8,crop) as b:delta=visual_delta(a,b,not chosen['exact'])
                    acceptance['detail_crop']={'page':detail_page,'crop':crop,'dpi':576,**delta}
                    from browser_check import check_browser
                    acceptance['browser']=check_browser(source,chosen['path'],pages)
                    report.update(acceptance)
                    break
                except Exception as exc:
                    report['rejected'].append({'kind':chosen['kind'],'reason':'Final renderer/browser acceptance: '+str(exc)})
                    valid.remove(chosen)
                    chosen=choose_candidate(source,valid,measured)
            if chosen:
                if filehash(source)!=report['source_sha256']:raise ValueError('Original changed during processing.')
                report.update({'status':'optimized','classification':'faster' if chosen['cpu_reduction']>=.1 else 'storage-only','selected':chosen['kind'],'output':str(destination),'output_sha256':filehash(chosen['path'])})
                report['selected_transformations']={k:chosen['manifest'][k] for k in
                    ['kind','count','converted','skipped','deduplication_skipped'] if k in chosen['manifest']}
        if filehash(source)!=report['source_sha256']:raise ValueError('Original changed during processing.')
        created=[]
        try:
            if report['status']=='optimized':
                with destination.open('xb') as out:
                    created.append(destination)
                    with Path(chosen['path']).open('rb') as inp:shutil.copyfileobj(inp,out)
                if filehash(destination)!=report['output_sha256']:raise ValueError('Published copy failed its checksum; output removed.')
            for suffix,body in [('.json',json.dumps(report,indent=2)),('.md',report_text(report))]:
                target=destination.with_suffix(suffix)
                with target.open('x',encoding='utf-8') as stream:
                    created.append(target);stream.write(body)
        except BaseException:
            for target in created:target.unlink(missing_ok=True)
            raise
    print(f"Viewing-copy result: {report['status']}",flush=True)
    return source.stat().st_size,destination.stat().st_size if destination.exists() else source.stat().st_size
