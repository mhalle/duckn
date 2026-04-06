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
    meta: DucknMetadata | None = None,
    space: str = "world",
    convention: str = "lps",
) -> Volume:
    """Convert a SimpleITK Image to a duckn Volume.

    Parameters
    ----------
    img : sitk.Image
    meta : optional DucknMetadata to preserve (spatial fields will be
           updated from the sitk image). If None, creates minimal metadata.
    space : coordinate space the sitk image is in ("world", etc.)

    Returns
    -------
    Volume with data and spatial metadata from the sitk image
    """
    import SimpleITK as sitk

    data = sitk.GetArrayFromImage(img)

    spacing_xyz = np.array(img.GetSpacing())
    origin_xyz = np.array(img.GetOrigin())
    ndim = img.GetDimension()
    direction_flat = np.array(img.GetDirection()).reshape(ndim, ndim)

    # Reverse xyz → zyx for duckn C-order
    spacing_zyx = spacing_xyz[::-1]
    direction_zyx = direction_flat[:, ::-1]

    # Convert from external convention back to duckn space
    if meta is not None:
        new_meta = deepcopy(meta)
        flip = _get_target_flip(meta, convention=convention)
    else:
        from .models import AxisKind, AxisMetadata, Centering, SpaceName
        flip = np.array([1, 1, 1], dtype=float)
        default_space = (
            SpaceName.RIGHT_ANTERIOR_SUPERIOR
            if convention == "ras"
            else SpaceName.LEFT_POSTERIOR_SUPERIOR
        )
        new_meta = DucknMetadata(
            space=default_space,
            space_origin=[0.0] * ndim,
            axes=[
                AxisMetadata(
                    kind=AxisKind.SPACE,
                    centering=Centering.CELL,
                    space_direction=[0.0] * ndim,
                    unit="mm",
                )
                for _ in range(ndim)
            ],
        )

    # Undo LPS flip on origin
    origin = origin_xyz * flip
    new_meta.space_origin = origin.tolist()

    # Undo LPS flip on direction and set space_direction
    for i in range(ndim):
        direction_zyx[i, :] *= flip[i]

    for j, ax in enumerate(new_meta.axes):
        if ax.space_direction is not None or (meta is None):
            # axis j in duckn C-order = axis (ndim-1-j) in xyz
            col = direction_zyx[:, j]
            ax.space_direction = (col * spacing_zyx[j]).tolist()
            ax.samples = None

    return Volume(data=data, meta=new_meta)
