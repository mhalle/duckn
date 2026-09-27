"""SimpleITK adapter for duckn volumes.

Converts between ``Volume`` and ``sitk.Image`` with correct
spatial metadata, axis ordering, and LPS convention handling.

Requires: ``pip install SimpleITK`` or ``pip install duckn[sitk]``
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np

from .adapters import _get_target_flip, to_lps_params
from .models import DucknMetadata
from .volume import Volume


def to_sitk(vol: Volume, space: str = "world", convention: str = "lps") -> Any:
    """Convert a duckn Volume to a SimpleITK Image.

    Parameters
    ----------
    vol : input Volume
    space : coordinate space ("world", "axis-aligned",
            "axis-aligned-centered", or any named space)

    Returns
    -------
    sitk.Image with correct spacing, origin, and direction
    """
    import SimpleITK as sitk

    params = to_lps_params(vol, space=space, convention=convention)

    img = sitk.GetImageFromArray(params["data"])
    img.SetSpacing(params["spacing"])
    img.SetOrigin(params["origin"])
    img.SetDirection(params["direction"])

    return img


def from_sitk(
    img: Any,
    metadata: DucknMetadata | None = None,
    space: str = "world",
    convention: str = "lps",
    *,
    derived: bool | None = None,
) -> Volume:
    """Convert a SimpleITK Image to a duckn Volume.

    Parameters
    ----------
    img : sitk.Image
    metadata : optional DucknMetadata to preserve (spatial fields will be
           updated from the sitk image). If None, creates minimal metadata:
           a 3D image in LPS (RAS with ``convention="ras"``), a 2D or 4D
           one in an unnamed space (``space_dimension``), and a trailing
           ``list`` axis for a multi-component (vector) image.
    space : coordinate space the sitk image is in ("world", etc.)
    derived : whether the image is derived from the source ``metadata``
           describes (duckn-spec §4.5). A derived array keeps none of the
           source's format extensions (``dicom``, ``nifti``, ``fits``,
           ``nrrd``, ``keyvalues``): they describe the source file, not
           this array. ``None`` (default) judges it: the array is derived
           when ``metadata`` had ``value_transforms`` (the image holds
           calibrated values), when the grid moved, when the number of axes
           differs, or when the pixel type contradicts the ``dicom``
           extension's. Pass ``False`` for an image known to be the source,
           unchanged.

    Returns
    -------
    Volume with data and spatial metadata from the sitk image
    """
    import SimpleITK as sitk

    from .adapters import carry_metadata

    data = sitk.GetArrayFromImage(img)

    spacing_xyz = np.array(img.GetSpacing())
    origin_xyz = np.array(img.GetOrigin())
    ndim = img.GetDimension()
    n_components = img.GetNumberOfComponentsPerPixel()
    direction_flat = np.array(img.GetDirection()).reshape(ndim, ndim)

    # Reverse xyz → zyx for duckn C-order
    spacing_zyx = spacing_xyz[::-1]
    direction_zyx = direction_flat[:, ::-1]

    from .models import AxisKind, AxisMetadata, Centering, SpaceName

    # Convert from external convention back to duckn space
    if metadata is not None:
        new_meta = deepcopy(metadata)
        # The incoming array holds calibrated values — `to_*` writes
        # `vol.data`, and these formats have no notion of a duckn value
        # transform. Carrying the transforms forward would apply them a
        # second time on the next read (duckn-spec §4.3, materialize). `[]`,
        # not an absent field: from convention 1.2 absence means "not
        # stated", and these values ARE the quantity (§3.1).
        new_meta.value_transforms = []
        flip = _get_target_flip(metadata, convention=convention)
    else:
        flip = np.array([1, 1, 1], dtype=float)
        spatial_axes = [
            AxisMetadata(
                kind=AxisKind.SPACE,
                centering=Centering.CELL,
                space_direction=[0.0] * ndim,
                unit="mm",
            )
            for _ in range(ndim)
        ]
        if n_components > 1:
            # a vector image: GetArrayFromImage puts the components last (0.5.4 gave the 4D
            # array three axes)
            spatial_axes.append(AxisMetadata(kind=AxisKind.LIST))
        if ndim == 3:
            named: dict[str, Any] = {"space": (
                SpaceName.RIGHT_ANTERIOR_SUPERIOR
                if convention == "ras"
                else SpaceName.LEFT_POSTERIOR_SUPERIOR
            )}
        else:
            # LPS and RAS are 3D spaces; a 2D image's two-component origin cannot be placed
            # in one (0.5.4 raised building it), so its space is unnamed
            named = {"space_dimension": ndim}
        new_meta = DucknMetadata(
            version="1.0",   # the convention's own rule: always present (duckn-spec §3.1)
            space_origin=[0.0] * ndim,
            axes=spatial_axes,
            **named,
        )

    # the flip is per world axis of a 3D named space; any other space is unflipped
    flip = np.asarray(flip, dtype=float)
    flip = flip[:ndim] if len(flip) >= ndim and ndim == 3 else np.ones(ndim)

    # Undo LPS flip on origin
    origin = origin_xyz * flip
    new_meta.space_origin = origin.tolist()

    # Undo LPS flip on direction and set space_direction
    for i in range(ndim):
        direction_zyx[i, :] *= flip[i]

    j = 0
    for ax in new_meta.axes:
        if j >= ndim:
            break
        if ax.space_direction is not None or (metadata is None):
            # axis j in duckn C-order = axis (ndim-1-j) in xyz
            col = direction_zyx[:, j]
            ax.space_direction = (col * spacing_zyx[j]).tolist()
            ax.samples = None
            j += 1

    if metadata is not None:
        new_meta = carry_metadata(metadata, new_meta, data, derived)
    return Volume(raw=data, metadata=new_meta)
