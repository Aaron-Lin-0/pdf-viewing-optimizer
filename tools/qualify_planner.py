"""Compare bounded planner choices with completed exhaustive comparison evidence."""
import argparse
import json
from pathlib import Path


def candidate_id(row):
    return f"{row.get('tool')}/{row.get('profile')}"


def canonical(identifier):
    if identifier in {'repository/default', 'repository/compact', 'repository/inline',
                      'repository/inline+compact', 'repository/viewing-copy'}:
        return 'repository/viewing-copy'
    return identifier


def nondominated(document):
    return {identifier for operations in document.get('nondominated', {}).values()
            for choices in operations.values() for identifier in choices}


def strict_useful(row, source_bytes, nondominated_ids):
    identifier = candidate_id(row)
    strict = row.get('recommendation') == 'Strict preservation candidate'
    smaller = row.get('bytes') is not None and source_bytes is not None and row['bytes'] < source_bytes
    measured_choice = identifier in nondominated_ids
    return strict and identifier != 'source/original' and (smaller or measured_choice), {
        'smaller': smaller, 'nondominated_operations': sorted(
            operation for operation, axes in row.get('_document_nondominated', {}).items()
            if any(identifier in choices for choices in axes.values()))}


def assess_viewing_decision(plan, timings, strict_ids, missed):
    if plan.get('objective') != 'viewing':
        return {'status': 'unassessed', 'reason': 'Only the viewing objective is calibrated.',
                'operations': [], 'material_regret_operations': [], 'missed_alternatives': []}
    available = set(timings)
    selected = {'source/original'} & available
    for identifier in plan.get('selected', []):
        if identifier in available:
            selected.add(identifier)
        else:
            selected.update(value for value in available if canonical(value) == canonical(identifier))
    eligible = strict_ids & available
    operations = sorted({operation for identifier in eligible for operation in timings[identifier]
                         if operation.split('/')[0] in {'navigation', 'first_visit', 'fresh_session', 'zoom'}})
    comparisons = []
    for operation in operations:
        exhaustive = {identifier: timings[identifier][operation] for identifier in eligible
                      if operation in timings[identifier] and timings[identifier][operation] > 0}
        shortlist = {identifier: value for identifier, value in exhaustive.items() if identifier in selected}
        if not exhaustive or not shortlist:
            continue
        best_ms = min(exhaustive.values())
        selected_ms = min(shortlist.values())
        threshold_ms = max(best_ms * .1, 50.0)
        delta_ms = selected_ms - best_ms
        comparisons.append({'operation': operation,
            'best_exhaustive_ids': sorted(identifier for identifier, value in exhaustive.items() if value == best_ms),
            'best_exhaustive_ms': best_ms,
            'best_shortlist_ids': sorted(identifier for identifier, value in shortlist.items() if value == selected_ms),
            'best_shortlist_ms': selected_ms, 'delta_ms': delta_ms,
            'delta_percent': delta_ms / best_ms * 100, 'threshold_ms': threshold_ms,
            'material_regret': delta_ms > threshold_ms})
    material = [item['operation'] for item in comparisons if item['material_regret']]
    reasons = {item['id']: item.get('reason', '') for item in plan.get('candidates', [])}
    alternatives = []
    for item in missed:
        identifier = item['id']
        wins = [row['operation'] for row in comparisons
                if row['material_regret'] and identifier in row['best_exhaustive_ids']]
        objective_mismatch = 'progressive network delivery' in reasons.get(identifier, '').lower()
        classification = ('unassessed' if not comparisons else 'material-regret' if wins else 'objective-mismatched'
                          if objective_mismatch else 'redundant-safe-alternative')
        alternatives.append({'id': identifier, 'classification': classification,
                             'material_operations': wins})
    return {'status': 'unassessed' if not comparisons else 'complete-with-regret' if material else 'complete',
            'threshold': 'best shortlist median exceeds best strict median by more than max(10%, 50 ms)',
            'operations': comparisons, 'material_regret_operations': material,
            'missed_alternatives': alternatives}


