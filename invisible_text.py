"""Remove isolated BOOX OCR scopes without rewriting drawing commands."""
import re
from pypdf.generic import ContentStream, DecodedStreamObject, DictionaryObject, NameObject

NUMBER = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
OCR = re.compile(
    rb"(?<!\S)q\s+0\s+0\s+0\s+rg\s+0\s+0\s+0\s+RG\s+BT\s+"
    + rb"(?:" + NUMBER + rb"\s+){6}Tm\s+/[^\s/<>\[\]()]+\s+"
    + NUMBER + rb"\s+Tf\s+3\s+Tr\s+<[0-9A-Fa-f\s]*>\s+Tj\s+ET\s+Q(?!\S)"
)
OPERATORS = [b'q', b'rg', b'RG', b'BT', b'Tm', b'Tf', b'Tr', b'Tj', b'ET', b'Q']


def _operations(data, writer):
    stream = DecodedStreamObject()
    stream.set_data(data)
    return ContentStream(stream, writer).operations


def _form_has_text(resources, seen):
    resources = resources.get_object()
    for reference in resources.get('/XObject', DictionaryObject()).get_object().values():
        form = reference.get_object()
        if form.get('/Subtype') != '/Form' or id(form) in seen:
            continue
        seen.add(id(form))
        # Conservative: even a false-positive token retains fonts rather than risking text.
        if b'Tf' in form.get_data() or _form_has_text(form.get('/Resources', DictionaryObject()), seen):
            return True
    return False


def remove_invisible_text(writer):
    removed = fonts_removed = 0
    for page in writer.pages:
        content = page.get_contents()
        if content is None:
            continue
        data = content.get_data()
        matches = list(OCR.finditer(data))
        if not matches:
            continue
        expected = list(_operations(data, writer))
        for match in matches:
            block = _operations(match[0], writer)
            if [op for args, op in block] != OPERATORS or block[6][0] != [3]:
                raise ValueError('Unrecognized OCR scope; candidate rejected.')
            # Also prove the byte match corresponds to an actual operation sequence.
            for index in range(len(expected) - len(block) + 1):
                if expected[index:index + len(block)] == block:
                    del expected[index:index + len(block)]
                    break
            else:
                raise ValueError('OCR match was not a standalone graphics scope.')
        updated_data = OCR.sub(b'', data)
        remaining = _operations(updated_data, writer)
        if remaining != expected:
            raise ValueError('Non-OCR commands changed; candidate rejected.')
        updated = DecodedStreamObject()
        updated.set_data(updated_data)
        page[NameObject('/Contents')] = writer._add_object(updated.flate_encode())
        resources = page.get('/Resources', DictionaryObject()).get_object()
        # Annotation appearances and inherited Form text may rely on page fonts.
        if not page.get('/Annots') and not _form_has_text(resources, set()):
            used = {args[0] for args, op in remaining if op == b'Tf'}
            old_fonts = resources.get('/Font', DictionaryObject()).get_object()
            kept = DictionaryObject({name: ref for name, ref in old_fonts.items() if name in used})
            fonts_removed += len(old_fonts) - len(kept)
            new_resources = DictionaryObject(resources)
            if kept:
                new_resources[NameObject('/Font')] = kept
            else:
                new_resources.pop('/Font', None)
            page[NameObject('/Resources')] = new_resources
        removed += len(matches)
    if not removed:
        raise ValueError('No supported invisible BOOX text blocks found; nothing changed.')
    print(f'Removed {removed:,} invisible text blocks and {fonts_removed:,} unused page font references.', flush=True)
    return removed, fonts_removed
