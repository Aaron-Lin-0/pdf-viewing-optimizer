"""Unattended local research: optimizer cost, PDF drawing cost and approximate path evidence."""
import argparse
from contextlib import closing
import cProfile
import csv
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import pstats
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]

from pdf_runtime import DiskBudgetExceeded, RunBudget, TimeBudgetExceeded, filehash, qpdf_identity
from tools.compare_optimizers import Progress, run_process, save_json
from viewing_copy import benchmark_worker, node_identity, runtime_node

SCHEMA = 'overnight-1'
RECIPES = ['qpdf-only', 'direct-unpacked', 'pack-1', 'pack-6', 'pack-9', 'recompress-9', 'batched',
           'rdp-0', 'rdp-0.01', 'rdp-0.025', 'rdp-0.05', 'midpoint-0.01', 'midpoint-0.025', 'midpoint-0.05',
           'joined-rdp-0','joined-rdp-0.01','joined-rdp-0.025','joined-rdp-0.05']


def hypothesis(recipe):
    if recipe == 'original':
        return {'baseline': 'original', 'expect': 'Immutable reference for source appearance and viewing cost.',
                'falsifier': 'Any changed source hash invalidates the corresponding evidence.'}
    if recipe.startswith('joined-rdp-'):
        tolerance=recipe.rsplit('-',1)[1]
        return {'baseline':'pack-9' if tolerance=='0' else 'joined-rdp-0',
                'expect': ('Exact-endpoint, identical-width round-stroke chaining lowers paint-call overhead; '
                           'zero tolerance isolates reconstruction and its edge-shading changes. '
                           f'At {tolerance} mm, bounded reduction may additionally lower vertex/rasterization cost.'),
                'falsifier':'No supported chains, no repeatable gain versus the exact/control copy, or unacceptable local ink changes.'}
    if recipe.startswith(('rdp-', 'midpoint-')):
        algorithm, tolerance = recipe.split('-')
        if tolerance == '0':
            return {'baseline': 'original', 'expect': 'Same saved path bytes and pixels; cloning/packing alone does not reduce drawing work.',
                    'falsifier': 'Any changed path or tested pixel; a repeatable viewing change needs a storage/layout explanation.'}
        return {'baseline': 'rdp-0',
                'expect': f'{algorithm} at {tolerance} mm removes redundant existing vertices and may reduce cold drawing CPU; '
                          'higher tolerance may remove more points and alter more ink. Midpoint splitting may retain more vertices than RDP.',
                'falsifier': 'No eligible paths, no removed points, no repeatable drawing gain, or unacceptable local damage.'}
    expectations = {
        'qpdf-only': 'Skipping drawing transformations lowers generation cost; path/paint work and viewing cost stay similar to original.',
        'direct-unpacked': 'Omitting packing lowers generation cost but increases bytes; inlining may lower viewing overhead.',
        'pack-1': 'Level 1 packs faster than level 9 with larger output; no systematic drawing gain is expected.',
        'pack-6': 'Level 6 trades packing time against bytes; drawing cost should resemble level 9.',
        'pack-9': 'Current Direct-style inlining/cleanup/packing may lower viewing overhead versus original; verification may dominate total processing time.',
        'recompress-9': 'Recompressing existing Flate streams costs extra generation time for possible size savings, with little drawing gain.',
        'batched': 'Opacity-qualified batching reduces paint calls and may improve drawing versus pack-9, with possible edge-shading changes.',
    }
    return {'baseline': 'original' if recipe == 'pack-9' else 'pack-9', 'expect': expectations[recipe],
            'falsifier': 'The expected runtime/size direction is absent or reverses outside observed variation; any required preservation failure disqualifies it.'}


def packing(source, output, recipe):
    executable = qpdf_identity()[0]
    level = recipe.removeprefix('pack-') if recipe.startswith('pack-') else '9'
    flags = ['--compress-streams=y', '--decode-level=none', '--compression-level=' + level,
             '--object-streams=generate', '--preserve-unreferenced', '--remove-unreferenced-resources=no']
    if recipe == 'recompress-9':
        flags += ['--decode-level=generalized', '--recompress-flate']
    result = subprocess.run([executable, str(source), *flags, str(output)],
                            capture_output=True, text=True, check=True, timeout=1800)
    if result.stderr.strip():
        raise ValueError('qpdf reported warnings: ' + result.stderr[-1000:])
    from lossless_verify import verify_lossless
    return verify_lossless(source, output)