def qualify_case(case, base):
    plan_path = (base/case['plan']).resolve()
    results_path = (base/case['results']).resolve()
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    results = json.loads(results_path.read_text(encoding='utf-8'))
    if plan.get('schema') != 1 or results.get('status') != 'complete':
        raise ValueError(f"{case['name']}: plan or exhaustive result is incomplete")
    documents = results.get('documents', [])
    if len(documents) != 1:
        raise ValueError(f"{case['name']}: qualification requires one whole document")
    document = documents[0]
    if document.get('source_sha256') != plan.get('source', {}).get('sha256'):
        raise ValueError(f"{case['name']}: plan and exhaustive source hashes differ")
    rows = {candidate_id(row): row for row in document.get('outputs', [])}
    source = rows.get('source/original', {})
    nondominated_ids = nondominated(document)
    selected = plan.get('selected', [])
    selected_canonical = {canonical(identifier) for identifier in selected}
    reasons = {item['id']: item.get('reason') for item in plan.get('candidates', [])}
    verified = []
    unresolved = []
    false_safe = []
    unassessed = []
    for identifier in selected:
        row = rows.get(identifier)
        if row is None:
            aliases = [value for key, value in rows.items() if canonical(key) == canonical(identifier)]
            row = aliases[0] if aliases else None
        if row is None or row.get('status') not in {'usable', 'original selected'}:
            unassessed.append(identifier)
        elif row.get('recommendation') == 'Feature-loss diagnostic':
            false_safe.append(identifier)
        elif row.get('recommendation') == 'Strict preservation candidate':
            verified.append(identifier)
        else:
            unresolved.append(identifier)
    useful = []
    for identifier, row in rows.items():
        row['_document_nondominated'] = document.get('nondominated', {})
        qualifies, evidence = strict_useful(row, source.get('bytes'), nondominated_ids)
        if qualifies:
            useful.append({'id': identifier, 'canonical_id': canonical(identifier), 'evidence': evidence})
    missed = []
    for item in useful:
        if item['canonical_id'] not in selected_canonical:
            item['planner_reason'] = reasons.get(item['id'])
            missed.append(item)
    diagnostics = [identifier for identifier, row in rows.items()
                   if row.get('recommendation') == 'Feature-loss diagnostic']
    status = 'failed' if false_safe else 'complete-with-findings' if missed or unresolved or unassessed else 'complete'
    timings = {identifier: {operation: timing['median_ms'] for operation, timing in row.get('firefox_summary', {}).items()
                            if timing.get('resolved') and timing.get('median_ms') is not None}
               for identifier, row in rows.items()}
    strict_ids = {identifier for identifier, row in rows.items()
                  if row.get('recommendation') == 'Strict preservation candidate'}
    return {'name': case['name'], 'kind': case['kind'], 'status': status,
            'source_sha256': document['source_sha256'], 'pages': document.get('pages'),
            'selected': selected, 'selected_verified': verified,
            'selected_unresolved': unresolved, 'selected_unassessed': unassessed,
            'false_safe_recommendations': false_safe,
            'useful_exhaustive_candidates': useful, 'missed_useful_candidates': missed,
            'exhaustive_feature_loss_diagnostics': diagnostics,
            'exhaustive_unavailable_or_failed': [identifier for identifier, row in rows.items()
                if row.get('status') not in {'usable', 'original selected'}],
            'decision_quality': assess_viewing_decision(plan, timings, strict_ids, missed)}


def review_candidate_status(candidate, page_count):
    function_states = {item.get('status') for item in candidate.get('functions', {}).values()}
    appearance = candidate.get('appearance', {})
    detail = appearance.get('detail_576', {}).get('metrics', {})
    strict = (function_states == {'pass'} and appearance.get('all_pages_144_complete') is True
              and appearance.get('exact_pages_144') == page_count
              and detail.get('status') == 'pass' and detail.get('exact') is True)
    explicit_failure = 'fail' in function_states or detail.get('status') == 'fail'
    return 'verified' if strict else 'failed' if explicit_failure else 'unresolved'


