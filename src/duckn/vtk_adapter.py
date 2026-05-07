"""VTK adapter for duckn volumes.

Converts between ``Volume`` and ``vtkImageData`` with correct
spatial metadata, axis ordering, and LPS convention handling.

Requires: ``pip install vtk`` or ``pip install duckn[vtk]``
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np

from .adapters import _get_target_flip, to_lps_params
from .models import DucknMetadata
from .volume import Volume

# numpy dtype → VTK type constant
_NUMPY_TO_VTK = {
    "uint8": 3,     # VTK_UNSIGNED_CHAR
    "int8": 15,     # VTK_SIGNED_CHAR
    "uint16": 5,    # VTK_UNSIGNED_SHORT
    "int16": 4,     # VTK_SHORT
    "uint32": 7,    # VTK_UNSIGNED_INT
    "int32": 6,     # VTK_INT
    "float32": 10,  # VTK_FLOAT
    "float64": 11,  # VTK_DOUBLE
}


def to_vtk(vol: Volume, space: str = "world", convention: str = "lps") -> Any:
    """Convert a duckn Volume to a vtkImageData.

    Parameters
    ----------
    vol : input Volume
    space : coordinate space ("world", "axis-aligned",
            "axis-aligned-centered", or any named space)
    convention : "lps" (default, for ITK/VTK pipelines) or "ras"
                 (for Slicer / RAS-native consumers)

    Returns
    -------
    vtkImageData with correct spacing, origin, and direction
    """
    import vtk
    from vtk.util.numpy_support import numpy_to_vtk

    params = to_lps_params(vol, space=space, convention=convention)
    data = params["data"]
    ndim = vol.geometry.ndim

    img = vtk.vtkImageData()

    # VTK dimensions are xyz (fastest-first)
    dims_xyz = list(data.shape[::-1])
    img.SetDimensions(*dims_xyz)
    img.SetSpacing(*params["spacing"])
    img.SetOrigin(*params["origin"])

    # Direction matrix (VTK 9+ supports SetDirectionMatrix)
    if hasattr(img, "SetDirectionMatrix"):
        dm = vtk.vtkMatrix3x3()
        direction = params["direction"]
        for i in range(ndim):
            for j in range(ndim):
                dm.SetElement(i, j, direction[i * ndim + j])
        img.SetDirectionMatrix(dm)

    # Set scalar data
    flat = data.flatten(order="C")
    vtk_arr = numpy_to_vtk(flat, deep=True)
    vtk_arr.SetName("DucknScalars")
    img.GetPointData().SetScalars(vtk_arr)

    return img


def from_vtk(
    img: Any,
    metadata: DucknMetadata | None = None,
    space: str = "world",
    convention: str = "lps",
) -> Volume:
    """Convert a vtkImageData to a duckn Volume.

    Parameters
    ----------
    img : vtkImageData
    metadata : optional DucknMetadata to preserve (spatial fields will be
           updated from the VTK image). If None, creates minimal metadata.
    space : coordinate space the VTK image is in ("world", etc.)

    Returns
    -------
    Volume with data and spatial metadata from the VTK image
    """
    from vtk.util.numpy_support import vtk_to_numpy

    dims = img.GetDimensions()
    ndim = 3 if dims[2] > 1 else 2

    # Get data as numpy array, reshape to zyx
    scalars = img.GetPointData().GetScalars()
    flat = vtk_to_numpy(scalars)
    shape_xyz = tuple(dims[:ndim])
    shape_zyx = shape_xyz[::-1]
    data = flat.reshape(shape_zyx)

    spacing_xyz = np.array(img.GetSpacing()[:ndim])
    origin_xyz = np.array(img.GetOrigin()[:ndim])

    # Direction matrix
    if hasattr(img, "GetDirectionMatrix"):
        dm = img.GetDirectionMatrix()
        direction = np.array([
            [dm.GetElement(i, j) for j in range(ndim)]
            for i in range(ndim)
        ])
    else:
        direction = np.eye(ndim)

    # Reverse xyz → zyx
    spacing_zyx = spacing_xyz[::-1]
    direction_zyx = direction[:, ::-1]

    # Convert from external convention back to duckn space
    if metadata is not None:
        new_meta = deepcopy(metadata)
        flip = _get_target_flip(metadata, convention=convention)
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

    origin = origin_xyz * flip
    new_meta.space_origin = origin.tolist()

    for i in range(ndim):
        direction_zyx[i, :] *= flip[i]

    for j, ax in enumerate(new_meta.axes):
        if ax.space_direction is not None or (metadata is None):
            col = direction_zyx[:, j]
            ax.space_direction = (col * spacing_zyx[j]).tolist()
            ax.samples = None

    return Volume(raw=data, metadata=new_meta)
