"""Resample duckn volumes to a target resolution.

Supports three ways to specify the target:
- ``spacing``: physical resolution (always isotropic)
- ``shape``: pixel count (scalar=cube, tuple=per-axis)
- ``factor``: relative zoom (scalar=uniform, list=per-axis)

Handles both upsampling and downsampling per-axis. Downsampled
axes are pre-blurred with a Gaussian to prevent aliasing.
"""

from __future__ import annotations

from copy import deepcopy
from enum import IntEnum
from typing import Any

import numpy as np

from .models import DucknMetadata
from .spatial import VolumeGeometry
from .volume import Volume


def _require_scipy_ndimage():
    """Lazy-import scipy.ndimage with a helpful error if missing."""
    try:
        from scipy import ndimage
    except ImportError as e:
        raise ImportError(
            "duckn.resample requires scipy. "
            "Install with: pip install duckn[resample]  (or `pip install scipy`)"
        ) from e
    return ndimage


class Interpolation(IntEnum):
    """Interpolation method for resampling."""

    NEAREST = 0
    LINEAR = 1
    CUBIC = 3


def _compute_zoom_factors(
    vol: Volume,
    spacing: float | None,
    shape: int | tuple[int, ...] | None,
    factor: float | list[float] | None,
) -> np.ndarray:
    """Compute per-spatial-axis zoom factors from the target specification.

    Returns array of zoom factors (>1 = upsample, <1 = downsample).
    """
    geom = vol.geometry
    current_spacing = geom.voxel_size
    current_shape = np.array(geom.shape, dtype=float)
    ndim = geom.ndim

    # Count how many targets are specified
    n_specified = sum(x is not None for x in (spacing, shape, factor))
    if n_specified > 1:
        raise ValueError("Only one of spacing, shape, or factor may be specified")

    if n_specified == 0:
        # Default: isotropic at finest spacing
        target_spacing = np.full(ndim, current_spacing.min())
        return current_spacing / target_spacing

    if spacing is not None:
        # Isotropic at the given spacing
        target_spacing = np.full(ndim, float(spacing))
        return current_spacing / target_spacing

    if factor is not None:
        # Relative zoom
        if isinstance(factor, (int, float)):
            return np.full(ndim, float(factor))
        factors = list(factor)
        if len(factors) != ndim:
            raise ValueError(
                f"factor list length {len(factors)} != spatial ndim {ndim}"
            )
        return np.array([float(f) for f in factors])

    if shape is not None:
        # Target pixel count
        if isinstance(shape, (int, float)):
            # Scalar = cube: same size on all axes
            target = np.full(ndim, float(shape))
        else:
            target = list(shape)
            if len(target) != ndim:
                raise ValueError(
                    f"shape tuple length {len(target)} != spatial ndim {ndim}"
                )
            target = np.array([float(s) for s in target])

        return target / current_shape

    raise RuntimeError("unreachable")


def resample(
    vol: Volume,
    *,
    spacing: float | None = None,
    shape: int | tuple[int, ...] | None = None,
    factor: float | list[float] | None = None,
    order: int | Interpolation = Interpolation.LINEAR,
    fill: float = 0,
) -> Volume:
    """Resample a volume to a target resolution.

    Specify the target with exactly one of ``spacing``, ``shape``, or
    ``factor``.  When none is given, resamples to isotropic at the
    finest current spacing.

    Parameters
    ----------
    vol : input Volume
    spacing : float, optional
        Isotropic target spacing in physical units (e.g., 1.0 for 1mm).
    shape : int or tuple of int, optional
        Target pixel count.  Scalar = uniform cube (e.g., 128 → 128³).
        Tuple = per-axis (e.g., (128, 256, 256)).
    factor : float or list of float, optional
        Relative zoom factor.  Scalar = uniform (e.g., 2 = double
        resolution).  List = per-axis (e.g., [2, 1, 1] = double
        only the slice axis).
    order : interpolation method
        Interpolation.NEAREST (0) — for labelmaps/segmentations
        Interpolation.LINEAR (1)  — default, for images
        Interpolation.CUBIC (3)   — high-quality images
    fill : value for out-of-bounds voxels (default 0)

    Returns
    -------
    Volume with resampled data and updated metadata

    Examples
    --------
    >>> resample(vol)                          # isotropic at finest spacing
    >>> resample(vol, spacing=1.0)             # isotropic at 1mm
    >>> resample(vol, shape=128)               # 128³ cube
    >>> resample(vol, shape=(128, 256, 256))   # fully specified
    >>> resample(vol, factor=2)                # double resolution
    >>> resample(vol, factor=[2, 1, 1])        # double slice axis only
    >>> resample(vol, factor=0.5)              # half resolution (pyramid)
    >>> resample(seg_vol, order=0)             # nearest for labels
    """
    geom = vol.geometry
    current_spacing = geom.voxel_size
    zoom_factors = _compute_zoom_factors(vol, spacing, shape, factor)

    # Check if any resampling is needed
    if np.allclose(zoom_factors, 1.0, rtol=1e-6):
        return vol

    ndimage = _require_scipy_ndimage()

    # Resample on raw stored values. Linear value_transforms commute with
    # linear interpolation, so the result is equivalent to resampling
    # calibrated values, while preserving the source dtype and the
    # metadata's value_transforms for the result.
    data = vol.raw.astype(float) if order > 0 else vol.raw
    spatial_indices = [
        i for i, ax in enumerate(vol.metadata.axes)
        if ax.space_direction is not None
    ]

    for axis in range(geom.ndim):
        if zoom_factors[axis] < 1.0 - 1e-6 and order > 0:
            sigma = [0.0] * vol.raw.ndim
            data_axis = spatial_indices[axis]
            sigma[data_axis] = 0.5 / zoom_factors[axis]
            data = ndimage.gaussian_filter(data, sigma)

    # Build full zoom array (1.0 for non-spatial axes)
    full_zoom = np.ones(vol.raw.ndim)
    for i, si in enumerate(spatial_indices):
        full_zoom[si] = zoom_factors[i]

    # Resample
    resampled = ndimage.zoom(
        data, full_zoom, order=int(order), mode="constant", cval=fill,
    )

    # Cast back to original dtype for nearest-neighbor
    if order == 0:
        resampled = resampled.astype(vol.raw.dtype)

    # Update metadata — scale space_direction, thickness, clear samples
    new_meta = deepcopy(vol.metadata)
    spatial_idx = 0
    for i, ax in enumerate(new_meta.axes):
        if ax.space_direction is not None:
            scale = 1.0 / zoom_factors[spatial_idx]
            ax.space_direction = [v * scale for v in ax.space_direction]
            if ax.thickness is not None:
                ax.thickness = ax.thickness * scale
            ax.samples = None  # no longer valid after resampling
            spatial_idx += 1

    return Volume(raw=resampled, metadata=new_meta)