def generate(source, folder, recipe):
    from viewing_copy import build_candidate
    from navigation import reader_for, validate_input
    from lossless_verify import validate_quick_input
    with reader_for(source) as reader:
        validate_input(reader)
    validate_quick_input(source)
    output = folder / 'candidate.pdf'
    approximation = recipe.startswith(('rdp-', 'midpoint-','joined-rdp-'))
    manifest = {}
    if approximation:
        from tools.approximate_paths import make_candidate
        algorithm, tolerance = recipe.rsplit('-',1)
        intermediate = folder / 'reduced.pdf'
        manifest = make_candidate(source, intermediate, folder, float(tolerance), algorithm)
        if float(tolerance) > 0 and not manifest['edits']:
            for name in ('unchanged-clone.pdf', 'reduced.pdf', 'restored-for-verification.pdf'):
                (folder/name).unlink(missing_ok=True)
            return {'status': 'no opportunity', 'manifest': manifest}
    elif recipe == 'qpdf-only':
        intermediate = source
    else:
        intermediate = output if recipe == 'direct-unpacked' else folder / 'transformed.pdf'
        manifest = build_candidate(source, intermediate, 'batched' if recipe == 'batched' else 'direct')
    if intermediate != output:
        packing(intermediate, output, recipe)
    from pdf_runtime import check_qpdf
    check_qpdf(output, qpdf_identity())
    return {'status': 'generated', 'path': str(output), 'sha256': filehash(output),
            'bytes': output.stat().st_size, 'approximate': approximation and recipe != 'rdp-0',
            'edge_shading': recipe == 'batched', 'manifest': manifest}


def screen(source, candidate, folder, generated):
    from viewing_copy import render, verify_candidate
    from navigation import reader_for
    if not generated['approximate']:
        if generated['edge_shading']:
            from viewing_copy import verify_pdfjs
            proof = verify_candidate(source, candidate, generated['manifest'], allow_edges=True, full=True)
            with reader_for(source) as reader:
                verify_pdfjs(source, candidate, len(reader.pages), allow_edges=True, workers=1)
        elif generated['manifest'].get('kind') == 'direct':
            from quick_lossless import verify_direct
            report = {}
            proof = verify_direct(source, candidate, generated['manifest'], RunBudget(1800), folder,
                                  lambda name, operation: operation(), report)
        else:
            from lossless_verify import verify_lossless
            proof = verify_lossless(source, candidate)
        return {'status': 'passed', 'appearance_policy': ('edge shading allowed' if generated['edge_shading'] else
                'exact render checks' if generated['manifest'].get('kind') == 'direct' else 'strict graph; pixels not rendered here'),
                'proof': proof}
    with reader_for(source) as a, reader_for(candidate) as b:
        if len(a.pages) != len(b.pages):
            raise ValueError('Page count changed.')
        count = len(a.pages)
    for page in range(count):
        with render(source, page) as a, render(candidate, page) as b:
            if a.size != b.size:
                raise ValueError('Page dimensions changed.')
            if a.convert('L').getextrema()[0] < 200 and b.convert('L').getextrema()[0] >= 250:
                raise ValueError('Visible page became blank.')
    return {'status': 'screened only', 'pages': count,
            'quality': 'Gross screen only; full evidence and human review still required.'}


def analyze_page(source, candidate, folder, page, scale):
    from tools.comparison_metrics import evidence, flagged_regions, image_metrics
    from viewing_copy import render
    with render(source, page - 1, scale) as a, render(candidate, page - 1, scale) as b:
        page_mm = (a.width / scale * 25.4 / 72, a.height / scale * 25.4 / 72)
        metrics = image_metrics(a, b, page_mm)
        if metrics.get('status') != 'pass':
            raise ValueError('Unresolved appearance metrics.')
        result = {'page': page, 'dpi': scale * 72, 'metrics': metrics}
        if not metrics['exact']:
            result['evidence'] = evidence(a, b, folder, 'page')
            result['changed_tiles'] = flagged_regions(a, b)
        return result


