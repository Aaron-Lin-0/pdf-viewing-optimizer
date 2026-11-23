"""Make verified static BOOX ink appearances ordinary vector page drawings."""
import hashlib
import math
import re
import json
from decimal import Decimal
from navigation import Graph
from pypdf.generic import (ArrayObject, DecodedStreamObject,
                           DictionaryObject, IndirectObject, NameObject, NumberObject, StreamObject, TextStringObject)
from pypdf.generic import NullObject

IDENTITY = [1, 0, 0, 1, 0, 0]
BOUNDED_PLACEMENT = 'bounded-placement-research-1'
BOUNDED_INLINE = 'bounded-inline-research-1'
FORBIDDEN = {'/A', '/AA', '/Popup', '/IRT', '/Contents', '/RC', '/StructParent', '/AS', '/MK'}
STATIC_INK_KEYS = {'/Type','/Subtype','/Name','/F','/Rect','/AP','/CA','/OC','/P','/NM',
                   '/M','/CreationDate','/T','/C','/Border','/BS','/onyxtag','/L','/LE',
                   '/IC','/RD','/LL','/LLE','/IT','/Cap','/CP','/Contents','/RC',
                   '/onyxpoints','/onyxpointsObjNum'}


def has_interactive_data(annot):
    return any(k in annot for k in FORBIDDEN-{'/Contents','/RC'}) or bool(annot.get('/Contents') or annot.get('/RC'))


def layer_of(annot):
    layer=annot.get('/OC')
    if layer is not None:layer=layer.get_object()
    return None if layer is None or isinstance(layer,NullObject) else layer


def form_attributes(form):
    return {k:v for k,v in form.items() if k not in {'/Length','/Filter','/DecodeParms','/Resources','/OC'}}


def eligible(annot):
    if has_interactive_data(annot) or annot.get('/F', 0) != 4:
        return None
    subtype = annot.get('/Subtype')
    if subtype == '/Stamp':
        if annot.get('/Name') != '/#ONYX-STROKE':
            return None
    elif subtype not in {'/Line', '/Square', '/Circle'} or '/onyxtag' not in annot:
        return None
    appearances = annot.get('/AP', DictionaryObject()).get_object()
    if set(appearances) != {'/N'}:
        return None
    form = appearances['/N']
    if not isinstance(form, StreamObject) or form.get('/Subtype') != '/Form' or '/Group' in form:
        return None
    if annot.get('/CA', 1) != 1:
        return None
    layer = layer_of(annot)
    if layer is not None:
        if layer.get('/Type') != '/OCG':
            return None
    rect = annot['/Rect'] if '/Rect' in annot else None
    bbox = form['/BBox'] if '/BBox' in form else None
    matrix = [exact_decimal(n) for n in (form['/Matrix'] if '/Matrix' in form else IDENTITY)]
    if rect is None or bbox is None or len(rect) != 4 or len(bbox) != 4 or len(matrix)!=6:
        return None
    if any(not n.is_finite() for n in matrix) or matrix[0]*matrix[3]-matrix[1]*matrix[2]==0:return None
    bbox = [exact_decimal(n) for n in bbox]
    rect = [exact_decimal(n) for n in rect]
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1] or rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None
    if form.get('/OC') is not None:return None
    pending=[form];seen=set()
    while pending:
        drawing=pending.pop().get_object()
        if id(drawing) in seen:continue
        seen.add(id(drawing))
        if '/Group' in drawing:return None
        resources=drawing.get('/Resources',DictionaryObject()).get_object()
        for state in resources.get('/ExtGState',DictionaryObject()).get_object().values():
            if state.get_object().get('/SMask','/None')!='/None':return None
        for prop in resources.get('/Properties',DictionaryObject()).get_object().values():
            if prop.get_object().get('/Type')=='/OCMD':return None
        for ref in resources.get('/XObject',DictionaryObject()).get_object().values():
            if ref.get_object().get('/Subtype')=='/Form':pending.append(ref)
    return form


