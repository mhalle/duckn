"""DICOM to duckn Zarr v3 conversion.

Reads DICOM files (single-frame series directory or enhanced multi-frame)
and writes a duckn Zarr v3 store with the DICOM provenance extension.

Requires pydicom: install with ``pip install duckn[dicom]``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import zarr

from .convert import _auto_chunks, _build_compressors
from .zarr_io import _is_zip_path, open_store
from .models import (
    AxisKind,
    AxisMetadata,
    Centering,
    CodedEntry,
    DicomClassification,
    DicomExtension,
    DucknMetadata,
    SampleMetadata,
    Segment,
    SegmentationExtension,
    SourceRepresentation,
    SpaceName,
    ValueTransform,
)


# ---------------------------------------------------------------------------
# pydicom lazy guard
# ---------------------------------------------------------------------------


def _require_pydicom() -> None:
    """Raise a helpful error if pydicom is not installed."""
    try:
        import pydicom  # noqa: F401
    except ImportError:
        raise ImportError(
            "pydicom is required for DICOM conversion. "
            "Install it with: pip install duckn[dicom]"
        ) from None


# ---------------------------------------------------------------------------
# Internal geometry dataclass
# ---------------------------------------------------------------------------


@dataclass
class DicomGeometry:
    """Computed spatial geometry from DICOM headers."""

    shape: tuple[int, ...]
    dtype: np.dtype
    space: SpaceName
    space_origin: list[float]
    space_directions: list[list[float]]  # C order: [slice, row, col]
    slice_thickness: float | None
    rescale_slope: float | None
    rescale_intercept: float | None
    rescale_type: str | None


# ---------------------------------------------------------------------------
# Tag extraction (dicom-spec.md §4)
# ---------------------------------------------------------------------------

# VRs that are binary — encoded as base64 strings in JSON
_BINARY_VRS = frozenset({"OB", "OW", "OF", "OD", "OL", "OV", "UN"})

# Tags to skip: bulk binary data represented by the Zarr array itself,
# and geometry fields already captured by convention fields
_SKIP_KEYWORDS = frozenset({
    "PixelData",
    "OverlayData",
    "Rows",
    "Columns",
    "NumberOfFrames",
    "BitsAllocated",
    "BitsStored",
    "HighBit",
    "PixelRepresentation",
    "SamplesPerPixel",
    "PhotometricInterpretation",
    "PlanarConfiguration",
    "ImagePositionPatient",
    "ImageOrientationPatient",
})


def _should_be_array(elem: Any) -> bool:
    """Check if this element should be a JSON array per §4.6 VM rules.

    VM=1 → bare value.  Anything else (1-n, 2, 3, 6, …) → always array.
    """
    import pydicom.datadict as dd

    keyword = elem.keyword
    if not keyword:
        # Private/unknown tag — use actual multiplicity
        return elem.VM > 1
    try:
        tag = dd.tag_for_keyword(keyword)
        if tag is None:
            return elem.VM > 1
        vm_str = dd.dictionary_VM(tag)
        return vm_str != "1"
    except (KeyError, AttributeError):
        return elem.VM > 1


def _convert_value(elem: Any) -> Any:
    """Convert a single pydicom DataElement value to JSON-native type."""
    from pydicom.dataset import Dataset as DsType
    from pydicom.sequence import Sequence as SqType
    from pydicom.valuerep import PersonName

    val = elem.value
    vr = elem.VR
    is_array = _should_be_array(elem)

    # None / empty
    if val is None:
        return None

    # Sequence → list of dicts (recursive)
    if vr == "SQ":
        if isinstance(val, SqType):
            return [_dataset_to_tags(item, _skip_geometry=False) for item in val]
        return None

    # Person Name
    if vr == "PN":
        if isinstance(val, PersonName):
            s = str(val)
            return s if s else None
        return str(val) if val else None

    # Decimal String → float
    if vr == "DS":
        return _convert_numeric(val, float, is_array)

    # Integer String → int
    if vr == "IS":
        return _convert_numeric(val, int, is_array)

    # Native integer types
    if vr in ("US", "SS", "UL", "SL"):
        return _convert_numeric(val, int, is_array)

    # Native float types
    if vr in ("FL", "FD"):
        return _convert_numeric(val, float, is_array)

    # Attribute Tag → hex string
    if vr == "AT":
        tag = val
        if hasattr(tag, "group"):
            return f"{tag.group:04X}{tag.element:04X}"
        return str(tag)

    # Binary VRs → base64
    if vr in _BINARY_VRS:
        import base64
        if isinstance(val, (bytes, bytearray)):
            return base64.b64encode(val).decode("ascii")
        return None

    # Everything else → string
    return _convert_string(val, is_array)


def _convert_numeric(val: Any, cast: type, is_array: bool) -> Any:
    """Convert a numeric value or MultiValue to JSON number(s)."""
    if _is_multi(val):
        return [cast(v) for v in val]
    if is_array:
        return [cast(val)]
    return cast(val)


def _convert_string(val: Any, is_array: bool) -> Any:
    """Convert a string value or MultiValue to JSON string(s)."""
    if _is_multi(val):
        return [str(v) for v in val]
    s = str(val)
    if not s:
        return None
    if is_array:
        return [s]
    return s


def _is_multi(val: Any) -> bool:
    """Check if val is a pydicom MultiValue or similar iterable (not str/bytes)."""
    return hasattr(val, "__iter__") and not isinstance(val, (str, bytes))


def _dataset_to_tags(
    ds: Any,
    *,
    _skip_geometry: bool = True,
    _include_binary: bool = False,
) -> dict[str, Any]:
    """Convert a pydicom Dataset to a tags dict per dicom-spec.md §4."""
    tags: dict[str, Any] = {}

    # Include File Meta Information (group 0002) if present
    file_meta = getattr(ds, "file_meta", None)
    if file_meta is not None:
        for elem in file_meta:
            if elem.tag.element == 0x0000:
                continue
            keyword = elem.keyword
            if not keyword or keyword == "":
                keyword = f"{elem.tag.group:04X}{elem.tag.element:04X}"
            tags[keyword] = _convert_value(elem)

    for elem in ds:
        # Skip group length tags (xxxx,0000)
        if elem.tag.element == 0x0000:
            continue
        # Skip binary VRs unless opted in
        if not _include_binary and elem.VR in _BINARY_VRS:
            continue

        # Determine key: keyword or hex for private/unknown tags
        keyword = elem.keyword
        if not keyword or keyword == "":
            keyword = f"{elem.tag.group:04X}{elem.tag.element:04X}"

        # Skip geometry tags already captured by convention fields
        if _skip_geometry and keyword in _SKIP_KEYWORDS:
            continue

        tags[keyword] = _convert_value(elem)

    return tags


# ---------------------------------------------------------------------------
# Anonymization detection
# ---------------------------------------------------------------------------


def _detect_anonymized(ds: Any) -> bool | None:
    """Heuristic: both PatientName and PatientID absent/empty → anonymized."""
    pn = getattr(ds, "PatientName", None)
    pid = getattr(ds, "PatientID", None)
    pn_empty = pn is None or str(pn) == ""
    pid_empty = pid is None or str(pid) == ""
    if pn_empty and pid_empty:
        return True
    return None


def _get_transfer_syntax(ds: Any) -> str | None:
    """Extract Transfer Syntax UID from file meta, if available."""
    meta = getattr(ds, "file_meta", None)
    if meta is None:
        return None
    tsuid = getattr(meta, "TransferSyntaxUID", None)
    return str(tsuid) if tsuid else None


# ---------------------------------------------------------------------------
# Geometry computation
# ---------------------------------------------------------------------------


def _compute_geometry(
    datasets: list[Any],
) -> tuple[np.ndarray, DicomGeometry]:
    """Compute geometry and stack pixel data from sorted datasets.

    Parameters
    ----------
    datasets : list of pydicom Dataset, already sorted by slice position

    Returns
    -------
    volume : np.ndarray with shape (n_slices, Rows, Columns)
    geometry : DicomGeometry
    """
    ds0 = datasets[0]

    # Orientation
    iop = [float(x) for x in ds0.ImageOrientationPatient]
    row_cosines = np.array(iop[:3])
    col_cosines = np.array(iop[3:])
    slice_normal = np.cross(row_cosines, col_cosines)
    nrm = np.linalg.norm(slice_normal)
    if nrm > 0:
        slice_normal = slice_normal / nrm

    # Pixel spacing: [row_spacing, col_spacing]
    ps = [float(x) for x in ds0.PixelSpacing]
    row_spacing, col_spacing = ps[0], ps[1]

    # Positions
    positions = [np.array([float(x) for x in ds.ImagePositionPatient]) for ds in datasets]
    space_origin = positions[0].tolist()

    # Slice direction and spacing
    if len(datasets) > 1:
        # Project positions onto slice normal and compute spacing
        projections = [float(np.dot(p, slice_normal)) for p in positions]
        diffs = np.diff(projections)
        # Use median spacing — robust to outlier gaps at series edges
        slice_spacing = float(np.median(diffs))
        if slice_spacing <= 0:
            slice_spacing = float(np.mean(diffs))

        # Always use cross-product normal for direction — robust to
        # irregular first/last slice positions
        slice_direction = (slice_normal * slice_spacing).tolist()

        # Warn on non-uniform spacing
        if len(datasets) > 2:
            if slice_spacing != 0 and np.max(np.abs(diffs - slice_spacing)) > 0.01 * abs(slice_spacing):
                warnings.warn(
                    f"Non-uniform slice spacing detected (range: "
                    f"{float(np.min(diffs)):.4f} to {float(np.max(diffs)):.4f}, "
                    f"median: {slice_spacing:.4f}). Using median spacing.",
                    stacklevel=3,
                )
    else:
        # Single slice fallback
        thickness = getattr(ds0, "SliceThickness", None)
        sbs = getattr(ds0, "SpacingBetweenSlices", None)
        if thickness is not None:
            slice_spacing = float(thickness)
        elif sbs is not None:
            slice_spacing = float(sbs)
        else:
            warnings.warn(
                "Single slice with no SliceThickness or SpacingBetweenSlices. "
                "Using 1.0 mm for slice axis.",
                stacklevel=3,
            )
            slice_spacing = 1.0
        slice_direction = (slice_normal * slice_spacing).tolist()

    # Space directions in C order: [slice, row, col]
    # axes[1] (row index → column direction): col_cosines × PixelSpacing[0]
    # axes[2] (col index → row direction):    row_cosines × PixelSpacing[1]
    space_directions = [
        slice_direction,
        (col_cosines * row_spacing).tolist(),
        (row_cosines * col_spacing).tolist(),
    ]

    # Thickness
    slice_thickness = None
    st = getattr(ds0, "SliceThickness", None)
    if st is not None:
        slice_thickness = float(st)

    # Rescale
    rescale_slope = None
    rs = getattr(ds0, "RescaleSlope", None)
    if rs is not None:
        rescale_slope = float(rs)

    rescale_intercept = None
    ri = getattr(ds0, "RescaleIntercept", None)
    if ri is not None:
        rescale_intercept = float(ri)

    rescale_type = None
    rt = getattr(ds0, "RescaleType", None)
    if rt is not None:
        rescale_type = str(rt)

    # Dtype
    bits = int(ds0.BitsAllocated)
    signed = int(ds0.PixelRepresentation)
    _dtype_map = {
        (8, 0): np.uint8, (8, 1): np.int8,
        (16, 0): np.uint16, (16, 1): np.int16,
        (32, 0): np.uint32, (32, 1): np.int32,
    }
    dtype = np.dtype(_dtype_map.get((bits, signed), np.uint16))

    rows = int(ds0.Rows)
    cols = int(ds0.Columns)
    n_slices = len(datasets)

    geometry = DicomGeometry(
        shape=(n_slices, rows, cols),
        dtype=dtype,
        space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
        space_origin=space_origin,
        space_directions=space_directions,
        slice_thickness=slice_thickness,
        rescale_slope=rescale_slope,
        rescale_intercept=rescale_intercept,
        rescale_type=rescale_type,
    )

    # Stack pixel data
    try:
        slices = [ds.pixel_array for ds in datasets]
    except Exception as e:
        raise RuntimeError(
            f"Failed to read pixel data: {e}. "
            "For compressed DICOM, you may need: pip install pylibjpeg pylibjpeg-libjpeg"
        ) from e
    volume = np.stack(slices, axis=0).astype(dtype)

    return volume, geometry


def _sort_datasets(datasets: list[Any]) -> list[Any]:
    """Sort datasets by slice position along the slice normal."""
    ds0 = datasets[0]
    iop = [float(x) for x in ds0.ImageOrientationPatient]
    row_cosines = np.array(iop[:3])
    col_cosines = np.array(iop[3:])
    slice_normal = np.cross(row_cosines, col_cosines)
    nrm = np.linalg.norm(slice_normal)
    if nrm > 0:
        slice_normal = slice_normal / nrm

    def _projection(ds: Any) -> float:
        pos = np.array([float(x) for x in ds.ImagePositionPatient])
        return float(np.dot(pos, slice_normal))

    return sorted(datasets, key=_projection)


# ---------------------------------------------------------------------------
# Single-frame series loader
# ---------------------------------------------------------------------------


def _load_single_frame_series(
    dir_path: Path,
) -> tuple[np.ndarray, DicomGeometry, list[Any]]:
    """Load a directory of single-frame DICOM files as one volume."""
    import pydicom

    # Scan for .dcm files (also try files without extension)
    dcm_files = sorted(dir_path.glob("*.dcm"))
    if not dcm_files:
        dcm_files = sorted(
            p for p in dir_path.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )
    if not dcm_files:
        raise FileNotFoundError(f"No DICOM files found in {dir_path}")

    datasets = []
    for f in dcm_files:
        try:
            ds = pydicom.dcmread(str(f))
            # Only include files that have pixel data and position
            if hasattr(ds, "PixelData") and hasattr(ds, "ImagePositionPatient"):
                datasets.append(ds)
        except Exception:
            continue

    if not datasets:
        raise ValueError(f"No valid DICOM image files found in {dir_path}")

    # Validate single series
    series_uids = {str(ds.SeriesInstanceUID) for ds in datasets if hasattr(ds, "SeriesInstanceUID")}
    if len(series_uids) > 1:
        raise ValueError(
            f"Directory contains {len(series_uids)} series. "
            "Please provide a directory with a single DICOM series. "
            f"Series UIDs: {series_uids}"
        )

    # Sort by position
    datasets = _sort_datasets(datasets)

    volume, geometry = _compute_geometry(datasets)
    return volume, geometry, datasets


# ---------------------------------------------------------------------------
# DICOM SEG loaders
# ---------------------------------------------------------------------------


def _load_seg_labelmap(
    ds: Any,
) -> tuple[np.ndarray, DicomGeometry, list[Any]]:
    """Load a DICOM LABELMAP Segmentation as a 3D volume.

    Pixel values are segment numbers directly. One frame per slice.
    """
    per_frame = getattr(ds, "PerFrameFunctionalGroupsSequence", None)
    shared = getattr(ds, "SharedFunctionalGroupsSequence", None)
    if per_frame is None:
        raise ValueError("DICOM LABELMAP SEG missing PerFrameFunctionalGroupsSequence")

    # Extract shared geometry
    shared_iop = None
    shared_ps = None
    shared_st = None
    if shared and len(shared) > 0:
        s0 = shared[0]
        orient_seq = getattr(s0, "PlaneOrientationSequence", None)
        if orient_seq and len(orient_seq) > 0:
            shared_iop = [float(x) for x in orient_seq[0].ImageOrientationPatient]
        measures_seq = getattr(s0, "PixelMeasuresSequence", None)
        if measures_seq and len(measures_seq) > 0:
            shared_ps = [float(x) for x in measures_seq[0].PixelSpacing]
            st = getattr(measures_seq[0], "SliceThickness", None)
            if st is not None:
                shared_st = float(st)

    rows, cols = int(ds.Rows), int(ds.Columns)
    pixel_array = ds.pixel_array  # (n_frames, rows, cols)

    # Gather frame positions and sort by Z
    iop = shared_iop
    for fg in per_frame:
        orient_seq = getattr(fg, "PlaneOrientationSequence", None)
        if orient_seq and len(orient_seq) > 0:
            iop = [float(x) for x in orient_seq[0].ImageOrientationPatient]
            break
    if iop is None:
        raise ValueError("No ImageOrientationPatient found")

    row_cos = np.array(iop[:3])
    col_cos = np.array(iop[3:])
    slice_normal = np.cross(row_cos, col_cos)
    nrm = np.linalg.norm(slice_normal)
    if nrm > 0:
        slice_normal = slice_normal / nrm

    frame_infos = []
    for i, fg in enumerate(per_frame):
        pos_seq = getattr(fg, "PlanePositionSequence", None)
        if pos_seq and len(pos_seq) > 0:
            pos = [float(x) for x in pos_seq[0].ImagePositionPatient]
        else:
            pos = [0.0, 0.0, 0.0]
        z_proj = float(np.dot(np.array(pos), slice_normal))
        frame_infos.append({"_frame_index": i, "position": pos, "z_proj": z_proj})

    frame_infos.sort(key=lambda fi: fi["z_proj"])

    # Stack sorted frames into 3D volume
    sorted_indices = [fi["_frame_index"] for fi in frame_infos]
    volume = np.stack([pixel_array[idx] for idx in sorted_indices], axis=0)

    # Determine dtype
    bits = int(ds.BitsAllocated)
    signed = int(ds.PixelRepresentation)
    _dtype_map = {
        (8, 0): np.uint8, (8, 1): np.int8,
        (16, 0): np.uint16, (16, 1): np.int16,
        (32, 0): np.uint32, (32, 1): np.int32,
    }
    dtype = np.dtype(_dtype_map.get((bits, signed), np.uint16))
    volume = volume.astype(dtype)

    # Compute spatial geometry
    ps = shared_ps or [1.0, 1.0]
    row_spacing, col_spacing = ps[0], ps[1]
    n_z = len(frame_infos)

    if n_z > 1:
        z_vals = [fi["z_proj"] for fi in frame_infos]
        diffs = np.diff(z_vals)
        slice_spacing = float(np.median(diffs))
        if slice_spacing <= 0:
            slice_spacing = float(np.mean(diffs))
    else:
        slice_spacing = float(shared_st) if shared_st else 1.0

    space_origin = frame_infos[0]["position"]
    slice_direction = (slice_normal * slice_spacing).tolist()
    space_directions = [
        slice_direction,
        (col_cos * row_spacing).tolist(),
        (row_cos * col_spacing).tolist(),
    ]

    geometry = DicomGeometry(
        shape=volume.shape,
        dtype=dtype,
        space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
        space_origin=space_origin,
        space_directions=space_directions,
        slice_thickness=shared_st,
        rescale_slope=None,
        rescale_intercept=None,
        rescale_type=None,
    )

    return volume, geometry, [ds]


def _load_seg(
    ds: Any,
) -> tuple[np.ndarray, DicomGeometry, list[Any]]:
    """Load a DICOM Segmentation object.

    For BINARY segmentations, returns a 4D volume (n_segments, n_z, rows, cols)
    with one binary channel per segment.

    For LABELMAP segmentations, returns a 3D volume (n_z, rows, cols) where
    each voxel value is a segment number.
    """
    seg_type = str(getattr(ds, "SegmentationType", "BINARY"))

    if seg_type == "LABELMAP":
        return _load_seg_labelmap(ds)

    per_frame = getattr(ds, "PerFrameFunctionalGroupsSequence", None)
    shared = getattr(ds, "SharedFunctionalGroupsSequence", None)
    if per_frame is None:
        raise ValueError("DICOM SEG missing PerFrameFunctionalGroupsSequence")

    # Extract shared geometry
    shared_iop = None
    shared_ps = None
    shared_st = None
    if shared and len(shared) > 0:
        s0 = shared[0]
        orient_seq = getattr(s0, "PlaneOrientationSequence", None)
        if orient_seq and len(orient_seq) > 0:
            shared_iop = [float(x) for x in orient_seq[0].ImageOrientationPatient]
        measures_seq = getattr(s0, "PixelMeasuresSequence", None)
        if measures_seq and len(measures_seq) > 0:
            shared_ps = [float(x) for x in measures_seq[0].PixelSpacing]
            st = getattr(measures_seq[0], "SliceThickness", None)
            if st is not None:
                shared_st = float(st)

    # Collect per-frame info: segment number, Z position, frame index
    n_segments = len(ds.SegmentSequence)
    rows, cols = int(ds.Rows), int(ds.Columns)
    pixel_array = ds.pixel_array  # (n_frames, rows, cols)

    # Gather all unique Z positions and per-frame segment assignments
    z_positions: set[float] = set()
    frame_records: list[dict[str, Any]] = []

    for i, fg in enumerate(per_frame):
        seg_id_seq = getattr(fg, "SegmentIdentificationSequence", None)
        seg_num = int(seg_id_seq[0].ReferencedSegmentNumber) if seg_id_seq else 1

        pos_seq = getattr(fg, "PlanePositionSequence", None)
        if pos_seq and len(pos_seq) > 0:
            pos = [float(x) for x in pos_seq[0].ImagePositionPatient]
        else:
            pos = [0.0, 0.0, 0.0]

        iop = None
        orient_seq = getattr(fg, "PlaneOrientationSequence", None)
        if orient_seq and len(orient_seq) > 0:
            iop = [float(x) for x in orient_seq[0].ImageOrientationPatient]

        frame_records.append({
            "_frame_index": i,
            "seg_num": seg_num,
            "position": pos,
            "iop": iop or shared_iop,
        })

    # Compute slice normal from orientation
    iop = frame_records[0]["iop"]
    if iop is None:
        raise ValueError("No ImageOrientationPatient found")
    row_cos = np.array(iop[:3])
    col_cos = np.array(iop[3:])
    slice_normal = np.cross(row_cos, col_cos)
    nrm = np.linalg.norm(slice_normal)
    if nrm > 0:
        slice_normal = slice_normal / nrm

    # Project all positions onto slice normal to get Z ordering
    for fr in frame_records:
        fr["z_proj"] = float(np.dot(np.array(fr["position"]), slice_normal))
        z_positions.add(fr["z_proj"])

    z_sorted = sorted(z_positions)
    z_to_idx = {z: i for i, z in enumerate(z_sorted)}
    n_z = len(z_sorted)

    # Build 4D volume: (n_segments, n_z, rows, cols)
    volume = np.zeros((n_segments, n_z, rows, cols), dtype=np.uint8)
    for fr in frame_records:
        seg_idx = fr["seg_num"] - 1  # 0-based
        z_idx = z_to_idx[fr["z_proj"]]
        volume[seg_idx, z_idx, :, :] = pixel_array[fr["_frame_index"]]

    # Compute spatial geometry from Z positions
    ps = shared_ps or [1.0, 1.0]
    row_spacing, col_spacing = ps[0], ps[1]

    if n_z > 1:
        diffs = np.diff(z_sorted)
        slice_spacing = float(np.median(diffs))
        if slice_spacing <= 0:
            slice_spacing = float(np.mean(diffs))
        if n_z > 2 and slice_spacing != 0:
            if np.max(np.abs(diffs - slice_spacing)) > 0.01 * abs(slice_spacing):
                warnings.warn(
                    f"Non-uniform slice spacing (range: {float(np.min(diffs)):.4f} "
                    f"to {float(np.max(diffs)):.4f}, median: {slice_spacing:.4f}). "
                    f"Using median.",
                    stacklevel=3,
                )
    else:
        slice_spacing = float(shared_st) if shared_st else 1.0

    # Origin: position of the first (lowest Z) frame
    min_z_frame = min(frame_records, key=lambda fr: fr["z_proj"])
    space_origin = min_z_frame["position"]

    # Space directions in C order: [segment(none), slice, row, col]
    slice_direction = (slice_normal * slice_spacing).tolist()
    space_directions = [
        slice_direction,
        (col_cos * row_spacing).tolist(),
        (row_cos * col_spacing).tolist(),
    ]

    slice_thickness = shared_st

    geometry = DicomGeometry(
        shape=volume.shape,
        dtype=np.dtype(np.uint8),
        space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
        space_origin=space_origin,
        space_directions=space_directions,
        slice_thickness=slice_thickness,
        rescale_slope=None,
        rescale_intercept=None,
        rescale_type=None,
    )

    return volume, geometry, [ds]


# ---------------------------------------------------------------------------
# Enhanced multi-frame loader
# ---------------------------------------------------------------------------


def _load_multiframe(
    file_path: Path,
) -> tuple[np.ndarray, DicomGeometry, list[Any]]:
    """Load an enhanced multi-frame DICOM file."""
    import pydicom

    ds = pydicom.dcmread(str(file_path))

    # Route DICOM SEG to dedicated loader
    if _is_dicom_seg(ds):
        return _load_seg(ds)

    n_frames = int(getattr(ds, "NumberOfFrames", 1))
    if n_frames <= 1 and hasattr(ds, "ImagePositionPatient"):
        # Single-frame file — treat like a 1-slice volume
        datasets = [ds]
        datasets = _sort_datasets(datasets)
        volume, geometry = _compute_geometry(datasets)
        return volume, geometry, datasets

    # Enhanced multi-frame: extract per-frame geometry
    per_frame = getattr(ds, "PerFrameFunctionalGroupsSequence", None)
    shared = getattr(ds, "SharedFunctionalGroupsSequence", None)

    if per_frame is None:
        raise ValueError(
            "Enhanced multi-frame DICOM missing PerFrameFunctionalGroupsSequence"
        )

    # Extract shared geometry defaults
    shared_iop = None
    shared_ps = None
    if shared and len(shared) > 0:
        s0 = shared[0]
        orient_seq = getattr(s0, "PlaneOrientationSequence", None)
        if orient_seq and len(orient_seq) > 0:
            shared_iop = [float(x) for x in orient_seq[0].ImageOrientationPatient]
        measures_seq = getattr(s0, "PixelMeasuresSequence", None)
        if measures_seq and len(measures_seq) > 0:
            shared_ps = [float(x) for x in measures_seq[0].PixelSpacing]

    # Build per-frame info
    frame_infos: list[dict[str, Any]] = []
    for i, frame_group in enumerate(per_frame):
        info: dict[str, Any] = {"_frame_index": i}

        # Position
        pos_seq = getattr(frame_group, "PlanePositionSequence", None)
        if pos_seq and len(pos_seq) > 0:
            info["ImagePositionPatient"] = [
                float(x) for x in pos_seq[0].ImagePositionPatient
            ]

        # Orientation (per-frame or shared)
        orient_seq = getattr(frame_group, "PlaneOrientationSequence", None)
        if orient_seq and len(orient_seq) > 0:
            info["ImageOrientationPatient"] = [
                float(x) for x in orient_seq[0].ImageOrientationPatient
            ]
        elif shared_iop is not None:
            info["ImageOrientationPatient"] = shared_iop

        # Pixel spacing (per-frame or shared)
        measures_seq = getattr(frame_group, "PixelMeasuresSequence", None)
        if measures_seq and len(measures_seq) > 0:
            info["PixelSpacing"] = [float(x) for x in measures_seq[0].PixelSpacing]
            st = getattr(measures_seq[0], "SliceThickness", None)
            if st is not None:
                info["SliceThickness"] = float(st)
        elif shared_ps is not None:
            info["PixelSpacing"] = shared_ps

        # Temporal position — try multiple sources
        temporal_pos = None
        fc_seq = getattr(frame_group, "FrameContentSequence", None)
        if fc_seq and len(fc_seq) > 0:
            fc = fc_seq[0]
            tpi = getattr(fc, "TemporalPositionIndex", None)
            if tpi is not None:
                temporal_pos = int(tpi)
            elif hasattr(fc, "DimensionIndexValues"):
                # DimensionIndexValues: first index is often temporal
                div = list(fc.DimensionIndexValues)
                if len(div) >= 2:
                    temporal_pos = int(div[0])
        info["temporal_pos"] = temporal_pos

        frame_infos.append(info)

    # Validate we have positions
    if not all("ImagePositionPatient" in fi for fi in frame_infos):
        raise ValueError("Not all frames have ImagePositionPatient")
    if not any("ImageOrientationPatient" in fi for fi in frame_infos):
        raise ValueError("No ImageOrientationPatient found in frame or shared groups")

    # Compute slice normal
    iop = frame_infos[0]["ImageOrientationPatient"]
    row_cos = np.array(iop[:3])
    col_cos = np.array(iop[3:])
    slice_normal = np.cross(row_cos, col_cos)
    nrm = np.linalg.norm(slice_normal)
    if nrm > 0:
        slice_normal = slice_normal / nrm

    # Project positions onto slice normal
    for fi in frame_infos:
        fi["z_proj"] = float(np.dot(np.array(fi["ImagePositionPatient"]), slice_normal))

    # Detect if this is a 4D temporal dataset
    temporal_values = {fi["temporal_pos"] for fi in frame_infos if fi["temporal_pos"] is not None}
    is_4d_temporal = len(temporal_values) > 1

    rows, cols = int(ds.Rows), int(ds.Columns)
    pixel_array_full = ds.pixel_array  # (n_frames, Rows, Columns)

    # Dtype
    bits = int(ds.BitsAllocated)
    signed = int(ds.PixelRepresentation)
    _dtype_map = {
        (8, 0): np.uint8, (8, 1): np.int8,
        (16, 0): np.uint16, (16, 1): np.int16,
        (32, 0): np.uint32, (32, 1): np.int32,
    }
    dtype = np.dtype(_dtype_map.get((bits, signed), np.uint16))

    if is_4d_temporal:
        # --- 4D temporal volume ---
        z_values = sorted({fi["z_proj"] for fi in frame_infos})
        t_values = sorted(temporal_values)
        z_to_idx = {z: i for i, z in enumerate(z_values)}
        t_to_idx = {t: i for i, t in enumerate(t_values)}
        n_t = len(t_values)
        n_z = len(z_values)

        volume = np.zeros((n_t, n_z, rows, cols), dtype=dtype)
        for fi in frame_infos:
            t_idx = t_to_idx[fi["temporal_pos"]]
            z_idx = z_to_idx[fi["z_proj"]]
            volume[t_idx, z_idx, :, :] = pixel_array_full[fi["_frame_index"]]

        # Spatial geometry from Z positions
        ps = frame_infos[0].get("PixelSpacing", [1.0, 1.0])
        row_spacing, col_spacing = ps[0], ps[1]

        if n_z > 1:
            diffs = np.diff(z_values)
            slice_spacing = float(np.median(diffs))
            if slice_spacing <= 0:
                slice_spacing = float(np.mean(diffs))
        else:
            st = frame_infos[0].get("SliceThickness")
            slice_spacing = float(st) if st else 1.0

        min_z_frame = min(frame_infos, key=lambda fi: fi["z_proj"])
        space_origin = min_z_frame["ImagePositionPatient"]
        slice_direction = (slice_normal * slice_spacing).tolist()
        space_directions = [
            slice_direction,
            (col_cos * row_spacing).tolist(),
            (row_cos * col_spacing).tolist(),
        ]

        slice_thickness = frame_infos[0].get("SliceThickness")

        # Rescale
        rescale_slope = None
        rs = getattr(ds, "RescaleSlope", None)
        if rs is not None:
            rescale_slope = float(rs)
        rescale_intercept = None
        ri = getattr(ds, "RescaleIntercept", None)
        if ri is not None:
            rescale_intercept = float(ri)
        rescale_type = None
        rt = getattr(ds, "RescaleType", None)
        if rt is not None:
            rescale_type = str(rt)

        geometry = DicomGeometry(
            shape=volume.shape,
            dtype=dtype,
            space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
            space_origin=space_origin,
            space_directions=space_directions,
            slice_thickness=slice_thickness,
            rescale_slope=rescale_slope,
            rescale_intercept=rescale_intercept,
            rescale_type=rescale_type,
        )
        return volume, geometry, [ds]

    else:
        # --- 3D volume (existing path) ---
        frame_infos.sort(key=lambda fi: fi["z_proj"])

        class _FrameProxy:
            pass

        proxies = []
        for fi in frame_infos:
            proxy = _FrameProxy()
            proxy.ImageOrientationPatient = fi["ImageOrientationPatient"]  # type: ignore[attr-defined]
            proxy.PixelSpacing = fi["PixelSpacing"]  # type: ignore[attr-defined]
            proxy.ImagePositionPatient = fi["ImagePositionPatient"]  # type: ignore[attr-defined]
            proxy.Rows = ds.Rows  # type: ignore[attr-defined]
            proxy.Columns = ds.Columns  # type: ignore[attr-defined]
            proxy.BitsAllocated = ds.BitsAllocated  # type: ignore[attr-defined]
            proxy.PixelRepresentation = ds.PixelRepresentation  # type: ignore[attr-defined]
            idx = fi["_frame_index"]
            proxy.pixel_array = pixel_array_full[idx]  # type: ignore[attr-defined]
            if "SliceThickness" in fi:
                proxy.SliceThickness = fi["SliceThickness"]  # type: ignore[attr-defined]
            elif hasattr(ds, "SliceThickness"):
                proxy.SliceThickness = ds.SliceThickness  # type: ignore[attr-defined]
            for attr in ("RescaleSlope", "RescaleIntercept", "RescaleType"):
                if hasattr(ds, attr):
                    setattr(proxy, attr, getattr(ds, attr))
            proxies.append(proxy)

        volume, geometry = _compute_geometry(proxies)
        return volume, geometry, [ds]


# ---------------------------------------------------------------------------
# DICOM SEG → slicerseg extension
# ---------------------------------------------------------------------------


# SOP Class UID for Segmentation Storage
_SEG_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.66.4"


def _coded_entry_from_sequence(seq: Any) -> CodedEntry | None:
    """Extract a CodedEntry from a DICOM code sequence item."""
    if seq is None or len(seq) == 0:
        return None
    item = seq[0]
    scheme = str(getattr(item, "CodingSchemeDesignator", ""))
    code = str(getattr(item, "CodeValue", ""))
    meaning = str(getattr(item, "CodeMeaning", ""))
    if not scheme or not code or not meaning:
        return None
    return CodedEntry(scheme=scheme, code=code, meaning=meaning)


def _is_dicom_seg(ds: Any) -> bool:
    """Check if a dataset is a DICOM Segmentation object."""
    sop_class = str(getattr(getattr(ds, "file_meta", None), "MediaStorageSOPClassUID", ""))
    if sop_class == _SEG_SOP_CLASS_UID:
        return True
    sop_class2 = str(getattr(ds, "SOPClassUID", ""))
    if sop_class2 == _SEG_SOP_CLASS_UID:
        return True
    return hasattr(ds, "SegmentSequence")


def _cielab_to_rgb(L: float, a: float, b: float) -> list[float]:
    """Convert CIELab (as stored in DICOM, scaled 0-65535) to RGB [0-1]."""
    import math

    # DICOM stores CIELab with L in [0, 65535] mapping to [0, 100],
    # a and b in [0, 65535] mapping to [-128, 127]
    L_norm = L * 100.0 / 65535.0
    a_norm = a * 255.0 / 65535.0 - 128.0
    b_norm = b * 255.0 / 65535.0 - 128.0

    # CIELab → XYZ (D65 illuminant)
    fy = (L_norm + 16.0) / 116.0
    fx = a_norm / 500.0 + fy
    fz = fy - b_norm / 200.0

    delta = 6.0 / 29.0
    delta3 = delta ** 3

    x = 0.9505 * (fx ** 3 if fx > delta else (fx - 16.0 / 116.0) * 3 * delta ** 2)
    y = 1.0000 * (fy ** 3 if fy > delta else (fy - 16.0 / 116.0) * 3 * delta ** 2)
    z = 1.0890 * (fz ** 3 if fz > delta else (fz - 16.0 / 116.0) * 3 * delta ** 2)

    # XYZ → linear sRGB
    r_lin = 3.2406 * x - 1.5372 * y - 0.4986 * z
    g_lin = -0.9689 * x + 1.8758 * y + 0.0415 * z
    b_lin = 0.0557 * x - 0.2040 * y + 1.0570 * z

    # Linear → sRGB gamma
    def gamma(c: float) -> float:
        c = max(0.0, min(1.0, c))
        return 12.92 * c if c <= 0.0031308 else 1.055 * math.pow(c, 1.0 / 2.4) - 0.055

    return [round(gamma(r_lin), 4), round(gamma(g_lin), 4), round(gamma(b_lin), 4)]


def _extract_seg_extension(ds: Any) -> SegmentationExtension | None:
    """Extract a slicerseg extension from a DICOM SEG dataset."""
    seg_seq = getattr(ds, "SegmentSequence", None)
    if seg_seq is None or len(seg_seq) == 0:
        return None

    # Determine source representation
    seg_type = str(getattr(ds, "SegmentationType", "BINARY"))
    if seg_type == "FRACTIONAL":
        source_rep = SourceRepresentation.FRACTIONAL_LABELMAP
    else:
        source_rep = SourceRepresentation.BINARY_LABELMAP

    segments: list[Segment] = []
    for seg_item in seg_seq:
        seg_number = int(getattr(seg_item, "SegmentNumber", 0))
        seg_label = str(getattr(seg_item, "SegmentLabel", ""))
        seg_id = seg_label or f"Segment_{seg_number}"

        # Color from RecommendedDisplayCIELabValue
        color = None
        cielab = getattr(seg_item, "RecommendedDisplayCIELabValue", None)
        if cielab is not None and len(cielab) == 3:
            color = _cielab_to_rgb(float(cielab[0]), float(cielab[1]), float(cielab[2]))

        # DICOM classification
        category = _coded_entry_from_sequence(
            getattr(seg_item, "SegmentedPropertyCategoryCodeSequence", None)
        )
        seg_type_entry = _coded_entry_from_sequence(
            getattr(seg_item, "SegmentedPropertyTypeCodeSequence", None)
        )
        # Type modifier from within the type sequence
        type_modifier = None
        type_seq = getattr(seg_item, "SegmentedPropertyTypeCodeSequence", None)
        if type_seq and len(type_seq) > 0:
            mod_seq = getattr(type_seq[0], "SegmentedPropertyTypeModifierCodeSequence", None)
            type_modifier = _coded_entry_from_sequence(mod_seq)

        anatomic_region = _coded_entry_from_sequence(
            getattr(seg_item, "AnatomicRegionSequence", None)
        )
        anatomic_region_modifier = None
        anat_seq = getattr(seg_item, "AnatomicRegionSequence", None)
        if anat_seq and len(anat_seq) > 0:
            mod_seq = getattr(anat_seq[0], "AnatomicRegionModifierSequence", None)
            anatomic_region_modifier = _coded_entry_from_sequence(mod_seq)

        dicom_class = None
        if any(x is not None for x in (category, seg_type_entry, type_modifier,
                                        anatomic_region, anatomic_region_modifier)):
            dicom_class = DicomClassification(
                category=category,
                type=seg_type_entry,
                type_modifier=type_modifier,
                anatomic_region=anatomic_region,
                anatomic_region_modifier=anatomic_region_modifier,
            )

        seg_kwargs: dict[str, Any] = {"id": seg_id}
        if seg_type == "LABELMAP":
            seg_kwargs["label_value"] = seg_number
        else:
            seg_kwargs["label_value"] = 1
            seg_kwargs["layer"] = seg_number - 1  # 0-based layer index
        if seg_label:
            seg_kwargs["name"] = seg_label
        if color is not None:
            seg_kwargs["color"] = color
        if dicom_class is not None:
            seg_kwargs["dicom"] = dicom_class

        segments.append(Segment(**seg_kwargs))

    return SegmentationExtension(
        version="1.0",
        source_representation=source_rep,
        segments=segments,
    )


# ---------------------------------------------------------------------------
# Metadata building
# ---------------------------------------------------------------------------


def _build_samples(
    datasets: list[Any],
    slice_normal: np.ndarray,
    space_origin: list[float],
    space_direction: list[float],
    include_tags: bool,
    include_binary: bool,
) -> list[SampleMetadata] | None:
    """Build per-sample metadata for the slice axis.

    Returns None if all slices are uniformly spaced with no per-instance
    tag variation (samples would add no information).
    """
    n_slices = len(datasets)
    if n_slices <= 1:
        return None

    # Collect per-slice positions
    positions_3d: list[list[float]] = []
    for ds in datasets:
        if hasattr(ds, "ImagePositionPatient"):
            positions_3d.append([float(x) for x in ds.ImagePositionPatient])
        else:
            return None  # can't build samples without positions

    origin_arr = np.array(space_origin)
    dir_arr = np.array(space_direction)
    dir_mag = np.linalg.norm(dir_arr)

    # Project positions onto slice normal to get scalar positions
    projections = [float(np.dot(np.array(p), slice_normal)) for p in positions_3d]

    # Check if positions differ only along the slice normal (position vs origin)
    # Residual = position minus the component along slice normal
    residuals = []
    for p in positions_3d:
        p_arr = np.array(p)
        along = float(np.dot(p_arr, slice_normal)) * slice_normal
        residuals.append(p_arr - along)

    # If all residuals are the same (within tolerance), use position (scalar)
    use_position = True
    ref_residual = residuals[0]
    for r in residuals[1:]:
        if not np.allclose(r, ref_residual, atol=1e-4):
            use_position = False
            break

    # Check if spacing is uniform (matches space_direction model)
    # Non-uniform if: along-normal spacing varies OR in-plane origin shifts
    is_uniform = use_position  # in-plane variation → not uniform
    if is_uniform and dir_mag > 0:
        expected_positions = [
            projections[0] + i * dir_mag for i in range(n_slices)
        ]
        for actual, expected in zip(projections, expected_positions):
            if abs(actual - expected) > 1e-4 * dir_mag:
                is_uniform = False
                break

    # Split tags: find tags that vary across slices
    per_slice_tags: list[dict[str, Any] | None] = [None] * n_slices
    has_varying_tags = False

    if include_tags and n_slices > 1:
        # Extract tags from all datasets
        all_tags = [
            _dataset_to_tags(ds, _include_binary=include_binary)
            for ds in datasets
        ]

        # Find keys that vary
        all_keys = set()
        for t in all_tags:
            all_keys.update(t.keys())

        varying_keys: set[str] = set()
        for key in all_keys:
            values = [t.get(key) for t in all_tags]
            ref = values[0]
            for v in values[1:]:
                if v != ref:
                    varying_keys.add(key)
                    break

        if varying_keys:
            has_varying_tags = True
            # Remove varying keys from the first dataset's tags
            # (caller uses datasets[0] for series-level tags)
            for key in varying_keys:
                for t in all_tags:
                    pass  # don't mutate here; we'll handle in the caller

            per_slice_tags = [
                {k: t[k] for k in varying_keys if k in t}
                for t in all_tags
            ]

    # If everything is uniform and no per-slice tags, skip samples
    if is_uniform and not has_varying_tags:
        return None

    # Build samples
    samples: list[SampleMetadata] = []
    for i in range(n_slices):
        kwargs: dict[str, Any] = {}

        if not is_uniform:
            if use_position:
                kwargs["position"] = projections[i]
            else:
                kwargs["origin"] = positions_3d[i]

        if has_varying_tags and per_slice_tags[i]:
            kwargs["extensions"] = {"dicom": per_slice_tags[i]}

        samples.append(SampleMetadata(**kwargs))

    return samples


def _get_varying_tag_keys(datasets: list[Any], include_binary: bool) -> set[str]:
    """Return tag keys whose values differ across datasets."""
    if len(datasets) <= 1:
        return set()

    all_tags = [_dataset_to_tags(ds, _include_binary=include_binary) for ds in datasets]
    all_keys: set[str] = set()
    for t in all_tags:
        all_keys.update(t.keys())

    varying: set[str] = set()
    for key in all_keys:
        values = [t.get(key) for t in all_tags]
        ref = values[0]
        for v in values[1:]:
            if v != ref:
                varying.add(key)
                break
    return varying


def build_duckn_metadata(
    geometry: DicomGeometry,
    datasets: list[Any],
    anonymized: bool | None,
    include_tags: bool,
    include_binary: bool = False,
) -> DucknMetadata:
    """Build duckn metadata from geometry and DICOM datasets."""
    # Axes in C order
    axes = []

    # For 4D data, prepend the appropriate axis
    is_4d = len(geometry.shape) == 4
    if is_4d:
        ds0 = datasets[0]
        if _is_dicom_seg(ds0):
            axes.append(AxisMetadata(kind=AxisKind.LIST))
        else:
            axes.append(AxisMetadata(kind=AxisKind.TIME))

    # Compute slice normal for per-sample geometry
    slice_dir = geometry.space_directions[0] if geometry.space_directions else [0, 0, 1]
    slice_normal = np.array(slice_dir)
    nrm = np.linalg.norm(slice_normal)
    if nrm > 0:
        slice_normal = slice_normal / nrm

    # Build per-sample metadata for the slice axis
    samples = _build_samples(
        datasets, slice_normal, geometry.space_origin,
        slice_dir, include_tags, include_binary,
    )

    # Determine which tag keys vary (to exclude from series-level tags)
    varying_keys = _get_varying_tag_keys(datasets, include_binary) if include_tags else set()

    # Spatial axes: [slice, row, col]
    for i, direction in enumerate(geometry.space_directions):
        ax_kwargs: dict[str, Any] = {
            "kind": AxisKind.SPACE,
            "centering": Centering.CELL,
            "space_direction": direction,
            "unit": "mm",
        }
        if i == 0 and geometry.slice_thickness is not None:
            ax_kwargs["thickness"] = geometry.slice_thickness
        if i == 0 and samples is not None:
            ax_kwargs["samples"] = samples
        axes.append(AxisMetadata(**ax_kwargs))

    # Value transforms from RescaleSlope/Intercept
    value_transforms = None
    if geometry.rescale_slope is not None and geometry.rescale_intercept is not None:
        value_transforms = [
            ValueTransform(
                name="linear",
                parameters={
                    "slope": geometry.rescale_slope,
                    "intercept": geometry.rescale_intercept,
                },
            )
        ]

    # Sample units from RescaleType
    sample_units = None
    if geometry.rescale_type:
        sample_units = geometry.rescale_type

    # DICOM extension (series-level tags only)
    extensions = None
    if include_tags:
        ds0 = datasets[0]
        series_tags = _dataset_to_tags(ds0, _include_binary=include_binary)
        # Remove varying keys — they're in per-sample extensions
        for key in varying_keys:
            series_tags.pop(key, None)

        anon = anonymized
        if anon is None:
            anon = _detect_anonymized(ds0)

        dicom_ext = DicomExtension(
            version="1.0",
            anonymized=anon if anon else None,
            source_transfer_syntax=_get_transfer_syntax(ds0),
            tags=series_tags if series_tags else None,
        )
        extensions = {"dicom": dicom_ext.model_dump(exclude_none=True, by_alias=True)}

    # Segmentation extension from DICOM SEG
    ds0 = datasets[0]
    if _is_dicom_seg(ds0):
        seg_ext = _extract_seg_extension(ds0)
        if seg_ext is not None:
            if extensions is None:
                extensions = {}
            extensions["slicerseg"] = seg_ext.model_dump(exclude_none=True)

    return DucknMetadata(
        version="1.0",
        space=geometry.space,
        space_origin=geometry.space_origin,
        sample_units=sample_units,
        value_transforms=value_transforms,
        axes=axes,
        extensions=extensions,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def dicom_to_zarr(
    input_path: str | Path,
    output_path: str | Path,
    *,
    chunks: tuple[int, ...] | None = None,
    compressor: str = "zstd",
    level: int = 3,
    overwrite: bool = False,
    anonymized: bool | None = None,
    tags: bool = True,
    binary_tags: bool = False,
) -> None:
    """Convert DICOM file(s) to a duckn Zarr v3 store.

    Parameters
    ----------
    input_path : path to a directory of single-frame .dcm files (one series)
        or a single enhanced multi-frame DICOM file.
    output_path : path for the output Zarr store (directory).
    chunks : explicit chunk shape, or None for auto-chunking (~1 MB target).
    compressor : "zstd", "gzip", or "none".
    level : compression level.
    overwrite : if True, overwrite existing store.
    anonymized : explicit override for the anonymized flag. None = auto-detect.
    tags : if True (default), extract DICOM tags into the extension.
    binary_tags : if True, include binary VR attributes (LUTs, ICC profiles)
        as base64-encoded strings. Default False to keep metadata lean.
    """
    _require_pydicom()

    input_p = Path(input_path)

    if input_p.is_dir():
        volume, geometry, datasets = _load_single_frame_series(input_p)
    elif input_p.is_file():
        volume, geometry, datasets = _load_multiframe(input_p)
    else:
        raise FileNotFoundError(f"Input path does not exist: {input_p}")

    meta = build_duckn_metadata(geometry, datasets, anonymized, tags, binary_tags)

    if chunks is None:
        if volume.ndim == 4:
            # 4D: one chunk per time point or segment, full spatial extent
            chunks = (1, volume.shape[1], volume.shape[2], volume.shape[3])
        else:
            chunks = _auto_chunks(volume.shape, volume.dtype)

    compressors_list = _build_compressors(compressor, level)

    attrs = {"duckn": meta.model_dump(exclude_none=True)}

    # Dimension names
    if volume.ndim == 4:
        ds0 = datasets[0]
        if _is_dicom_seg(ds0):
            dim_names = ["segment", "k", "j", "i"]
        else:
            dim_names = ["t", "k", "j", "i"]
    else:
        dim_names = ["k", "j", "i"]

    is_zip = _is_zip_path(output_path)
    with open_store(output_path, mode="w", overwrite=overwrite) as store:
        zarr.create_array(
            store,
            data=volume,
            chunks=chunks,
            compressors=compressors_list,
            dimension_names=dim_names,
            attributes=attrs,
            overwrite=False if is_zip else overwrite,
            fill_value=0,
        )


# ---------------------------------------------------------------------------
# Pixel data byte range
# ---------------------------------------------------------------------------

# Uncompressed transfer syntaxes (raw pixel bytes in file)
UNCOMPRESSED_TRANSFER_SYNTAXES = frozenset({
    "1.2.840.10008.1.2",        # Implicit VR Little Endian
    "1.2.840.10008.1.2.1",      # Explicit VR Little Endian
    "1.2.840.10008.1.2.1.99",   # Deflated Explicit VR Little Endian
    "1.2.840.10008.1.2.2",      # Explicit VR Big Endian (retired)
})


def get_pixel_data_range(
    file_path: str | Path,
) -> tuple[int, int, dict[str, Any]]:
    """Return the byte range of raw pixel data in an uncompressed DICOM file.

    Parameters
    ----------
    file_path : path to a single DICOM file

    Returns
    -------
    offset : byte offset where pixel data starts
    length : number of pixel data bytes
    info : dict with keys: rows, columns, bits_allocated, pixel_representation,
           transfer_syntax, file_size
    """
    _require_pydicom()
    import pydicom

    file_path = Path(file_path)

    with open(file_path, "rb") as fh:
        ds = pydicom.dcmread(fh, stop_before_pixels=True, force=True)
        tag_offset = fh.tell()

        # Read pixel data tag
        tag_bytes = fh.read(4)
        if len(tag_bytes) < 4:
            raise ValueError(f"No pixel data tag found in {file_path}")

        group = int.from_bytes(tag_bytes[:2], "little")
        elem = int.from_bytes(tag_bytes[2:4], "little")
        if (group, elem) != (0x7FE0, 0x0010):
            raise ValueError(
                f"Expected PixelData tag (7FE0,0010), got ({group:04X},{elem:04X})"
            )

        # Determine header size based on transfer syntax
        tsuid = str(getattr(ds.file_meta, "TransferSyntaxUID", ""))
        is_implicit = tsuid == "1.2.840.10008.1.2"

        if is_implicit:
            # Implicit VR: tag(4) + length(4) = 8 bytes
            length_bytes = fh.read(4)
            pixel_length = int.from_bytes(length_bytes, "little")
            pixel_offset = tag_offset + 8
        else:
            # Explicit VR: tag(4) + VR(2) + reserved(2) + length(4) = 12 bytes
            fh.read(4)  # VR + reserved
            length_bytes = fh.read(4)
            pixel_length = int.from_bytes(length_bytes, "little")
            pixel_offset = tag_offset + 12

    if tsuid not in UNCOMPRESSED_TRANSFER_SYNTAXES:
        raise ValueError(
            f"Transfer syntax {tsuid} is compressed. "
            f"get_pixel_data_range() only supports uncompressed DICOM."
        )

    file_size = file_path.stat().st_size

    # 0xFFFFFFFF means undefined length (encapsulated) — shouldn't happen
    # for uncompressed, but check anyway
    if pixel_length == 0xFFFFFFFF:
        pixel_length = file_size - pixel_offset

    info = {
        "rows": int(ds.Rows),
        "columns": int(ds.Columns),
        "bits_allocated": int(ds.BitsAllocated),
        "pixel_representation": int(ds.PixelRepresentation),
        "transfer_syntax": tsuid,
        "file_size": file_size,
    }

    return pixel_offset, pixel_length, info


# ---------------------------------------------------------------------------
# Streaming DICOM → Zarr (one chunk per slice, no full volume in memory)
# ---------------------------------------------------------------------------


def _scan_headers(
    dir_path: Path,
) -> list[tuple[Path, Any]]:
    """Scan and sort DICOM files by slice position. Returns (path, dataset) pairs."""
    import pydicom

    dcm_files = sorted(dir_path.glob("*.dcm"))
    if not dcm_files:
        dcm_files = sorted(
            p for p in dir_path.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )
    if not dcm_files:
        raise FileNotFoundError(f"No DICOM files found in {dir_path}")

    entries: list[tuple[Path, Any]] = []
    for f in dcm_files:
        try:
            ds = pydicom.dcmread(str(f), stop_before_pixels=True, force=True)
            if hasattr(ds, "ImagePositionPatient") and hasattr(ds, "Rows"):
                entries.append((f, ds))
        except Exception:
            continue

    if not entries:
        raise ValueError(f"No valid DICOM image files found in {dir_path}")

    # Validate single series
    series_uids = {str(ds.SeriesInstanceUID) for _, ds in entries if hasattr(ds, "SeriesInstanceUID")}
    if len(series_uids) > 1:
        raise ValueError(
            f"Directory contains {len(series_uids)} series. "
            "Please provide a directory with a single DICOM series."
        )

    # Validate uniform dimensions
    shapes = {(int(ds.Rows), int(ds.Columns)) for _, ds in entries}
    if len(shapes) > 1:
        raise ValueError(
            f"Slices have inconsistent dimensions: {shapes}. "
            "Filter to a single geometry before converting."
        )

    # Sort by slice position
    ds0 = entries[0][1]
    iop = [float(x) for x in ds0.ImageOrientationPatient]
    row_cos = np.array(iop[:3])
    col_cos = np.array(iop[3:])
    slice_normal = np.cross(row_cos, col_cos)
    nrm = np.linalg.norm(slice_normal)
    if nrm > 0:
        slice_normal = slice_normal / nrm

    def _proj(entry: tuple[Path, Any]) -> float:
        pos = np.array([float(x) for x in entry[1].ImagePositionPatient])
        return float(np.dot(pos, slice_normal))

    entries.sort(key=_proj)
    return entries


def geometry_from_headers(
    datasets: list[Any],
) -> DicomGeometry:
    """Compute geometry from sorted header-only datasets (no pixel data)."""
    ds0 = datasets[0]

    iop = [float(x) for x in ds0.ImageOrientationPatient]
    row_cosines = np.array(iop[:3])
    col_cosines = np.array(iop[3:])
    slice_normal = np.cross(row_cosines, col_cosines)
    nrm = np.linalg.norm(slice_normal)
    if nrm > 0:
        slice_normal = slice_normal / nrm

    ps = [float(x) for x in ds0.PixelSpacing]
    row_spacing, col_spacing = ps[0], ps[1]

    positions = [np.array([float(x) for x in ds.ImagePositionPatient]) for ds in datasets]
    space_origin = positions[0].tolist()

    if len(datasets) > 1:
        projections = [float(np.dot(p, slice_normal)) for p in positions]
        diffs = np.diff(projections)
        slice_spacing = float(np.median(diffs))
        if slice_spacing <= 0:
            slice_spacing = float(np.mean(diffs))
        slice_direction = (slice_normal * slice_spacing).tolist()

        if len(datasets) > 2 and slice_spacing != 0:
            if np.max(np.abs(diffs - slice_spacing)) > 0.01 * abs(slice_spacing):
                warnings.warn(
                    f"Non-uniform slice spacing (range: {float(np.min(diffs)):.4f} "
                    f"to {float(np.max(diffs)):.4f}, median: {slice_spacing:.4f}). "
                    f"Using median.",
                    stacklevel=3,
                )
    else:
        thickness = getattr(ds0, "SliceThickness", None)
        sbs = getattr(ds0, "SpacingBetweenSlices", None)
        slice_spacing = float(thickness) if thickness else float(sbs) if sbs else 1.0
        slice_direction = (slice_normal * slice_spacing).tolist()

    space_directions = [
        slice_direction,
        (col_cosines * row_spacing).tolist(),
        (row_cosines * col_spacing).tolist(),
    ]

    rows = int(ds0.Rows)
    cols = int(ds0.Columns)
    bits = int(ds0.BitsAllocated)
    signed = int(ds0.PixelRepresentation)
    _dtype_map = {
        (8, 0): np.uint8, (8, 1): np.int8,
        (16, 0): np.uint16, (16, 1): np.int16,
        (32, 0): np.uint32, (32, 1): np.int32,
    }
    dtype = np.dtype(_dtype_map.get((bits, signed), np.uint16))

    slice_thickness = None
    st = getattr(ds0, "SliceThickness", None)
    if st is not None:
        slice_thickness = float(st)

    rescale_slope = None
    rs = getattr(ds0, "RescaleSlope", None)
    if rs is not None:
        rescale_slope = float(rs)
    rescale_intercept = None
    ri = getattr(ds0, "RescaleIntercept", None)
    if ri is not None:
        rescale_intercept = float(ri)
    rescale_type = None
    rt = getattr(ds0, "RescaleType", None)
    if rt is not None:
        rescale_type = str(rt)

    return DicomGeometry(
        shape=(len(datasets), rows, cols),
        dtype=dtype,
        space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
        space_origin=space_origin,
        space_directions=space_directions,
        slice_thickness=slice_thickness,
        rescale_slope=rescale_slope,
        rescale_intercept=rescale_intercept,
        rescale_type=rescale_type,
    )


def dicom_to_zarr_streaming(
    input_path: str | Path,
    output_path: str | Path,
    *,
    compressor: str = "none",
    level: int = 3,
    overwrite: bool = False,
    anonymized: bool | None = None,
    tags: bool = True,
    binary_tags: bool = False,
) -> None:
    """Convert a directory of single-frame DICOM files to a duckn Zarr v3 store.

    Streams one slice at a time — never holds the full volume in memory.
    Each slice becomes one Zarr chunk with shape [1, Rows, Columns].

    For uncompressed DICOM, pixel bytes are copied directly from the file
    without going through numpy. For compressed DICOM, each slice is
    decompressed individually.

    Parameters
    ----------
    input_path : path to a directory of single-frame .dcm files (one series)
    output_path : path for the output Zarr store (directory)
    compressor : "none" (default), "zstd", or "gzip"
    level : compression level (ignored when compressor="none")
    overwrite : if True, overwrite existing store
    anonymized : explicit override for the anonymized flag
    tags : if True (default), extract DICOM tags into the extension
    binary_tags : if True, include binary VR attributes as base64
    """
    _require_pydicom()

    input_p = Path(input_path)
    output_p = Path(output_path)

    if not input_p.is_dir():
        raise ValueError(
            "dicom_to_zarr_streaming requires a directory of single-frame files. "
            "Use dicom_to_zarr() for enhanced multi-frame DICOM."
        )

    # Phase 1: scan headers, sort, compute geometry
    entries = _scan_headers(input_p)
    paths = [e[0] for e in entries]
    headers = [e[1] for e in entries]
    geometry = geometry_from_headers(headers)

    n_slices, rows, cols = geometry.shape
    chunk_shape = (1, rows, cols)

    # Build metadata
    meta = build_duckn_metadata(geometry, headers, anonymized, tags, binary_tags)
    compressors_list = _build_compressors(compressor, level)
    attrs = {"duckn": meta.model_dump(exclude_none=True)}

    # Determine if we can do raw byte copy
    tsuid = str(getattr(headers[0].file_meta, "TransferSyntaxUID", ""))
    is_uncompressed = tsuid in UNCOMPRESSED_TRANSFER_SYNTAXES
    is_big_endian = tsuid == "1.2.840.10008.1.2.2"

    if is_big_endian and geometry.dtype.itemsize > 1:
        is_uncompressed = False  # need byte-swap, can't raw copy

    use_raw_copy = is_uncompressed and compressor == "none"

    # Phase 2: create Zarr store and write chunks
    is_zip = _is_zip_path(output_p)
    if output_p.exists() and overwrite:
        if is_zip:
            import os
            os.remove(output_p)
        else:
            import shutil
            shutil.rmtree(output_p)

    with open_store(output_p, mode="w") as store:
        # Create array metadata (no data)
        zarr.create_array(
            store,
            shape=geometry.shape,
            dtype=geometry.dtype,
            chunks=chunk_shape,
            compressors=compressors_list,
            dimension_names=["k", "j", "i"],
            attributes=attrs,
            fill_value=0,
        )

        # Write each slice as a chunk
        for k, file_path in enumerate(paths):
            chunk_key = f"c/{k}/0/0"

            if use_raw_copy:
                # Direct byte copy — no numpy, no decompression
                offset, length, _ = get_pixel_data_range(file_path)
                with open(file_path, "rb") as fh:
                    fh.seek(offset)
                    raw_bytes = fh.read(length)

                if is_zip:
                    from zarr.core.sync import sync
                    sync(store.set(chunk_key, raw_bytes))
                else:
                    chunk_path = output_p / "c" / str(k) / "0" / "0"
                    chunk_path.parent.mkdir(parents=True, exist_ok=True)
                    chunk_path.write_bytes(raw_bytes)
            else:
                # Decompress via pydicom, then write through Zarr
                import pydicom
                ds = pydicom.dcmread(str(file_path))
                slice_data = ds.pixel_array.astype(geometry.dtype)

                if compressor == "none":
                    raw_bytes = slice_data.tobytes()
                    if is_zip:
                        from zarr.core.sync import sync
                        sync(store.set(chunk_key, raw_bytes))
                    else:
                        chunk_path = output_p / "c" / str(k) / "0" / "0"
                        chunk_path.parent.mkdir(parents=True, exist_ok=True)
                        chunk_path.write_bytes(raw_bytes)
                else:
                    # Let Zarr handle compression via the array API
                    arr = zarr.open_array(store, mode="r+")
                    arr[k, :, :] = slice_data


# ---------------------------------------------------------------------------
# Zarr → DICOM (Enhanced Multi-frame)
# ---------------------------------------------------------------------------

# SOP Class UIDs for Enhanced Multi-frame
_ENHANCED_SOP_CLASSES = {
    "CT": "1.2.840.10008.5.1.4.1.1.2.1",    # Enhanced CT Image Storage
    "MR": "1.2.840.10008.5.1.4.1.1.4.1",    # Enhanced MR Image Storage
    "PT": "1.2.840.10008.5.1.4.1.1.128.1",  # Enhanced PET Image Storage
}

# Dtype → (BitsAllocated, BitsStored, HighBit, PixelRepresentation)
_DTYPE_TO_PIXEL_DESC = {
    np.dtype("uint8"):  (8, 8, 7, 0),
    np.dtype("int8"):   (8, 8, 7, 1),
    np.dtype("uint16"): (16, 16, 15, 0),
    np.dtype("int16"):  (16, 16, 15, 1),
    np.dtype("uint32"): (32, 32, 31, 0),
    np.dtype("int32"):  (32, 32, 31, 1),
}

# Tags that belong in the restored dataset (skip pixel/geometry and
# tags that are set programmatically by the writer)
_ZARR_TO_DICOM_SKIP = frozenset({
    "PixelData", "OverlayData",
    "Rows", "Columns", "NumberOfFrames",
    "BitsAllocated", "BitsStored", "HighBit", "PixelRepresentation",
    "SamplesPerPixel", "PhotometricInterpretation",
    "ImageOrientationPatient", "ImagePositionPatient",
    "PixelSpacing", "SliceThickness", "SpacingBetweenSlices", "SliceLocation",
    # File meta tags restored separately
    "MediaStorageSOPClassUID", "MediaStorageSOPInstanceUID",
    "TransferSyntaxUID", "ImplementationClassUID", "ImplementationVersionName",
    "FileMetaInformationVersion", "FileMetaInformationGroupLength",
    # Enhanced MF tags set by writer
    "SOPClassUID",
})


def _restore_tag(ds: Any, keyword: str, value: Any) -> None:
    """Restore a single DICOM tag from stored JSON value."""
    import pydicom
    import pydicom.datadict as dd
    from pydicom.sequence import Sequence
    from pydicom.dataset import Dataset

    # Skip hex-coded private tags for now
    if keyword[0].isdigit():
        return

    try:
        tag = dd.tag_for_keyword(keyword)
    except (KeyError, ValueError):
        return
    if tag is None:
        return

    vr = dd.dictionary_VR(tag)

    if value is None:
        return

    # Sequence
    if vr == "SQ" and isinstance(value, list):
        items = []
        for item_dict in value:
            item_ds = Dataset()
            for k, v in item_dict.items():
                _restore_tag(item_ds, k, v)
            items.append(item_ds)
        ds.add_new(tag, vr, Sequence(items))
        return

    # Handle arrays → MultiValue where appropriate
    if isinstance(value, list):
        ds.add_new(tag, vr, value)
    else:
        ds.add_new(tag, vr, value)


def zarr_to_dicom(
    input_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> None:
    """Convert a duckn Zarr v3 store to an Enhanced Multi-frame DICOM file.

    Creates a single DICOM file with all slices as frames, using the
    Enhanced CT/MR/PET Image Storage SOP class. Geometry is reconstructed
    from duckn space_directions/space_origin. Original DICOM tags from
    the dicom extension are restored where possible.

    Parameters
    ----------
    input_path : path to the input Zarr store
    output_path : path for the output .dcm file
    overwrite : if True, overwrite existing file
    """
    _require_pydicom()
    import pydicom
    from pydicom.dataset import Dataset
    from pydicom.sequence import Sequence
    from pydicom.uid import generate_uid, ExplicitVRLittleEndian

    input_path = Path(input_path)
    output_path = Path(output_path)

    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists (use --overwrite)")

    # Read data and metadata
    with open_store(input_path, mode="r") as store:
        arr = zarr.open_array(store, mode="r")
        data = arr[:]
        duckn_attrs = arr.attrs.get("duckn", {})
        meta = DucknMetadata(**duckn_attrs)

    if data.ndim != 3:
        raise ValueError(f"Expected 3D data, got {data.ndim}D")

    n_slices, rows, cols = data.shape

    # Parse DICOM extension if present
    stored_tags: dict[str, Any] = {}
    if meta.extensions and "dicom" in meta.extensions:
        dicom_ext = meta.extensions["dicom"]
        stored_tags = dicom_ext.get("tags", {})

    # Detect modality from stored tags
    modality = stored_tags.get("Modality", "OT")

    # Decompose geometry from duckn metadata
    # space_directions are in C order: [slice, row, col]
    if meta.axes and len(meta.axes) >= 3:
        slice_dir = np.array(meta.axes[0].space_direction)
        row_dir = np.array(meta.axes[1].space_direction)
        col_dir = np.array(meta.axes[2].space_direction)
    else:
        raise ValueError("Missing spatial axis metadata")

    # duckn convention: space_direction includes spacing
    # DICOM needs: PixelSpacing (row, col magnitudes),
    # ImageOrientationPatient (unit row/col cosines),
    # slice normal direction + spacing
    row_spacing = float(np.linalg.norm(row_dir))
    col_spacing = float(np.linalg.norm(col_dir))
    slice_spacing = float(np.linalg.norm(slice_dir))

    # duckn axes[1] = col_cosines * row_spacing (row index direction)
    # duckn axes[2] = row_cosines * col_spacing (col index direction)
    col_cosines = row_dir / row_spacing if row_spacing > 0 else np.array([0, 1, 0])
    row_cosines = col_dir / col_spacing if col_spacing > 0 else np.array([1, 0, 0])
    slice_normal = slice_dir / slice_spacing if slice_spacing > 0 else np.array([0, 0, 1])

    origin = np.array(meta.space_origin) if meta.space_origin else np.zeros(3)

    # Pixel description from dtype
    pixel_desc = _DTYPE_TO_PIXEL_DESC.get(data.dtype)
    if pixel_desc is None:
        raise ValueError(f"Unsupported dtype: {data.dtype}")
    bits_alloc, bits_stored, high_bit, pixel_rep = pixel_desc

    # --- Build the Enhanced Multi-frame DICOM ---
    ds = Dataset()

    # Restore stored tags (skip geometry/pixel tags we set ourselves)
    for keyword, value in stored_tags.items():
        if keyword in _ZARR_TO_DICOM_SKIP:
            continue
        try:
            _restore_tag(ds, keyword, value)
        except Exception:
            continue

    # SOP Class
    sop_class_uid = _ENHANCED_SOP_CLASSES.get(modality, "1.2.840.10008.5.1.4.1.1.7.2")
    sop_instance_uid = generate_uid()

    ds.SOPClassUID = sop_class_uid
    ds.SOPInstanceUID = sop_instance_uid
    ds.Modality = modality

    # Image dimensions
    ds.Rows = rows
    ds.Columns = cols
    ds.NumberOfFrames = n_slices
    ds.BitsAllocated = bits_alloc
    ds.BitsStored = bits_stored
    ds.HighBit = high_bit
    ds.PixelRepresentation = pixel_rep
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"

    # Image Orientation Patient (row_cosines + col_cosines, 6 values)
    iop = row_cosines.tolist() + col_cosines.tolist()

    # Shared Functional Groups
    shared_fg = Dataset()

    # Plane Orientation
    orient_item = Dataset()
    orient_item.ImageOrientationPatient = iop
    shared_fg.PlaneOrientationSequence = Sequence([orient_item])

    # Pixel Measures
    measures_item = Dataset()
    measures_item.PixelSpacing = [row_spacing, col_spacing]
    if meta.axes[0].thickness is not None:
        measures_item.SliceThickness = meta.axes[0].thickness
    else:
        measures_item.SliceThickness = slice_spacing
    measures_item.SpacingBetweenSlices = slice_spacing
    shared_fg.PixelMeasuresSequence = Sequence([measures_item])

    ds.SharedFunctionalGroupsSequence = Sequence([shared_fg])

    # Per-Frame Functional Groups
    per_frame = []
    for k in range(n_slices):
        frame_fg = Dataset()

        # Plane Position
        pos_item = Dataset()
        frame_pos = origin + k * slice_dir
        pos_item.ImagePositionPatient = frame_pos.tolist()
        frame_fg.PlanePositionSequence = Sequence([pos_item])

        per_frame.append(frame_fg)

    ds.PerFrameFunctionalGroupsSequence = Sequence(per_frame)

    # Pixel data — frames stacked along first axis
    ds.PixelData = data.tobytes()

    # File Meta
    ds.file_meta = Dataset()
    ds.file_meta.MediaStorageSOPClassUID = sop_class_uid
    ds.file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"
    ds.file_meta.ImplementationVersionName = "duckn-0.1.0"

    ds.is_little_endian = True
    ds.is_implicit_VR = False

    pydicom.dcmwrite(str(output_path), ds, write_like_original=False)