def worker(request, folder):
    action = request['action']
    source = Path(request['source']) if 'source' in request else None
    if source and filehash(source) != request['source_sha256']:
        raise ValueError('Source identity changed.')
    candidate = Path(request['candidate']) if 'candidate' in request else None
    if candidate and filehash(candidate) != request['candidate_sha256']:
        raise ValueError('Candidate identity changed.')
    if action == 'inventory':
        from lossless_verify import validate_quick_input
        from tools.experiment_manifest import build_experiment_manifest
        validate_quick_input(source)
        return build_experiment_manifest(source)
    if action == 'normalize-boox':
        from tools.normalize_boox import normalize
        return normalize(source, folder/'normalized.pdf')
    if action == 'generate':
        return generate(source, folder, request['recipe'])
    if action == 'screen':
        return screen(source, candidate, folder, request['generated'])
    if action == 'analyze':
        return analyze_page(source, candidate, folder, request['page'], request['scale'])
    if action == 'pdfjs-image':
        from PIL import Image
        from tools.comparison_metrics import image_metrics, evidence
        from viewing_copy import render
        node = runtime_node()
        for name, path in (('source', source), ('candidate', candidate)):
            subprocess.run([node, str(ROOT/'pdfjs_benchmark.mjs'), str(path), str(request['page']),
                            'render', '1404', str(folder / (name + '.png'))], check=True, timeout=300,
                           stdout=subprocess.DEVNULL)
        with Image.open(folder/'source.png') as raw_a, Image.open(folder/'candidate.png') as raw_b, \
                raw_a.convert('RGB') as a, raw_b.convert('RGB') as b:
            with render(source, request['page']-1, 1) as dimensions:
                page_mm = (dimensions.width*25.4/72, dimensions.height*25.4/72)
            result = image_metrics(a, b, page_mm)
            if result.get('status') != 'pass':
                raise ValueError('Unresolved PDF.js appearance metrics.')
            if not result.get('exact'):
                result['evidence'] = evidence(a, b, folder, 'pdfjs')
            return result
    if action == 'timing':
        return benchmark_worker(candidate, request['page'], request['mode'])
    if action == 'native-smoke':
        import firefox_measure
        return firefox_measure.smoke(request['firefox'], folder)
    if action == 'native':
        import firefox_measure
        return firefox_measure.measure(candidate, request['firefox'], folder, request['pages'])
    if action == 'profile':
        profiler = cProfile.Profile()
        profiler.runcall(generate, source, folder, 'pack-9')
        profiler.dump_stats(str(folder/'optimizer.prof'))
        output = io.StringIO()
        pstats.Stats(profiler, stream=output).sort_stats('cumulative').print_stats(40)
        (folder/'profile.txt').write_text(output.getvalue(), encoding='utf-8')
        return {'note': 'Profiled separately; excludes external-process internals; not a timed repetition.'}
    raise ValueError('Unknown benchmark action.')


def identity():
    names = sorted([*ROOT.glob('*.py'), *ROOT.glob('*.mjs'), *ROOT.glob('*.html'),
                    *ROOT.glob('tools/*.py'), *ROOT.glob('tests/*.py'), ROOT/'package-lock.json'])
    code = {str(path.relative_to(ROOT)): filehash(path) for path in names}
    versions = {name: importlib.metadata.version(name) for name in
                ('pypdf', 'pypdfium2', 'Pillow', 'numpy', 'scipy', 'scikit-image', 'psutil', 'selenium')}
    node = runtime_node()
    return {'schema': SCHEMA, 'code': code, 'packages': versions, 'python': sys.version,
            'platform': platform.platform(), 'machine': platform.machine(), 'cpu_count': os.cpu_count(),
            'node': node_identity(node), 'renderer': benchmark_worker(ROOT/'unused.pdf', 1, 'preflight'),
            'qpdf': list(qpdf_identity())}


class Tasks:
    def __init__(self, output, budget, progress):
        self.output, self.budget, self.progress = output, budget, progress
        self.failures = 0

    def run(self, request, maximum=1800):
        self.budget.check('next operation', (self.output,))
        if shutil.disk_usage(self.output).free < 512*1024**2:
            raise DiskBudgetExceeded('Less than 512 MiB of free disk space remains.')
        if (self.output / 'STOP').exists():
            raise TimeBudgetExceeded('STOP file requested a stop between operations.')
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:24]
        task = self.output / 'tasks' / key
        task.mkdir(parents=True, exist_ok=True)
        attempts = sorted(task.glob('attempt-*'))
        for attempt in attempts:
            marker = attempt / 'complete.json'
            if marker.is_file():
                record = json.loads(marker.read_text(encoding='utf-8'))
                if record['request'] != request:
                    raise ValueError('Task identity collision.')
                for name, digest in record['artifacts'].items():
                    path = attempt / name
                    if not path.resolve().is_relative_to(attempt.resolve()) or not path.is_file() or filehash(path) != digest:
                        raise ValueError('Completed benchmark artifact changed: ' + str(path))
                return json.loads((attempt/'observation.json').read_text(encoding='utf-8'))
        attempt = task / f'attempt-{len(attempts)+1:03d}'
        attempt.mkdir()
        save_json(attempt/'request.json', request)
        self.progress(f"{request['action']}: {request.get('recipe', '')} {request.get('page', '')} {key}")
        record = run_process([sys.executable, str(Path(__file__).resolve()), '--worker',
                              str(attempt/'request.json')], attempt, 'process', self.budget.timeout(maximum))
        self.budget.check('finished operation', (self.output,))
        if record['status'] != 'completed' or record.get('exit_code') != 0:
            self.failures += 1
            self.progress('Operation failed; retained logs and continuing: ' + str(attempt))
            return {'error': record['status'], 'logs': str(attempt)}
        result = json.loads((attempt/'result.json').read_text(encoding='utf-8'))
        result['process_wall_s'] = record.get('duration_s')
        save_json(attempt/'observation.json', result)
        artifacts = {path.relative_to(attempt).as_posix(): filehash(path)
                     for path in attempt.rglob('*') if path.is_file()}
        save_json(attempt/'complete.json', {'request': request, 'artifacts': artifacts})
        return result


