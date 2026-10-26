"""Create a bounded, explainable optimization plan from PDF characterization."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]
from compare_optimizers import load_config
from pdf_characterize import characterize
from viewing_copy import filehash


REPOSITORY_FILES = ('optimize_pdf.py', 'viewing_copy.py', 'handwriting.py',
                    'annotation_ink.py', 'geometry.py', 'navigation.py')


def repository_provenance():
    commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                            capture_output=True, text=True).stdout.strip() or None
    dirty = bool(subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain'],
                                capture_output=True, text=True).stdout.strip())
    return {'availability': 'available', 'declared_version': commit or 'working-tree',
            'version_preflight_required': False, 'repository_dirty': dirty,
            'prefix_hashes': {name: filehash(ROOT/name) for name in REPOSITORY_FILES}}


def external_provenance(config, tool, source_sha256, profile):
    imports = [entry for entry in config.get('imports', [])
               if entry['source_sha256'] == source_sha256 and
               entry['tool'] == tool and entry['profile'] == profile]
    if imports:
        entry = imports[0]
        path = Path(entry['path'])
        return {'availability': 'available' if path.is_file() else 'unavailable',
                'source': 'import', 'declared_version': entry['version'],
                'settings': entry['settings'],
                'output_sha256': filehash(path) if path.is_file() else None,
                'reason': None if path.is_file() else 'Configured import is missing.'}
    spec = config.get('tools', {}).get(tool)
    if not spec:
        return {'availability': 'unavailable', 'source': 'executable',
                'reason': f'{tool} is not configured.'}
    executable = shutil.which(spec['command'][0]) or spec['command'][0]
    available = Path(executable).is_file()
    hashes = {}
    for value in [executable, *spec['command'][1:]]:
        if Path(value).is_file():
            hashes[str(Path(value).resolve())] = filehash(value)
    return {'availability': 'available' if available else 'unavailable',
            'source': 'executable', 'declared_version': spec['version'],
            'version_preflight_required': True, 'prefix_hashes': hashes,
            'reason': None if available else 'Configured executable is missing.'}


def signals(characterization):
    pages = characterization['pages']
    return {
        'pages': characterization['page_count'],
        'partial_pages': characterization['coverage']['pages_partial'],
        'document_features': characterization['coverage']['document_features'],
        'expanded_operations': sum(page['content'].get('expanded_operations', 0) for page in pages),
        'form_calls': sum(page['content'].get('form_calls', 0) for page in pages),
        'eligible_annotation_prefix': sum(page['annotations']['eligible_leading_prefix'] for page in pages),
        'same_width_adjacent_segments': sum(page['content'].get('same_width_adjacent_segments', 0) for page in pages),
        'transparency_uses': sum(page['content'].get('transparency_state_uses', 0) +
                                 page['content'].get('transparency_groups', 0) for page in pages),
        'image_calls': characterization['document']['images']['calls'],
        'text_pages': characterization['document']['text']['pages_with_text'],
        'protected_features': bool(characterization['document']['forms']['present'] or
                                   characterization['document']['layers']['present'] or
                                   characterization['document']['attachments'] or
                                   characterization['document']['links']['count'] or
                                   characterization['document']['structural_tags']['present']),
        'xfa': characterization['document']['forms']['xfa'],
    }


def candidate(identifier, family, tool, profile, lane, applicable, reason, evidence,
              expected, target, risks, prerequisites, provenance, confidence='medium'):
    decision = 'skip'
    if applicable and provenance['availability'] == 'unavailable':
        decision = 'unavailable'
        reason = provenance.get('reason') or f'{tool} is unavailable.'
    return {'id': identifier, 'family': family, 'tool': tool, 'profile': profile,
            'lane': lane, 'decision': decision, 'reason': reason,
            'eligibility_evidence': evidence,
            'expected_storage_effect': {'estimate': 'uncalibrated', 'mechanism': expected},
            'target_viewing_operation': target, 'confidence': confidence,
            'preservation_risks': risks,
            'cost_estimate': {'estimate': 'uncalibrated',
                              'drivers': ['page count', 'expanded drawing operations',
                                          'all-page preservation checks']},
            'prerequisites': prerequisites, 'provenance': provenance,
            '_applicable': applicable}


def build_plan(characterization, config, objective='viewing', allow_experimental=False):
    if characterization.get('schema') != 2:
        raise ValueError('Planner requires characterization schema 2.')
    source_hash = characterization['source_sha256']
    evidence_hash = hashlib.sha256(json.dumps(characterization, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()
    signal = signals(characterization)
    repository = repository_provenance()
    qpdf_flate = external_provenance(config, 'qpdf', source_hash, 'flate')
    qpdf_linear = external_provenance(config, 'qpdf', source_hash, 'flate-linearized')
    mupdf = external_provenance(config, 'mupdf', source_hash, 'compress-packed')
    mattdesl = external_provenance(config, 'mattdesl', source_hash, 'quality-95')
    boox = external_provenance(config, 'boox-pdf', source_hash, 'vector-6')
    boox_raster = external_provenance(config, 'boox-pdf', source_hash, 'color-300')
    ocrmypdf = external_provenance(config, 'ocrmypdf', source_hash, 'level-1')
    pdfsizeopt = external_provenance(config, 'pdfsizeopt', source_hash, 'default')
    ghostscript = external_provenance(config, 'ghostscript', source_hash, 'default')
    acrobat = external_provenance(config, 'acrobat', source_hash, 'conservative')
    boox_note = external_provenance(config, 'boox-note', source_hash, 'default-export')
    complete = signal['partial_pages'] == 0 and signal['document_features'] == 'complete'
    exact_signal = signal['form_calls'] > 0 or signal['eligible_annotation_prefix'] > 0
    fast_signal = signal['same_width_adjacent_segments'] > 0 and signal['transparency_uses'] == 0
    candidates = [
        candidate('repository/viewing-copy', 'repository exact inlining/conversion',
            'repository', 'viewing-copy', 'conservative', complete and exact_signal,
            'No recognized Form-call or eligible annotation-prefix opportunity.' if not exact_signal else
            'Applicable exact repository mechanism.',
            {'form_calls': signal['form_calls'],
             'eligible_annotation_prefix': signal['eligible_annotation_prefix']},
            'May remove duplicate structure and inline supported vector calls; storage effect is document-specific.',
            ['first draw', 'zoom redraw', 'storage'],
            ['Unsupported representations are retained or reject the candidate.',
             'Converted eligible annotations lose individual editability in the viewing copy.'],
            ['PDFium, PDF.js, Node.js and isolated Firefox acceptance'], repository, 'high'),
        candidate('qpdf/flate', 'qpdf Flate/object streams', 'qpdf', 'flate', 'conservative',
            complete and objective != 'progressive',
            'Progressive delivery uses the linearized profile.' if objective == 'progressive' else
            'Conservative structural/compression trial; benefit remains uncalibrated.',
            {'source_bytes': characterization['bytes'], 'image_calls': signal['image_calls']},
            'Recompresses streams and generates object streams; does not simplify handwriting geometry.',
            ['storage', 'load'],
            ['Smaller bytes do not establish faster drawing.', 'All PDF functions still require independent verification.'],
            ['Pinned qpdf version/interface preflight'], qpdf_flate),
        candidate('qpdf/flate-linearized', 'qpdf linearized', 'qpdf', 'flate-linearized', 'conservative',
            complete and objective == 'progressive',
            'Only applicable when progressive network delivery is the objective.' if objective != 'progressive' else
            'Progressive delivery explicitly requested.',
            {'objective': objective},
            'Adds linearization for compatible network delivery; local render benefit is not expected.',
            ['network first page', 'storage'],
            ['Local-file viewing is a different objective.', 'All PDF functions require independent verification.'],
            ['Pinned qpdf version/interface preflight', 'compatible HTTP delivery'], qpdf_linear),
        candidate('repository/fast-handwriting', 'repository fast-handwriting', 'repository',
            'fast-handwriting', 'experimental', complete and fast_signal,
            'Requires consecutive same-width runs, no transparency, and explicit experimental permission.'
            if not fast_signal or not allow_experimental else 'Applicable experimental batching mechanism.',
            {'same_width_adjacent_segments': signal['same_width_adjacent_segments'],
             'transparency_uses': signal['transparency_uses']},
            'Batches recognized consecutive line segments; storage and rendering effects are uncalibrated.',
            ['first draw', 'zoom redraw'],
            ['P0 found severe local changes on the reviewed document.',
             'Never eligible for publication without full appearance and local-width review.'],
            ['Explicit experimental permission', 'all-page and local-width verification'], repository, 'low'),
        candidate('mupdf/compress-packed', 'MuPDF clean', 'mupdf', 'compress-packed', 'held',
            False, 'P0 left two PDF-function signatures unproven; held from normal routing.',
            {'protected_features': signal['protected_features']},
            'May clean duplicate structure and compress streams.', ['storage', 'load'],
            ['Function equivalence is unproven despite exact tested pixels in P0.'],
            ['Qualification that resolves function-signature differences'], mupdf, 'low'),
        candidate('mattdesl/quality-95', 'image optimizer', 'mattdesl', 'quality-95', 'held',
            False, 'Lossy image processing was not requested.',
            {'image_calls': signal['image_calls']},
            'May recompress inefficient raster images; quality 95 is still lossy.', ['load', 'storage'],
            ['May alter photographs, diagrams or image-based handwriting.'],
            ['Explicit lossy-image permission', 'source-derived image-region review'], mattdesl, 'low'),
        candidate('boox-pdf/vector-6', 'BOOX PDF vector', 'boox-pdf', 'vector-6', 'quarantined',
            False, 'Quarantined after confirmed missing fraction bars and underline evidence.',
            {'application_label_used': False},
            'Tool-specific vector rewriting; no general benefit estimate.', ['uncalibrated'],
            ['Confirmed visible-content regression in P0 evidence.'],
            ['Regression fix and independent requalification'], boox, 'low'),
        candidate('ocrmypdf/level-1', 'OCRmyPDF', 'ocrmypdf', 'level-1', 'held', False,
            'OCR or optimization-only OCRmyPDF processing was not requested and its pinned no-OCR interface is unqualified.',
            {'text_pages': signal['text_pages'], 'image_calls': signal['image_calls']},
            'May optimize scanned images and optionally add OCR; no effect is assumed.', ['storage', 'search'],
            ['Unrequested OCR can alter searchable text and image encoding.'],
            ['Explicit OCR intent or qualified no-OCR interface'], ocrmypdf, 'low'),
        candidate('pdfsizeopt/default', 'pdfsizeopt', 'pdfsizeopt', 'default', 'held', False,
            'No qualified TeX/font-heavy routing rule exists.', {},
            'May compact font/image-heavy TeX PDFs; viewing benefit is uncalibrated.', ['storage'],
            ['Pinned implementation has not been qualified on current fixtures.'],
            ['Pinned executable and fixture qualification'], pdfsizeopt, 'low'),
        candidate('ghostscript/default', 'Ghostscript pdfwrite', 'ghostscript', 'default', 'held', False,
            'Broad PDF rewriting requires explicit tradeoff permission.', {},
            'Rewrites document structure and resources; effects are profile- and document-specific.', ['uncalibrated'],
            ['May alter handwriting, color, fonts, transparency and PDF functions.'],
            ['Explicit broad-conversion permission and full independent verification'], ghostscript, 'low'),
        candidate('acrobat/conservative', 'Acrobat Optimizer', 'acrobat', 'conservative', 'held', False,
            'No source-hash-matched imported Acrobat output was requested.', {},
            'Uses recorded local Acrobat settings; effect depends on those settings.', ['storage'],
            ['Discard or flatten settings can remove required PDF functions.'],
            ['Licensed local run, recorded settings and source-hash-matched import'], acrobat, 'low'),
        candidate('boox-pdf/color-300', 'BOOX PDF raster', 'boox-pdf', 'color-300', 'held', False,
            'Whole-page rasterization was not explicitly permitted.', {},
            'Replaces vector pages with fixed-resolution images.', ['first draw'],
            ['May lose thin strokes, searchable text, navigation and interactive data.'],
            ['Explicit visual tradeoff and human review'], boox_raster, 'low'),
        candidate('boox-note/default-export', 'BOOX Note Optimizer', 'boox-note', 'default-export', 'held', False,
            'No matching source-hash-verified .note workflow was supplied.', {},
            'Optimizes an upstream BOOX note before PDF export.', ['uncalibrated'],
            ['Point reduction and export can alter pressure, width and layout.'],
            ['Matching .note source and independently verified export workflow'], boox_note, 'low'),
    ]
    for item in candidates:
        item['cost_estimate']['drivers'] = {
            'pages': signal['pages'], 'expanded_operations': signal['expanded_operations']}
    if not complete or signal['xfa']:
        reason = ('XFA is outside the acceptance harness.' if signal['xfa'] else
                  'Characterization is partial; unresolved source content blocks planning.')
        for item in candidates:
            if item['decision'] != 'unavailable':
                item['decision'], item['reason'] = 'skip', reason
    else:
        conservative = 0
        experimental = 0
        for item in candidates:
            if not item.pop('_applicable') or item['decision'] == 'unavailable':
                continue
            if item['lane'] == 'conservative' and conservative < 2:
                item['decision'] = 'run'; conservative += 1
            elif item['lane'] == 'experimental' and allow_experimental and experimental < 1:
                item['decision'] = 'run'; experimental += 1
        for item in candidates:
            item.pop('_applicable', None)
    for item in candidates:
        item.pop('_applicable', None)
    selected = [item['id'] for item in candidates if item['decision'] == 'run']
    return {'schema': 1, 'objective': objective,
            'source': {'sha256': source_hash, 'bytes': characterization['bytes'],
                       'pages': characterization['page_count']},
            'characterization': {'schema': 2, 'sha256': evidence_hash,
                                 'coverage': characterization['coverage']},
            'signals': signal,
            'limits': {'conservative_candidates': 2, 'experimental_candidates': 1},
            'selected': selected, 'candidates': candidates,
            'publication_policy': 'Planning never approves publication; every output must pass its applicable preservation and performance gates.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--tools-config', type=Path)
    parser.add_argument('--objective', choices=('viewing', 'storage', 'progressive'), default='viewing')
    parser.add_argument('--allow-experimental', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.tools_config) if args.tools_config else {'tools': {}, 'imports': []}
        plan = build_plan(characterize(args.pdf), config, args.objective, args.allow_experimental)
        text = json.dumps(plan, indent=2, sort_keys=True)
        if args.output:
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(text + '\n')
        else:
            print(text)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    main()
