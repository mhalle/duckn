"""DICOM to nrrdz Zarr v3 conversion.

Reads DICOM files (single-frame series directory or enhanced multi-frame)
and writes a nrrdz Zarr v3 store with the DICOM provenance extension.

Requires pydicom: install with ``pip install nrrdz[dicom]``.
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
    DicomExtension,
    NrrdMetadata,
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
            "Install it with: pip install nrrdz[dicom]"
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

# VRs whose values are binary blobs — skip entirely
_BINARY_VRS = frozenset({"OB", "OW", "OF", "OD", "OL", "OV", "UN"})

# Tags to skip: pixel data and geometry captured by convention fields
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
) -> dict[str, Any]:
    """Convert a pydicom Dataset to a tags dict per dicom-spec.md §4."""
    tags: dict[str, Any] = {}

    for elem in ds:
        # Skip group length tags (xxxx,0000)
        if elem.tag.element == 0x0000:
            continue
        # Skip File Meta Information (group 0002)
        if elem.tag.group == 0x0002:
            continue
        # Skip binary VRs
        if elem.VR in _BINARY_VRS:
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

    # Stack pixel data
    try:
        slices = [ds.pixel_array for ds in datasets]
    except Exception as e:
        raise RuntimeError(
            f"Failed to read pixel data: {e}. "
            "For compressed DICOM, you may need: pip install pylibjpeg pylibjpeg-libjpeg"
        ) from e
    volume = np.stack(slices, axis=0).astype(dtype)

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
# Enhanced multi-frame loader
# ---------------------------------------------------------------------------


def _load_multiframe(
    file_path: Path,
) -> tuple[np.ndarray, DicomGeometry, list[Any]]:
    """Load an enhanced multi-frame DICOM file."""
    import pydicom

    ds = pydicom.dcmread(str(file_path))

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

    # Build per-frame pseudo-datasets for sorting
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

        frame_infos.append(info)

    # Validate we have positions
    if not all("ImagePositionPatient" in fi for fi in frame_infos):
        raise ValueError("Not all frames have ImagePositionPatient")
    if not any("ImageOrientationPatient" in fi for fi in frame_infos):
        raise ValueError("No ImageOrientationPatient found in frame or shared groups")

    # Sort frames by slice position
    iop = frame_infos[0]["ImageOrientationPatient"]
    row_cos = np.array(iop[:3])
    col_cos = np.array(iop[3:])
    slice_normal = np.cross(row_cos, col_cos)
    nrm = np.linalg.norm(slice_normal)
    if nrm > 0:
        slice_normal = slice_normal / nrm

    frame_infos.sort(
        key=lambda fi: float(np.dot(np.array(fi["ImagePositionPatient"]), slice_normal))
    )
    sorted_indices = [fi["_frame_index"] for fi in frame_infos]

    # Build a pseudo-dataset for geometry computation
    # Use first sorted frame's geometry
    fi0 = frame_infos[0]

    # Create a lightweight object that _compute_geometry can use
    class _FrameProxy:
        pass

    proxies = []
    pixel_array_full = ds.pixel_array  # shape: (n_frames, Rows, Columns)

    for fi in frame_infos:
        proxy = _FrameProxy()
        proxy.ImageOrientationPatient = fi["ImageOrientationPatient"]  # type: ignore[attr-defined]
        proxy.PixelSpacing = fi["PixelSpacing"]  # type: ignore[attr-defined]
        proxy.ImagePositionPatient = fi["ImagePositionPatient"]  # type: ignore[attr-defined]
        proxy.BitsAllocated = ds.BitsAllocated  # type: ignore[attr-defined]
        proxy.PixelRepresentation = ds.PixelRepresentation  # type: ignore[attr-defined]
        # Provide a pixel_array property that returns the correct frame
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
# Metadata building
# ---------------------------------------------------------------------------


def _build_nrrd_metadata(
    geometry: DicomGeometry,
    datasets: list[Any],
    anonymized: bool | None,
    include_tags: bool,
) -> NrrdMetadata:
    """Build NrrdMetadata from geometry and DICOM datasets."""
    # Axes in C order: [slice, row, col]
    axes = []
    for i, direction in enumerate(geometry.space_directions):
        ax_kwargs: dict[str, Any] = {
            "kind": AxisKind.SPACE,
            "centering": Centering.CELL,
            "space_direction": direction,
            "unit": "mm",
        }
        if i == 0 and geometry.slice_thickness is not None:
            ax_kwargs["thickness"] = geometry.slice_thickness
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

    # DICOM extension
    extensions = None
    if include_tags:
        ds0 = datasets[0]
        dicom_tags = _dataset_to_tags(ds0)

        anon = anonymized
        if anon is None:
            anon = _detect_anonymized(ds0)

        dicom_ext = DicomExtension(
            version="1.0",
            anonymized=anon if anon else None,
            source_transfer_syntax=_get_transfer_syntax(ds0),
            tags=dicom_tags if dicom_tags else None,
        )
        extensions = {"dicom": dicom_ext.model_dump(exclude_none=True, by_alias=True)}

    return NrrdMetadata(
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
) -> None:
    """Convert DICOM file(s) to a nrrdz Zarr v3 store.

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
    """
    _require_pydicom()

    input_p = Path(input_path)

    if input_p.is_dir():
        volume, geometry, datasets = _load_single_frame_series(input_p)
    elif input_p.is_file():
        volume, geometry, datasets = _load_multiframe(input_p)
    else:
        raise FileNotFoundError(f"Input path does not exist: {input_p}")

    meta = _build_nrrd_metadata(geometry, datasets, anonymized, tags)

    if chunks is None:
        chunks = _auto_chunks(volume.shape, volume.dtype)

    compressors_list = _build_compressors(compressor, level)

    attrs = {"nrrd": meta.model_dump(exclude_none=True)}

    is_zip = _is_zip_path(output_path)
    with open_store(output_path, mode="w", overwrite=overwrite) as store:
        zarr.create_array(
            store,
            data=volume,
            chunks=chunks,
            compressors=compressors_list,
            dimension_names=["k", "j", "i"],
            attributes=attrs,
            overwrite=False if is_zip else overwrite,
            fill_value=0,
        )
