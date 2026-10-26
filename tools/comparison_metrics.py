"""Descriptive foreground measurements; no automatic handwriting acceptance rule."""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage.metrics import structural_similarity
from skimage.morphology import skeletonize
from skimage.measure import euler_number

THRESHOLDS = (200, 225, 250)
TOLERANCES_MM = (0, .025, .05, .1)


def distribution(values):
    if not values.size:
        return {'p05': None, 'median': None, 'p95': None, 'max': None}
    return dict(zip(('p05', 'median', 'p95', 'max'), map(float, np.percentile(values, [5, 50, 95, 100]))))


def topology(mask):
    skeleton = skeletonize(mask)
    neighbors = ndi.convolve(skeleton.astype('uint8'), np.ones((3, 3), dtype='uint8')) - skeleton
    labels, count = ndi.label(mask, np.ones((3, 3)))
    return {'components': int(count), 'endpoints': int(np.count_nonzero(skeleton & (neighbors == 1))),
            'junction_pixels': int(np.count_nonzero(skeleton & (neighbors > 2))),
            'holes': int(count - euler_number(mask, connectivity=2))}, skeleton, labels


def source_features(mask, skeleton, labels, width, mm_per_pixel):
    neighbors = ndi.convolve(skeleton.astype('uint8'), np.ones((3, 3), dtype='uint8')) - skeleton
    components = []
    for number, bounds in enumerate(ndi.find_objects(labels), 1):
        component = labels[bounds] == number
        component_skeleton = skeleton[bounds] & component
        component_widths = width[bounds][component_skeleton]
        pixel_width, pixel_height = bounds[1].stop-bounds[1].start, bounds[0].stop-bounds[0].start
        components.append({
            'bbox_pixels': [bounds[1].start, bounds[0].start, bounds[1].stop, bounds[0].stop],
            'pixels': int(component.sum()), 'skeleton_pixels': int(component_skeleton.sum()),
            'width_mm': distribution(component_widths),
            'endpoints': int(np.count_nonzero(component_skeleton & (neighbors[bounds] == 1))),
            'junction_pixels': int(np.count_nonzero(component_skeleton & (neighbors[bounds] > 2))),
            'loops': int(1-euler_number(component, connectivity=2)),
            'dot_candidate': int(component_skeleton.sum()) <= 1,
            'fraction_bar_candidate': pixel_width >= 4*max(1, pixel_height) and pixel_height*mm_per_pixel[1] <= .5,
        })
    widths = width[skeleton]
    bins = {'under_0.1': int(np.count_nonzero(widths < .1)),
            '0.1_to_0.25': int(np.count_nonzero((widths >= .1) & (widths < .25))),
            '0.25_to_0.5': int(np.count_nonzero((widths >= .25) & (widths < .5))),
            '0.5_and_over': int(np.count_nonzero(widths >= .5))}
    return {'components': components, 'component_count': len(components),
            'dot_candidates': sum(item['dot_candidate'] for item in components),
            'fraction_bar_candidates': sum(item['fraction_bar_candidate'] for item in components),
            'crossing_or_join_components': sum(bool(item['junction_pixels']) for item in components),
            'loop_components': sum(bool(item['loops']) for item in components),
            'width_sample_bins_mm': bins,
            'filled_outline_status': 'not distinguishable from raster foreground',
            'quantization_mm_per_pixel': list(mm_per_pixel)}