def inlineable(form):
    if form.get('/Type','/XObject') != '/XObject' or form.get('/FormType',1) != 1:return False
    if list(form['/Matrix'] if '/Matrix' in form else IDENTITY)!=IDENTITY:return False
    if set(form)-{'/Type','/Subtype','/FormType','/BBox','/Matrix','/Resources','/OC','/Length','/Filter','/DecodeParms'}:return False
    resources = form.get('/Resources', DictionaryObject()).get_object()
    states = resources.get('/ExtGState', DictionaryObject()).get_object()
    for state in states.values():
        state = state.get_object()
        if state.get('/CA', 1) != 1 or state.get('/ca', 1) != 1 or state.get('/BM', '/Normal') != '/Normal' or state.get('/SMask', '/None') != '/None' or state.get('/OP',False) or state.get('/op',False):
            return False
    # Unfamiliar drawing/resource bodies keep their original Form calls.
    if set(resources) - {'/ExtGState'}:
        return False
    allowed = {b'gs', b'rg', b'RG', b'g', b'G', b'w', b'J', b'j', b'M', b'd',
               b'm', b'l', b'c', b'v', b'y', b'h', b're', b'S', b's', b'f', b'f*', b'n'}
    data=form.get_data()
    if re.search(rb'/GS(?! gs(?:\s|$))',data):return False
    # BOOX appearance grammar has no strings, comments, arrays or indirect resource names.
    token=re.compile(rb'(?<!\S)(?:[-+]?(?:\d+(?:\.\d*)?|\.\d+)|/GS|' + b'|'.join(re.escape(op) for op in sorted(allowed,key=len,reverse=True)) + rb')(?=\s|$)')
    if not data.strip() or token.sub(b'',data).strip() or set(states)!={'/GS'}:
        return False
    if data.split()[-1] not in {b'S',b's',b'f',b'f*',b'n'}:return False
    return True


def exact_decimal(value):
    value = value.get_object() if isinstance(value, IndirectObject) else value
    return value.exact_decimal if hasattr(value, 'exact_decimal') else Decimal(str(value))


def number(value):
    value = exact_decimal(value)
    if not value.is_finite():
        raise ValueError('Non-finite appearance coordinate.')
    return format(value, 'f').encode('ascii')


def placement(annot, form):
    rect = [exact_decimal(n) for n in annot['/Rect']]
    bbox = [exact_decimal(n) for n in form['/BBox']]
    a,b,c,d,e,f=[exact_decimal(n) for n in (form['/Matrix'] if '/Matrix' in form else IDENTITY)]
    corners=[(a*x+c*y+e,b*x+d*y+f) for x,y in [(bbox[0],bbox[1]),(bbox[0],bbox[3]),(bbox[2],bbox[1]),(bbox[2],bbox[3])]]
    bbox=[min(x for x,y in corners),min(y for x,y in corners),max(x for x,y in corners),max(y for x,y in corners)]
    x = (rect[2] - rect[0]) / (bbox[2] - bbox[0])
    y = (rect[3] - rect[1]) / (bbox[3] - bbox[1])
    return b' '.join(number(n) for n in [x, 0, 0, y, rect[0] - bbox[0]*x, rect[1] - bbox[1]*y])


def protected_ink(writer, retained=None):
    protected = set()
    visited = set()

    def references(obj):
        if isinstance(obj, IndirectObject):
            protected.add(obj.idnum)
            obj = obj.get_object()
        if id(obj) in visited:
            return
        visited.add(id(obj))
        if isinstance(obj, dict):
            if obj.get('/Type') == '/Page':
                return
            for key, value in obj.items():
                if key not in {'/P', '/Pages'}:
                    references(value)
        elif isinstance(obj, (list, tuple)):
            for value in obj:
                references(value)

    # Catalog metadata and retained interactive annotations may reference handwriting objects.
    for key, value in writer.root_object.items():
        if key != '/Pages':
            references(value)
    for page in writer.pages:
        for key,value in page.items():
            if key not in {'/Annots','/Parent'}:
                references(value)
        for ref in (page['/Annots'] if '/Annots' in page else []):
            annot = ref.get_object()
            if (retained(annot) if retained is not None else eligible(annot) is None):
                references(ref)
    return protected