def qualify_review_case(case, base):
    plan = json.loads((base/case['plan']).resolve().read_text(encoding='utf-8'))
    review = json.loads((base/case['review']).resolve().read_text(encoding='utf-8'))
    if plan.get('schema') != 1 or review.get('status') != 'complete':
        raise ValueError(f"{case['name']}: plan or targeted review is incomplete")
    source = review.get('source', {})
    if source.get('sha256') != plan.get('source', {}).get('sha256'):
        raise ValueError(f"{case['name']}: plan and targeted-review source hashes differ")
    candidates = review.get('candidates', {})
    selected = plan.get('selected', [])
    verified, unresolved, false_safe, unassessed = [], [], [], []
    for identifier in selected:
        matches = [value for key, value in candidates.items()
                   if canonical(key) == canonical(identifier)]
        if not matches:
            unassessed.append(identifier)
            continue
        states = {review_candidate_status(value, review.get('page_count')) for value in matches}
        if 'verified' in states:
            verified.append(identifier)
        elif 'failed' in states:
            false_safe.append(identifier)
        else:
            unresolved.append(identifier)
    selected_canonical = {canonical(identifier) for identifier in selected}
    useful = [{'id': identifier, 'canonical_id': canonical(identifier),
               'evidence': {'smaller': candidate.get('artifact', {}).get('bytes', source.get('bytes'))
                                      < source.get('bytes')}}
              for identifier, candidate in candidates.items()
              if review_candidate_status(candidate, review.get('page_count')) == 'verified'
              and candidate.get('artifact', {}).get('bytes') is not None
              and source.get('bytes') is not None
              and candidate['artifact']['bytes'] < source['bytes']]
    missed = [item for item in useful if item['canonical_id'] not in selected_canonical]
    status = 'failed' if false_safe else 'complete-with-findings' if missed or unresolved or unassessed else 'complete'
    timings = {identifier: {operation: timing['median_total_ms'] for operation, timing in values.items()
                            if timing.get('complete_repetitions') == timing.get('required_repetitions')
                            and timing.get('median_total_ms') is not None}
               for identifier, values in review.get('timings', {}).items()}
    strict_ids = {'source/original'} | {identifier for identifier, candidate in candidates.items()
                                       if review_candidate_status(candidate, review.get('page_count')) == 'verified'}
    return {'name': case['name'], 'kind': case['kind'], 'evidence_type': 'targeted-review',
            'status': status, 'source_sha256': source['sha256'], 'pages': review.get('page_count'),
            'selected': selected, 'selected_verified': verified,
            'selected_unresolved': unresolved, 'selected_unassessed': unassessed,
            'false_safe_recommendations': false_safe,
            'useful_exhaustive_candidates': useful, 'missed_useful_candidates': missed,
            'exhaustive_feature_loss_diagnostics': [],
            'exhaustive_unavailable_or_failed': [identifier for identifier, candidate in candidates.items()
                if review_candidate_status(candidate, review.get('page_count')) != 'verified'],
            'decision_quality': assess_viewing_decision(plan, timings, strict_ids, missed)}


def execute(manifest, output):
    manifest = Path(manifest).resolve()
    specification = json.loads(manifest.read_text(encoding='utf-8'))
    cases = specification.get('cases', [])
    kinds = {case.get('kind') for case in cases}
    if not cases or not {'generated', 'authorized'} <= kinds:
        raise ValueError('Qualification requires generated and authorized whole-document cases.')
    results = [qualify_review_case(case, manifest.parent) if 'review' in case
               else qualify_case(case, manifest.parent) for case in cases]
    false_safe = sum((case['false_safe_recommendations'] for case in results), [])
    missed = sum((case['missed_useful_candidates'] for case in results), [])
    decision_cases = [case['decision_quality'] for case in results]
    decision_status = ('complete-with-regret' if any(case['material_regret_operations'] for case in decision_cases)
                       else 'complete-with-coverage-gaps' if any(case['status'] == 'unassessed' for case in decision_cases)
                       else 'complete')
    status = 'failed' if false_safe else 'complete-with-findings' if missed or any(
        case['selected_unresolved'] or case['selected_unassessed'] for case in results) else 'complete'
    qualification = {'schema': 1, 'status': status, 'cases': results,
                     'false_safe_recommendations': false_safe,
                     'missed_useful_candidates': missed,
                     'decision_quality': {'status': decision_status,
                         'material_regret_operations': sum((case['material_regret_operations'] for case in decision_cases), [])},
                     'note': 'Useful means strict tested preservation plus smaller bytes or a nondominated measured operation; it is not a universal ranking.'}
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output/'qualification.json').write_text(json.dumps(qualification, indent=2), encoding='utf-8')
    lines = ['# Planner qualification', '', f'Overall status: **{status}**', '',
             f"Decision quality: **{decision_status}**", '',
             '| Case | Kind | Selected | Verified | False-safe | Missed useful | Decision quality |',
             '| --- | --- | ---: | ---: | ---: | ---: | --- |']
    for case in results:
        lines.append(f"| {case['name']} | {case['kind']} | {len(case['selected'])} | {len(case['selected_verified'])} | {len(case['false_safe_recommendations'])} | {len(case['missed_useful_candidates'])} | {case['decision_quality']['status']} |")
    lines += ['', 'No combined quality score is used. Missing, unresolved and unavailable evidence remains explicit.', '']
    (output/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return qualification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = execute(args.manifest, args.output)
        print(f"{result['status']}: {args.output.resolve()/'report.md'}")
    except (ValueError, OSError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    main()
