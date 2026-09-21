"""Pydantic v2 models for the duckn metadata convention."""

from __future__ import annotations


from enum import StrEnum
from typing import Annotated, Any, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator


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

# 3D spaces and their time variants
_3D_SPACES = [
    # Medical / patient-based
    "right-anterior-superior",
    "left-anterior-superior",
    "left-posterior-superior",
    # Scanner / instrument
    "scanner-xyz",
    # General 3D (viewer-relative)
    "right-up-back",          # Three.js, OpenGL
    "right-up-forward",       # Babylon.js, DirectX, Unity
    "right-forward-up",       # Blender, CAD
    "right-down-forward",     # Vulkan, screen space
    "forward-right-up",       # Unreal Engine
    "east-north-up",          # Geospatial, surveying
    # Generic (no axis semantics)
    "3D-right-handed",
    "3D-left-handed",
]

_SPACE_DIMENSIONS: dict[str, int] = {}
for _s in _3D_SPACES:
    _SPACE_DIMENSIONS[_s] = 3
    _SPACE_DIMENSIONS[f"{_s}-time"] = 4


def _normalize_space(v: Any) -> Any:
    if isinstance(v, str):
        return _SPACE_ABBREVS.get(v, v)
    return v


class SpaceName(StrEnum):
    # Medical / patient-based
    RIGHT_ANTERIOR_SUPERIOR = "right-anterior-superior"
    LEFT_ANTERIOR_SUPERIOR = "left-anterior-superior"
    LEFT_POSTERIOR_SUPERIOR = "left-posterior-superior"
    RIGHT_ANTERIOR_SUPERIOR_TIME = "right-anterior-superior-time"
    LEFT_ANTERIOR_SUPERIOR_TIME = "left-anterior-superior-time"
    LEFT_POSTERIOR_SUPERIOR_TIME = "left-posterior-superior-time"
    # Scanner / instrument
    SCANNER_XYZ = "scanner-xyz"
    SCANNER_XYZ_TIME = "scanner-xyz-time"
    # General 3D (viewer-relative)
    RIGHT_UP_BACK = "right-up-back"                      # Three.js, OpenGL
    RIGHT_UP_FORWARD = "right-up-forward"                # Babylon.js, DirectX, Unity
    RIGHT_FORWARD_UP = "right-forward-up"                # Blender, CAD
    RIGHT_DOWN_FORWARD = "right-down-forward"            # Vulkan, screen space
    FORWARD_RIGHT_UP = "forward-right-up"                # Unreal Engine
    EAST_NORTH_UP = "east-north-up"                      # Geospatial, surveying
    RIGHT_UP_BACK_TIME = "right-up-back-time"
    RIGHT_UP_FORWARD_TIME = "right-up-forward-time"
    RIGHT_FORWARD_UP_TIME = "right-forward-up-time"
    RIGHT_DOWN_FORWARD_TIME = "right-down-forward-time"
    FORWARD_RIGHT_UP_TIME = "forward-right-up-time"
    EAST_NORTH_UP_TIME = "east-north-up-time"
    # Generic (no axis semantics)
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


class LutParameters(BaseModel):
    """Explicit lookup table mapping stored values to real values.

    ``values[i]`` is the real value for stored value ``first_value + i``.
    Stored values outside the table clamp to its first or last entry, which
    is the behavior DICOM defines for a Modality LUT.
    """

    model_config = ConfigDict(extra="forbid")

    values: list[float]
    first_value: int = 0

    @model_validator(mode="after")
    def _non_empty(self) -> LutParameters:
        if not self.values:
            raise ValueError("lut transform requires a non-empty 'values' table")
        return self