def flatten_ink(writer):
    converted, evidence, skipped, page_commands = {}, [], [], {}
    graph = Graph(writer)
    protected = protected_ink(writer)
    for index, page in enumerate(writer.pages):
        refs = list(page['/Annots'] if '/Annots' in page else [])
        if not refs:
            continue
        if '/Group' in page:
            skipped.append({'page':index+1,'reason':'Page transparency group retained'})
            continue
        selected = []
        for position, ref in enumerate(refs):
            annot = ref.get_object()
            border=annot['/Border'] if '/Border' in annot else [0,0,1]
            style=annot.get('/BS',DictionaryObject()).get_object()
            width=style.get('/W',border[2] if len(border)>=3 else 1)
            if annot.get('/Subtype')=='/Link' and '/AP' not in annot and width==0:
                continue
            if isinstance(ref, IndirectObject) and ref.idnum in protected:
                skipped.append({'page': index + 1, 'reason': 'Handwriting referenced by protected content'})
                break
            form = eligible(annot)
            if form is None:
                skipped.append({'page': index + 1, 'reason': 'Retained annotation is a painting-order barrier'})
                break
            selected.append((position, annot, form))
        if not selected:
            continue
        resources = DictionaryObject(page.get('/Resources', DictionaryObject()).get_object())
        xobjects = DictionaryObject(resources.get('/XObject', DictionaryObject()).get_object())
        commands = [b'q\nn\n']
        for position, annot, form in selected:
            name = '/ViewingInk' + str(position)
            while name in xobjects:
                name += 'x'
            target = form
            layer = annot.get('/OC')
            if layer_of(annot) is not None:
                # Attach the annotation's layer to its Form without editing a shared appearance.
                target = type(form)()
                target.update(form)
                target._data=form._data
                target[NameObject('/OC')] = layer
            target_ref = writer._add_object(target) if target is not form else form.indirect_reference
            xobjects[NameObject(name)] = target_ref
            transform = placement(annot, form)
            commands.append(b'q ' + transform + b' cm ' + name.encode() + b' Do Q\n')
            evidence.append({'page': index, 'position': position,
                             'body': hashlib.sha256(form.get_data()).hexdigest(),
                             'bbox': list(map(str, form['/BBox'])), 'transform': transform.decode(),
                             'matrix':list(map(str,form['/Matrix'] if '/Matrix' in form else IDENTITY)),
                             'attributes':graph.value(form_attributes(form)),
                             'target': name, 'layer': graph.value(annot.get('/OC'), True),
                             'states': graph.value(form.get('/Resources',DictionaryObject()).get_object().get('/ExtGState',DictionaryObject())),
                             'resources':graph.value(form.get('/Resources',DictionaryObject()))})
            if hasattr(form,'decoded_self'):form.decoded_self=None
        commands.append(b'Q\n')
        resources[NameObject('/XObject')] = xobjects
        page[NameObject('/Resources')] = resources
        content = page.get_contents()
        old_data = content.get_data() if content is not None else b''
        if content is not None:
            depth = 0
            for _args, op in content.operations:
                if op == b'q': depth += 1
                elif op == b'Q': depth -= 1
                if depth < 0: raise ValueError('Unbalanced page graphics state; ink conversion blocked.')
            if depth: raise ValueError('Unbalanced page graphics state; ink conversion blocked.')
        stream = DecodedStreamObject()
        stream.set_data(b'q\n' + old_data + b'\nQ\n' + b''.join(commands))
        page[NameObject('/Contents')] = writer._add_object(stream.flate_encode())
        page_commands[index]=hashlib.sha256(stream.get_data()).hexdigest()
        positions=[position for position,annot,form in selected]
        selected_positions=set(positions)
        remaining = ArrayObject([ref for position,ref in enumerate(refs) if position not in selected_positions])
        if remaining: page[NameObject('/Annots')] = remaining
        else: page.pop('/Annots', None)
        converted[index] = positions
    return {'converted': converted, 'evidence': evidence, 'skipped': skipped,
            'page_commands':page_commands,'count': sum(map(len, converted.values()))}


def verify_flattened(reader, evidence):
    graph = Graph(reader)
    for item in evidence:
        page = reader.pages[item['page']]
        target = page['/Resources']['/XObject'][item['target']]
        if (hashlib.sha256(target.get_data()).hexdigest() != item['body'] or
                list(map(str, target['/BBox'])) != item['bbox'] or
                list(map(str,target['/Matrix'] if '/Matrix' in target else IDENTITY))!=item.get('matrix',list(map(str,IDENTITY))) or
                'attributes' in item and graph.value(form_attributes(target))!=item['attributes']):
            raise ValueError('Saved annotation geometry changed; candidate rejected.')
        needle = b'q ' + item['transform'].encode() + b' cm ' + item['target'].encode() + b' Do Q\n'
        if needle not in page.get_contents().get_data():
            raise ValueError('Saved annotation placement changed; candidate rejected.')
        resources=target.get('/Resources',DictionaryObject()).get_object()
        if (json.dumps(graph.value(target.get('/OC'),True))!=json.dumps(item['layer']) or
            graph.value(resources.get('/ExtGState',DictionaryObject()))!=item['states'] or
            'resources' in item and graph.value(resources)!=item['resources']):
            raise ValueError('Saved ink layer or pen state changed.')


