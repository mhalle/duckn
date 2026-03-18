"""duckn: axis-rich array metadata convention for Zarr V3."""

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
    NiftiCal,
    NiftiDimInfo,
    NiftiExtension,
    NiftiIntent,
    NiftiLegacy,
    NiftiLegacyTags,
    NiftiSliceTiming,
    NiftiTags,
    DucknMetadata,
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
from .zarr_io import get_zarr_attrs, open_store, read_duckn, read_duckn_metadata

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
    "NiftiCal",
    "NiftiDimInfo",
    "NiftiExtension",
    "NiftiIntent",
    "NiftiLegacy",
    "NiftiLegacyTags",
    "NiftiSliceTiming",
    "NiftiTags",
    "DucknMetadata",
    "Segment",
    "SegmentationExtension",
    "SourceRepresentation",
    "SpaceName",
    "TerminologyEntry",
    "UnitObject",
    "UnitSystemEntry",
    "ValueTransform",
    "build_duckn_metadata",
    "dicom_to_zarr",
    "geometry_from_headers",
    "get_zarr_attrs",
    "nifti_to_zarr",
    "nrrd_to_zarr",
    "nrrd_to_zarr_zerocopy",
    "open_store",
    "read_duckn",
    "read_duckn_metadata",
    "UNCOMPRESSED_TRANSFER_SYNTAXES",
    "validate_against_shape",
    "zarr_to_nifti",
    "zarr_to_nrrd",
    "zarr_to_nrrd_zerocopy",
]


def __getattr__(name: str) -> object:
    if name == "build_duckn_metadata":
        from .dicom_convert import build_duckn_metadata

        return build_duckn_metadata
    if name == "geometry_from_headers":
        from .dicom_convert import geometry_from_headers

        return geometry_from_headers
    if name == "UNCOMPRESSED_TRANSFER_SYNTAXES":
        from .dicom_convert import UNCOMPRESSED_TRANSFER_SYNTAXES

        return UNCOMPRESSED_TRANSFER_SYNTAXES
    if name == "dicom_to_zarr":
        from .dicom_convert import dicom_to_zarr

        return dicom_to_zarr
    if name == "nifti_to_zarr":
        from .nifti_convert import nifti_to_zarr

        return nifti_to_zarr
    if name == "zarr_to_nifti":
        from .nifti_convert import zarr_to_nifti

        return zarr_to_nifti
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
