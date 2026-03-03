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
    NiftiCal,
    NiftiDimInfo,
    NiftiExtension,
    NiftiIntent,
    NiftiLegacy,
    NiftiLegacyTags,
    NiftiSliceTiming,
    NiftiTags,
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
from .zarr_io import get_zarr_attrs, open_store, read_nrrdz, read_nrrdz_metadata

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
    "nifti_to_zarr",
    "nrrd_to_zarr",
    "nrrd_to_zarr_zerocopy",
    "open_store",
    "read_nrrdz",
    "read_nrrdz_metadata",
    "validate_against_shape",
    "zarr_to_nifti",
    "zarr_to_nrrd",
    "zarr_to_nrrd_zerocopy",
]


def __getattr__(name: str) -> object:
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