def consolidate_ink(writer, manifest, *, appearance_pages=None, inline_bodies=True):
    """Inline static appearance bytes; retain each clip, transform and optional layer."""
    checks = [];graph=Graph(writer)
    for original_index in manifest['converted']:
        index=int(original_index)
        page = writer.pages[index] if appearance_pages is None else appearance_pages[index]
        items = [e for e in manifest['evidence'] if e['page'] == index]
        resources = DictionaryObject(page['/Resources'])
        objects = DictionaryObject(resources['/XObject'])
        state_dict, property_dict = DictionaryObject(), DictionaryObject()
        call_objects=DictionaryObject();ordinary_checks=[]
        parts, cursor, body_checks = [], 0, []
        state_names, layer_names = {}, {}
        for item in items:
            form = objects[item['target']].get_object()
            if not inline_bodies or not inlineable(form):
                call_objects[NameObject(item['target'])]=objects.raw_get(item['target'])
                command=b'q '+item['transform'].encode()+b' cm '+item['target'].encode()+b' Do Q\n'
                parts.append(command);cursor+=len(command);ordinary_checks.append(item)
                del objects[item['target']]
                continue
            old_states = form['/Resources'].get('/ExtGState', DictionaryObject()).get_object()
            data = form.get_data()
            if set(old_states) != {'/GS'}:
                raise ValueError('Appearance pen state cannot be consolidated safely.')
            state = old_states['/GS']
            key = graph.value(state)
            if key not in state_names:
                name = '/InkPen' + str(len(state_names))
                state_dict[NameObject(name)] = old_states.raw_get('/GS')
                state_names[key] = name
            name = state_names[key]
            updated = data.replace(b'/GS gs', name.encode() + b' gs')
            if updated.replace(name.encode() + b' gs', b'/GS gs') != data:
                raise ValueError('Appearance bytes changed during pen renaming.')
            layer = form.get('/OC')
            prefix = b'q\n'
            suffix = b'Q\n'
            if layer:
                layer_key = layer.idnum if isinstance(layer, IndirectObject) else id(layer)
                if layer_key not in layer_names:
                    layer_name = '/InkLayer' + str(len(layer_names))
                    layer_names[layer_key] = layer_name
                    property_dict[NameObject(layer_name)] = layer
                prefix += b'/OC ' + layer_names[layer_key].encode() + b' BDC\n'
                suffix = b'EMC\n' + suffix
            x0, y0, x1, y1 = [number(n) for n in form['/BBox']]
            clip = b' '.join([x0,y0,b'm',x1,y0,b'l',x1,y1,b'l',x0,y1,b'l h W n\n'])
            prefix += item['transform'].encode() + b' cm\n' + clip
            body_checks.append({'position': item['position'], 'start': cursor + len(prefix), 'length': len(updated),
                                'transform': item['transform'],
                                'alias': name, 'hash': item['body'], 'states': item['states'],
                                'layer': item['layer'], 'layer_alias': layer_names.get(layer_key) if layer else None})
            parts.extend([prefix, updated, b'\n', suffix])
            cursor += len(prefix) + len(updated) + 1 + len(suffix)
            if hasattr(form,'decoded_self'):form.decoded_self=None
            del objects[item['target']]
        combined = DecodedStreamObject()
        combined.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                         NameObject('/FormType'): NumberObject(1),
                         NameObject('/BBox'): ArrayObject(page['/MediaBox']),
                         NameObject('/Resources'): DictionaryObject({NameObject('/ExtGState'): state_dict,
                                                                    NameObject('/Properties'): property_dict,
                                                                    NameObject('/XObject'):call_objects})})
        data = b''.join(parts)
        combined.set_data(data)
        name = '/ViewingInkLayer'
        while name in objects: name += 'x'
        objects[NameObject(name)] = writer._add_object(combined.flate_encode())
        resources[NameObject('/XObject')] = objects
        page[NameObject('/Resources')] = resources
        check = {'page': index, 'name': name, 'sha256': hashlib.sha256(data).hexdigest(),
                 'attributes': graph.value(form_attributes(combined)),
                 'bodies': body_checks, 'ordinary_calls': ordinary_checks}
        if appearance_pages is not None:
            checks.append(check)
            continue
        old = page.get_contents().get_data()
        expected = b''.join(b'q ' + e['transform'].encode() + b' cm ' + e['target'].encode() + b' Do Q\n' for e in items)
        if old.count(expected) != 1:
            raise ValueError('Converted ink ordering changed before consolidation.')
        changed = old.replace(expected, name.encode() + b' Do\n', 1)
        stream = DecodedStreamObject(); stream.set_data(changed)
        page[NameObject('/Contents')] = writer._add_object(stream.flate_encode())
        manifest.setdefault('page_commands',{})[index]=hashlib.sha256(changed).hexdigest()
        checks.append(check)
    manifest['consolidated'] = checks
    return checks


