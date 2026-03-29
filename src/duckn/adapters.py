"""Shared adapter utilities for converting duckn volumes to external formats.

External libraries (SimpleITK, VTK) typically use:
- LPS physical coordinate system
- xyz axis order (fastest-first) for spacing/origin/direction
- zyx numpy arrays (same as duckn C-order)

This module provides the common conversion logic.
"""

from __future__ import annotations

import numpy as np

from .models import DucknMetadata, SpaceName
from .spatial import VolumeGeometry
from .volume import Volume

# Sign flips from named space → LPS
_TO_LPS_FLIP: dict[str, list[float]] = {
    "left-posterior-superior": [1, 1, 1],       # already LPS
    "right-anterior-superior": [-1, -1, 1],     # RAS → LPS
    "left-anterior-superior": [1, -1, 1],       # LAS → LPS
}


def _get_lps_flip(meta: DucknMetadata) -> np.ndarray:
    """Get the sign flip vector to convert from the volume's space to LPS."""
    space = str(meta.space) if meta.space else "left-posterior-superior"
    flip = _TO_LPS_FLIP.get(space)
    if flip is None:
        # Unknown space — assume no flip needed
        flip = [1, 1, 1]
    return np.array(flip, dtype=float)


def to_lps_params(
    vol: Volume,
    space: str = "world",
) -> dict:
    """Compute LPS-convention spatial parameters for an external library.

    Returns dict with keys:
        spacing: (sx, sy, sz) in xyz order
        origin: (ox, oy, oz) in LPS
        direction: 9-element flat array, row-major, xyz basis vectors in LPS
        data: numpy array (unchanged, zyx C-order)

    Parameters
    ----------
    vol : input Volume
    space : coordinate space to export in ("world", "axis-aligned",
            "axis-aligned-centered", or any named space)
    """
    geom = vol.geometry
    flip = _get_lps_flip(vol.meta)

    if space == "world":
        origin = geom.origin * flip
        # Direction cosines in LPS: flip rows corresponding to flipped axes
        direction = geom.direction_cosines.copy()
        for i in range(geom.ndim):
            direction[i, :] *= flip[i]
        spacing = geom.spacing

    elif space == "axis-aligned":
        # Remove rotation, keep spacing
        origin = (geom.origin + geom.D @ geom.centering) * flip
        spacing = np.diag(geom.S)
        direction = np.eye(geom.ndim)
        for i in range(geom.ndim):
            direction[i, i] *= flip[i]

    elif space == "axis-aligned-centered":
        spacing = np.diag(geom.S)
        direction = np.eye(geom.ndim)
        for i in range(geom.ndim):
            direction[i, i] *= flip[i]
        # Origin at volume center
        p = geom.origin + geom.D @ geom.centering
        extent = np.array([geom.S[j, j] * geom.shape[j] for j in range(geom.ndim)])
        center = p + extent / 2
        origin = (p - center) * flip  # relative to center

    elif space in geom._named_transforms:
        # Transform origin to named space
        nt = geom._named_transforms[space]
        if nt.forward is not None:
            origin_world = geom.origin
            builtin_coords = geom._from_builtin(origin_world, nt.from_space)
            origin_named = geom._apply_affine(nt.forward, builtin_coords)
        else:
            origin_named = geom.origin
        origin = origin_named * flip
        spacing = geom.spacing
        direction = geom.direction_cosines.copy()
        for i in range(geom.ndim):
            direction[i, :] *= flip[i]

    else:
        raise ValueError(f"Unknown space: {space!r}")

    # Reverse spatial axes for xyz order (duckn is slowest-first, libs want fastest-first)
    spacing_xyz = spacing[::-1]
    origin_xyz = origin  # origin is already in xyz (physical space)

    # Direction: reverse column order (axis j → axis ndim-1-j)
    direction_xyz = direction[:, ::-1]
    direction_flat = direction_xyz.flatten().tolist()

    return {
        "spacing": tuple(float(s) for s in spacing_xyz),
        "origin": tuple(float(o) for o in origin_xyz),
        "direction": direction_flat,
        "data": vol.data,
    }