def mask_metrics(before, after, mm_per_pixel):
    """Distances use physical sampling; empty targets never acquire fictitious zero distance."""
    area = int(before.sum())
    out_area = int(after.sum())
    if np.array_equal(before, after):
        shape, skeleton, labels = topology(before)
        widths = 2 * ndi.distance_transform_edt(before, sampling=(mm_per_pixel[1], mm_per_pixel[0]))
        zero = {'median': 0., 'p95': 0., 'max': 0.} if area else distribution(np.array([]))
        if area:zero['p05'] = 0.
        skeleton_pixels = int(skeleton.sum())
        return {'original_foreground_pixels': area, 'output_foreground_pixels': out_area,
                'tolerances_mm': {str(t): {'missing_pixels': 0, 'added_pixels': 0,
                    'missing_fraction': 0. if area else None, 'added_fraction': 0. if area else None}
                    for t in TOLERANCES_MM},
                'stroke_displacement_mm': dict(zero), 'thickness_change_mm': dict(zero),
                'thickness_absolute_change_mm': dict(zero), 'thickness_matched_samples': int(skeleton.sum()),
                'source_width_mm': distribution(widths[skeleton]),
                'output_width_mm': distribution(widths[skeleton]),
                'matching_radius_mm': .1,
                'matched_skeleton_coverage': {
                    'source': {'matched': skeleton_pixels, 'total': skeleton_pixels,
                               'fraction': 1. if skeleton_pixels else None},
                    'output': {'matched': skeleton_pixels, 'total': skeleton_pixels,
                               'fraction': 1. if skeleton_pixels else None}},
                'source_features': source_features(before, skeleton, labels, widths, mm_per_pixel),
                'topology_before': shape, 'topology_after': dict(shape),
                'potential_topology_change': False, 'potential_missing_components': [],
                'potential_added_components': [], 'potential_breaks_merges': [],
                'empty_status': 'both blank' if not area else None}
    sampling = (mm_per_pixel[1], mm_per_pixel[0])
    to_after = ndi.distance_transform_edt(~after, sampling=sampling) if out_area else np.full(before.shape, np.inf)
    to_before = ndi.distance_transform_edt(~before, sampling=sampling) if area else np.full(before.shape, np.inf)
    missing_added = {}
    for tolerance in TOLERANCES_MM:
        missing = int(np.count_nonzero(before & (to_after > tolerance)))
        added = int(np.count_nonzero(after & (to_before > tolerance)))
        missing_added[str(tolerance)] = {'missing_pixels': missing, 'added_pixels': added,
            'missing_fraction': missing / area if area else None,
            'added_fraction': added / area if area else None}
    old_topology, old_skeleton, old_labels = topology(before)
    new_topology, new_skeleton, new_labels = topology(after)
    old_width = 2 * ndi.distance_transform_edt(before, sampling=sampling)
    new_width = 2 * ndi.distance_transform_edt(after, sampling=sampling)
    if old_skeleton.any() and new_skeleton.any():
        distances, nearest = ndi.distance_transform_edt(~new_skeleton, sampling=sampling, return_indices=True)
        matched = old_skeleton & (distances <= .1)
        source_thickness = new_width[tuple(nearest[:, matched])] - old_width[matched]
        reverse_distances, reverse_nearest = ndi.distance_transform_edt(~old_skeleton, sampling=sampling, return_indices=True)
        reverse_matched = new_skeleton & (reverse_distances <= .1)
        output_thickness = new_width[reverse_matched] - old_width[tuple(reverse_nearest[:, reverse_matched])]
        thickness = np.concatenate([source_thickness, output_thickness])
        reverse = reverse_distances[new_skeleton]
        forward = distances[old_skeleton]
        displacement = distribution(np.concatenate([forward, reverse]))
    else:
        matched = np.zeros_like(old_skeleton)
        reverse_matched = np.zeros_like(new_skeleton)
        thickness = np.array([])
        displacement = distribution(np.array([]))
    # All components survive analysis, including one-pixel dots and punctuation.
    unmatched = []
    added = []
    merges = []
    for number, bounds in enumerate(ndi.find_objects(old_labels), 1):
        component = old_labels[bounds] == number
        overlap = np.unique(new_labels[bounds][component])
        overlap = overlap[overlap > 0]
        if not len(overlap):
            unmatched.append({'bbox_pixels': [bounds[1].start, bounds[0].start, bounds[1].stop, bounds[0].stop],
                              'pixels': int(component.sum())})
        elif len(overlap) > 1:
            merges.append({'source_component': number, 'output_components': overlap.tolist(), 'kind': 'possible break'})
    for number, bounds in enumerate(ndi.find_objects(new_labels), 1):
        component = new_labels[bounds] == number
        overlap = np.unique(old_labels[bounds][component])
        overlap = overlap[overlap > 0]
        if not len(overlap):
            added.append({'bbox_pixels': [bounds[1].start, bounds[0].start, bounds[1].stop, bounds[0].stop],
                          'pixels': int(component.sum())})
        elif len(overlap) > 1:
            merges.append({'output_component': number, 'source_components': overlap.tolist(), 'kind': 'possible merge'})
    source_skeleton_pixels, output_skeleton_pixels = int(old_skeleton.sum()), int(new_skeleton.sum())
    return {'original_foreground_pixels': area, 'output_foreground_pixels': out_area,
            'tolerances_mm': missing_added, 'stroke_displacement_mm': displacement,
            'thickness_change_mm': distribution(thickness),
            'thickness_absolute_change_mm': distribution(np.abs(thickness)),
            'thickness_matched_samples': int(thickness.size),
            'source_width_mm': distribution(old_width[old_skeleton]),
            'output_width_mm': distribution(new_width[new_skeleton]),
            'matching_radius_mm': .1,
            'matched_skeleton_coverage': {
                'source': {'matched': int(matched.sum()), 'total': source_skeleton_pixels,
                           'fraction': float(matched.sum()/source_skeleton_pixels) if source_skeleton_pixels else None},
                'output': {'matched': int(reverse_matched.sum()), 'total': output_skeleton_pixels,
                           'fraction': float(reverse_matched.sum()/output_skeleton_pixels) if output_skeleton_pixels else None}},
            'source_features': source_features(before, old_skeleton, old_labels, old_width, mm_per_pixel),
            'topology_before': old_topology, 'topology_after': new_topology,
            'potential_topology_change': old_topology != new_topology or bool(merges),
            'potential_missing_components': unmatched, 'potential_added_components': added,
            'potential_breaks_merges': merges,
            'empty_status': 'both blank' if not area and not out_area else
                            'original blank' if not area else 'all original ink missing' if not out_area else None}


