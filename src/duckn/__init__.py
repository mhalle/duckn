"""duckn: a Zarr-based imaging file format with lovely nested semantics.

duckn is a layered metadata convention for Zarr V3 arrays that combines
three concerns that imaging communities have struggled to unify for
decades:

- **Array data handling** from Zarr: chunking, compression, typing, and
  cloud-native storage are handled by the container format itself.
- **Image-to-world coordinate representation** from NRRD: per-axis
  semantics, spatial orientation, centering, and measurement frames
  provide a precise mapping between stored arrays and physical space.
- **Domain-specific semantics** from standards like DICOM, NIfTI, FITS,
  and others: provenance, acquisition parameters, and format-specific
  metadata are captured in typed extensions that preserve enough
  information to accurately round-trip well-formed files through duckn.

The user chooses what semantic level to use. A bare duckn store is a
valid Zarr array any reader can open. Adding spatial metadata makes it
an oriented image. Adding domain extensions makes it a lossless
representation of the source format's semantics.

duckn is designed to be extensible to accommodate new imaging domains
and evolutions of existing ones. Extensions live inside the ``"duckn"``
attribute and depend on convention semantics to be interpretable;
independent metadata can coexist as sibling Zarr attributes.

Reading and writing
-------------------
read_duckn(path)
    Read a duckn Zarr store, returning ``(data, DucknMetadata)``.
read_duckn_metadata(path)
    Read only the metadata (no array data loaded).
get_zarr_attrs(path)
    Return the raw Zarr attributes dict.
open_store(path, mode, overwrite)
    Context manager yielding a Zarr store (LocalStore or ZipStore).

Converters
----------
nrrd_to_zarr(nrrd_path, zarr_path, ...)
    Convert an NRRD file to a duckn Zarr store.
zarr_to_nrrd(zarr_path, nrrd_path, ...)
    Convert a duckn Zarr store back to NRRD.
nrrd_to_zarr_zerocopy(nrrd_path, zarr_path, ...)
    Zero-copy NRRD to Zarr (raw/gzip only, no recompression).
zarr_to_nrrd_zerocopy(zarr_path, nrrd_path, ...)
    Zero-copy Zarr to NRRD.
nifti_to_zarr(input_path, output_path, ...)
    Convert a NIfTI file to a duckn Zarr store. Requires ``duckn[nifti]``.
zarr_to_nifti(input_path, output_path, ...)
    Convert a duckn Zarr store to NIfTI. Requires ``duckn[nifti]``.
dicom_to_zarr(input_path, output_path, ...)
    Convert DICOM files to a duckn Zarr store. Requires ``duckn[dicom]``.

DICOM helpers
-------------
geometry_from_headers(datasets)
    Compute spatial geometry (origin, directions, spacing) from DICOM headers.
build_duckn_metadata(geometry, datasets, ...)
    Build a ``DucknMetadata`` from DICOM geometry and datasets.
UNCOMPRESSED_TRANSFER_SYNTAXES
    Set of DICOM transfer syntax UIDs that use uncompressed pixel data.

Core models
-----------
DucknMetadata
    Top-level metadata stored under the ``"duckn"`` Zarr attribute key.
AxisMetadata
    Per-axis metadata (kind, centering, space_direction, unit, etc.).
AxisKind
    Axis kind enumeration (space, time, list, scalar, vector, ...).
Centering
    Axis centering (cell or node).
SpaceName
    Named coordinate systems (right-anterior-superior, scanner-xyz, ...).
ValueTransform
    Stored-to-physical value mapping (e.g., linear slope/intercept).
UnitObject
    Structured unit with formal system binding.
validate_against_shape(meta, shape)
    Validate metadata consistency against an array shape.

Extension models
----------------
NiftiExtension, NiftiTags, NiftiLegacy
    NIfTI provenance metadata.
DicomExtension, DicomClassification
    DICOM provenance metadata.
DwmriExtension, DwmriAxisExtension, DwmriAcquisition
    Diffusion-weighted MRI metadata.
SegmentationExtension, Segment
    Segmentation label map metadata.
"""

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
    SampleMetadata,
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
    "SampleMetadata",
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