def verify_consolidated(reader, manifest):
    graph=Graph(reader)
    seen_annotations = set()
    for check in manifest.get('consolidated', []):
        page = reader.pages[check['page']]
        if 'annotation_position' in check:
            reference = page['/Annots'][check['annotation_position']]
            identity = (reference.idnum, reference.generation) if isinstance(reference, IndirectObject) else id(reference)
            if identity in seen_annotations:
                raise ValueError('Distinct static ink run annotations were merged.')
            seen_annotations.add(identity)
            annot = reference.get_object()
            if graph.value({k:v for k,v in annot.items() if k != '/AP'}) != check['annotation_attributes']:
                raise ValueError('Static ink run annotation placement or flags changed.')
            if set(annot['/AP']) != {'/N'}:
                raise ValueError('Static ink run appearance changed.')
            form = annot['/AP']['/N']
        else:
            form = page['/Resources']['/XObject'][check['name']]
        if 'attributes' in check and graph.value(form_attributes(form))!=check['attributes']:
            raise ValueError('Consolidated container clipping or transform changed.')
        data = form.get_data()
        if hashlib.sha256(data).hexdigest() != check['sha256']:
            raise ValueError('Consolidated clipping, layers or drawing order changed.')
        for item in check.get('ordinary_calls',[]):
            target=form['/Resources']['/XObject'][item['target']]
            if (hashlib.sha256(target.get_data()).hexdigest()!=item['body'] or
                list(map(str,target['/BBox']))!=item['bbox'] or
                list(map(str,target['/Matrix'] if '/Matrix' in target else IDENTITY))!=item['matrix'] or
                'attributes' in item and graph.value(form_attributes(target))!=item['attributes'] or
                graph.value(target.get('/Resources',DictionaryObject()).get_object().get('/ExtGState',DictionaryObject()))!=item['states'] or
                graph.value(target.get('/Resources',DictionaryObject()))!=item['resources'] or
                json.dumps(graph.value(target.get('/OC'),True))!=json.dumps(item['layer'])):
                raise ValueError('Retained appearance geometry or state changed.')
        for body in check['bodies']:
            raw = data[body['start']:body['start']+body['length']]
            raw = raw.replace(body['alias'].encode() + b' gs', b'/GS gs')
            if hashlib.sha256(raw).hexdigest() != body['hash']:
                raise ValueError('Consolidated handwriting body changed.')
            state=form['/Resources']['/ExtGState'].raw_get(body['alias'])
            if graph.value(DictionaryObject({NameObject('/GS'):state}))!=body['states']:
                raise ValueError('Consolidated pen state changed.')
            layer=form['/Resources']['/Properties'].get(body['layer_alias']) if body['layer_alias'] else None
            if json.dumps(graph.value(layer,True))!=json.dumps(body['layer']):
                raise ValueError('Consolidated layer membership changed.')


def _run_bounds(annotations):
    rectangles = [annot['/Rect'] for annot in annotations]
    return ArrayObject([op((rect[axis] for rect in rectangles),key=exact_decimal)
                        for axis,op in ((0,min),(1,min),(2,max),(3,max))])


def _bounded_envelope(bounds):
    outer = [round_value(exact_decimal(value)) for value,round_value in
             zip(bounds,(math.floor,math.floor,math.ceil,math.ceil))]
    if any(abs(value)>2**24 for value in outer):
        raise ValueError('Outer annotation bounds cannot be represented exactly in binary32.')
    x0,y0,x1,y1 = map(number,bounds)
    clip = b' '.join((b'q',x0,y0,b'm',x1,y0,b'l',x1,y1,b'l',x0,y1,b'l h W n\n'))
    return ArrayObject(map(NumberObject,outer)),clip