def image_metrics(before, after, page_mm):
    a, b = np.asarray(before.convert('RGB')), np.asarray(after.convert('RGB'))
    if a.shape != b.shape:
        return {'status': 'confirmed difference', 'reason': 'Rendered dimensions differ; no alignment or resizing applied'}
    mm_per_pixel = (page_mm[0] / a.shape[1], page_mm[1] / a.shape[0])
    gray_a = np.dot(a, [.299, .587, .114])
    gray_b = np.dot(b, [.299, .587, .114])
    delta = np.abs(a.astype(float) - b)
    union = (gray_a < 250) | (gray_b < 250)
    per_threshold = {str(t): mask_metrics(gray_a < t, gray_b < t, mm_per_pixel) for t in THRESHOLDS}
    signatures = [(x['potential_topology_change'], bool(x['potential_missing_components']),
                   x['tolerances_mm']['0.05']['missing_fraction']) for x in per_threshold.values()]
    similarity = float(structural_similarity(gray_a, gray_b, data_range=255)) if min(gray_a.shape) >= 7 else None
    foreground_delta = delta[union]
    return {'status': 'pass', 'analysis': 'foreground analysis; handwriting isolation uncertain',
            'exact': bool(np.array_equal(a, b)), 'mm_per_pixel': mm_per_pixel,
            'thresholds': per_threshold, 'threshold_sensitive': any(x != signatures[0] for x in signatures[1:]),
            'rgb_foreground_absolute_difference': distribution(foreground_delta.ravel()),
            'contrast_difference': distribution(np.abs(gray_a[union] - gray_b[union])),
            'whole_page_ssim': similarity, 'whole_page_mean_rgb_difference': float(delta.mean())}


def flagged_regions(before, after, side=192):
    """Nonoverlapping tiles retain tiny local defects; whitespace cannot average them away."""
    a, b = np.asarray(before), np.asarray(after)
    if a.shape != b.shape:
        return []
    changed = np.any(a != b, axis=2)
    regions = []
    for y in range(0, a.shape[0], side):
        for x in range(0, a.shape[1], side):
            if changed[y:y+side, x:x+side].any():
                regions.append((x, y, min(x+side, a.shape[1]), min(y+side, a.shape[0])))
    return regions


def evidence(before, after, directory, stem):
    directory = Path(directory)
    before.save(directory / f'{stem}-source.png')
    after.save(directory / f'{stem}-output.png')
    a, b = np.asarray(before), np.asarray(after)
    overlay = np.full_like(a, 255)
    ga = np.dot(a, [.299, .587, .114]) < 250
    gb = np.dot(b, [.299, .587, .114]) < 250
    overlay[ga] = [255, 0, 0]
    overlay[gb] = [0, 140, 255]
    overlay[ga & gb] = [0, 0, 0]
    Image.fromarray(overlay).save(directory / f'{stem}-overlay.png')
    heat = np.max(np.abs(a.astype('int16') - b), axis=2).astype('uint8')
    Image.fromarray(np.stack([heat, np.zeros_like(heat), 255-heat], axis=2)).save(directory / f'{stem}-heatmap.png')
    return {name: f'{stem}-{name}.png' for name in ('source', 'output', 'overlay', 'heatmap')}