class ValueTransform(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    parameters: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_parameters(self) -> ValueTransform:
        if self.name == "linear":
            if self.parameters is None:
                raise ValueError("linear transform requires parameters with slope and intercept")
            LinearParameters(**self.parameters)
        elif self.name == "lut":
            if self.parameters is None:
                raise ValueError("lut transform requires parameters with a 'values' table")
            LutParameters(**self.parameters)
        return self


# ---------------------------------------------------------------------------
# Per-axis metadata
# ---------------------------------------------------------------------------


class SampleMetadata(BaseModel):
    """Per-sample metadata for a single position along an axis.

    All fields are optional. An empty object means "use axis defaults."
    When ``samples`` is present on an axis, there must be exactly one
    entry per sample along that axis.
    """

    model_config = ConfigDict(extra="forbid")

    position: float | None = None
    origin: list[float] | None = None
    thickness: float | None = None
    directions: list[list[float]] | None = None
    metadata: dict[str, Any] | None = None


class AxisMetadata(BaseModel):
    """Metadata for a single array axis."""

    model_config = ConfigDict(extra="forbid")

    kind: AxisKind | None = None
    centering: Centering | None = None
    space_direction: list[float] | None = None
    thickness: float | None = None
    unit: UnitValue | None = None
    samples: list[SampleMetadata] | None = None
    extensions: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Space transforms
# ---------------------------------------------------------------------------


class SpaceReference(BaseModel):
    """Reference to a coordinate space (§4 of transform spec).

    Either a built-in space (via ``space``) or a named space (via ``name``).
    """

    model_config = ConfigDict(extra="forbid")

    space: str | None = None  # built-in: "world", "axis-aligned", etc.
    name: str | None = None   # named: "nifti:mni152", "surgical_plan", etc.
    axes: list[AxisMetadata] | None = None  # optional axis descriptions

    @model_validator(mode="after")
    def _check_exactly_one(self) -> SpaceReference:
        if self.space is not None and self.name is not None:
            raise ValueError("SpaceReference must have either 'space' or 'name', not both")
        if self.space is None and self.name is None:
            raise ValueError("SpaceReference must have either 'space' or 'name'")
        if self.space is not None and self.axes is not None:
            raise ValueError("'axes' is only valid with named space references")
        return self


class TransformObject(BaseModel):
    """A mathematical transform mapping (§6 of transform spec).

    Contains exactly one key: ``identity`` or ``affine``.
    """

    model_config = ConfigDict(extra="forbid")

    identity: bool | None = None
    affine: list[list[float]] | None = None

    @model_validator(mode="after")
    def _check_exactly_one(self) -> TransformObject:
        has_identity = self.identity is not None
        has_affine = self.affine is not None
        if has_identity == has_affine:
            raise ValueError("TransformObject must have exactly one of 'identity' or 'affine'")
        return self


class SpaceTransformEntry(BaseModel):
    """A single entry in the ``space_transforms`` array (§5 of transform spec).

    Describes a transform between two coordinate spaces.
    """

    model_config = ConfigDict(extra="allow")

    to: SpaceReference
    from_: SpaceReference | None = Field(None, alias="from")
    forward: TransformObject | None = None
    inverse: TransformObject | None = None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @model_validator(mode="after")
    def _check_has_transform(self) -> SpaceTransformEntry:
        if self.forward is None and self.inverse is None:
            raise ValueError("At least one of 'forward' or 'inverse' must be present")
        return self


# ---------------------------------------------------------------------------
# Top-level duckn metadata object
# ---------------------------------------------------------------------------


class DucknMetadata(BaseModel):
    """The `duckn` attributes object stored in a Zarr v3 array."""

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
    space_transforms: list[SpaceTransformEntry] | None = None
    extensions: dict[str, Any] | None = None
    unit_systems: dict[str, UnitSystemEntry] | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> DucknMetadata:
        # A lut indexes integer stored values, so it can only be the first
        # transform in the chain — anything before it would feed it
        # already-rescaled (typically floating point) values.
        for i, vt in enumerate(self.value_transforms or []):
            if vt.name == "lut" and i != 0:
                raise ValueError(
                    f"value_transforms[{i}] is a 'lut': a lut indexes stored "
                    "values and must be the first transform in the chain"
                )

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
            nrows = len(self.measurement_frame)
            if sd is not None and nrows != sd:
                raise ValueError(
                    f"measurement_frame has {nrows} rows, expected {sd}"
                )
            for i, row in enumerate(self.measurement_frame):
                if len(row) != nrows:
                    raise ValueError(
                        f"measurement_frame row {i} has {len(row)} columns, "
                        f"expected {nrows}"
                    )

        return self

    def _get_space_dim(self) -> int | None:
        """Return the space dimension from either space or space_dimension."""
        if self.space is not None:
            return _SPACE_DIMENSIONS[self.space.value]
        return self.space_dimension

    # ------------------------------------------------------------------
    # Convenience methods (in-memory metadata edits)
    # ------------------------------------------------------------------

    def add_transform(
        self,
        to_space: str,
        *,
        affine: "Any" = None,
        inverse: "Any" = None,
        identity: bool = False,
        metadata: dict | None = None,
    ) -> None:
        """Add a space transform from world to a named space.

        Exactly one of ``affine``, ``inverse``, or ``identity`` must be
        specified. ``metadata`` is an optional provenance dict.
        """
        import numpy as np

        n_specified = sum(x is not None for x in (affine, inverse)) + int(identity)
        if n_specified != 1:
            raise ValueError(
                "Exactly one of affine, inverse, or identity must be specified"
            )

        forward = None
        inverse_obj = None
        if identity:
            forward = TransformObject(identity=True)
        elif affine is not None:
            forward = TransformObject(affine=np.asarray(affine, dtype=float).tolist())
        elif inverse is not None:
            inverse_obj = TransformObject(
                affine=np.asarray(inverse, dtype=float).tolist()
            )

        entry = SpaceTransformEntry(
            to=SpaceReference(name=to_space),
            forward=forward,
            inverse=inverse_obj,
            metadata=metadata,
        )

        if self.space_transforms is None:
            self.space_transforms = []
        self.space_transforms.append(entry)

    def get_extension(self, name: str) -> Any | None:
        """Return the top-level extension by name, or None if not present."""
        if self.extensions is None:
            return None
        return self.extensions.get(name)

    def set_extension(self, name: str, value: Any) -> None:
        """Set a top-level extension. Overwrites if already present."""
        if self.extensions is None:
            self.extensions = {}
        self.extensions[name] = value


# ---------------------------------------------------------------------------
# Standalone validation against array shape
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Segmentation extension models
# ---------------------------------------------------------------------------


# The seg extension's models, rules and lookups live in ``seg_model`` (0.8);
# reading and migrating older files is ``seg_read``. What stays here is what
# they share with the rest of the convention - coded entries - and the
# pre-0.6 shape migration that ``seg_read`` runs as its first step. The seg
# names are still importable from this module (see ``__getattr__`` below).


class ConversionParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    description: str | None = None


class CodedEntry(BaseModel):
    """A coded concept reference: scheme + code identify, meaning renders.

    The scheme/code pair is authoritative; ``meaning`` is a convenience
    rendering of the concept under the registered terminology version and
    must not be treated as identity.
    """

    model_config = ConfigDict(extra="forbid")

    scheme: str
    code: str
    meaning: str | None = None

    @field_validator("meaning", mode="before")
    @classmethod
    def _empty_meaning_to_none(cls, v: Any) -> Any:
        if v == "":
            return None
        return v


class Designation(CodedEntry):
    """A coded reference to a concept in some terminology system.

    A segment carries a list of these — one per terminology in which the
    structure is identified — making the multiplicity of cross-ontology
    identity explicit. The first entry is the preferred identification.
    """

    modifier: "Designation | None" = None

    @field_validator("modifier")
    @classmethod
    def _modifier_depth_one(cls, v: "Designation | None") -> "Designation | None":
        if v is not None and v.modifier is not None:
            raise ValueError("designation modifiers nest one level only")
        return v


# Resolve the forward reference for the recursive `modifier` field.
Designation.model_rebuild()


# ---------------------------------------------------------------------------
# Backward compatibility: seg extension 0.5 and earlier
# ---------------------------------------------------------------------------
#
# 0.6 made `designations` canonical, promoted `dicom` to a first-class segment
# field, and moved 3D Slicer application state under `metadata.slicer`. Stores
# written before that carry the old shapes. `seg_read.migrate_seg_extension`
# runs this as the first step of reading a file older than 0.6.

_SLICER_EXT_FIELDS = (
    "contained_representations",
    "conversion_parameters",
    "reference_extent_offset",
)
_SLICER_SEGMENT_FIELDS = ("name_auto_generated", "color_auto_generated", "tags")

# Fields dropped from coded entries in 0.6: concept URLs now derive from the
# registry's url_template, and multilingual names live on the segment.
_DROPPED_CODED_ENTRY_FIELDS = ("url", "display")


def _move_to_slicer_metadata(obj: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Move legacy top-level fields into ``metadata.slicer``."""
    present = {f: obj[f] for f in fields if obj.get(f) is not None}
    if not present:
        for f in fields:
            obj.pop(f, None)
        return obj
    metadata = dict(obj.get("metadata") or {})
    slicer = dict(metadata.get("slicer") or {})
    for field, value in present.items():
        slicer.setdefault(field, value)
    metadata["slicer"] = slicer
    obj["metadata"] = metadata
    for f in fields:
        obj.pop(f, None)
    return obj


def _migrate_coded_entry_pre_0_6(entry: Any, *, default_scheme: str = "SCT") -> Any:
    """Normalize a pre-0.6 coded entry to ``{scheme, code, meaning?}``.

    The 0.5 spec allowed an ``{id, name}`` shape for DICOM classification
    entries with the coding scheme left implicit.
    """
    if not isinstance(entry, dict):
        return entry
    entry = dict(entry)
    if "code" not in entry and "id" in entry:
        entry["code"] = entry.pop("id")
        entry.setdefault("scheme", default_scheme)
    else:
        entry.pop("id", None)
    if "meaning" not in entry and "name" in entry:
        entry["meaning"] = entry.pop("name")
    else:
        entry.pop("name", None)
    for field in _DROPPED_CODED_ENTRY_FIELDS:
        entry.pop(field, None)
    if isinstance(entry.get("modifier"), dict):
        entry["modifier"] = _migrate_coded_entry_pre_0_6(
            entry["modifier"], default_scheme=default_scheme
        )
    return entry


def _migrate_segment_pre_0_6(seg: Any) -> Any:
    if not isinstance(seg, dict):
        return seg
    seg = dict(seg)

    # `identifiers` (0.5) → `designations`, without displacing existing ones.
    identifiers = seg.pop("identifiers", None)
    if isinstance(identifiers, dict):
        designations = list(seg.get("designations") or [])
        known = {
            (d.get("scheme"), d.get("code"))
            for d in designations
            if isinstance(d, dict)
        }
        for scheme, ident in identifiers.items():
            if not isinstance(ident, dict):
                continue
            code = ident.get("id")
            if code is None or (scheme, code) in known:
                continue
            entry = {"scheme": scheme, "code": code}
            if ident.get("name") is not None:
                entry["meaning"] = ident["name"]
            designations.append(entry)
        if designations:
            seg["designations"] = designations

    if isinstance(seg.get("designations"), list):
        seg["designations"] = [
            _migrate_coded_entry_pre_0_6(d) for d in seg["designations"]
        ]

    # DICOM classification moved out of `metadata` and became first-class.
    metadata = seg.get("metadata")
    if isinstance(metadata, dict) and "dicom" in metadata:
        metadata = dict(metadata)
        legacy_dicom = metadata.pop("dicom")
        seg["metadata"] = metadata or None
        if seg.get("dicom") is None and isinstance(legacy_dicom, dict):
            seg["dicom"] = legacy_dicom

    if isinstance(seg.get("dicom"), dict):
        seg["dicom"] = {
            key: _migrate_coded_entry_pre_0_6(value)
            for key, value in seg["dicom"].items()
        }

    return _move_to_slicer_metadata(seg, _SLICER_SEGMENT_FIELDS)


def _migrate_extension_pre_0_6(ext: dict[str, Any]) -> dict[str, Any]:
    """Migrate a pre-0.6 seg extension dict to the 0.6 shape.

    """
    out = dict(ext)
    if isinstance(out.get("segments"), list):
        out["segments"] = [_migrate_segment_pre_0_6(s) for s in out["segments"]]
    if not any(field in out for field in _SLICER_EXT_FIELDS):
        return out
    return _move_to_slicer_metadata(out, _SLICER_EXT_FIELDS)


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
    # True when the pixel data has ever been lossy compressed. DICOM makes
    # this sticky (PS3.3 C.7.6.1.1.5): it survives decompression and format
    # conversion, because it says the values are no longer the acquired
    # ones. First-class rather than a tag for the same reason
    # source_transfer_syntax is — a reader should not have to search a tag
    # dictionary to learn the data is degraded.
    lossy_compressed: bool | None = None
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


def duckn_attrs(meta: DucknMetadata) -> dict[str, Any]:
    """Serialize metadata into a Zarr ``attributes`` object, re-validating it.

    Pydantic runs validators at construction, so a model mutated afterwards
    (appending to ``value_transforms``, assigning a field) can hold a state
    no reader will accept. Re-validating the dumped form fails the write
    instead of producing a store that cannot be opened.
    """
    dumped = meta.model_dump(exclude_none=True)
    DucknMetadata.model_validate(dumped)
    return {"duckn": dumped}


def validate_against_shape(meta: DucknMetadata, shape: tuple[int, ...]) -> None:
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
            if ax.samples is not None and len(ax.samples) != shape[i]:
                raise ValueError(
                    f"axes[{i}] has {len(ax.samples)} samples but shape[{i}] is {shape[i]}"
                )


# ---------------------------------------------------------------------------
# The seg extension's names, importable from here as they always were
# ---------------------------------------------------------------------------

_SEG_MODEL_NAMES = frozenset({
    "SEG_VERSION", "DicomContent", "Segment", "SegmentationExtension", "TerminologyEntry",
    "background_value", "color_map", "segments_for", "topmost_for",
    "validate_seg_data", "validate_seg_extension",
})


def __getattr__(name: str) -> Any:
    # Lazy, because seg_model imports this module.
    if name == "SEG_EXTENSION_VERSION":
        name = "SEG_VERSION"
    if name in _SEG_MODEL_NAMES:
        from . import seg_model

        return getattr(seg_model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
