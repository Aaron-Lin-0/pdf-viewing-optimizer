"""Spawn-safe PDFium rendering workers used only by viewing-copy verification."""

from pathlib import Path
import math
import os
import shutil

from PIL import Image, ImageChops, ImageFilter, ImageStat
import pypdfium2 as pdfium


_DOCUMENTS = {}
_PERSISTENT = set()
# Pages above this size are rendered and compared in strips. Letter/A4 fit at 4x.
STRIP_PIXELS = 1 << 23


def bitmap_size(page, scale=2, crop=None):
    """Return the pixel size PdfPage.render would allocate."""
    left, bottom, right, top = (math.ceil(value * scale) for value in (crop or (0, 0, 0, 0)))
    return (math.ceil(page.get_width() * scale) - left - right,
            math.ceil(page.get_height() * scale) - bottom - top)


def strip_rows(width, height, strip_pixels=None):
    """Core row ranges; callers add a halo when edge comparisons need neighbours."""
    rows = max(1, (strip_pixels or STRIP_PIXELS) // width)
    return [(top, min(height, top + rows)) for top in range(0, height, rows)]


def halo(top, bottom, height):
    return max(0, top - 1), min(height, bottom + 1)


def render_rows(page, rows, height, scale=2, crop=None, print_mode=False):
    """Render pixel rows [low, high); a full-height range remains a plain render."""
    low, high = rows
    left, bottom, right, top = crop or (0, 0, 0, 0)
    kwargs = {'scale': scale, 'draw_annots': True,
              'crop': (left, bottom + (height - high) / scale, right, top + low / scale)}
    if print_mode:
        kwargs['optimize_mode'] = 'print'
    bitmap = page.render(**kwargs)
    try:
        image = bitmap.to_pil().convert('RGB')
    finally:
        bitmap.close()
    if image.height != high - low:
        image.close()
        raise ValueError('Rendered strip has an unexpected size.')
    return image


class DeltaAccumulator:
    """Combine strip differences into the whole-page appearance guard."""

    def __init__(self, allow_edges):
        self.allow_edges = allow_edges
        self.peak = 0
        self.total = 0.0
        self.channels = 0
        self.outside = False

    def add(self, source, output, core):
        if source.size != output.size:
            raise ValueError('Rendered page dimensions changed.')
        box = (0, core[0], source.width, core[1])
        with source.crop(box) as a, output.crop(box) as b, ImageChops.difference(a, b) as delta:
            peak = max(hi for _lo, hi in delta.getextrema())
            self.peak = max(self.peak, peak)
            self.total += sum(ImageStat.Stat(delta).sum)
            self.channels += 3 * delta.width * delta.height
            if peak and self.allow_edges and not self.outside:
                ink = source.convert('L').point(lambda value: 255 if value < 250 else 0)
                band = ImageChops.subtract(ink.filter(ImageFilter.MaxFilter(3)),
                                           ink.filter(ImageFilter.MinFilter(3))).crop(box)
                channels = delta.split()
                changed = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]),
                                             channels[2]).point(lambda value: 255 if value else 0)
                self.outside = bool(ImageChops.multiply(changed, ImageChops.invert(band)).getbbox())

    def result(self):
        mean = self.total / self.channels if self.channels else 0
        if not self.peak:
            return {'exact': True, 'peak': 0, 'mean': 0}
        if not self.allow_edges or self.peak > 96 or mean > 1.5:
            raise ValueError(f'Appearance changed beyond guard: peak={self.peak}, mean={mean:.6f}.')
        if self.outside:
            raise ValueError('Pixel changes extend beyond the one-pixel ink-edge band.')
        return {'exact': False, 'peak': self.peak, 'mean': mean}


def page_delta(source, candidate, index, allow_edges=False, scale=2, crop=None, print_mode=False):
    """Compare one PDFium page render of two files strip by strip."""
    with pdfium.PdfDocument(source) as before, pdfium.PdfDocument(candidate) as after:
        before.init_forms()
        after.init_forms()
        a, b = before[index], after[index]
        try:
            width, height = bitmap_size(a, scale, crop)
            if (width, height) != bitmap_size(b, scale, crop):
                raise ValueError('Rendered page dimensions changed.')
            delta = DeltaAccumulator(allow_edges)
            for top, bottom in strip_rows(width, height):
                low, high = halo(top, bottom, height)
                with render_rows(a, (low, high), height, scale, crop, print_mode) as old, \
                        render_rows(b, (low, high), height, scale, crop, print_mode) as new:
                    delta.add(old, new, (top - low, bottom - low))
            return delta.result()
        finally:
            a.close()
            b.close()


def _document(path,persistent=False):
    key = str(Path(path).absolute())
    if persistent:_PERSISTENT.add(key)
    document = _DOCUMENTS.get(key)
    if document is None:
        for stale in set(_DOCUMENTS)-_PERSISTENT:
            _DOCUMENTS.pop(stale).close()
        document = pdfium.PdfDocument(key)
        document.init_forms()
        _DOCUMENTS[key] = document
    return document


def render_source(task):
    path, index, destination, scale, crop, print_mode = task
    temporary = Path(str(destination) + f'.{os.getpid()}.tmp')
    page = _document(path, True)[index]
    try:
        width, height = bitmap_size(page, scale, crop)
        strips = strip_rows(width, height)
        if len(strips) == 1:
            with render_rows(page, (0, height), height, scale, crop, print_mode) as image:
                image.save(temporary, format='PNG', optimize=False)
        else:
            temporary.mkdir()
            for top, bottom in strips:
                with render_rows(page, halo(top, bottom, height), height, scale, crop, print_mode) as image:
                    image.save(temporary / f'{top:07d}-{bottom:07d}.png', format='PNG', optimize=False)
        os.replace(temporary, destination)
        return {'page': index + 1, 'width': width, 'height': height}
    finally:
        page.close()
        if temporary.is_dir():
            shutil.rmtree(temporary, ignore_errors=True)
        else:
            temporary.unlink(missing_ok=True)


def _source_strips(source_png):
    source = Path(source_png)
    if not source.is_dir():
        with Image.open(source) as image:
            return image.width, [(0, image.height, source)]
    strips = [(*map(int, path.stem.split('-')), path) for path in sorted(source.glob('*.png'))]
    if not strips or strips[0][0] != 0 or any(a[1] != b[0] for a, b in zip(strips, strips[1:])):
        raise ValueError('Cached source strips are incomplete.')
    with Image.open(strips[0][2]) as image:
        return image.width, strips


def compare_page(task):
    source_png, candidate, index, allow_edges, scale, crop, print_mode = task
    width, strips = _source_strips(source_png)
    height = strips[-1][1]
    page = _document(candidate)[index]
    try:
        if bitmap_size(page, scale, crop) != (width, height):
            raise ValueError('Rendered page dimensions changed.')
        delta = DeltaAccumulator(allow_edges)
        for top, bottom, path in strips:
            low, high = halo(top, bottom, height) if len(strips) > 1 else (top, bottom)
            with Image.open(path) as source, render_rows(page, (low, high), height, scale, crop, print_mode) as output:
                source.load()
                delta.add(source, output, (top - low, bottom - low))
        return {'page': index + 1, **delta.result()}
    finally:
        page.close()
