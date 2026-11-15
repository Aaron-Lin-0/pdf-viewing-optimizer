"""Research-only reduction of existing opaque, round-joined polyline Form paths."""
from collections import Counter
from contextlib import closing
from pathlib import Path
import math
import re
import hashlib
import subprocess
import tempfile

from pypdf import PdfWriter
from pypdf.generic import ContentStream, StreamObject, IndirectObject, ArrayObject, ByteStringObject, TextStringObject

from handwriting import HEADER, NUMBER, PEN_STATE, batching_contexts
from lossless_verify import verify_lossless, strict_parsing, strict_reader, validate_quick_input
from pdf_runtime import filehash, qpdf_identity, _run

TOKEN = re.compile(NUMBER + rb"|[A-Za-z]+")
MAX_POINTS = 100000


def exact_writer(reader):
    writer=PdfWriter(reader,full=True)
    identifiers=reader.trailer.get('/ID')
    if identifiers is not None:
        writer._ID=ArrayObject([ByteStringObject(value.original_bytes if isinstance(value,TextStringObject) else bytes(value))
                                for value in identifiers])
    return writer


def paths(data):
    """Return literal coordinate tokens and deletion spans, or decline the entire Form."""
    if not data.startswith(HEADER):
        return None
    body = data[len(HEADER):]
    tokens = []
    end = 0
    for token in TOKEN.finditer(body):
        if len(tokens) >= MAX_POINTS * 4:
            return None
        if body[end:token.start()].strip():
            return None
        tokens.append(token)
        end = token.end()
    if body[end:].strip():
        return None
    result = []
    i = 0
    try:
        while i < len(tokens):
            width = float(tokens[i][0])
            if not math.isfinite(width) or not 0 < width <= 1e9 or tokens[i + 1][0] != b'w':
                return None
            start = i
            i += 2
            points, literals, spans = [], [], []
            while True:
                x, y, op = tokens[i:i + 3]
                if op[0] != (b'm' if not points else b'l'):
                    return None
                point = float(x[0]), float(y[0])
                if not all(math.isfinite(v) and abs(v) <= 1e9 for v in point):
                    return None
                points.append(point)
                literals.append((x[0].decode(), y[0].decode()))
                spans.append((len(HEADER) + x.start(), len(HEADER) + op.end()))
                i += 3
                if tokens[i][0] == b'S':
                    i += 1
                    break
            if len(points) < 2:
                return None
            result.append({'width': width, 'width_literal': tokens[start][0].decode(),
                           'points': points, 'literals': literals, 'spans': spans,
                           'range': (len(HEADER)+tokens[start].start(),len(HEADER)+tokens[i-1].end())})
    except (ValueError, IndexError):
        return None
    return result or None


def distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) if length else 0
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)


def reduce_points(points, tolerance, algorithm):
    if tolerance == 0 or len(points) < 4 or len(set(points)) != len(points):
        return list(range(len(points)))
    # shortcut: keep loops, tiny runs and cusps; general topology qualification remains P16 research.
    protected = [0]
    for i in range(1, len(points) - 1):
        a = (points[i][0] - points[i-1][0], points[i][1] - points[i-1][1])
        b = (points[i+1][0] - points[i][0], points[i+1][1] - points[i][1])
        lengths = math.hypot(*a) * math.hypot(*b)
        if not lengths or (a[0]*b[0] + a[1]*b[1]) / lengths < math.cos(math.pi / 6):
            protected.append(i)
    protected.append(len(points) - 1)
    kept = set(protected)
    pending = list(zip(protected, protected[1:]))
    work = 0
    while pending:
        first, last = pending.pop()
        if last <= first + 1:
            continue
        work += last - first
        if work > 5000000:
            raise ValueError('Point reduction exceeded its work bound.')
        errors = [(distance(points[i], points[first], points[last]), i) for i in range(first + 1, last)]
        error, split = max(errors)
        if error > tolerance:
            if algorithm == 'midpoint':
                split = (first + last) // 2
            kept.add(split)
            pending.extend(((first, split), (split, last)))
    return sorted(kept)


