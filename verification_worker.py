"""Spawn-safe PDFium rendering workers used only by viewing-copy verification."""

from pathlib import Path
import os

from PIL import Image, ImageChops, ImageFilter, ImageStat
import pypdfium2 as pdfium


_DOCUMENTS = {}


def _document(path):
    key = str(Path(path).absolute())
    document = _DOCUMENTS.get(key)
    if document is None:
        document = pdfium.PdfDocument(key)
        document.init_forms()
        _DOCUMENTS[key] = document
    return document


def _render(path, index, scale=2, crop=None, print_mode=False):
    page = _document(path)[index]
    try:
        kwargs = {'scale': scale, 'draw_annots': True}
        if crop:
            kwargs['crop'] = crop
        if print_mode:
            kwargs['optimize_mode'] = 'print'
        bitmap = page.render(**kwargs)
        try:
            return bitmap.to_pil().convert('RGB')
        finally:
            bitmap.close()
    finally:
        page.close()


def render_source(task):
    path, index, destination, scale, crop, print_mode = task
    temporary = str(destination) + f'.{os.getpid()}.tmp'
    try:
        with _render(path, index, scale, crop, print_mode) as image:
            image.save(temporary, format='PNG', optimize=False)
            os.replace(temporary, destination)
            return {'page': index + 1, 'width': image.width, 'height': image.height}
    finally:
        Path(temporary).unlink(missing_ok=True)


def compare_page(task):
    source_png, candidate, index, allow_edges, scale, crop, print_mode = task
    with Image.open(source_png) as source, _render(candidate, index, scale, crop, print_mode) as output:
        source.load()
        if source.size != output.size:
            raise ValueError('Rendered page dimensions changed.')
        with ImageChops.difference(source, output) as delta:
            peak = max(hi for _lo, hi in delta.getextrema())
            mean = sum(ImageStat.Stat(delta).mean) / 3
            if peak and (not allow_edges or peak > 96 or mean > 1.5):
                raise ValueError(f'Appearance changed beyond guard: peak={peak}, mean={mean:.6f}.')
            if peak:
                ink = source.convert('L').point(lambda value: 255 if value < 250 else 0)
                band = ImageChops.subtract(ink.filter(ImageFilter.MaxFilter(3)),
                                           ink.filter(ImageFilter.MinFilter(3)))
                channels = delta.split()
                changed = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]),
                                             channels[2]).point(lambda value: 255 if value else 0)
                outside = ImageChops.multiply(changed, ImageChops.invert(band))
                if outside.getbbox():
                    raise ValueError('Pixel changes extend beyond the one-pixel ink-edge band.')
            return {'page': index + 1, 'exact': peak == 0, 'peak': peak, 'mean': mean}
