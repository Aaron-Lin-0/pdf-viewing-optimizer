"""Direct exact transformations and verification, without optimizer benchmarks."""
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from lossless_verify import POLICY_VERSION, UnsupportedPDF, validate_quick_input, verify_lossless
from pdf_runtime import (RunBudget, TimeBudgetExceeded, DiskBudgetExceeded, filehash,
                         qpdf_identity, build_qpdf_candidate, check_qpdf, _run)


def acceptance_context(identity,optimizer='qpdf',budget=None):
    digest=hashlib.sha256();root=Path(__file__).resolve().parent
    names=['quick_lossless.py','lossless_verify.py','pdf_runtime.py','navigation.py']
    if optimizer=='adaptive':
        names+=['viewing_copy.py','handwriting.py','annotation_ink.py','geometry.py',
                'verification_worker.py','pdfjs_benchmark.mjs','tools/revision_pages.py','package-lock.json']
    for name in names:
        digest.update(name.encode())
        path=root/name
        digest.update(b'not shipped' if name=='package-lock.json' and not path.is_file() else path.read_bytes())
    context={'profile':'quick','policy':'direct-viewing-2' if optimizer=='adaptive' else POLICY_VERSION,
            'recipe':'inline-dedup-cleanup-pack' if optimizer=='adaptive' else 'preserve-encoded-streams',
            'optimizer':optimizer,
            'code_sha256':digest.hexdigest(),
            'qpdf_version':identity[1],'qpdf_sha256':identity[2],
            'pypdf_version':importlib.metadata.version('pypdf')}
    if optimizer=='adaptive':
        from viewing_copy import runtime_node
        node=runtime_node()
        runtime=_run([node,str(root/'pdfjs_benchmark.mjs'),'unused.pdf','1','preflight','1'],
                     30,budget,'renderer dependency check',capture_output=True,text=True,check=True,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        renderer=json.loads(runtime.stdout)
        if renderer['version']!='6.4.299':
            raise UnsupportedPDF('Direct optimization requires PDF.js 6.4.299. Run Setup Dependencies.cmd.')
        context.update(pdfium_version=importlib.metadata.version('pypdfium2'),
                       pillow_version=importlib.metadata.version('Pillow'),
                       node_sha256=filehash(node),renderer=renderer)
    return context


def direct_candidate(source,working,identity,budget,stage,report):
    """One transformation chain; packing is a storage step, not a competitor."""
    from viewing_copy import build_candidate
    transformed=working/'direct.pdf'
    manifest=stage('exact inlining, deduplication and cleanup',lambda:build_candidate(source,transformed,'direct'))
    packed=working/'direct-packed.pdf';selected=transformed
    report['transformations']=manifest
    report['pipeline']=['exact supported inlining','safe deduplication','unreferenced-object cleanup','qpdf packing']
    try:
        stage('pack transformed copy',lambda:build_qpdf_candidate(transformed,packed,budget,identity,strict=True))
        if packed.stat().st_size<transformed.stat().st_size:
            check=lambda:budget.check('packing equivalence',(working,))
            report['packing_verification']=stage('verify lossless packing',lambda:verify_lossless(transformed,packed,check))
            selected=packed
        else:report['packing_skipped']='Packing did not shrink the transformed copy.'
    except (TimeBudgetExceeded,DiskBudgetExceeded):raise
    except Exception as exc:
        report['packing_skipped']='Retained the uncompressed transformed copy: '+str(exc)
    return selected,manifest


def verify_direct(source,candidate,manifest,budget,working,stage,report):
    from viewing_copy import (verify_candidate,verify_pdfjs,detail_crop,
                              VerificationSession,default_verification_workers)
    from verification_worker import page_comparisons
    workers=default_verification_workers()
    report['verification_workers']=workers
    def preserve():
        from tools.revision_pages import revision_evidence
        try:
            evidence,reused=revision_evidence(source,candidate,budget.checkpoint,report['acceptance_context'],
                                             lambda:budget.check('revision page matching',(working,)))
        except (TimeBudgetExceeded,DiskBudgetExceeded):
            raise
        except Exception as exc:
            evidence,reused={},set()
            report['revision_reuse_skipped']=str(exc)
        session=VerificationSession(source,working/'source-evidence',workers,budget,
                                    revision_evidence=(Path(candidate).absolute(),reused))
        if evidence:
            report['revision_evidence']=evidence
        if reused:
            print(f'Reused exact 144-DPI comparisons for {len(reused)} unchanged page(s).',flush=True)
        try:return verify_candidate(source,candidate,manifest,allow_edges=False,full=True,session=session)
        finally:session.close(force=True)
    proof=stage('verify preserved content on every page',preserve)
    if not proof['exact']:raise ValueError('Direct optimization requires identical tested pixels.')
    pages=len(proof['pages'])
    report['pdfjs_all_pages']=stage('PDF.js appearance on every page',
        lambda:verify_pdfjs(source,candidate,pages,allow_edges=False,budget=budget,workers=workers))
    samples=sorted({1,(pages+1)//2,pages})
    def details():
        page,crop=detail_crop(source,samples)
        with page_comparisons(source,candidate) as compare:
            for sample in samples:
                budget.check('high-resolution and print verification',(working,))
                compare(sample-1,allow_edges=False,scale=4)
                compare(sample-1,allow_edges=False,print_mode=True)
            budget.check('detail verification',(working,))
            return {'sample_pages':samples,'detail_page':page,
                    'detail':compare(page-1,allow_edges=False,scale=8,crop=crop)}
    report['high_resolution_and_print']=stage('high-resolution and print verification',details)
    report['visual_checks']='passed: all-page PDFium/PDF.js and high-resolution/print/detail checks'
    proof.update(complete=True,policy='direct-viewing-2',rendering_performance='not measured',
                 browser_checks='not run',visual_checks=report['visual_checks'])
    return proof


def report_text(report):
    lines=['# Direct viewing-copy report' if report['optimizer']=='adaptive' else '# Compression-only copy report','',f"Status: {report['status']}",
           f"Classification: {report['classification']}",f"Reason: {report.get('reason','All required checks passed.')}",
           f"Original: {report['source']}",f"Original bytes: {report['source_bytes']}",
           f"Candidate bytes: {report.get('candidate_bytes','not generated')}",
           f"Original SHA256: {report['source_sha256']}",
           f"Total elapsed time: {report['total_elapsed_seconds']:.3f}s",'',
           ('Exact inlining, safe deduplication, current-document cleanup and compression.' if report['optimizer']=='adaptive'
            else 'Lossless compression only; all unreferenced objects retained.'),
           'Rendering performance: not measured. No rendering-speed benchmarks or candidate tournament.',
           'Visual checks: '+report['visual_checks']+'. Browser checks: '+report['browser_checks']+'.',
           ('Live annotations are not flattened; unreachable editor/recovery data may be omitted.' if report['optimizer']=='adaptive'
            else 'Verification: strict document graph, reference identities and stream bytes.'),
           'Unsupported or unresolved preservation prevents publication.','']
    lines.extend(f"- {row['stage']}: {row['wall_seconds']:.3f}s ({row['outcome']})" for row in report['stages'])
    if report.get('revision_evidence'):
        pages=report['revision_evidence']['pdfium_pages_reused']
        lines.append(f"Prior exact 144-DPI page comparisons reused: {len(pages)}.")
    return '\n'.join(lines)+'\n'


def optimize_quick(source,destination,expected_source_sha256=None,checkpoint=None,
                   time_budget_seconds=None,disk_budget_bytes=None,optimizer='adaptive'):
    if optimizer not in {'adaptive','qpdf'}:raise ValueError('Quick mode supports adaptive or qpdf.')
    source=Path(source).expanduser().absolute();destination=Path(destination).expanduser().absolute()
    if not source.is_file():raise ValueError('Input must be a PDF file.')
    targets=[destination,destination.with_suffix('.json'),destination.with_suffix('.md')]
    if any(p.exists() or p.is_symlink() for p in targets):
        raise FileExistsError('Output/report already exists. Choose a new filename.')
    if not destination.parent.is_dir():raise ValueError('The output folder does not exist.')
    for label,value in (('Time',time_budget_seconds),('Disk',disk_budget_bytes)):
        if value is not None and (not math.isfinite(value) or value<=0):
            raise ValueError(label+' budget must be finite and positive.')
    checkpoint=Path(checkpoint).absolute() if checkpoint else destination.with_suffix('.checkpoint')
    if checkpoint.is_symlink() or checkpoint.parent.resolve()!=destination.parent.resolve():
        raise ValueError('Checkpoint must stay beside the output and must not be a shortcut.')
    budget=RunBudget(time_budget_seconds,disk_budget_bytes,checkpoint)
    source_hash=filehash(source)
    if expected_source_sha256 is not None:
        if not re.fullmatch('[0-9a-fA-F]{64}',expected_source_sha256):
            raise ValueError('Expected source SHA256 must be 64 hexadecimal characters.')
        if source_hash!=expected_source_sha256.lower():raise ValueError('Original does not match the reviewed source hash.')
    report={'status':'no improvement','classification':'not processed','profile':'quick','optimizer':optimizer,
            'source':str(source),'source_bytes':source.stat().st_size,'source_sha256':source_hash,
            'rendering_performance':'not measured','visual_checks':'not run','browser_checks':'not run',
            'stages':[]}
    checkpoint.mkdir(exist_ok=True)
    state_folder=checkpoint/'quick'
    if state_folder.is_symlink():raise ValueError('Quick checkpoint must not be a shortcut.')
    state_folder.mkdir(exist_ok=True)
    def state(stage,status='running',report_sha256=None):
        target=state_folder/'state.json';temporary=state_folder/'.state.tmp'
        # Do not follow a planted checkpoint-file symlink.
        if target.is_symlink() or temporary.is_symlink():raise ValueError('Checkpoint file is a shortcut.')
        temporary.write_text(json.dumps({'schema':1,'status':status,'stage':stage,
            'profile':'quick','source_sha256':source_hash,'stage_started':time.time(),
            'report_sha256':report_sha256}),encoding='utf-8')
        os.replace(temporary,target)
    def fresh_source():
        if filehash(source)!=source_hash:raise ValueError('Original changed during processing.')
    with tempfile.TemporaryDirectory(prefix='pdf-quick-',dir=destination.parent) as temporary:
        working=Path(temporary);candidate=working/'candidate.pdf'
        def check():budget.check('quick lossless verification',(working,))
        def stage(name,operation):
            check();state(name);print('Stage: '+name,flush=True);started=time.monotonic()
            outcome='complete'
            try:return operation()
            except BaseException:
                outcome='failed';raise
            finally:
                report['stages'].append({'stage':name,'wall_seconds':time.monotonic()-started,'outcome':outcome})
        identity=None;candidate_hash=None
        try:
            report['pages']=stage('quick input checks',lambda:validate_quick_input(source))
            identity=stage('qpdf preflight',lambda:qpdf_identity(budget))
            if identity[1]!='qpdf version 12.4.2':
                raise UnsupportedPDF('Quick mode is qualified with qpdf 12.4.2; restore the bundled runtime.')
            if importlib.metadata.version('pypdf')!='6.10.0':
                raise UnsupportedPDF('Quick mode requires pypdf 6.10.0. Run Setup Dependencies.cmd.')
            report['acceptance_context']=stage('renderer and policy context',lambda:acceptance_context(identity,optimizer,budget))
            manifest=None
            if optimizer=='adaptive':
                candidate,manifest=direct_candidate(source,working,identity,budget,stage,report)
            else:stage('quick compression',lambda:build_qpdf_candidate(source,candidate,budget,identity,strict=True))
            check()
            report['candidate_bytes']=candidate.stat().st_size
            if candidate.stat().st_size<report['source_bytes']:
                stage('qpdf validity check',lambda:check_qpdf(candidate,identity,budget))
                candidate_hash=filehash(candidate)
                if optimizer=='adaptive':
                    report['verification']=verify_direct(source,candidate,manifest,budget,working,stage,report)
                else:report['verification']=stage('lossless graph verification',lambda:verify_lossless(source,candidate,check))
                fresh_source()
                if filehash(candidate)!=candidate_hash:raise ValueError('Candidate changed during verification.')
                report.update(status='optimized',classification='smaller-lossless',output=str(destination),
                              output_sha256=candidate_hash)
            else:
                report.update(classification='no size benefit',reason='The compression candidate was not smaller.')
        except (TimeBudgetExceeded,DiskBudgetExceeded):
            raise
        except Exception as exc:
            report.update(status='no improvement',classification='unsupported verification' if isinstance(exc,UnsupportedPDF)
                          else 'verification failed',reason=str(exc))
        check();fresh_source();created=[]
        try:
            def publish():
                if report['status']!='optimized':return
                with destination.open('xb') as output:
                    created.append(destination)
                    with candidate.open('rb') as verified:shutil.copyfileobj(verified,output)
                if filehash(destination)!=candidate_hash:raise ValueError('Published copy failed its checksum.')
                fresh_source();check()
            stage('publication',publish)
            report['total_elapsed_seconds']=time.monotonic()-budget.started
            for suffix,body in (('.json',json.dumps(report,indent=2)),('.md',report_text(report))):
                fresh_source();target=destination.with_suffix(suffix)
                with target.open('x',encoding='utf-8') as output:
                    created.append(target);output.write(body)
            fresh_source();check()
            state('complete',report['status'],filehash(destination.with_suffix('.json')))
        except BaseException:
            for target in created:target.unlink(missing_ok=True)
            raise
    print(f"Quick lossless result: {report['classification']}",flush=True)
    return report['source_bytes'],destination.stat().st_size if report['status']=='optimized' else report['source_bytes']
