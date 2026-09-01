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

from .models import Centering, DucknMetadata
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


# Extensions that describe a *source file* rather than this array. They do
# not survive derivation (duckn-spec §4.5); recording what an array was
# derived from is the `provenance` extension's job, not theirs.
_SOURCE_PROVENANCE_EXTENSIONS = frozenset({"dicom", "nifti", "fits"})


def _resolve_centering(vol: Volume, override: Centering | None) -> Centering:
    """Decide which sample/extent convention governs this resample.

    Centering is what relates sample count to spatial extent (spec §"centering"),
    so it decides both how scipy maps output samples onto the input grid and
    where the resampled grid lands in world space. Getting it wrong shifts the
    image by half a voxel.

    The axes must agree: scipy applies one convention to the whole array, and
    guessing for a disagreeing axis would silently shift that axis alone.
    """
    if override is not None:
        return override

    declared = {
        i: ax.centering
        for i, ax in enumerate(vol.metadata.axes)
        if ax.space_direction is not None
    }
    distinct = {c for c in declared.values() if c is not None}
    if len(distinct) > 1:
        disagreeing = ", ".join(
            f"axis {i}: {c.value}" for i, c in declared.items() if c is not None
        )
        raise ValueError(
            f"Cannot resample: spatial axes declare different centerings "
            f"({disagreeing}). scipy applies one convention to the whole array. "
            f"Pass centering= to choose explicitly."
        )
    if distinct:
        return distinct.pop()

    # Unknown: the spec says omit centering when it is unknown, so nothing in
    # the metadata answers this. Cell is what every duckn converter writes and
    # what DICOM/NIfTI mean by a voxel, so it is the useful default — but it is
    # an assumption, which is why the resolved value is recorded on the output.
    return Centering.CELL


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
    centering: Centering | None = None,
    anti_alias: bool = True,
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
        Rarely reached: a resample preserves the extent, so every output
        sample lands inside the input's own footprint and the boundary is
        clamped rather than padded.
    centering : Centering, optional
        Override the sample/extent convention. By default it is read from the
        axes' declared ``centering``, which must agree across spatial axes;
        axes that declare none are treated as ``cell``. The resolved value is
        recorded on the output axes.

        ``cell`` preserves the field of view: ``n * spacing`` is held constant,
        so sample positions shift by half the spacing difference.
        ``node`` preserves the sample extent: the first and last samples stay
        put and ``(n - 1) * spacing`` is held constant.
    anti_alias : bool
        Pre-blur downsampled axes to suppress aliasing (default True). Turn it
        off to reproduce a plain ``ndimage.zoom``, which is what a consumer
        trained or validated on unfiltered resampling expects: the blur trades
        contrast in small structures for the absence of aliasing, and for
        those consumers that trade is a distribution shift rather than an
        improvement. Has no effect when upsampling or when ``order=0``.

    Returns
    -------
    Volume with resampled data and updated metadata

    Notes
    -----
    Output spacing is derived from the shape scipy actually produced, not from
    the requested target: ``shape``/``factor``/``spacing`` all round to a whole
    number of samples, and the realized spacing is what the array means.

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
    resolved_centering = _resolve_centering(vol, centering)

    # Check if any resampling is needed
    if np.allclose(zoom_factors, 1.0, rtol=1e-6):
        return vol

    ndimage = _require_scipy_ndimage()

    # Resample on raw stored values. Affine value_transforms commute with
    # interpolation, so the result is equivalent to resampling calibrated
    # values, while preserving the source dtype and the metadata's
    # value_transforms for the result.
    #
    # A non-affine transform (lut) does not commute: the table applied to an
    # interpolated stored value is not the interpolation of the looked-up
    # values (spec §4.4). It must be applied first, which materializes the
    # result. Nearest-neighbor is exempt — it selects an existing sample
    # rather than averaging, so it commutes with any transform.
    from .zarr_io import has_nonlinear_transforms

    materialize = order > 0 and has_nonlinear_transforms(vol.metadata.value_transforms)

    if materialize:
        data = vol.data.astype(float)
    else:
        data = vol.raw.astype(float) if order > 0 else vol.raw
    spatial_indices = [
        i for i, ax in enumerate(vol.metadata.axes)
        if ax.space_direction is not None
    ]

    for axis in range(geom.ndim):
        if anti_alias and zoom_factors[axis] < 1.0 - 1e-6 and order > 0:
            sigma = [0.0] * vol.raw.ndim
            data_axis = spatial_indices[axis]
            sigma[data_axis] = 0.5 / zoom_factors[axis]
            data = ndimage.gaussian_filter(data, sigma)

    # Build full zoom array (1.0 for non-spatial axes)
    full_zoom = np.ones(vol.raw.ndim)
    for i, si in enumerate(spatial_indices):
        full_zoom[si] = zoom_factors[i]

    # Resample. grid_mode carries the centering into scipy: True measures
    # distance across the full sample extent (cell), False between sample
    # centers (node).
    #
    # Boundary handling is "nearest" because a resample preserves the extent:
    # every output sample lands inside the input's own footprint, so there is
    # nothing genuinely outside to fill. Under cell centering the outermost
    # half-cell does sit beyond the last sample *center*, and clamping is what
    # "the sample owns its cell" means there. Padding with cval instead would
    # blend every boundary voxel toward a value the data never had.
    grid_mode = resolved_centering is Centering.CELL
    resampled = ndimage.zoom(
        data,
        full_zoom,
        order=int(order),
        mode="nearest",
        cval=fill,
        grid_mode=grid_mode,
    )

    # Cast back to original dtype for nearest-neighbor
    if order == 0:
        resampled = resampled.astype(vol.raw.dtype)

    # Update metadata — scale space_direction, thickness, clear samples
    new_meta = deepcopy(vol.metadata)

    # The values written are now the calibrated ones, so the transforms that
    # produced them must not be carried forward (spec §4.3): keeping them
    # would apply the chain a second time on the next read.
    if materialize:
        new_meta.value_transforms = None

    # A resampled array is derived with respect to whatever its source
    # format described, so format-specific provenance does not survive
    # (spec §4.5). What that metadata says about an acquisition is no
    # longer true of this array's values or its grid.
    if new_meta.extensions:
        kept = {
            name: ext
            for name, ext in new_meta.extensions.items()
            if name not in _SOURCE_PROVENANCE_EXTENSIONS
        }
        new_meta.extensions = kept or None
    # Update the grid. The scale comes from the shape scipy actually produced,
    # because rounding to a whole number of samples means the realized spacing
    # is not the requested one, and the array means what it realized.
    origin_shift = np.zeros(len(new_meta.space_origin or []) or geom.ndim)
    spatial_idx = 0
    for i, ax in enumerate(new_meta.axes):
        if ax.space_direction is None:
            continue
        data_axis = spatial_indices[spatial_idx]
        n_in, n_out = vol.raw.shape[data_axis], resampled.shape[data_axis]

        if resolved_centering is Centering.NODE and n_in > 1 and n_out > 1:
            # Node: the first and last samples are fixed, so n-1 intervals
            # span an extent that does not change.
            scale = (n_in - 1) / (n_out - 1)
        else:
            # Cell: each sample owns a cell, so n * spacing is what is fixed.
            scale = n_in / n_out

        old_direction = list(ax.space_direction)
        ax.space_direction = [v * scale for v in old_direction]
        if ax.thickness is not None:
            ax.thickness = ax.thickness * scale
        ax.samples = None  # no longer valid after resampling
        # Record the convention that was applied, so a later reader — or a
        # second resample — does not have to make the same assumption again.
        ax.centering = resolved_centering

        if resolved_centering is Centering.CELL:
            # The field of view is preserved, so the outer boundary stays put
            # while the first sample center moves inward by half the change in
            # spacing. Expressed as direction vectors this holds for oblique
            # and rotated frames too.
            origin_shift += (
                np.asarray(ax.space_direction) - np.asarray(old_direction)
            ) / 2.0
        spatial_idx += 1

    if new_meta.space_origin is not None and origin_shift.any():
        new_meta.space_origin = [
            float(o + d) for o, d in zip(new_meta.space_origin, origin_shift)
        ]

    return Volume(raw=resampled, metadata=new_meta)
