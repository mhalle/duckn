"""Resample duckn volumes to a target spacing.

Handles both upsampling and downsampling per-axis. Downsampled
axes are pre-blurred with a Gaussian to prevent aliasing.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np
from scipy import ndimage

from .models import DucknMetadata
from .spatial import VolumeGeometry
from .volume import Volume


def _resolve_target_spacing(
    current_spacing: np.ndarray,
    target: Any,
) -> np.ndarray:
    """Resolve a target specification to a concrete spacing vector.

    target: None           → isotropic (match finest axis)
            float          → uniform spacing
            list/array     → per-axis (None elements = keep current)
            Volume         → match that volume's spacing
            VolumeGeometry → match that geometry's spacing
    """
    if target is None:
        # Isotropic at finest current spacing
        return np.full_like(current_spacing, current_spacing.min())

    if isinstance(target, Volume):
        return target.geometry.voxel_size

    if isinstance(target, VolumeGeometry):
        return target.voxel_size

    if isinstance(target, (int, float)):
        return np.full_like(current_spacing, float(target))

    # List/array — None means keep current
    target = list(target)
    result = np.array(current_spacing, dtype=float)
    for i, t in enumerate(target):
        if t is not None:
            result[i] = float(t)
    return result


def resample(
    vol: Volume,
    target: Any = None,
    *,
    order: int = 1,
    fill: float = 0,
) -> Volume:
    """Resample a volume to a target spacing.

    Parameters
    ----------
    vol : input Volume
    target : target spacing specification
        None           → isotropic (match finest current spacing)
        float          → uniform spacing (e.g., 1.0 for 1mm isotropic)
        [a, b, c]      → per-axis spacing (None = keep current)
        Volume         → match that volume's spacing
        VolumeGeometry → match that geometry's spacing
    order : interpolation order
        0 = nearest-neighbor (for labelmaps/segmentations)
        1 = linear (default, for images)
        3 = cubic B-spline
    fill : value for out-of-bounds voxels (default 0)

    Returns
    -------
    Volume with resampled data and updated metadata
    """
    geom = vol.geometry
    current_spacing = geom.voxel_size
    target_spacing = _resolve_target_spacing(current_spacing, target)

    # Compute zoom factors per spatial axis
    zoom_factors = current_spacing / target_spacing

    # Check if any resampling is needed
    if np.allclose(zoom_factors, 1.0, rtol=1e-6):
        return vol  # nothing to do

    # For axes being downsampled, pre-blur to prevent aliasing
    data = vol.data.astype(float) if order > 0 else vol.data
    for axis in range(geom.ndim):
        if zoom_factors[axis] < 1.0 - 1e-6:
            # Downsampling this axis — Gaussian blur with sigma proportional
            # to the downsample ratio
            sigma = [0.0] * vol.data.ndim
            # Map spatial axis to data axis
            spatial_indices = [
                i for i, ax in enumerate(vol.meta.axes)
                if ax.space_direction is not None
            ]
            data_axis = spatial_indices[axis]
            sigma[data_axis] = 0.5 / zoom_factors[axis]
            if order > 0:
                data = ndimage.gaussian_filter(data, sigma)

    # Build full zoom array (1.0 for non-spatial axes)
    full_zoom = np.ones(vol.data.ndim)
    spatial_indices = [
        i for i, ax in enumerate(vol.meta.axes)
        if ax.space_direction is not None
    ]
    for i, si in enumerate(spatial_indices):
        full_zoom[si] = zoom_factors[i]

    # Resample
    resampled = ndimage.zoom(
        data, full_zoom, order=order, mode="constant", cval=fill,
    )

    # Cast back to original dtype for nearest-neighbor
    if order == 0:
        resampled = resampled.astype(vol.data.dtype)

    # Update metadata
    new_meta = deepcopy(vol.meta)
    spatial_idx = 0
    for i, ax in enumerate(new_meta.axes):
        if ax.space_direction is not None:
            # Scale space_direction to new spacing
            old_mag = current_spacing[spatial_idx]
            new_mag = target_spacing[spatial_idx]
            scale = new_mag / old_mag
            ax.space_direction = [v * scale for v in ax.space_direction]
            # Clear per-sample data (no longer valid after resampling)
            ax.samples = None
            spatial_idx += 1

    return Volume(data=resampled, meta=new_meta)
