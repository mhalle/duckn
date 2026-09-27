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

# Sign flips from named source space → target convention.
# Each entry maps "<source space name>" → flip vector for the named target.
_TO_TARGET_FLIP: dict[str, dict[str, list[float]]] = {
    "lps": {
        "left-posterior-superior": [1, 1, 1],     # already LPS
        "right-anterior-superior": [-1, -1, 1],   # RAS → LPS
        "left-anterior-superior": [1, -1, 1],     # LAS → LPS
    },
    "ras": {
        "left-posterior-superior": [-1, -1, 1],   # LPS → RAS
        "right-anterior-superior": [1, 1, 1],     # already RAS
        "left-anterior-superior": [-1, 1, 1],     # LAS → RAS
    },
}


#: Extensions that describe the SOURCE file an array was converted from (format provenance).
#: They are true of a faithful re-encoding of that file and of nothing derived from it
#: (duckn-spec §4.5, §4.7; keyvalues-extension §3.2).
SOURCE_FORMAT_EXTENSIONS: frozenset[str] = frozenset({
    "dicom", "nifti", "fits", "nrrd", "keyvalues", "legacy",
})


def _pixel_type_disagrees(meta: DucknMetadata, dtype: np.dtype) -> bool:
    """True when the dicom extension states a pixel type the array does not have."""
    tags = ((meta.extensions or {}).get("dicom") or {}).get("tags") or {}
    bits, rep = tags.get("BitsAllocated"), tags.get("PixelRepresentation")
    if dtype.kind == "f":
        return bits is not None or rep is not None
    try:
        if bits is not None and int(bits) != dtype.itemsize * 8:
            return True
        if rep is not None and int(rep) != (1 if dtype.kind == "i" else 0):
            return True
    except (TypeError, ValueError):
        return True
    return False


def _geometry_differs(src: DucknMetadata, new: DucknMetadata) -> bool:
    if (src.space_origin is None) != (new.space_origin is None):
        return True
    if src.space_origin is not None and not np.allclose(
            src.space_origin, new.space_origin, rtol=1e-6, atol=1e-6):
        return True
    for a, b in zip(src.axes or [], new.axes or []):
        if (a.space_direction is None) != (b.space_direction is None):
            return True
        if a.space_direction is not None and not np.allclose(
                a.space_direction, b.space_direction, rtol=1e-6, atol=1e-6):
            return True
    return False


def carry_metadata(
    src: DucknMetadata,
    new: DucknMetadata,
    data: np.ndarray,
    derived: bool | None,
) -> DucknMetadata:
    """Drop ``new``'s source-format extensions when the array is derived from ``src``'s source.

    An adapter handed ``metadata=`` cannot see what was done to the image in between, so it
    judges from what it can see (``derived=None``): the array is derived when ``src`` had
    ``value_transforms`` (the incoming values are calibrated, not the source's stored values),
    when its grid moved (origin or any axis direction or spacing), when its number of axes
    differs, or when it contradicts the pixel type the dicom extension states. ``derived=True``
    or ``False`` says so outright. 0.5.4 always carried them, so a shrunk float image kept
    ``dicom.stored_values: true`` and ``BitsStored``.
    """
    if derived is None:
        derived = bool(
            src.value_transforms
            or len(src.axes or []) != data.ndim
            or _geometry_differs(src, new)
            or _pixel_type_disagrees(src, data.dtype)
        )
    if not derived:
        return new
    if new.extensions:
        kept = {k: v for k, v in new.extensions.items() if k not in SOURCE_FORMAT_EXTENSIONS}
        new.extensions = kept or None
    for ax in new.axes or []:
        if ax.extensions:
            kept = {k: v for k, v in ax.extensions.items() if k not in SOURCE_FORMAT_EXTENSIONS}
            ax.extensions = kept or None
        if ax.samples:
            # per-sample metadata carried from the source (a slice's DICOM tags) goes with it;
            # positions and origins are geometry and stay
            ax.samples = [
                sm.model_copy(update={"metadata": {
                    k: v for k, v in sm.metadata.items() if k not in SOURCE_FORMAT_EXTENSIONS
                } or None}) if sm.metadata else sm
                for sm in ax.samples
            ]
    return new


def _get_target_flip(meta: DucknMetadata, convention: str = "lps") -> np.ndarray:
    """Get the sign flip vector to convert from the volume's space to *convention*."""
    if convention not in _TO_TARGET_FLIP:
        raise ValueError(
            f"Unknown convention {convention!r}; expected one of {list(_TO_TARGET_FLIP)}"
        )
    space = str(meta.space) if meta.space else "left-posterior-superior"
    flip = _TO_TARGET_FLIP[convention].get(space)
    if flip is None:
        # Unknown space — assume no flip needed
        flip = [1, 1, 1]
    return np.array(flip, dtype=float)


# Backward-compat alias — existing call sites assume LPS.
def _get_lps_flip(meta: DucknMetadata) -> np.ndarray:
    return _get_target_flip(meta, convention="lps")


def to_lps_params(
    vol: Volume,
    space: str = "world",
    convention: str = "lps",
) -> dict:
    """Compute spatial parameters for an external library.

    Despite the name, this function supports both LPS and RAS targets via
    the *convention* parameter; the default remains LPS for backward
    compatibility with existing adapter call sites.

    Returns dict with keys:
        spacing: (sx, sy, sz) in xyz order
        origin: (ox, oy, oz) in *convention*
        direction: 9-element flat array, row-major, xyz basis vectors in *convention*
        data: numpy array (unchanged, zyx C-order)

    Parameters
    ----------
    vol : input Volume
    space : coordinate space to export in ("world", "axis-aligned",
            "axis-aligned-centered", or any named space)
    convention : "lps" (default) or "ras"
    """
    geom = vol.geometry
    flip = _get_target_flip(vol.metadata, convention=convention)

    if space == "world":
        origin = geom.origin * flip
        # Direction cosines in LPS: flip rows corresponding to flipped axes
        direction = geom.direction_cosines.copy()
        for i in range(geom.ndim):
            direction[i, :] *= flip[i]
        spacing = geom.spacing

    elif space == "axis-aligned":
        # Remove rotation, keep spacing. Origin is first sample position.
        origin = geom.origin * flip
        spacing = np.diag(geom.S)
        direction = np.eye(geom.ndim)
        for i in range(geom.ndim):
            direction[i, i] *= flip[i]

    elif space == "axis-aligned-centered":
        spacing = np.diag(geom.S)
        direction = np.eye(geom.ndim)
        for i in range(geom.ndim):
            direction[i, i] *= flip[i]
        # Center = origin + (n-1)/2 * spacing
        center = geom.origin + np.array([
            (geom.shape[j] - 1) / 2.0 * geom.S[j, j] for j in range(geom.ndim)
        ])
        origin = (geom.origin - center) * flip

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
