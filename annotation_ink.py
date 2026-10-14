"""Make verified static BOOX ink appearances ordinary vector page drawings."""
import hashlib
import re
import json
from decimal import Decimal
from navigation import Graph
from pypdf.generic import (ArrayObject, DecodedStreamObject,
                           DictionaryObject, IndirectObject, NameObject, NumberObject, StreamObject)
from pypdf.generic import NullObject

IDENTITY = [1, 0, 0, 1, 0, 0]
FORBIDDEN = {'/A', '/AA', '/Popup', '/IRT', '/Contents', '/RC', '/StructParent', '/AS', '/MK'}


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
    matrix = list(form['/Matrix']) if '/Matrix' in form else IDENTITY
    if rect is None or bbox is None or len(rect) != 4 or len(bbox) != 4 or len(matrix)!=6:
        return None
    if any(not Decimal(str(n)).is_finite() for n in matrix) or matrix[0]*matrix[3]-matrix[1]*matrix[2]==0:return None
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
    # BOOX appearance grammar has no strings, comments, arrays or indirect resource names.
    token=re.compile(rb'(?<!\S)(?:[-+]?(?:\d+(?:\.\d*)?|\.\d+)|/GS|' + b'|'.join(re.escape(op) for op in sorted(allowed,key=len,reverse=True)) + rb')(?=\s|$)')
    if not data.strip() or token.sub(b'',data).strip() or set(states)!={'/GS'}:
        return False
    if data.split()[-1] not in {b'S',b's',b'f',b'f*',b'n'}:return False
    return True


def number(value):
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError('Non-finite appearance coordinate.')
    return format(value, 'f').encode('ascii')


def placement(annot, form):
    rect = [Decimal(str(n)) for n in annot['/Rect']]
    bbox = [Decimal(str(n)) for n in form['/BBox']]
    a,b,c,d,e,f=[Decimal(str(n)) for n in (form['/Matrix'] if '/Matrix' in form else IDENTITY)]
    corners=[(a*x+c*y+e,b*x+d*y+f) for x,y in [(bbox[0],bbox[1]),(bbox[0],bbox[3]),(bbox[2],bbox[1]),(bbox[2],bbox[3])]]
    bbox=[min(x for x,y in corners),min(y for x,y in corners),max(x for x,y in corners),max(y for x,y in corners)]
    x = (rect[2] - rect[0]) / (bbox[2] - bbox[0])
    y = (rect[3] - rect[1]) / (bbox[3] - bbox[1])
    return b' '.join(number(n) for n in [x, 0, 0, y, rect[0] - bbox[0]*x, rect[1] - bbox[1]*y])


def flatten_ink(writer):
    converted, evidence, skipped, page_commands = {}, [], [], {}
    graph = Graph(writer)
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
            if key not in {'/Annots','/Contents','/Resources','/Parent'}:
                references(value)
        for ref in (page['/Annots'] if '/Annots' in page else []):
            annot = ref.get_object()
            if eligible(annot) is None:
                references(ref)
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
            for args, op in content.operations:
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


def consolidate_ink(writer, manifest):
    """Inline static appearance bytes; retain each clip, transform and optional layer."""
    checks = [];graph=Graph(writer)
    for original_index in manifest['converted']:
        index=int(original_index)
        page = writer.pages[index]
        items = [e for e in manifest['evidence'] if e['page'] == index]
        resources = DictionaryObject(page['/Resources'])
        objects = DictionaryObject(resources['/XObject'])
        state_dict, property_dict = DictionaryObject(), DictionaryObject()
        call_objects=DictionaryObject();ordinary_checks=[]
        parts, cursor, body_checks = [], 0, []
        state_names, layer_names = {}, {}
        for item in items:
            form = objects[item['target']].get_object()
            if not inlineable(form):
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
            key = str(sorted(state.items()))
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
            body_checks.append({'start': cursor + len(prefix), 'length': len(updated),
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
        old = page.get_contents().get_data()
        expected = b''.join(b'q ' + e['transform'].encode() + b' cm ' + e['target'].encode() + b' Do Q\n' for e in items)
        if old.count(expected) != 1:
            raise ValueError('Converted ink ordering changed before consolidation.')
        changed = old.replace(expected, name.encode() + b' Do\n', 1)
        stream = DecodedStreamObject(); stream.set_data(changed)
        page[NameObject('/Contents')] = writer._add_object(stream.flate_encode())
        manifest.setdefault('page_commands',{})[index]=hashlib.sha256(changed).hexdigest()
        checks.append({'page': index, 'name': name, 'sha256': hashlib.sha256(data).hexdigest(),
                       'attributes':graph.value(form_attributes(combined)),
                       'bodies': body_checks,'ordinary_calls':ordinary_checks})
    manifest['consolidated'] = checks
    return checks


def verify_consolidated(reader, manifest):
    graph=Graph(reader)
    for check in manifest.get('consolidated', []):
        page = reader.pages[check['page']]
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
