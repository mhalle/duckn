"""nrrdz: NRRD-Zarr axis-rich array metadata convention for Zarr V3."""

from .convert import nrrd_to_zarr, nrrd_to_zarr_zerocopy, zarr_to_nrrd, zarr_to_nrrd_zerocopy
from .models import (
    AxisKind,
    AxisMetadata,
    Centering,
    NrrdMetadata,
    SpaceName,
    UnitObject,
    UnitSystemEntry,
    ValueTransform,
    validate_against_shape,
)
from .zarr_io import get_zarr_attrs, read_nrrdz, read_nrrdz_metadata

__all__ = [
    "AxisKind",
    "AxisMetadata",
    "Centering",
    "NrrdMetadata",
    "SpaceName",
    "UnitObject",
    "UnitSystemEntry",
    "ValueTransform",
    "get_zarr_attrs",
    "nrrd_to_zarr",
    "nrrd_to_zarr_zerocopy",
    "read_nrrdz",
    "read_nrrdz_metadata",
    "validate_against_shape",
    "zarr_to_nrrd",
    "zarr_to_nrrd_zerocopy",
]
