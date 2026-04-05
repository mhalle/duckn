"""Nibabel adapter for duckn volumes.

Converts between ``Volume`` and ``nib.Nifti1Image`` with correct
spatial metadata. NIfTI uses RAS convention.

Requires: ``pip install nibabel`` or ``pip install duckn[nibabel]``
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np

from .models import DucknMetadata, SpaceName
from .spatial import VolumeGeometry
from .volume import Volume

# Sign flips from named space → RAS
_TO_RAS_FLIP: dict[str, list[float]] = {
    "right-anterior-superior": [1, 1, 1],       # already RAS
    "left-posterior-superior": [-1, -1, 1],      # LPS → RAS
    "left-anterior-superior": [-1, 1, 1],        # LAS → RAS
}


def _get_ras_flip(meta: DucknMetadata) -> np.ndarray:
    """Get the sign flip vector to convert from the volume's space to RAS."""
    space = str(meta.space) if meta.space else "right-anterior-superior"
    flip = _TO_RAS_FLIP.get(space)
    if flip is None:
        flip = [1, 1, 1]
    return np.array(flip, dtype=float)


def to_nifti(vol: Volume, space: str = "world") -> Any:
    """Convert a duckn Volume to a nibabel Nifti1Image.

    Parameters
    ----------
    vol : input Volume
    space : coordinate space ("world", "axis-aligned",
            "axis-aligned-centered", or any named space)

    Returns
    -------
    nib.Nifti1Image with correct affine
    """
    import nibabel as nib

    geom = vol.geometry
    flip = _get_ras_flip(vol.meta)
    ndim = geom.ndim

    if space == "world":
        # Build 4×4 affine: RAS = flip * D @ index + flip * origin
        # Axes are reversed: duckn C-order (slowest-first) → NIfTI (fastest-first)
        # space_origin is the position of the first sample — no centering offset.
        D_ras = np.zeros((ndim, ndim))
        for i in range(ndim):
            for j in range(ndim):
                D_ras[i][j] = geom.D[i][ndim - 1 - j] * flip[i]

        origin_ras = geom.origin * flip

        affine = np.eye(4)
        affine[:3, :3] = D_ras
        affine[:3, 3] = origin_ras

    elif space == "axis-aligned":
        spacing = np.array([geom.S[j, j] for j in range(ndim)])
        spacing_rev = spacing[::-1]
        origin_ras = geom.origin * flip

        affine = np.eye(4)
        for j in range(ndim):
            affine[j, j] = spacing_rev[j] * flip[j]
        affine[:3, 3] = origin_ras

    elif space == "axis-aligned-centered":
        spacing = np.array([geom.S[j, j] for j in range(ndim)])
        spacing_rev = spacing[::-1]
        center = geom.origin + np.array([
            (geom.shape[j] - 1) / 2.0 * geom.S[j, j] for j in range(ndim)
        ])
        origin_centered = (geom.origin - center) * flip

        affine = np.eye(4)
        for j in range(ndim):
            affine[j, j] = spacing_rev[j] * flip[j]
        affine[:3, 3] = origin_centered

    else:
        raise ValueError(f"Unsupported space: {space!r}")

    # Reverse data axes to match NIfTI i,j,k (fastest-first)
    data_nifti = vol.data.transpose()

    return nib.Nifti1Image(data_nifti, affine)


def from_nifti(
    img: Any,
    meta: DucknMetadata | None = None,
    space: str = "world",
) -> Volume:
    """Convert a nibabel Nifti1Image to a duckn Volume.

    Parameters
    ----------
    img : nib.Nifti1Image
    meta : optional DucknMetadata to preserve. If None, creates
           minimal metadata in RAS space.
    space : coordinate space the NIfTI image is in

    Returns
    -------
    Volume with data and spatial metadata
    """
    import nibabel as nib

    data_nifti = np.asarray(img.dataobj)
    affine = img.affine
    ndim = 3

    # Reverse NIfTI i,j,k (fastest-first) → duckn C-order (slowest-first)
    data = data_nifti.transpose()

    if meta is not None:
        new_meta = deepcopy(meta)
        flip = _get_ras_flip(meta)
    else:
        from .models import AxisKind, AxisMetadata, Centering
        flip = np.array([1, 1, 1], dtype=float)  # RAS
        new_meta = DucknMetadata(
            space=SpaceName.RIGHT_ANTERIOR_SUPERIOR,
            space_origin=[0.0, 0.0, 0.0],
            axes=[
                AxisMetadata(
                    kind=AxisKind.SPACE,
                    centering=Centering.CELL,
                    space_direction=[0.0, 0.0, 0.0],
                    unit="mm",
                )
                for _ in range(ndim)
            ],
        )

    # Extract direction and spacing from affine
    # affine[:3, :3] = D_ras (in NIfTI i,j,k order)
    D_ras = affine[:3, :3]
    origin_ras = affine[:3, 3]

    # Undo RAS flip
    origin = origin_ras / flip

    # Reverse column order back to duckn C-order and undo flip
    D_duckn = np.zeros((3, 3))
    for i in range(3):
        for j in range(3):
            D_duckn[i][j] = D_ras[i][2 - j] / flip[i]

    new_meta.space_origin = origin.tolist()

    # space_origin = position of first sample (no centering offset)
    new_meta.space_origin = origin.tolist()

    spatial_idx = 0
    for ax in new_meta.axes:
        if ax.space_direction is not None or meta is None:
            ax.space_direction = D_duckn[:, spatial_idx].tolist()
            ax.samples = None
            spatial_idx += 1

    return Volume(data=data, meta=new_meta)