def verify_subsequence(before, after, tolerance):
    """Independent continuous centerline bound: each source span lies in a convex capsule.

    Both endpoints coincide, so projection continuity also bounds the reverse distance.
    Round joins/caps are disk offsets of these centerlines. This does not prove topology.
    """
    if before['width_literal'] != after['width_literal']:
        raise ValueError('Stroke width changed.')
    old, new = before['literals'], after['literals']
    if old[0] != new[0] or old[-1] != new[-1]:
        raise ValueError('Stroke endpoint changed.')
    position = 0
    worst = 0.0
    for literal in new[1:]:
        end = position + 1
        while end < len(old) and old[end] != literal:
            end += 1
        if end == len(old):
            raise ValueError('Candidate is not a literal ordered subsequence.')
        a, b = before['points'][position], before['points'][end]
        # Independently use the area/dot formulation rather than the reducer's distance routine.
        vx, vy = b[0] - a[0], b[1] - a[1]
        norm = math.hypot(vx, vy)
        for x, y in before['points'][position:end + 1]:
            dot = (x-a[0])*vx + (y-a[1])*vy
            if not norm or dot <= 0:
                delta = math.hypot(x-a[0], y-a[1])
            elif dot >= norm * norm:
                delta = math.hypot(x-b[0], y-b[1])
            else:
                delta = abs(vx*(y-a[1]) - vy*(x-a[0])) / norm
            worst = max(worst, delta)
        position = end
    if position != len(old) - 1 or worst > tolerance:
        raise ValueError('Independent continuous path bound failed.')
    return worst


def chain_runs(runs):
    """Join only adjacent, same-width nonzero segments with literal shared endpoints."""
    chains=[]
    for index,run in enumerate(runs):
        nonzero=len(run['points'])==2 and run['points'][0]!=run['points'][1]
        if (chains and nonzero and chains[-1]['segments_only'] and
                chains[-1]['width_literal']==run['width_literal'] and
                chains[-1]['literals'][-1]==run['literals'][0]):
            chain=chains[-1]
            chain['points'].append(run['points'][-1]);chain['literals'].append(run['literals'][-1])
            chain['last']=index+1;chain['range']=(chain['range'][0],run['range'][1])
        else:
            chains.append(dict(run,points=list(run['points']),literals=list(run['literals']),
                               first=index,last=index+1,segments_only=nonzero))
    return chains


def verify_chains(before,after,ranges,bounds):
    """Validate saved reconstruction independently, then bound each saved centerline."""
    if len(after)!=len(ranges) or len(after)!=len(bounds):
        raise ValueError('Saved chain inventory changed.')
    position=0;deviations=[]
    for new,(first,last),bound in zip(after,ranges,bounds):
        if first!=position or not first<last<=len(before):
            raise ValueError('Chain partition changed painting order or omitted strokes.')
        source=before[first:last]
        old=dict(source[0],points=list(source[0]['points']),literals=list(source[0]['literals']))
        if len(source)>1:
            for index,run in enumerate(source):
                if (len(run['points'])!=2 or run['points'][0]==run['points'][1] or
                        run['width_literal']!=old['width_literal']):
                    raise ValueError('Chain crossed a width change, dot or non-segment path.')
                if index:
                    if source[index-1]['literals'][-1]!=run['literals'][0]:
                        raise ValueError('Chain crossed a pen lift.')
                    old['points'].append(run['points'][-1]);old['literals'].append(run['literals'][-1])
        deviations.append(verify_subsequence(old,new,bound))
        position=last
    if position!=len(before):
        raise ValueError('Chain partition omitted final strokes.')
    return deviations


def invocation_scales(writer):
    """Maximum physical scale for every call; shear, dashes and nonuniform scales decline."""
    scales, blocked = {}, set()
    visits = 0

    def scale(matrix):
        a, b, c, d, e, f = map(float, matrix)
        u, v = math.hypot(a, b), math.hypot(c, d)
        if (not all(math.isfinite(x) for x in (a,b,c,d,e,f)) or u <= 0 or
                not math.isclose(u, v, rel_tol=1e-10) or abs(a*c+b*d) > 1e-10*u*v):
            return None
        return max(u, v)

    def walk(stream, resources, factor, dashed, active):
        nonlocal visits
        visits += 1
        if visits > 100000 or len(active) > 128 or id(stream) in active:
            raise ValueError('Unresolved or excessive Form invocation graph.')
        active = active | {id(stream)}
        stack = []
        for args, op in ContentStream(stream, writer).operations:
            if op == b'q':
                stack.append((factor, dashed))
            elif op == b'Q':
                factor, dashed = stack.pop()
            elif op == b'cm':
                local = scale(args)
                factor = factor * local if factor is not None and local is not None else None
            elif op == b'd':
                dashed = bool(args[0])
            elif op == b'gs':
                state = resources['/ExtGState'][args[0]]
                if '/D' in state:
                    dashed = bool(state['/D'][0])
            elif op == b'Do':
                form = resources['/XObject'][args[0]].get_object()
                if form.get('/Subtype') != '/Form':
                    continue
                local = scale(form.get('/Matrix', [1,0,0,1,0,0]))
                child_factor = factor * local if factor is not None and local is not None else None
                if child_factor is None or dashed:
                    blocked.add(id(form))
                else:
                    scales[id(form)] = max(scales.get(id(form), 0), child_factor)
                walk(form, form.get('/Resources', resources), child_factor, dashed, active)
        if stack:
            raise ValueError('Unbalanced drawing state.')
    for page in writer.pages:
        content = page.get_contents()
        unit = float(page.get('/UserUnit', 1))
        if not math.isfinite(unit) or unit <= 0:
            raise ValueError('Invalid page unit.')
        if content is not None:
            walk(content, page.get('/Resources', {}), unit, False, set())
    return {key: value for key, value in scales.items() if key not in blocked}