def ordered(rows, repetition):
    offset = repetition % len(rows)
    result = rows[offset:] + rows[:offset]
    return list(reversed(result)) if repetition % 2 else result


def timing_summary(records):
    groups = {}
    for record in records:
        if 'error' in record:
            continue
        for field in ('load', 'cold', 'cached', 'ready'):
            for unit in ('wall', 'cpu'):
                groups.setdefault((record['page'], field + '_' + unit), []).append(record[field][unit]*1000)
    return {f'p{page}/{metric}': {'raw_ms': values, 'median_ms': statistics.median(values),
                               'min_ms': min(values), 'max_ms': max(values), 'n': len(values)}
            for (page, metric), values in groups.items()}


def normalized_inventory(normalized):
    count = normalized['pages']
    return {'source': {'page_count': count, 'sha256': normalized['output_sha256']},
            'sample_pages': {'selected_samples': sorted({1, (count+1)//2, count}),
                             'selection_criteria': 'Verified NeoPdf snapshot: first/middle/last; density and small-writing ranking not precomputed.'},
            'opportunity_report': {'first_supported_subset': {'supported': None,
                'status': 'Not pre-screened; each treatment inspects the full document.'}},
            'drawing_inventory': 'Deferred to treatments; no source-density coverage claim.'}


def compare(row, baseline, pages, repeats):
    if baseline is None:
        return {'status': 'comparator unavailable'}
    result = {'baseline': baseline['recipe'], 'status': 'descriptive; inspect raw spread'}
    for metric in ('cold_cpu', 'cold_wall', 'cached_wall'):
        keys = [f'p{page}/{metric}' for page in pages]
        a, b = baseline.get('summary', {}), row.get('summary', {})
        if not all(key in a and key in b and a[key]['n'] == repeats and b[key]['n'] == repeats for key in keys):
            result[metric] = {'status': 'incomplete'}
            continue
        before, after = sum(a[key]['median_ms'] for key in keys), sum(b[key]['median_ms'] for key in keys)
        if before <= 0 or (metric.endswith('cpu') and any(min(a[key]['median_ms'],b[key]['median_ms']) <= 0 for key in keys)):
            result[metric] = {'status': 'timer unresolved'}
            continue
        result[metric] = {'reduction_pct': 100*(before-after)/before,
            'noisy': (any(r['max_ms']-r['min_ms'] > r['median_ms']*.25 for key in keys for r in (a[key],b[key]))
                      if repeats >= 3 else 'unresolved: fewer than three repetitions'),
            'regression_pages': [page for page, key in zip(pages, keys)
                if b[key]['median_ms'] - a[key]['median_ms'] > max(50, a[key]['median_ms']*.1)]}
    return result


def report(path, state):
    lines = ['# Overnight benchmark', '', 'Status: ' + state['status'], '',
             'Research evidence only. Approximate candidates require human review. Originals are untouched.', '',
             'Fresh renderer processes retain OS caches. Measurements apply to the recorded machine and sample.', '',
             'Optimizer wall times exclude later evidence analysis; worker CPU excludes external child processes.', '']
    for document in state['documents']:
        if document.get('preflight_blocked'):
            lines += ['Required treatment preflight failed: '+', '.join(document['preflight_blocked'])+
                      '. No viewing benchmarks ran for this document.', '']
        if document.get('preprocessing', {}).get('preservation', {}).get('complete'):
            lines += ['BOOX preprocessing: verified current-revision snapshot; historical revision layout',
                      'is not retained in the research copy. The original is unchanged and remains the viewing-time reference.',
                      'Preprocessing time is recorded separately from candidate generation.', '']
            lines += ['Timing sample: first/middle/last pages. Detailed source density/small-writing ranking',
                      'was not precomputed; each treatment retains its own whole-document checks.', '']
        lines += ['## ' + Path(document.get('original', document['source'])).name, '',
                  '| Treatment | Bytes | Generation median s | Verification/screen median s | Cold CPU ms* | Quality |',
                  '| --- | ---: | ---: | ---: | ---: | --- |']
        if 'error' in document.get('inventory', {}):
            lines += ['', 'Input could not be inventoried; see its recorded failure/logs.', '']
        for row in document['candidates']:
            generation = [r['worker_wall_s'] for r in row.get('generation', []) if r.get('status') == 'generated']
            checks = [r['worker_wall_s'] for r in row.get('screens', []) if 'error' not in r]
            cold = [r['median_ms'] for key, r in row.get('summary', {}).items() if key.endswith('/cold_cpu')]
            cpu = f'{sum(cold):.2f}' if len(cold) == len(document.get('timed_pages', [])) and cold else 'unavailable'
            quality = row.get('quality', 'unavailable')
            if row.get('analysis_errors'):
                quality += '; incomplete evidence'
            generation_text = f'{statistics.median(generation):.3f}' if generation else 'n/a'
            checks_text = f'{statistics.median(checks):.3f}' if checks else 'n/a'
            lines.append(f"| {row['recipe']} | {row.get('bytes', '')} | {generation_text} | {checks_text} | {cpu} | {quality} |")
        lines += ['', '*Sum of per-page median cold CPU over the frozen sample; operations are never added together.',
                  'See results JSON for raw repetitions, per-page wall/CPU, preparation, native observations,',
                  'changed-page evidence, skips, hashes and unresolved operations. No automatic lossy winner.', '']
        lines += ['Native viewer: ' + document.get('native_smoke', {}).get('status', 'not reached') + '.', '']
        lines += ['### Registered predictions and observed results', '']
        for row in document['candidates']:
            if 'hypothesis' not in row:
                continue
            prediction = row['hypothesis']
            lines += [f"**{row['recipe']}** vs {prediction['baseline']}: {prediction['expect']}", '',
                      'Counterevidence: ' + prediction['falsifier'], '']
            process_times = [r['process_wall_s'] for r in row.get('generation', []) if r.get('process_wall_s') is not None]
            if process_times:
                lines += [f'Fresh generation process, including startup/imports: median {statistics.median(process_times):.3f}s.', '']
            comparison = row.get('comparison', {})
            observed = comparison.get('cold_cpu', {})
            if 'reduction_pct' in observed:
                lines += [f"Observed sample cold-CPU reduction: {observed['reduction_pct']:.1f}%; "
                          f"noisy: {observed['noisy']}. Quality: {row.get('quality', 'unavailable')}.", '']
            else:
                lines += ['Observed: ' + row.get('quality', 'unavailable') + '; ' +
                          observed.get('status', comparison.get('status', 'comparison incomplete')) + '.', '']
    with path.open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(lines))
    with path.with_suffix('.csv').open('x', encoding='utf-8', newline='') as stream:
        table = csv.writer(stream)
        table.writerow(['document','treatment','bytes','generation_wall_s','screen_wall_s','cold_cpu_vs_baseline_pct','quality'])
        for document in state['documents']:
            for row in document['candidates']:
                gen = [r['worker_wall_s'] for r in row.get('generation', []) if r.get('status') == 'generated']
                checks = [r['worker_wall_s'] for r in row.get('screens', []) if 'error' not in r]
                table.writerow([document.get('original',document['source']),row['recipe'],row.get('bytes'),
                    statistics.median(gen) if gen else None, statistics.median(checks) if checks else None,
                    row.get('comparison',{}).get('cold_cpu',{}).get('reduction_pct'),row.get('quality')])


def document_run(source, tasks, settings, state):
    digest = filehash(source)
    base = {'source': str(source), 'source_sha256': digest}
    original_path = next(row['original'] for row in state['inputs'] if row['snapshot'] == str(source))
    document = {'source': str(source), 'original': original_path, 'source_sha256': digest, 'candidates': []}
    state['documents'].append(document)
    inventory = tasks.run(dict(base, action='inventory'))
    document['inventory'] = inventory
    reference = source
    if 'error' in inventory:
        normalized = tasks.run(dict(base, action='normalize-boox',
            hypothesis='A storage-only NeoPdf current-revision snapshot passes strict input checks while preserving every current logical object and stream.'))
        document['preprocessing'] = normalized
        if 'error' in normalized:
            return
        source = Path(normalized['path'])
        base = {'source': str(source), 'source_sha256': normalized['output_sha256']}
        inventory = normalized_inventory(normalized)
        document['inventory'] = inventory
    samples = inventory['sample_pages']['selected_samples']
    count = inventory['source']['page_count']
    pages = sorted(list(dict.fromkeys([1, *samples, (count+1)//2, count]))[:settings['page_limit']])
    document['timed_pages'] = pages
    original = {'recipe': 'original', 'path': str(reference), 'sha256': digest, 'bytes': reference.stat().st_size,
                'quality': 'source reference', 'hypothesis': hypothesis('original'), 'timings': [], 'preparation': []}
    document['candidates'].append(original)
    rows = [{'recipe': recipe, 'hypothesis': settings['hypotheses'][recipe],
             'generation': [], 'screens': [], 'timings': [], 'preparation': []}
            for recipe in settings['recipes']]
    document['candidates'].extend(rows)
    # Generate/check sequentially, then close every worker before any viewing timings.
    for repetition in range(settings['optimizer_repeats']):
        for row in ordered(rows, repetition):
            if (row['recipe'].startswith(('rdp-', 'midpoint-')) and row['recipe'] != 'rdp-0' and
                    inventory['opportunity_report']['first_supported_subset']['supported'] is False):
                row['quality'] = 'no opportunity (source inventory); not generated'
                continue
            generated = tasks.run(dict(base, action='generate', recipe=row['recipe'], repetition=repetition,
                                       hypothesis=row['hypothesis']))
            row['generation'].append(generated)
            if generated.get('status') != 'generated':
                row['quality'] = generated.get('status', 'generation failed')
                continue
            check = tasks.run(dict(base, action='screen', candidate=generated['path'],
                                   candidate_sha256=generated['sha256'], generated=generated, repetition=repetition))
            row['screens'].append(check)
            if 'error' in check:
                row['quality'] = 'verification/screen failed'
                continue
            if 'path' not in row:
                row.update({key: generated[key] for key in ('path','sha256','bytes','approximate','edge_shading','manifest')})
                row['quality'] = ('review required; geometry/topology unqualified' if row['approximate'] else
                                  'exact geometry; edge shading allowed' if row['edge_shading'] else 'exact checks passed')
    required=settings.get('required_recipes',[])
    missing=[name for name in required if not any(row['recipe']==name and 'path' in row and
        (not row.get('approximate') or bool(row['manifest'].get('edits'))) for row in rows)]
    if missing:
        document['preflight_blocked']=missing
        tasks.progress('Required treatment preflight failed: '+', '.join(missing)+'. No viewing benchmarks run for this document.')
        return
    active = [original, *[row for row in rows if 'path' in row]]
    for page in pages:
        for repetition in range(settings['repeats']):
            for row in ordered(active, repetition):
                request = dict(base, candidate=row['path'], candidate_sha256=row['sha256'], page=page,
                               action='timing', mode='render', repetition=repetition)
                measured = tasks.run(request, 360)
                measured['page'] = page
                row['timings'].append(measured)
        for row in active:
            row['preparation'].append(tasks.run(dict(base, action='timing', mode='prepare', page=page,
                candidate=row['path'], candidate_sha256=row['sha256']), 360))
    for row in active:
        row['summary'] = timing_summary(row['timings'])
    by_recipe = {row['recipe']: row for row in active}
    for row in rows:
        row['comparison'] = compare(row, by_recipe.get(row['hypothesis']['baseline']), pages, settings['repeats'])
    if settings['firefox']:
        smoke = tasks.run({'action': 'native-smoke', 'firefox': settings['firefox']}, 180)
        document['native_smoke'] = smoke
        if smoke.get('status') == 'available':
            # Always retain an exact comparator alongside the two fastest remaining candidates.
            def rank(row):
                values = [r['median_ms'] for key, r in row['summary'].items() if key.endswith('/cold_cpu')]
                return sum(values) if len(values) == len(pages) else math.inf
            exact = [row for row in active[1:] if not row['approximate'] and not row['edge_shading']]
            best_exact = min(exact, key=rank) if exact else None
            rest = sorted([row for row in active[1:] if row is not best_exact], key=rank)
            shortlisted = [original, *([best_exact] if best_exact else []), *rest[:2]]
            for repetition in range(min(3, settings['repeats'])):
                for row in ordered(shortlisted, repetition):
                    row.setdefault('native', []).append(tasks.run(dict(base, action='native',
                        candidate=row['path'], candidate_sha256=row['sha256'], firefox=settings['firefox'],
                        pages=pages, repetition=repetition), 900))
    else:
        document['native_smoke'] = {'status': 'unavailable', 'reason': 'No Firefox selected/discovered.'}
    # Analysis/profiling happen after timings. Each page/resolution has its own timeout/record.
    for row in active[1:]:
        if not row.get('approximate') and not row.get('edge_shading'):
            continue
        row['appearance'] = []
        for page in range(1, count + 1):
            for scale in (2, 4):
                result = tasks.run(dict(base, action='analyze', candidate=row['path'],
                    candidate_sha256=row['sha256'], page=page, scale=scale), 300)
                row['appearance'].append(result)
            row['appearance'].append(tasks.run(dict(base, action='pdfjs-image', candidate=row['path'],
                candidate_sha256=row['sha256'], page=page), 600))
        row['analysis_errors'] = sum('error' in result for result in row['appearance'])
    document['profile'] = tasks.run(dict(base, action='profile'), 1800)
    if filehash(reference) != digest:
        raise ValueError('Frozen source changed during measurement.')


def fixtures(folder):
    from reportlab.pdfgen import canvas
    import math
    sys.path.insert(0, str(ROOT/'tests'))
    from test_handwriting import make_boox
    from pypdf import PdfWriter
    from navigation import reader_for
    from pypdf.generic import NameObject
    from handwriting import HEADER
    paths = []
    # A dense continuous-path case plus the genuine separate-segment grammar and raster/no-op control.
    seed = folder/'segments.pdf'
    make_boox(seed)
    paths.append(seed)
    with reader_for(seed) as reader, closing(PdfWriter(clone_from=reader)) as writer:
        form = writer.pages[0]['/Resources']['/XObject']['/FFT0']
        parts = [HEADER]
        for line in range(36):
            parts.append(f'0.8 w 10 {12+line*4} m '.encode())
            parts.extend(f'{10+i*.09:.4f} {12+line*4+math.sin(i*.025)*1.5:.4f} l '.encode() for i in range(1, 1901))
            parts.append(b'S\n')
        form.set_data(b''.join(parts))
        # Remove the second drawing so the dense control has one invocation per path.
        from pypdf.generic import DecodedStreamObject
        content = DecodedStreamObject()
        content.set_data(b'q /FFT0 Do Q\n')
        writer.pages[0][NameObject('/Contents')] = writer._add_object(content)
        dense = folder/'dense-polylines.pdf'
        with dense.open('xb') as output:
            writer.write(output)
        paths.append(dense)
    control = folder/'printed-control.pdf'
    c = canvas.Canvas(str(control), pagesize=(200,200))
    c.drawString(20, 100, 'Control: 0.1, x2, a/b, e/c')
    c.save()
    paths.append(control)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', nargs='*', type=Path, help='Explicitly selected local PDFs; never searched automatically')
    parser.add_argument('--output', type=Path, required=True, help='New ignored run directory, or same directory with --resume')
    parser.add_argument('--fixtures', action='store_true', help='Add generated inputs; needs requirements-comparison.txt')
    parser.add_argument('--resume', action='store_true', help='Reuse only complete hash-validated operations from this run')
    parser.add_argument('--hours', type=float, default=8, help='Wall-time budget for this invocation (default 8)')
    parser.add_argument('--disk-gb', type=float, default=20, help='Run-folder byte budget (default 20 GiB)')
    parser.add_argument('--repeats', type=int, default=5, help='Fresh-process render repetitions (default 5)')
    parser.add_argument('--optimizer-repeats', type=int, default=3)
    parser.add_argument('--page-limit', type=int, default=6, help='Maximum source-selected timing pages; quality covers every page')
    parser.add_argument('--recipes',nargs='+',choices=RECIPES,help='Optional fixed subset of research treatments; original is always included')
    parser.add_argument('--require-recipes',nargs='+',choices=RECIPES,help='Do not time a document unless these selected treatments produce checked candidates; approximate trials must change target bodies')
    parser.add_argument('--firefox', help='Local Firefox executable for optional bundled-viewer measurements')
    parser.add_argument('--skip-native', action='store_true')
    parser.add_argument('--smoke', action='store_true', help='Small generated check: 1 repetition, 1 timing page; 3 recipes unless selected explicitly')
    args = parser.parse_args()
    if (not all(math.isfinite(v) and v > 0 for v in (args.hours, args.disk_gb)) or
            min(args.repeats, args.optimizer_repeats, args.page_limit) < 1):
        parser.error('Budgets and counts must be positive and finite.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=args.resume)
    budget = RunBudget(args.hours*3600, int(args.disk_gb*1024**3))
    lock = output/'.benchmark-lock'
    import psutil
    if lock.exists():
        owner = json.loads(lock.read_text())
        if psutil.pid_exists(owner['pid']) and psutil.Process(owner['pid']).create_time() == owner['created']:
            raise ValueError('Another benchmark owns this run directory.')
        lock.unlink()
    save_json(lock, {'pid': os.getpid(), 'created': psutil.Process().create_time()})
    keep_awake = None
    if sys.platform == 'win32':
        import ctypes
        keep_awake = ctypes.windll.kernel32.SetThreadExecutionState
        keep_awake(0x80000001)
    try:
        import firefox_measure
        browser = None if args.skip_native else firefox_measure.discover(args.firefox)
        if args.firefox and not browser:
            parser.error('The selected Firefox executable does not exist.')
        current = identity()
        if browser:
            current['firefox_sha256'] = filehash(browser)
        recipes = args.recipes or (['qpdf-only', 'rdp-0', 'rdp-0.025'] if args.smoke else RECIPES)
        if not set(args.require_recipes or [])<=set(recipes):
            parser.error('--require-recipes must be included in --recipes.')
        settings = {'recipes': recipes, 'hypotheses': {recipe: hypothesis(recipe) for recipe in ['original', *recipes]},
                    'required_recipes':args.require_recipes or [],
                    'repeats': 1 if args.smoke else args.repeats,
                    'optimizer_repeats': 1 if args.smoke else args.optimizer_repeats,
                    'page_limit': 1 if args.smoke else args.page_limit, 'firefox': browser}
        if args.resume:
            plan = json.loads((output/'plan.json').read_text(encoding='utf-8'))
            if plan['identity'] != current or plan['settings'] != settings:
                raise ValueError('Code, tools or settings changed; choose a new run directory.')
            if args.sources or args.fixtures:
                parser.error('Resume uses the frozen sources; omit positional PDFs and --fixtures.')
            sources = [Path(row['snapshot']) for row in plan['sources']]
            for row in plan['sources']:
                if filehash(row['snapshot']) != row['sha256']:
                    raise ValueError('Frozen source changed; refusing resume.')
        else:
            inputs = [path.resolve(strict=True) for path in args.sources]
            if args.fixtures or args.smoke:
                generated = output/'generated'
                generated.mkdir()
                inputs += fixtures(generated)
            if not inputs:
                parser.error('Provide explicit PDFs or --fixtures.')
            snapshots = output/'inputs'
            snapshots.mkdir()
            source_rows, sources = [], []
            for number, path in enumerate(dict.fromkeys(inputs), 1):
                budget.check('snapshot source', (output,))
                if path.stat().st_size > 512*1024**2:
                    raise ValueError('Input exceeds the strict 512 MiB research bound: ' + str(path))
                digest = filehash(path)
                snapshot = snapshots/f'{number:03d}.pdf'
                with path.open('rb') as before, snapshot.open('xb') as after:
                    shutil.copyfileobj(before, after)
                if filehash(snapshot) != digest or filehash(path) != digest:
                    raise ValueError('Source changed while snapshotting.')
                source_rows.append({'original': str(path), 'snapshot': str(snapshot), 'sha256': digest})
                sources.append(snapshot)
            plan = {'identity': current, 'settings': settings, 'sources': source_rows}
            save_json(output/'plan.json', plan)
        session = output / f'session-{len(list(output.glob("session-*")))+1:03d}'
        session.mkdir()
        state = {'schema': SCHEMA, 'status': 'running', 'settings': settings, 'inputs': plan['sources'], 'documents': []}
        with Progress() as progress:
            progress.attach(session/'progress.log')
            tasks = Tasks(output, budget, progress)
            try:
                for source in sources:
                    document_run(source, tasks, settings, state)
                if identity() != {key: value for key, value in current.items() if key != 'firefox_sha256'}:
                    raise ValueError('Code or tools changed during this run; measurements are unproven.')
                blocked=sum(bool(d.get('preflight_blocked')) for d in state['documents'])
                measured=any(c.get('timings') for d in state['documents'] for c in d['candidates'])
                state['status'] = (f'required treatment preflight failed for {blocked} document(s); no viewing benchmarks ran'
                    if blocked and not measured else
                    f'completed operations; {tasks.failures} failed operations; {blocked} preflight-blocked document(s); review required')
            except (TimeBudgetExceeded, DiskBudgetExceeded, KeyboardInterrupt) as exc:
                state['status'] = type(exc).__name__ + ': ' + str(exc)
            except Exception as exc:
                state['status'] = 'failed: ' + str(exc)
                raise
            finally:
                save_json(session/'results.json', state)
                report(session/'report.md', state)
                progress('Saved report: ' + str(session/'report.md'))
    finally:
        if keep_awake:
            keep_awake(0x80000000)
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        request_path = Path(sys.argv[2]).resolve()
        request = json.loads(request_path.read_text(encoding='utf-8'))
        start, cpu = time.monotonic(), time.process_time()
        result = worker(request, request_path.parent)
        result.update(worker_wall_s=time.monotonic()-start, worker_cpu_s=time.process_time()-cpu)
        for label in ('source', 'candidate'):
            if label in request and filehash(request[label]) != request[label+'_sha256']:
                raise ValueError('PDF changed during the operation: ' + label)
        save_json(request_path.parent/'result.json', result)
    else:
        main()
