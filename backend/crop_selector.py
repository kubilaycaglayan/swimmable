"""Numba accelerated fast-v2.2 lower-water crop selection.

Policy ported from wave-regression-model's step_2_b_crop_selector.py. The
release's preprocessing contract is pinned alongside the model submodule.
"""
import time

import numpy as np
from numba import njit

OUTPUT_SIZE = (224, 224)
LOWER_WATER_FRACTION = 0.35
MINIMUM_LOWER_RETENTION = 0.95
BLACK_RATIO_BUCKET = 0.005
MINIMUM_WATER_FRACTION = 0.0005
MINIMUM_CROP_AREA_FRACTION = 0.10
MINIMUM_CROP_WIDTH = 8


@njit(cache=True)
def _summed_area(mask, row_start):
    height, width = mask.shape
    table = np.zeros((height + 1, width + 1), dtype=np.int64)
    for y in range(height):
        row_sum = 0
        for x in range(width):
            if row_start < 0 or y >= row_start:
                row_sum += int(mask[y, x])
            table[y + 1, x + 1] = table[y, x + 1] + row_sum
    return table


@njit(cache=True)
def _rectangle_sum(table, left, top, right, bottom):
    return table[bottom, right] - table[top, right] - table[bottom, left] + table[top, left]


@njit(cache=True)
def _select_crop_kernel(water_mask, black_mask):
    height, width = water_mask.shape
    bbox_left, bbox_top = width, height
    bbox_right, bbox_bottom = -1, -1
    total_water = 0
    for y in range(height):
        for x in range(width):
            if water_mask[y, x]:
                total_water += 1
                bbox_left = min(bbox_left, x)
                bbox_right = max(bbox_right, x)
                bbox_top = min(bbox_top, y)
                bbox_bottom = max(bbox_bottom, y)

    empty_box = np.zeros(4, dtype=np.int64)
    empty_metrics = np.zeros(5, dtype=np.float64)
    if bbox_right < bbox_left:
        return False, empty_box, empty_metrics
    bbox_right += 1
    bbox_bottom += 1
    bbox_width, bbox_height = bbox_right - bbox_left, bbox_bottom - bbox_top
    minimum_water_pixels = max(32, int(np.rint(height * width * MINIMUM_WATER_FRACTION)))
    if total_water < minimum_water_pixels:
        return False, empty_box, empty_metrics

    lower_top = max(bbox_top, bbox_bottom - max(1, int(np.rint(bbox_height * LOWER_WATER_FRACTION))))
    lower_total = 0
    for y in range(lower_top, bbox_bottom):
        for x in range(width):
            lower_total += int(water_mask[y, x])
    if lower_total < minimum_water_pixels:
        return False, empty_box, empty_metrics

    horizontal_step = max(1, int(np.rint(width / 56.0)))
    boundaries = np.empty(width + 1, dtype=np.int64)
    boundary_count = 0
    x = bbox_left
    while x <= bbox_right:
        boundaries[boundary_count] = x
        boundary_count += 1
        x += horizontal_step
    if boundaries[boundary_count - 1] != bbox_right:
        boundaries[boundary_count] = bbox_right
        boundary_count += 1

    water_table = _summed_area(water_mask, -1)
    black_table = _summed_area(black_mask, -1)
    lower_table = _summed_area(water_mask, lower_top)
    minimum_crop_area = max(256, int(np.rint(bbox_width * bbox_height * MINIMUM_CROP_AREA_FRACTION)))
    found = False
    best_tier = best_bucket = best_water_pixels = 0
    best_water_ratio = best_shape_penalty = 0.0
    best_left = best_top = best_right = 0
    best_black_ratio = best_retention = 0.0

    for left_index in range(boundary_count - 1):
        left = boundaries[left_index]
        for right_index in range(left_index + 1, boundary_count):
            right = boundaries[right_index]
            crop_width = right - left
            if crop_width < MINIMUM_CROP_WIDTH:
                continue
            lower_pixels = _rectangle_sum(lower_table, left, lower_top, right, bbox_bottom)
            if lower_pixels < minimum_water_pixels:
                continue
            for top in range(bbox_top, lower_top + 1):
                crop_height = bbox_bottom - top
                if crop_height < 8:
                    continue
                crop_area = crop_width * crop_height
                if crop_area < minimum_crop_area:
                    continue
                retention = _rectangle_sum(lower_table, left, top, right, bbox_bottom) / lower_pixels
                if retention + 1e-12 < MINIMUM_LOWER_RETENTION:
                    continue
                water_pixels = _rectangle_sum(water_table, left, top, right, bbox_bottom)
                black_pixels = _rectangle_sum(black_table, left, top, right, bbox_bottom)
                scale = min(224 / crop_width, 224 / crop_height)
                resized_width = max(1, int(np.rint(crop_width * scale)))
                resized_height = max(1, int(np.rint(crop_height * scale)))
                occupied = resized_width * resized_height / (224 * 224)
                water_ratio = (water_pixels / crop_area) * occupied
                black_ratio = 1.0 - ((crop_area - black_pixels) / crop_area) * occupied
                shape_penalty = abs(np.log(crop_width / crop_height))
                tier = 3 if retention >= 0.995 else (2 if retention >= 0.98 else 1)
                bucket = int(np.rint(black_ratio / BLACK_RATIO_BUCKET))
                better = not found
                if found:
                    if tier != best_tier:
                        better = tier > best_tier
                    elif bucket != best_bucket:
                        better = bucket < best_bucket
                    elif water_pixels != best_water_pixels:
                        better = water_pixels > best_water_pixels
                    elif water_ratio != best_water_ratio:
                        better = water_ratio > best_water_ratio
                    elif shape_penalty != best_shape_penalty:
                        better = shape_penalty < best_shape_penalty
                    elif left != best_left:
                        better = left < best_left
                    elif top != best_top:
                        better = top < best_top
                    elif right != best_right:
                        better = right < best_right
                if better:
                    found = True
                    best_tier, best_bucket = tier, bucket
                    best_water_pixels, best_water_ratio = water_pixels, water_ratio
                    best_shape_penalty = shape_penalty
                    best_left, best_top, best_right = left, top, right
                    best_black_ratio, best_retention = black_ratio, retention

    if not found:
        return False, empty_box, empty_metrics
    return (
        True,
        np.asarray((best_left, best_top, best_right, bbox_bottom), dtype=np.int64),
        np.asarray((best_water_ratio, best_black_ratio, best_retention,
                    best_water_pixels, best_shape_penalty), dtype=np.float64),
    )


def warm_crop_selector():
    started = time.perf_counter()
    empty = np.zeros(OUTPUT_SIZE, dtype=np.bool_)
    _select_crop_kernel(empty, empty)
    return time.perf_counter() - started


def select_crop(water_mask, black_mask):
    water = np.ascontiguousarray(water_mask, dtype=np.bool_)
    black = np.ascontiguousarray(black_mask, dtype=np.bool_)
    found, box, metrics = _select_crop_kernel(water, black)
    if not found:
        return None
    return (tuple(int(value) for value in box), float(metrics[0]), float(metrics[1]),
            float(metrics[2]), int(metrics[3]), float(metrics[4]))