def make_candidate(source, destination, directory, tolerance_mm, algorithm):
    if not math.isfinite(tolerance_mm) or tolerance_mm < 0 or algorithm not in {'rdp', 'midpoint','joined-rdp'}:
        raise ValueError('Invalid experimental reduction policy.')
    if destination.exists():
        raise FileExistsError('Research output already exists.')
    original_hash=filehash(source)
    validate_quick_input(source)
    with tempfile.TemporaryDirectory(prefix='path-proof-',dir=directory) as temporary:
        working=Path(temporary)
        staged=working/'classic.pdf'
        identity=qpdf_identity()
        command=[identity[0],str(source),'--object-streams=disable','--stream-data=preserve',
                 '--preserve-unreferenced','--remove-unreferenced-resources=no',str(staged)]
        process=_run(command,1800,None,'classic research staging',capture_output=True,text=True,check=True,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if process.stderr.strip():raise ValueError('qpdf staging reported warnings: '+process.stderr[-1000:])
        # qpdf retains old physical xref/object streams as orphans with --preserve-unreferenced.
        with strict_parsing(),strict_reader(source) as original:
            storage=set(getattr(original,'quick_xref_ids',set()))
            storage.update((value[0],0) for value in original.xref_objStm.values())
            transport=set()
            for number,generation in storage:
                value=original.get_object(IndirectObject(number,generation,original))
                if isinstance(value,StreamObject):
                    transport.add((value.get('/Type'),hashlib.sha256(value._data).digest()))
        if transport:
            cleaned=working/'storage-neutral.pdf'
            with strict_parsing(),strict_reader(staged) as reader,closing(exact_writer(reader)) as writer:
                for index,value in enumerate(writer._objects):
                    if (isinstance(value,StreamObject) and
                            (value.get('/Type'),hashlib.sha256(value._data).digest()) in transport):
                        writer._objects[index]=None
                with cleaned.open('xb') as stream:writer.write(stream)
            dense=working/'classic-dense.pdf'
            compact=[identity[0],str(cleaned),*command[2:-1],str(dense)]
            process=_run(compact,1800,None,'classic research compaction',capture_output=True,text=True,check=True,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if process.stderr.strip():raise ValueError('qpdf compaction reported warnings: '+process.stderr[-1000:])
            staged=dense
        staging_proof=verify_lossless(source,staged)
        result=_rewrite_candidate(staged,destination,working,tolerance_mm,algorithm)
        if filehash(source)!=original_hash:raise ValueError('Original changed during reduction.')
        result.update(source_sha256=original_hash,staging={'policy':'strictly verified classic-xref copy',
            'qpdf_version':identity[1],'qpdf_sha256':identity[2],'preservation':staging_proof,
            'source_sha256':original_hash,'output_sha256':filehash(staged)},
            object_number_scope='Unchanged-clone object numbers; target bodies identified by SHA256.')
        return result


def _rewrite_candidate(source,destination,directory,tolerance_mm,algorithm):
    joined=algorithm=='joined-rdp'
    with strict_parsing(), strict_reader(source) as reader, closing(exact_writer(reader)) as writer:
        control = directory / 'unchanged-clone.pdf'
        with control.open('xb') as stream:
            writer.write(stream)
        verify_lossless(source, control)
        contexts, scales = batching_contexts(writer), invocation_scales(writer)
        edits, skips = [], Counter()
        for number, form in enumerate(writer._objects, 1):
            if not isinstance(form, StreamObject) or form.get('/Subtype') != '/Form':
                continue
            if id(form) not in scales or id(form) not in contexts or contexts[id(form)]:
                skips['unsafe_or_unsupported_invocation'] += 1
                continue
            resources = form.get('/Resources', {})
            if (set(resources) != {'/ExtGState'} or set(resources['/ExtGState']) != {'/GS'} or
                    dict(resources['/ExtGState']['/GS']) != PEN_STATE or '/Group' in form):
                skips['unsupported_form'] += 1
                continue
            original = form.get_data()
            runs = paths(original)
            if runs is None:
                skips['not_existing_constant_width_polyline'] += 1
                continue
            work_runs=chain_runs(runs) if joined else runs
            deletions,bounds,replacements=[],[],[]
            for run in work_runs:
                tolerance = min(tolerance_mm * 72 / 25.4 / scales[id(form)], run['width'] * .1)
                if len(run['points']) < 4 or math.dist(run['points'][0], run['points'][-1]) < run['width'] * 4:
                    keep = list(range(len(run['points'])))
                else:
                    keep = reduce_points(run['points'], tolerance * .999999, 'rdp' if joined else algorithm)
                retained = set(keep)
                if joined:
                    if run['last']-run['first']>1 or len(keep)<len(run['points']):
                        text=(run['width_literal']+' w '+' '.join(run['literals'][0])+' m '+
                              ' '.join(' '.join(run['literals'][i])+' l' for i in keep[1:])+' S').encode('ascii')
                        replacements.append((*run['range'],text))
                else:
                    deletions.extend(span for i, span in enumerate(run['spans']) if i not in retained)
                bounds.append(tolerance)
            if not deletions and not replacements:
                skips['no_reduction'] += 1
                continue
            parts, cursor = [], 0
            for start, end, text in sorted(replacements if joined else [(a,b,b'') for a,b in deletions]):
                parts.append(original[cursor:start])
                parts.append(text)
                cursor = end
            parts.append(original[cursor:])
            updated = b''.join(parts)
            form.set_data(updated)
            edits.append({'object': number, 'before_sha256': hashlib.sha256(original).hexdigest(),
                          'after_sha256': hashlib.sha256(updated).hexdigest(),
                          'bounds_local_units': bounds, 'physical_scale': scales[id(form)],
                          'source_run_ranges': [(r['first'],r['last']) for r in work_runs] if joined else None,
                          'paint_calls_before':len(runs),'paint_calls_after':len(paths(updated)),
                          'chain_vertices_before_reduction':sum(len(r['points']) for r in work_runs),
                          'before_points': sum(len(r['points']) for r in runs),
                          'after_points': sum(len(r['points']) for r in paths(updated))})
        with destination.open('xb') as stream:
            writer.write(stream)
    # Read saved paths back, then restore only the declared bodies and compare the entire graph.
    with strict_parsing(), strict_reader(control) as original, strict_reader(destination) as candidate, closing(exact_writer(candidate)) as restored:
        for edit in edits:
            old = original.get_object(IndirectObject(edit['object'], 0, original))
            new = candidate.get_object(IndirectObject(edit['object'], 0, candidate))
            before, after = paths(old.get_data()), paths(new.get_data())
            if before is None or after is None or (not joined and len(before)!=len(after)):
                raise ValueError('Saved stroke inventory changed.')
            edit['verified_max_deviation_local'] = (verify_chains(before,after,edit['source_run_ranges'],edit['bounds_local_units'])
                if joined else [verify_subsequence(a,b,bound) for a,b,bound in zip(before,after,edit['bounds_local_units'])])
            restored.get_object(IndirectObject(edit['object'], 0, restored)).set_data(old.get_data())
        proof = directory / 'restored-for-verification.pdf'
        with proof.open('xb') as stream:
            restored.write(stream)
    restoration_proof=verify_lossless(source, proof)
    return {'policy': 'exploratory-polyline-2', 'algorithm': algorithm, 'tolerance_mm': tolerance_mm,
            'source_sha256': filehash(source), 'output_sha256': filehash(destination),
            'edits': edits, 'skips': dict(skips), 'non_target_graph': 'verified after declared-body restoration',
            'control_sha256':filehash(control),'restoration_proof':restoration_proof,
            'quality': 'review required; local gap/topology and semantic preservation unqualified',
            'compositing': 'Joined round-cap/round-join envelopes; raster edge shading may change' if joined else 'Original paint-call partition retained'}