def compact_ink_runs(writer, source_reader=None, *, bounded=False, keep_native=(), inline_forms=False):
    """Replace consecutive opaque ink with static appearances in annotation order."""
    from handwriting import opaque_drawing
    from tools.vector_study_placement import candidate_placement, verify_placement
    if keep_native and not bounded:
        raise ValueError('Native-stroke fallback is only supported for bounded study research.')
    if inline_forms and not bounded:
        raise ValueError('Static Form inlining requires the bounded research recipe.')
    graph = Graph(writer)
    decisions = {}
    used_names = {str(ref.get_object().get('/NM')) for page in writer.pages
                  for ref in (page['/Annots'] if '/Annots' in page else [])}
    if source_reader is not None and len(source_reader.pages) != len(writer.pages):
        raise ValueError('Static ink source page count changed before conversion.')
    for page_index,page in enumerate(writer.pages):
        refs = page['/Annots'] if '/Annots' in page else []
        source_refs = (source_reader.pages[page_index]['/Annots'] if source_reader is not None and
                       '/Annots' in source_reader.pages[page_index] else refs)
        if len(refs) != len(source_refs):
            raise ValueError('Static ink annotation count changed before conversion.')
        for position,ref in enumerate(refs):
            annot = ref.get_object()
            form = eligible(annot)
            fitted = None
            if (page_index,position) in keep_native:
                reason = 'Retained by the study appearance screen'
            elif form is None:
                reason = 'Unsupported or missing appearance retained'
            elif set(annot) - STATIC_INK_KEYS:
                reason = 'Unrecognized annotation data retained'
            elif not supported_editing_payload(source_refs[position].get_object()):
                reason = 'Unrecognized editable point-data structure retained'
            elif '/Resources' not in form:
                reason = 'Appearance resource inheritance is unresolved'
            elif not opaque_drawing(form, writer):
                reason = 'Appearance opacity is unresolved'
            else:
                reason = None
                if bounded:
                    try:
                        rect,box,matrix = annot['/Rect'],form['/BBox'],form.get('/Matrix',IDENTITY)
                        _bounded_envelope(rect)
                        operands = candidate_placement(rect,box,matrix)
                        verify_placement(rect,box,matrix,operands,user_unit=page.get('/UserUnit',1))
                        fitted = ' '.join(operands)
                    except (ValueError, OverflowError):
                        reason = 'Placement outside the bounded study recipe'
            # If a shared annotation is retained on any page, it remains a barrier everywhere.
            if id(annot) not in decisions or reason is not None:
                decisions[id(annot)] = form,reason,fitted
    protected = protected_ink(writer,lambda annot: decisions[id(annot)][1] is not None)
    runs, barriers = [], []
    for index, page in enumerate(writer.pages):
        refs = list(page['/Annots'] if '/Annots' in page else [])
        initial_runs = len(runs)
        remaining, selected = ArrayObject(), []

        def flush():
            if len(selected) < 2:
                remaining.extend(ref for _, ref, _ in selected)
                selected.clear()
                return
            bounds = _run_bounds([ref.get_object() for _,ref,_ in selected])
            if bounded:bounds,clip = _bounded_envelope(bounds)
            objects, items, originals = DictionaryObject(), [], []
            for position, ref, form in selected:
                annot = ref.get_object()
                name = '/ViewingInk' + str(position)
                target = form
                if layer_of(annot) is not None:
                    target = type(form)(); target.update(form); target._data = form._data
                    target[NameObject('/OC')] = annot.raw_get('/OC')
                objects[NameObject(name)] = (writer._add_object(target) if target is not form
                                            else form.indirect_reference or writer._add_object(form))
                resources = form.get('/Resources', DictionaryObject()).get_object()
                items.append({'page': index, 'position': position, 'target': name,
                              'body': hashlib.sha256(form.get_data()).hexdigest(),
                              'bbox': list(map(str, form['/BBox'])),
                              'matrix': list(map(str, form.get('/Matrix', IDENTITY))),
                              'transform': decisions[id(annot)][2] if bounded else placement(annot,form).decode(),
                              'attributes': graph.value(form_attributes(form)),
                              'states': graph.value(resources.get('/ExtGState', DictionaryObject())),
                              'resources': graph.value(resources),
                              'layer': graph.value(annot.get('/OC'), True)})
                originals.append(hashlib.sha256(repr(graph.value(annot)).encode()).hexdigest())
            shadow = DictionaryObject({NameObject('/MediaBox'): bounds,
                                       NameObject('/Resources'): DictionaryObject({NameObject('/XObject'): objects})})
            manifest = {'converted': {index: []}, 'evidence': items}
            check = consolidate_ink(writer, manifest, appearance_pages={index: shadow},
                                    inline_bodies=inline_forms)[0]
            appearance = shadow['/Resources']['/XObject'].raw_get(check['name'])
            if bounded:
                data = clip + appearance.get_object().get_data() + b'Q\n'
                appearance.get_object().set_data(data)
                check['sha256'] = hashlib.sha256(data).hexdigest()
                for body in check['bodies']:
                    body['start'] += len(clip)
            annot = DictionaryObject({NameObject('/Type'): NameObject('/Annot'),
                                      NameObject('/Subtype'): NameObject('/Stamp'),
                                      NameObject('/F'): NumberObject(4), NameObject('/Rect'): bounds,
                                      NameObject('/AP'): DictionaryObject({NameObject('/N'): appearance})})
            name = f'ViewingCopyInk-{index}-{len(runs)}'
            while name in used_names:name += 'x'
            used_names.add(name)
            annot[NameObject('/NM')] = TextStringObject(name)
            check.update(annotation_position=len(remaining),
                         annotation_attributes=graph.value({k:v for k,v in annot.items() if k != '/AP'}),
                         source_positions=[position for position, _, _ in selected],
                         source_annotations=originals)
            remaining.append(writer._add_object(annot))
            runs.append(check)
            selected.clear()

        for position, ref in enumerate(refs):
            annot = ref.get_object()
            form,reason,_ = decisions[id(annot)]
            if '/Group' in page:
                reason = 'Page transparency group retained'
            elif reason is None and isinstance(ref, IndirectObject) and ref.idnum in protected:
                reason = 'Annotation referenced by protected content'
            if reason:
                flush()
                remaining.append(ref)
                barriers.append({'page': index + 1, 'position': position, 'reason': reason})
            else:
                selected.append((position, ref, form))
        flush()
        if len(runs) != initial_runs:
            page[NameObject('/Annots')] = remaining
    result = {'ink_runs':runs,'barriers':barriers,'count':sum(len(run['source_positions']) for run in runs)}
    if bounded:result['placement_policy'] = BOUNDED_INLINE if inline_forms else BOUNDED_PLACEMENT
    return result


