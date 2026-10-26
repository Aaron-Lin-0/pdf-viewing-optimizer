"""Execute a bounded optimization plan without publishing a candidate."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]
from compare_optimizers import commands, independent_checks, load_config, run_process
from navigation import reader_for, validate_input
from viewing_copy import filehash
from evidence_cache import EvidenceCache

REPOSITORY_FILES = {'optimize_pdf.py', 'viewing_copy.py', 'handwriting.py',
                    'annotation_ink.py', 'geometry.py', 'navigation.py'}


def copy_exclusive(source, destination):
    with Path(source).open('rb') as before, Path(destination).open('xb') as after:
        shutil.copyfileobj(before, after)


def verify_plan(plan, source):
    if plan.get('schema') != 1:
        raise ValueError('Runner requires plan schema 1.')
    selected = plan.get('selected', [])
    by_id = {candidate['id']: candidate for candidate in plan.get('candidates', [])}
    if len(selected) != len(set(selected)) or any(name not in by_id for name in selected):
        raise ValueError('Plan selected list is invalid.')
    if any(by_id[name].get('decision') != 'run' for name in selected):
        raise ValueError('Plan may execute only candidates marked run.')
    conservative = sum(by_id[name]['lane'] == 'conservative' for name in selected)
    experimental = sum(by_id[name]['lane'] == 'experimental' for name in selected)
    limits = plan.get('limits', {})
    if conservative > limits.get('conservative_candidates', 0) or experimental > limits.get('experimental_candidates', 0):
        raise ValueError('Plan exceeds its candidate limits.')
    digest = filehash(source)
    if digest != plan.get('source', {}).get('sha256') or Path(source).stat().st_size != plan['source']['bytes']:
        raise ValueError('Source does not match the plan hash and size.')
    return digest, [by_id[name] for name in selected]


def verify_provenance(candidate, config, source_sha256):
    provenance = candidate['provenance']
    if provenance.get('availability') != 'available':
        raise ValueError('Selected candidate is not available.')
    if candidate['tool'] == 'repository':
        if set(provenance.get('prefix_hashes', {})) != REPOSITORY_FILES:
            raise ValueError('Repository tool hashes are incomplete.')
        for name, digest in provenance.get('prefix_hashes', {}).items():
            path = ROOT/name
            if not path.is_file() or filehash(path) != digest:
                raise ValueError(f'Repository tool hash changed: {name}')
        return None
    if provenance.get('source') == 'import':
        imports = [entry for entry in config.get('imports', [])
                   if entry['source_sha256'] == source_sha256 and
                   entry['tool'] == candidate['tool'] and entry['profile'] == candidate['profile']]
        if not imports:
            raise ValueError('Source-hash-matched import is missing.')
        entry = imports[0]
        if (entry['version'] != provenance.get('declared_version') or
                entry['settings'] != provenance.get('settings')):
            raise ValueError('Imported version/settings do not match the plan.')
        return entry
    spec = config.get('tools', {}).get(candidate['tool'])
    if not spec or spec['version'] != provenance.get('declared_version'):
        raise ValueError('Configured tool/version does not match the plan.')
    executable = shutil.which(spec['command'][0]) or spec['command'][0]
    expected = {str(Path(value).resolve()) for value in [executable, *spec['command'][1:]]
                if Path(value).is_file()}
    if set(provenance.get('prefix_hashes', {})) != expected:
        raise ValueError('Configured tool hashes are incomplete.')
    for name, digest in provenance.get('prefix_hashes', {}).items():
        path = Path(name)
        if not path.is_file() or filehash(path) != digest:
            raise ValueError(f'Configured tool hash changed: {name}')
    return spec


def preflight_tool(candidate, spec, work, timeout):
    if candidate['tool'] == 'repository' or candidate['provenance'].get('source') == 'import':
        return {'version_s': 0, 'help_s': 0}
    version_command = spec.get('version_command') or spec['command'] + spec['version_args']
    version = run_process(version_command, work, 'version', timeout)
    help_result = run_process(spec['command'] + spec['help_args'], work, 'help', timeout)
    version_text = ''.join((work/f'version.{kind}.log').read_text(errors='replace') for kind in ('stdout', 'stderr'))
    help_text = ''.join((work/f'help.{kind}.log').read_text(errors='replace') for kind in ('stdout', 'stderr'))
    _, tokens = commands(candidate['tool'], candidate['profile'], 'input.pdf', 'output.pdf')
    if (version.get('status') != 'completed' or version.get('exit_code') != 0 or
            candidate['provenance']['declared_version'] not in version_text):
        raise ValueError('Pinned version preflight failed.')
    if help_result.get('status') != 'completed' or help_result.get('exit_code') != 0 or any(token not in help_text for token in tokens):
        raise ValueError('Pinned command interface preflight failed.')
    return {'version_s': version.get('duration_s', 0), 'help_s': help_result.get('duration_s', 0)}


def check_status(checks):
    states = {value['status'] for value in checks.values()}
    if 'confirmed difference' in states or 'check failed' in states:
        return 'rejected'
    return 'function-pass' if states == {'pass'} else 'unproven'


def write_report(directory, state):
    (directory/'result.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
    lines = ['# Companion execution report', '', f"Status: **{state['status']}**", '',
             'Final fallback: **original source snapshot**.', '',
             '| Candidate | Status | Bytes | Reason |', '| --- | --- | ---: | --- |']
    for row in state['candidates']:
        lines.append(f"| {row['id']} | {row['status']} | {row.get('bytes', '')} | {row.get('reason', '')} |")
    (directory/'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def execute(args):
    source = args.pdf.resolve(strict=True)
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    digest, selected = verify_plan(plan, source)
    config = load_config(args.tools_config) if args.tools_config else {'tools': {}, 'imports': []}
    directory = args.output.resolve()
    resume = getattr(args, 'resume', False)
    run_context = {'schema': 1, 'source_sha256': digest, 'plan_sha256': filehash(args.plan),
                   'config_sha256': filehash(args.tools_config) if args.tools_config else None,
                   'selected': [item['id'] for item in selected],
                   'timeout': args.timeout, 'verification_timeout': args.verification_timeout,
                   'max_output_bytes': args.max_output_bytes,
                   'runner_sha256': filehash(Path(__file__).resolve())}
    if resume:
        if not directory.is_dir():
            raise FileNotFoundError('Resume output directory does not exist.')
        saved = json.loads((directory/'context.json').read_text(encoding='utf-8'))
        if saved != run_context:
            raise ValueError('Resume context does not match source, plan, tools, budgets and runner.')
        final = directory/'result.json'
        if final.is_file():
            state = json.loads(final.read_text(encoding='utf-8'))
            if state.get('status') == 'complete':
                return state
    else:
        if directory.exists() or directory.is_symlink():
            raise FileExistsError('Output must not exist.')
        directory.mkdir(parents=True, exist_ok=False)
        with (directory/'context.json').open('x', encoding='utf-8') as stream:
            json.dump(run_context, stream, indent=2)
    snapshot = directory/'source.pdf'
    if not snapshot.exists():
        copy_exclusive(source, snapshot)
    if filehash(snapshot) != digest or filehash(source) != digest:
        raise RuntimeError('Source changed during snapshot.')
    with reader_for(snapshot) as reader:
        validate_input(reader)
        page_count = len(reader.pages)
    state = {'schema': 1, 'status': 'running', 'source_sha256': digest,
             'pages': page_count, 'fallback': 'source.pdf', 'candidates': [], 'stage_costs': {}}
    operations = EvidenceCache(directory/'operations')
    for candidate in selected:
        candidate_hash = hashlib.sha256(json.dumps(candidate, sort_keys=True,
                                                   separators=(',', ':')).encode()).hexdigest()
        completion_context = {
            'operation': 'candidate-execution', 'source_sha256': digest,
            'candidate_sha256': candidate_hash, 'page': None, 'crop': None,
            'renderer': candidate['tool'],
            'renderer_version': candidate['provenance'].get('declared_version'),
            'geometry': {'pages': page_count},
            'resolution': {'timeout': args.timeout, 'verification_timeout': args.verification_timeout,
                           'max_output_bytes': args.max_output_bytes},
            'background': 'not-applicable', 'thresholds': [],
            'metric_version': run_context['runner_sha256']}
        cached = operations.load_json(completion_context)
        work = directory/candidate['id'].replace('/', '-')
        output = work/'output.pdf'
        if cached is not None:
            if cached['status'] in {'function-pass', 'unproven'}:
                if not output.is_file() or filehash(output) != cached.get('output_sha256'):
                    raise RuntimeError('Completed candidate output is missing or changed; refusing resume.')
            state['candidates'].append(cached)
            continue
        if work.exists():
            if work.parent != directory:
                raise RuntimeError('Candidate work path leaves run directory.')
            shutil.rmtree(work)
        work.mkdir()
        private_input = work/'input.pdf'
        row = {'id': candidate['id'], 'status': 'failed', 'stage_costs': {}}
        try:
            spec = verify_provenance(candidate, config, digest)
            preflight = preflight_tool(candidate, spec, work, args.timeout)
            row['stage_costs']['preflight_s'] = preflight['version_s'] + preflight['help_s']
            copy_exclusive(snapshot, private_input)
            if candidate['provenance'].get('source') == 'import':
                imported = Path(spec['path']).resolve(strict=True)
                copy_exclusive(imported, output)
                if filehash(output) != candidate['provenance']['output_sha256']:
                    raise RuntimeError('Imported output hash changed.')
            else:
                tail, _ = commands(candidate['tool'], candidate['profile'], private_input, output)
                command = tail if candidate['tool'] == 'repository' else spec['command'] + tail
                row['execution'] = run_process(command, work, 'optimizer', args.timeout)
                row['stage_costs']['optimization_s'] = row['execution'].get('duration_s')
                success = (0, 3) if candidate['tool'] == 'qpdf' else (0,)
                if row['execution'].get('status') == 'timeout':
                    row['status'] = 'timeout'
                if row['execution'].get('status') != 'completed' or row['execution'].get('exit_code') not in success:
                    raise RuntimeError('Optimizer failed or exceeded its time budget.')
            if filehash(snapshot) != digest or filehash(source) != digest or filehash(private_input) != digest:
                raise RuntimeError('Source or disposable input changed.')
            if not output.is_file():
                report_path = output.with_suffix('.json')
                report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
                if candidate['tool'] == 'repository' and candidate['profile'] == 'viewing-copy' and report.get('status') == 'no improvement':
                    row.update(status='no-improvement', reason='Production viewing-copy retained the original.')
                else:
                    raise RuntimeError('Optimizer produced no output.')
            else:
                if output.stat().st_size > args.max_output_bytes:
                    row['status'] = 'budget-exhausted'
                    raise RuntimeError('Candidate exceeded its output-size budget.')
                with reader_for(output) as reader:
                    validate_input(reader)
                    if len(reader.pages) != page_count:
                        raise RuntimeError('Candidate page count changed.')
                check_file = work/'function-results.json'
                check_run = run_process([sys.executable, str(Path(__file__).resolve()), '--check-worker',
                                         str(snapshot), str(output), str(check_file)],
                                        work, 'function-check', args.verification_timeout)
                row['stage_costs']['function_checks_s'] = check_run.get('duration_s')
                if check_run.get('status') == 'timeout':
                    row['status'] = 'timeout'
                if check_run.get('status') != 'completed' or check_run.get('exit_code') != 0 or not check_file.is_file():
                    raise RuntimeError('Independent function checks failed or exceeded their time budget.')
                checks = json.loads(check_file.read_text(encoding='utf-8'))
                row.update(status=check_status(checks), bytes=output.stat().st_size,
                           output_sha256=filehash(output), function_checks=checks,
                           publication='not approved')
                if row['status'] == 'rejected':
                    output.unlink()
        except Exception as exc:
            row['reason'] = f'{type(exc).__name__}: {exc}'
            if output.exists():
                output.unlink()
        finally:
            if private_input.exists():
                private_input.unlink()
        operations.store_json(completion_context, row)
        state['candidates'].append(row)
    state['source_integrity'] = filehash(source) == digest and filehash(snapshot) == digest
    state['status'] = 'complete' if state['source_integrity'] else 'failed'
    state['cache'] = dict(operations.stats)
    state['stage_costs'] = {row['id']: row.get('stage_costs', {}) for row in state['candidates']}
    write_report(directory, state)
    return state


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--check-worker':
        result = independent_checks(sys.argv[2], sys.argv[3])
        with Path(sys.argv[4]).open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tools-config', type=Path)
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--verification-timeout', type=float, default=1800)
    parser.add_argument('--max-output-bytes', type=int, default=2_000_000_000)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        state = execute(args)
        print(f"{state['status']}: {args.output.resolve()/'report.md'}")
    except (ValueError, OSError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    main()
