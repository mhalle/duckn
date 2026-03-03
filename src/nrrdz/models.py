"""Pydantic v2 models for the nrrdz metadata convention."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Any, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

_SPACE_ABBREVS: dict[str, str] = {
    "RAS": "right-anterior-superior",
    "LAS": "left-anterior-superior",
    "LPS": "left-posterior-superior",
    "RAST": "right-anterior-superior-time",
    "LAST": "left-anterior-superior-time",
    "LPST": "left-posterior-superior-time",
}

_SPACE_DIMENSIONS: dict[str, int] = {
    "right-anterior-superior": 3,
    "left-anterior-superior": 3,
    "left-posterior-superior": 3,
    "right-anterior-superior-time": 4,
    "left-anterior-superior-time": 4,
    "left-posterior-superior-time": 4,
    "scanner-xyz": 3,
    "scanner-xyz-time": 4,
    "3D-right-handed": 3,
    "3D-left-handed": 3,
    "3D-right-handed-time": 4,
    "3D-left-handed-time": 4,
}


def _normalize_space(v: Any) -> Any:
    if isinstance(v, str):
        return _SPACE_ABBREVS.get(v, v)
    return v


class SpaceName(StrEnum):
    RIGHT_ANTERIOR_SUPERIOR = "right-anterior-superior"
    LEFT_ANTERIOR_SUPERIOR = "left-anterior-superior"
    LEFT_POSTERIOR_SUPERIOR = "left-posterior-superior"
    RIGHT_ANTERIOR_SUPERIOR_TIME = "right-anterior-superior-time"
    LEFT_ANTERIOR_SUPERIOR_TIME = "left-anterior-superior-time"
    LEFT_POSTERIOR_SUPERIOR_TIME = "left-posterior-superior-time"
    SCANNER_XYZ = "scanner-xyz"
    SCANNER_XYZ_TIME = "scanner-xyz-time"
    THREE_D_RIGHT_HANDED = "3D-right-handed"
    THREE_D_LEFT_HANDED = "3D-left-handed"
    THREE_D_RIGHT_HANDED_TIME = "3D-right-handed-time"
    THREE_D_LEFT_HANDED_TIME = "3D-left-handed-time"


class AxisKind(StrEnum):
    # Domain kinds
    DOMAIN = "domain"
    SPACE = "space"
    TIME = "time"
    # Range kinds
    LIST = "list"
    POINT = "point"
    VECTOR = "vector"
    COVARIANT_VECTOR = "covariant-vector"
    NORMAL = "normal"
    STUB = "stub"
    SCALAR = "scalar"
    COMPLEX = "complex"
    TWO_VECTOR = "2-vector"
    THREE_COLOR = "3-color"
    RGB_COLOR = "RGB-color"
    HSV_COLOR = "HSV-color"
    XYZ_COLOR = "XYZ-color"
    FOUR_COLOR = "4-color"
    RGBA_COLOR = "RGBA-color"
    THREE_VECTOR = "3-vector"
    THREE_GRADIENT = "3-gradient"
    THREE_NORMAL = "3-normal"
    FOUR_VECTOR = "4-vector"
    QUATERNION = "quaternion"
    TWO_D_SYMMETRIC_MATRIX = "2D-symmetric-matrix"
    TWO_D_MASKED_SYMMETRIC_MATRIX = "2D-masked-symmetric-matrix"
    TWO_D_MATRIX = "2D-matrix"
    TWO_D_MASKED_MATRIX = "2D-masked-matrix"
    THREE_D_SYMMETRIC_MATRIX = "3D-symmetric-matrix"
    THREE_D_MASKED_SYMMETRIC_MATRIX = "3D-masked-symmetric-matrix"
    THREE_D_MATRIX = "3D-matrix"
    THREE_D_MASKED_MATRIX = "3D-masked-matrix"


# Mapping from kind -> required axis size (None means no constraint)
KIND_REQUIRED_SIZES: dict[str, int | None] = {
    "stub": 1,
    "scalar": 1,
    "complex": 2,
    "2-vector": 2,
    "3-color": 3,
    "RGB-color": 3,
    "HSV-color": 3,
    "XYZ-color": 3,
    "4-color": 4,
    "RGBA-color": 4,
    "3-vector": 3,
    "3-gradient": 3,
    "3-normal": 3,
    "4-vector": 4,
    "quaternion": 4,
    "2D-symmetric-matrix": 3,
    "2D-masked-symmetric-matrix": 4,
    "2D-matrix": 4,
    "2D-masked-matrix": 5,
    "3D-symmetric-matrix": 6,
    "3D-masked-symmetric-matrix": 7,
    "3D-matrix": 9,
    "3D-masked-matrix": 10,
}


class Centering(StrEnum):
    CELL = "cell"
    NODE = "node"


# ---------------------------------------------------------------------------
# Unit models
# ---------------------------------------------------------------------------


class UnitObject(BaseModel):
    """Structured unit with formal system binding."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    scheme: str
    code: str
    url: str | None = None