def supported_editing_payload(annot):
    """Only the BOOX point stream/hint pair is disposable stroke-editing data."""
    if not ({'/onyxpoints','/onyxpointsObjNum'} & set(annot)):
        return True
    if not {'/onyxpoints','/onyxpointsObjNum'} <= set(annot):
        return False
    raw = annot.raw_get('/onyxpoints')
    reference = raw if isinstance(raw, IndirectObject) else getattr(raw, 'indirect_reference', None)
    points, hint = raw.get_object() if isinstance(raw, IndirectObject) else raw, annot['/onyxpointsObjNum']
    if not isinstance(reference, IndirectObject):
        return False
    if isinstance(hint, TextStringObject):
        valid_hint = str(hint).isascii() and str(hint).isdigit() and int(str(hint)) > 0
    else:
        valid_hint = isinstance(hint, NumberObject) and hint > 0
    return (isinstance(points, StreamObject) and not set(points) - {'/Length','/Filter'} and
            points.get('/Filter') in (None, '/FlateDecode') and
            valid_hint)


def verify_ink_runs(before, after, manifest, *, bounded=False, inline_forms=False):
    """Bind each saved run to its original annotations and exact array position."""
    from handwriting import opaque_drawing
    if inline_forms and not bounded:
        raise ValueError('Static Form inlining requires the bounded research verifier.')
    policy = (BOUNDED_INLINE if inline_forms else BOUNDED_PLACEMENT) if bounded else None
    if manifest.get('placement_policy') != policy:
        raise ValueError('Static ink placement policy does not match the verifier.')
    source_graph = Graph(before)
    def retained(annot):
        form = eligible(annot)
        return (form is None or bool(set(annot) - STATIC_INK_KEYS) or not supported_editing_payload(annot)
                or '/Resources' not in form
                or not opaque_drawing(form, before))
    protected = protected_ink(before, retained) if manifest.get('ink_runs') else set()
    source_excluded, output_excluded = {}, {}
    for run in manifest.get('ink_runs', []):
        index = run['page']
        positions = run['source_positions']
        if type(index) is not int or index < 0 or index >= len(before.pages):
            raise ValueError('Static ink source page is invalid.')
        if any(type(p) is not int or p < 0 or p >= len(before.pages[index]['/Annots']) for p in positions):
            raise ValueError('Static ink source annotation position is invalid.')
        if type(run['annotation_position']) is not int or run['annotation_position'] < 0:
            raise ValueError('Static ink saved annotation position is invalid.')
        if len(positions) < 2 or positions != list(range(positions[0], positions[-1] + 1)):
            raise ValueError('Static ink run source order is invalid.')
        used = source_excluded.setdefault(index, [])
        if any(position in used for position in positions):
            raise ValueError('Static ink runs overlap.')
        used.extend(positions)
        output_excluded.setdefault(index, []).append(run['annotation_position'])
        refs = before.pages[index]['/Annots']
        if '/Group' in before.pages[index] or any(isinstance(refs[p], IndirectObject) and refs[p].idnum in protected
                                                for p in positions):
            raise ValueError('Static ink source is referenced by protected content.')
        if any(retained(refs[p].get_object()) for p in positions):
            raise ValueError('Static ink source opacity or supported data is unresolved.')
        originals = [hashlib.sha256(repr(source_graph.value(refs[position].get_object())).encode()).hexdigest()
                     for position in positions]
        if originals != run['source_annotations']:
            raise ValueError('Static ink source annotations changed.')
        _verify_run_program(before,after,run,source_graph,bounded=bounded,inline_forms=inline_forms)
        expected = positions[0] - sum(len(other['source_positions']) - 1
                                      for other in manifest['ink_runs']
                                      if other['page'] == index and other['source_positions'][0] < positions[0])
        if run['annotation_position'] != expected:
            raise ValueError('Static ink run moved across a retained annotation.')
    for index, positions in source_excluded.items():
        expected = len(before.pages[index]['/Annots']) - len(positions) + len(output_excluded[index])
        if len(after.pages[index]['/Annots']) != expected:
            raise ValueError('Static ink annotation count changed.')
    verify_consolidated(after, {'consolidated': manifest.get('ink_runs', [])})
    return source_excluded, output_excluded


