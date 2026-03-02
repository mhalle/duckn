"""nrrdz: NRRD-Zarr axis-rich array metadata convention for Zarr V3."""

from .convert import nrrd_to_zarr, nrrd_to_zarr_zerocopy, zarr_to_nrrd, zarr_to_nrrd_zerocopy
from .models import (
    AxisKind,
    AxisMetadata,
    Centering,
    CodedEntry,
    ConversionParameter,
    Designation,
    DicomClassification,
    DicomExtension,
    DwmriAcquisition,
    DwmriAxisExtension,
    DwmriExtension,
    NrrdMetadata,
    Segment,
    SegmentationExtension,
    SourceRepresentation,
    SpaceName,
    TerminologyEntry,
    UnitObject,
    UnitSystemEntry,
    ValueTransform,
    validate_against_shape,
)
from .zarr_io import get_zarr_attrs, read_nrrdz, read_nrrdz_metadata

__all__ = [  # noqa: RUF022
    "AxisKind",
    "AxisMetadata",
    "Centering",
    "CodedEntry",
    "ConversionParameter",
    "Designation",
    "DicomClassification",
    "DicomExtension",
    "DwmriAcquisition",
    "DwmriAxisExtension",
    "DwmriExtension",
    "NrrdMetadata",
    "Segment",
    "SegmentationExtension",
    "SourceRepresentation",
    "SpaceName",
    "TerminologyEntry",
    "UnitObject",
    "UnitSystemEntry",
    "ValueTransform",
    "dicom_to_zarr",
    "get_zarr_attrs",
    "nrrd_to_zarr",
    "nrrd_to_zarr_zerocopy",
    "read_nrrdz",
    "read_nrrdz_metadata",
    "validate_against_shape",
    "zarr_to_nrrd",
    "zarr_to_nrrd_zerocopy",
]


def __getattr__(name: str) -> object:
    if name == "dicom_to_zarr":
        from .dicom_convert import dicom_to_zarr

        return dicom_to_zarr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