UnitValue = Union[str, UnitObject]


class UnitSystemEntry(BaseModel):
    """Entry in the unit_systems registry."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    version: str | None = None
    url: str | None = None


# ---------------------------------------------------------------------------
# Value transforms
# ---------------------------------------------------------------------------


class LinearParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slope: float
    intercept: float


class ValueTransform(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    parameters: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_linear(self) -> ValueTransform:
        if self.name == "linear":
            if self.parameters is None:
                raise ValueError("linear transform requires parameters with slope and intercept")
            LinearParameters(**self.parameters)
        return self


# ---------------------------------------------------------------------------
# Per-axis metadata
# ---------------------------------------------------------------------------


class AxisMetadata(BaseModel):
    """Metadata for a single array axis."""

    model_config = ConfigDict(extra="forbid")

    kind: AxisKind | None = None
    centering: Centering | None = None
    space_direction: list[float] | None = None
    thickness: float | None = None
    unit: UnitValue | None = None
    extensions: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Top-level nrrd metadata object
# ---------------------------------------------------------------------------


class NrrdMetadata(BaseModel):
    """The `nrrd` attributes object stored in a Zarr v3 array."""

    model_config = ConfigDict(extra="forbid")

    version: str | None = None
    space: Annotated[SpaceName | None, BeforeValidator(_normalize_space)] = None
    space_dimension: int | None = None
    space_origin: list[float] | None = None
    measurement_frame: list[list[float]] | None = None
    sample_units: UnitValue | None = None
    value_transforms: list[ValueTransform] | None = None
    intent: str | None = None
    axes: list[AxisMetadata] | None = None
    extensions: dict[str, Any] | None = None
    unit_systems: dict[str, UnitSystemEntry] | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> NrrdMetadata:
        # space and space_dimension mutually exclusive
        if self.space is not None and self.space_dimension is not None:
            raise ValueError("space and space_dimension are mutually exclusive")

        sd = self._get_space_dim()

        # space_direction vector lengths must match space dimension
        if sd is not None and self.axes is not None:
            for i, ax in enumerate(self.axes):
                if ax.space_direction is not None and len(ax.space_direction) != sd:
                    raise ValueError(
                        f"axes[{i}].space_direction has {len(ax.space_direction)} "
                        f"components, expected {sd}"
                    )

        # space_origin length must match space dimension
        if sd is not None and self.space_origin is not None:
            if len(self.space_origin) != sd:
                raise ValueError(
                    f"space_origin has {len(self.space_origin)} components, expected {sd}"
                )

        # measurement_frame must be square with side = space dimension
        if self.measurement_frame is not None:
            ncols = len(self.measurement_frame)
            if sd is not None and ncols != sd:
                raise ValueError(
                    f"measurement_frame has {ncols} columns, expected {sd}"
                )
            for i, col in enumerate(self.measurement_frame):
                if len(col) != ncols:
                    raise ValueError(
                        f"measurement_frame column {i} has {len(col)} rows, "
                        f"expected {ncols}"
                    )

        return self

    def _get_space_dim(self) -> int | None:
        """Return the space dimension from either space or space_dimension."""
        if self.space is not None:
            return _SPACE_DIMENSIONS[self.space.value]
        return self.space_dimension


# ---------------------------------------------------------------------------
# Standalone validation against array shape
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Segmentation extension models
# ---------------------------------------------------------------------------


class SourceRepresentation(StrEnum):
    BINARY_LABELMAP = "binary-labelmap"
    FRACTIONAL_LABELMAP = "fractional-labelmap"


class TerminologyEntry(BaseModel):
    """Entry in the terminologies registry (coding system provenance)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    version: str | None = None
    url: str | None = None


class ConversionParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    description: str | None = None


class CodedEntry(BaseModel):
    """Reusable coded-concept shape (scheme/code/meaning)."""

    model_config = ConfigDict(extra="forbid")

    scheme: str
    code: str
    meaning: str
    display: dict[str, str] | None = None
    url: str | None = None


class Designation(CodedEntry):
    """A coded entry with an optional modifier."""

    modifier: CodedEntry | None = None


class DicomClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: CodedEntry | None = None
    type: CodedEntry | None = None
    type_modifier: CodedEntry | None = None
    anatomic_region: CodedEntry | None = None
    anatomic_region_modifier: CodedEntry | None = None


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    display: dict[str, str] | None = None
    name_auto_generated: bool | None = None
    color: list[float] | None = None
    color_auto_generated: bool | None = None
    label_value: int | list[int]
    layer: int | None = None
    extent: list[int] | None = None
    designations: list[Designation] | None = None
    dicom: DicomClassification | None = None
    tags: dict[str, str] | None = None


class SegmentationExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    source_representation: SourceRepresentation | None = None
    contained_representations: list[str] | None = None
    conversion_parameters: dict[str, ConversionParameter] | None = None
    reference_extent_offset: list[int] | None = None
    terminologies: dict[str, TerminologyEntry] | None = None
    segments: list[Segment]
    legacy: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# DWI extension models
# ---------------------------------------------------------------------------


class DwmriAcquisition(BaseModel):
    """MR acquisition parameters for DWI preprocessing (§4.1)."""

    model_config = ConfigDict(extra="forbid")

    phase_encoding_direction: str | None = None
    total_readout_time: float | None = None
    effective_echo_spacing: float | None = None
    echo_time: float | None = None
    repetition_time: float | None = None
    multiband_acceleration_factor: int | None = None
    parallel_reduction_factor_in_plane: int | None = None
    slice_timing: list[float] | None = None


class DwmriAxisExtension(BaseModel):
    """Per-axis DWI fields on the list axis (§4.2)."""

    model_config = ConfigDict(extra="forbid")

    gradients: list[list[float]] | None = None
    b_matrices: list[list[float]] | None = None
    b_values: list[float] | None = None
    nex: dict[str, int] | None = None


class DwmriExtension(BaseModel):
    """Top-level DWI extension (§4.1)."""

    model_config = ConfigDict(extra="forbid")

    version: str
    b_value: float
    b_value_units: str | None = None
    gradient_frame: str | None = None
    acquisition: DwmriAcquisition | None = None
    legacy: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# DICOM provenance extension models
# ---------------------------------------------------------------------------


class DicomExtension(BaseModel):
    """DICOM provenance extension (dicom-spec.md §3)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str
    anonymized: bool | None = None
    source_transfer_syntax: str | None = None
    standard_version: str | None = None
    schema_url: str | None = Field(None, alias="schema")
    tags: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# NIfTI provenance extension models (nifti-spec.md)
# ---------------------------------------------------------------------------


class NiftiDimInfo(BaseModel):
    """MRI encoding dimension identifiers (§4.2, 1-based)."""

    model_config = ConfigDict(extra="forbid")

    freq_dim: int | None = None
    phase_dim: int | None = None
    slice_dim: int | None = None


class NiftiIntent(BaseModel):
    """NIfTI intent code and parameters (§4.2)."""

    model_config = ConfigDict(extra="forbid")

    code: int
    name: str | None = None
    p1: float | None = None
    p2: float | None = None
    p3: float | None = None


class NiftiSliceTiming(BaseModel):
    """Slice acquisition timing metadata (§4.2)."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    start: int | None = None
    end: int | None = None
    duration: float | None = None


class NiftiCal(BaseModel):
    """Display calibration range (§4.2)."""

    model_config = ConfigDict(extra="forbid")

    min: float | None = None
    max: float | None = None


class NiftiTags(BaseModel):
    """NIfTI header fields not captured by convention fields (§4.2)."""

    model_config = ConfigDict(extra="forbid")

    sform_code: int | None = None
    qform_code: int | None = None
    dim_info: NiftiDimInfo | None = None
    intent: NiftiIntent | None = None
    slice_timing: NiftiSliceTiming | None = None
    toffset: float | None = None
    cal: NiftiCal | None = None
    descrip: str | None = None
    aux_file: str | None = None


class NiftiLegacyTags(BaseModel):
    """Original NIfTI affine matrices stored for provenance."""

    model_config = ConfigDict(extra="forbid")

    sform: list[list[float]] | None = None
    qform: list[list[float]] | None = None


class NiftiLegacy(BaseModel):
    """Legacy provenance data from the source NIfTI file."""

    model_config = ConfigDict(extra="forbid")

    tags: NiftiLegacyTags | None = None


class NiftiExtension(BaseModel):
    """NIfTI provenance extension (nifti-spec.md §4)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str
    url: str | None = None
    nifti_version: int | None = None
    tags: NiftiTags | None = None
    legacy: NiftiLegacy | None = None


# ---------------------------------------------------------------------------
# Standalone validation against array shape
# ---------------------------------------------------------------------------


def validate_against_shape(meta: NrrdMetadata, shape: tuple[int, ...]) -> None:
    """Validate that metadata is consistent with the given array shape.

    Raises ValueError on any inconsistency.
    """
    if meta.axes is not None:
        if len(meta.axes) != len(shape):
            raise ValueError(
                f"axes has {len(meta.axes)} entries but shape has {len(shape)} dimensions"
            )
        for i, ax in enumerate(meta.axes):
            if ax.kind is not None:
                required = KIND_REQUIRED_SIZES.get(ax.kind.value)
                if required is not None and shape[i] != required:
                    raise ValueError(
                        f"axes[{i}] kind {ax.kind.value!r} requires size {required}, "
                        f"but shape[{i}] is {shape[i]}"
                    )