def _verify_run_program(before, after, run, graph, *, bounded=False, inline_forms=False):
    """Reconstruct the saved drawing from the original appearances, independently."""
    stamp = after.pages[run['page']]['/Annots'][run['annotation_position']].get_object()
    form = stamp['/AP']['/N']
    bodies = {item['position']: item for item in run['bodies']}
    ordinary = {item['position']: item for item in run['ordinary_calls']}
    if len(bodies) != len(run['bodies']) or len(ordinary) != len(run['ordinary_calls']):
        raise ValueError('Static ink manifest contains duplicate source positions.')
    expected, cursor = [], 0
    if bounded:
        from tools.vector_study_placement import verify_placement
        if bodies and not inline_forms:
            raise ValueError('Bounded placement requires original child Forms.')
        source_annots = before.pages[run['page']]['/Annots']
        outer,clip = _bounded_envelope(_run_bounds([source_annots[pos].get_object() for pos in run['source_positions']]))
        if (list(map(exact_decimal,stamp['/Rect'])) != list(outer) or
                list(map(exact_decimal,form['/BBox'])) != list(outer) or
                list(map(exact_decimal,form.get('/Matrix',IDENTITY))) != IDENTITY or
                stamp.get('/F') != 4 or stamp.get('/Subtype') != '/Stamp' or '/OC' in stamp or
                '/OC' in form or '/Group' in form):
            raise ValueError('Saved bounded placement wrapper differs from the source-derived envelope.')
        expected.append(clip)
        cursor = len(clip)
    for position in run['source_positions']:
        annot = before.pages[run['page']]['/Annots'][position].get_object()
        original = eligible(annot)
        if original is None:
            raise ValueError('Static ink source is no longer eligible.')
        if bounded:
            item = bodies.get(position,ordinary.get(position))
            if item is None:
                raise ValueError('Bounded run has no placement for the source stroke.')
            transform = item['transform'].encode('ascii')
            operands = transform.split()
            if any(not re.fullmatch(rb'[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)',operand) for operand in operands):
                raise ValueError('Saved bounded placement contains a non-PDF numeric operand.')
            verify_placement(annot['/Rect'],original['/BBox'],original.get('/Matrix',IDENTITY),
                             [operand.decode('ascii') for operand in operands],
                             user_unit=before.pages[run['page']].get('/UserUnit',1))
        else:
            transform = placement(annot, original)
        resources = original.get('/Resources', DictionaryObject()).get_object()
        if position in bodies:
            item = bodies[position]
            if inline_forms and not inlineable(original):
                raise ValueError('Saved inline body failed source eligibility.')
            if item['hash'] != hashlib.sha256(original.get_data()).hexdigest():
                raise ValueError('Static ink body differs from its original appearance.')
            if (item['states'] != graph.value(resources.get('/ExtGState', DictionaryObject())) or
                    json.dumps(item['layer']) != json.dumps(graph.value(annot.get('/OC'), True))):
                raise ValueError('Static ink state or layer differs from the original.')
            layer = layer_of(annot) is not None
            if layer != bool(item['layer_alias']):
                raise ValueError('Static ink layer wrapper differs from the original.')
            prefix, suffix = b'q\n', b'Q\n'
            if layer:
                prefix += b'/OC ' + item['layer_alias'].encode() + b' BDC\n'
                suffix = b'EMC\n' + suffix
            x0,y0,x1,y1 = [number(n) for n in original['/BBox']]
            prefix += transform + b' cm\n' + b' '.join([x0,y0,b'm',x1,y0,b'l',x1,y1,b'l',x0,y1,b'l h W n\n'])
            body = original.get_data().replace(b'/GS gs', item['alias'].encode() + b' gs')
            if item['start'] != cursor + len(prefix) or item['length'] != len(body):
                raise ValueError('Static ink body position differs from original drawing order.')
            part = prefix + body + b'\n' + suffix
        else:
            item = ordinary.get(position)
            if item is None or (item['body'] != hashlib.sha256(original.get_data()).hexdigest() or
                    item['transform'] != transform.decode() or
                    item['bbox'] != list(map(str, original['/BBox'])) or
                    item['matrix'] != list(map(str, original.get('/Matrix', IDENTITY))) or
                    item['attributes'] != graph.value(form_attributes(original)) or
                    item['states'] != graph.value(resources.get('/ExtGState', DictionaryObject())) or
                    item['resources'] != graph.value(resources) or
                    json.dumps(item['layer']) != json.dumps(graph.value(annot.get('/OC'), True))):
                raise ValueError('Static ink Form call differs from the original appearance.')
            part = b'q ' + transform + b' cm ' + item['target'].encode() + b' Do Q\n'
        expected.append(part)
        cursor += len(part)
    if bounded:expected.append(b'Q\n')
    if set(bodies) | set(ordinary) != set(run['source_positions']) or form.get_data() != b''.join(expected):
        raise ValueError('Static ink clipping, placement or order differs from original appearances.')
