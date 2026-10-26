"""Finish a targeted P0 review from hash-matched comparison checkpoints."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]
from compare_optimizers import independent_checks
from comparison_metrics import image_metrics
from navigation import reader_for
from viewing_copy import detail_crop, filehash, render


DEFAULT_CANDIDATES = ('repository/inline', 'repository/fast-handwriting',
                      'qpdf/flate', 'mupdf/compress-packed')


def checkpoint(path, build):
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    value = build()
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
    try:
        try:
            os.link(temporary, path)
        except PermissionError:
            os.rename(temporary, path)
    except FileExistsError:
        pass
    finally:
        if temporary.exists():
            temporary.unlink()
    return json.loads(path.read_text(encoding='utf-8'))


def slug(name):
    return name.replace('/', '-')


def operation(key):
    name, _, qualifier = key.split('/', 2)
    return f'{name}/{qualifier}' if name == 'zoom' else name


def timing_summary(records, outputs, repetitions=3):
    firefox = [r for r in records if r.get('kind') == 'firefox' and not r.get('error')]
    baseline = sorted((r for r in firefox if r.get('output') == 'source/original'),
                      key=lambda r: r['repetition'])[:repetitions]
    if len(baseline) != repetitions:
        raise ValueError(f'Source needs {repetitions} completed Firefox repetitions.')
    expected = {}
    for key in baseline[0]['summary']:
        expected[operation(key)] = expected.get(operation(key), 0) + 1

    result = {}
    for output in ('source/original', *outputs):
        rows = sorted((r for r in firefox if r.get('output') == output),
                      key=lambda r: r['repetition'])[:repetitions]
        if len(rows) != repetitions:
            raise ValueError(f'{output} needs {repetitions} completed Firefox repetitions.')
        operations = {}
        for name, count in expected.items():
            totals = []
            resolved = total = 0
            for row in rows:
                values = [value for key, value in row['summary'].items() if operation(key) == name]
                total += len(values)
                resolved += sum(bool(value.get('resolved')) for value in values)
                if len(values) == count and all(value.get('resolved') for value in values):
                    totals.append(sum(value['median_ms'] for value in values))
            operations[name] = {
                'complete_repetitions': len(totals),
                'required_repetitions': repetitions,
                'resolved_observations': resolved,
                'total_observations': total,
                'median_total_ms': statistics.median(totals) if len(totals) == repetitions else None,
                'range_total_ms': [min(totals), max(totals)] if len(totals) == repetitions else None,
            }
        result[output] = operations
    for output in outputs:
        for name, value in result[output].items():
            source = result['source/original'][name]['median_total_ms']
            current = value['median_total_ms']
            value['change_percent'] = ((source - current) / source * 100
                                       if source and current is not None else None)
    return result


def load_evidence(directory, candidates):
    directory = Path(directory).resolve()
    records = [json.loads(path.read_text(encoding='utf-8'))
               for path in sorted(directory.glob('record-*.json'))]
    outputs = {record['output']: record['metadata'] for record in records
               if record.get('kind') == 'output' and record.get('metadata', {}).get('status') == 'usable'}
    wanted = ('source/original', *candidates)
    missing = [name for name in wanted if name not in outputs]
    if missing:
        raise ValueError('Missing usable outputs: ' + ', '.join(missing))
    launch = json.loads((directory/'launch.json').read_text(encoding='utf-8'))
    verified = {}
    for name in wanted:
        metadata = outputs[name]
        path = Path(metadata['path']).resolve()
        if directory not in path.parents:
            raise ValueError(f'{name} path leaves the comparison directory.')
        digest = filehash(path)
        if digest != metadata['output_sha256']:
            raise ValueError(f'{name} hash no longer matches its checkpoint.')
        verified[name] = {'path': path, 'sha256': digest, 'bytes': path.stat().st_size}
    if verified['source/original']['sha256'] != launch['expected_source_sha256']:
        raise ValueError('Source hash no longer matches launch metadata.')
    return records, verified


def compact(metrics):
    if metrics.get('status') != 'pass':
        return metrics
    thresholds = {}
    for threshold, value in metrics['thresholds'].items():
        tolerance = value['tolerances_mm']['0.05']
        thresholds[threshold] = {
            'missing_fraction_at_0.05_mm': tolerance['missing_fraction'],
            'added_fraction_at_0.05_mm': tolerance['added_fraction'],
            'displacement_p95_mm': value['stroke_displacement_mm']['p95'],
            'absolute_thickness_p95_mm': value['thickness_absolute_change_mm']['p95'],
            'matched_width_samples': value['thickness_matched_samples'],
            'missing_components': len(value['potential_missing_components']),
            'potential_topology_change': value['potential_topology_change'],
        }
    return {'status': 'pass', 'exact': metrics['exact'], 'mm_per_pixel': metrics['mm_per_pixel'],
            'whole_page_mean_rgb_difference': metrics['whole_page_mean_rgb_difference'],
            'threshold_sensitive': metrics['threshold_sensitive'], 'thresholds': thresholds}


def priority(metrics):
    value = metrics['thresholds']['225']
    return (value['missing_fraction_at_0.05_mm'] or 0,
            value['absolute_thickness_p95_mm'] or 0,
            metrics['whole_page_mean_rgb_difference'] or 0)


def appearance_checks(source, candidates, directory, progress=print):
    with reader_for(source) as reader:
        count = len(reader.pages)
        geometries = [(float(page.cropbox.width) * float(page.get('/UserUnit', 1)),
                       float(page.cropbox.height) * float(page.get('/UserUnit', 1)))
                      for page in reader.pages]
    results = {name: {'pages': [], 'all_pages_144_complete': True, 'exact_pages_144': 0}
               for name in candidates}
    for number in range(1, count + 1):
        def measure_page():
            progress(f'Appearance 144 DPI: page {number}/{count}')
            page_results = {}
            with render(source, number - 1, 2) as before:
                for name, path in candidates.items():
                    try:
                        with render(path, number - 1, 2) as after:
                            metrics = compact(image_metrics(before, after,
                                (geometries[number-1][0] * 25.4 / 72,
                                 geometries[number-1][1] * 25.4 / 72)))
                        page_results[name] = {'page': number, 'metrics': metrics}
                    except Exception as exc:
                        page_results[name] = {'page': number, 'status': 'check failed',
                                              'error': f'{type(exc).__name__}: {exc}'}
            return {'schema': 1, 'kind': 'appearance-144', 'page': number,
                    'candidates': page_results}
        saved = checkpoint(directory/f'appearance-page-{number:04}.json', measure_page)
        for name in candidates:
            page_result = saved['candidates'][name]
            metrics = page_result.get('metrics', {})
            results[name]['pages'].append(page_result)
            results[name]['exact_pages_144'] += bool(metrics.get('exact'))
            results[name]['all_pages_144_complete'] &= metrics.get('status') == 'pass'
    for name, result in results.items():
        measured = [page for page in result['pages'] if page.get('metrics', {}).get('status') == 'pass']
        worst = max(measured, key=lambda page: priority(page['metrics'])) if measured else None
        page = worst['page'] if worst else 1
        page, crop = detail_crop(source, [page])
        def measure_detail():
            progress(f'Local width 576 DPI: {name}, page {page}')
            try:
                with render(source, page - 1, 8, crop) as before, render(candidates[name], page - 1, 8, crop) as after:
                    width = (crop[2] - crop[0]) * 25.4 / 72
                    height = (crop[3] - crop[1]) * 25.4 / 72
                    return {'schema': 1, 'kind': 'detail-576', 'candidate': name,
                            'page': page, 'crop_pdf_points': crop,
                            'metrics': compact(image_metrics(before, after, (width, height)))}
            except Exception as exc:
                return {'schema': 1, 'kind': 'detail-576', 'candidate': name,
                        'page': page, 'crop_pdf_points': crop, 'status': 'check failed',
                        'error': f'{type(exc).__name__}: {exc}'}
        detail = checkpoint(directory/f'detail-{slug(name)}.json', measure_detail)
        result['detail_576'] = {key: value for key, value in detail.items()
                                if key not in {'schema', 'kind', 'candidate'}}
    return results


def write_report(directory, review):
    lines = ['# Targeted P0 evidence review', '', f"Status: **{review['status']}**", '',
             'Stored optimizer outputs and Firefox repetitions were accepted only after their SHA-256 hashes matched.', '',
             '| Candidate | First visits | 200% zoom | 144-DPI exact pages | Function checks |',
             '| --- | ---: | ---: | ---: | --- |']
    for name, candidate in review['candidates'].items():
        timing = review['timings'][name]
        first = timing['first_visit']['change_percent']
        zoom = timing.get('zoom/2', {}).get('change_percent')
        checks = candidate['functions']
        states = {value['status'] for value in checks.values()}
        function = 'pass' if states == {'pass'} else 'confirmed difference' if 'confirmed difference' in states else 'unproven'
        lines.append(f"| {name} | {first:.1f}% | {zoom:.1f}% | {candidate['appearance']['exact_pages_144']}/{review['page_count']} | {function} |")
    lines += ['', 'Positive timing changes mean less elapsed time than the source. Revisit operations with unresolved observations remain unavailable.', '',
              '## Findings', ''] + [f'- {finding}' for finding in review['findings']]
    (directory/'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def execute(args):
    directory = args.output.resolve()
    if args.resume:
        directory.mkdir(parents=True, exist_ok=True)
    else:
        directory.mkdir(parents=True, exist_ok=False)
    records, verified = load_evidence(args.comparison, args.candidate)
    source = verified['source/original']['path']
    paths = {name: verified[name]['path'] for name in args.candidate}
    context = {'schema': 1, 'comparison': str(args.comparison.resolve()),
               'source_sha256': verified['source/original']['sha256'],
               'candidates': {name: verified[name]['sha256'] for name in args.candidate}}
    saved_context = checkpoint(directory/'context.json', lambda: context)
    if saved_context != context:
        raise ValueError('Resume context does not match the current source and candidates.')
    with reader_for(source) as reader:
        page_count = len(reader.pages)
    timings = timing_summary(records, args.candidate)
    candidates = {}
    for name, path in paths.items():
        def check_functions(name=name, path=path):
            print(f'Independent PDF-function checks: {name}', flush=True)
            return {'schema': 1, 'kind': 'function-checks', 'candidate': name,
                    'checks': independent_checks(source, path)}
        saved = checkpoint(directory/f'functions-{slug(name)}.json', check_functions)
        candidates[name] = {'artifact': {k: v for k, v in verified[name].items() if k != 'path'},
                            'functions': saved['checks']}
    appearances = appearance_checks(source, paths, directory)
    findings = []
    for name in args.candidate:
        candidates[name]['appearance'] = appearances[name]
        states = {value['status'] for value in candidates[name]['functions'].values()}
        if 'confirmed difference' in states or 'check failed' in states:
            findings.append(f'{name}: excluded by independent PDF-function evidence.')
        elif states != {'pass'}:
            findings.append(f'{name}: preservation remains unproven because some function signatures differ.')
        elif not appearances[name]['all_pages_144_complete']:
            findings.append(f'{name}: appearance checking did not complete on every page.')
        else:
            exact = appearances[name]['exact_pages_144']
            findings.append(f'{name}: function checks passed; {exact}/{page_count} pages were pixel-exact at 144 DPI.')
    review = {'schema': 1, 'status': 'complete', 'source': {k: v for k, v in verified['source/original'].items() if k != 'path'},
              'page_count': page_count, 'timings': timings, 'candidates': candidates,
              'unfinished_prior_run': {'status': 'interrupted', 'repository/viewing-copy': 'timeout',
                                       'revisit_zero_timings': 'unresolved'},
              'findings': findings}
    (directory/'review.json').write_text(json.dumps(review, indent=2), encoding='utf-8')
    write_report(directory, review)
    return review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candidate', action='append', default=[])
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    args.candidate = tuple(args.candidate or DEFAULT_CANDIDATES)
    try:
        review = execute(args)
        print(f"{review['status']}: {args.output.resolve()/'report.md'}")
    except (ValueError, OSError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    main()
