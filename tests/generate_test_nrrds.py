#!/usr/bin/env python3
"""Generate synthetic NRRD test files and verify round-trip conversion.

Usage:
    uv run python tests/generate_test_nrrds.py
"""

from __future__ import annotations

import math
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import nrrd

from nrrdz import nrrd_to_zarr, nrrd_to_zarr_zerocopy, zarr_to_nrrd, zarr_to_nrrd_zerocopy
from nrrdz.convert import _NRRD_SPEC_FIELDS

DATA_DIR = Path(__file__).resolve().parent / "data"
REAL_WORLD_DIR = DATA_DIR / "real-world"


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------
# Each function returns (filename, data, header, fields_to_check).
# Data is in C order.  Header per-axis fields are in NRRD order
# (fastest-varying first), which is what pynrrd expects.


def case_scalar_3d_ras():
    """Basic 3D scalar in RAS, cell centering, isotropic 1 mm, labels, origin."""
    data = np.arange(4 * 5 * 6, dtype=np.float64).reshape(4, 5, 6)
    # C shape (4,5,6) → NRRD sizes [6,5,4]
    header = {
        "space": "right-anterior-superior",
        "space origin": np.array([10.0, 20.0, 30.0]),
        "space directions": np.array(
            [[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64
        ),
        "kinds": ["domain", "domain", "domain"],
        "centerings": ["cell", "cell", "cell"],
        "space units": ["mm", "mm", "mm"],
        "labels": ["x", "y", "z"],
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "centerings", "space units", "labels",
    ]
    return "scalar_3d_ras.nrrd", data, header, fields


def case_scalar_3d_lps_aniso():
    """LPS space, anisotropic spacing (1, 1, 2.5 mm), node centering."""
    data = np.random.default_rng(42).standard_normal((4, 5, 6)).astype(np.float32)
    header = {
        "space": "left-posterior-superior",
        "space origin": np.array([0.0, 0.0, 0.0]),
        "space directions": np.array(
            [[1, 0, 0], [0, 1, 0], [0, 0, 2.5]], dtype=np.float64
        ),
        "kinds": ["domain", "domain", "domain"],
        "centerings": ["node", "node", "node"],
        "space units": ["mm", "mm", "mm"],
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "centerings", "space units",
    ]
    return "scalar_3d_lps_aniso.nrrd", data, header, fields


def case_scalar_3d_oblique():
    """Non-axis-aligned (45-degree rotated) space directions."""
    data = np.random.default_rng(43).integers(0, 1000, (4, 5, 6), dtype=np.int16)
    c = np.cos(np.pi / 4)
    s = np.sin(np.pi / 4)
    header = {
        "space": "right-anterior-superior",
        "space origin": np.array([100.0, 200.0, 300.0]),
        "space directions": np.array(
            [[c, s, 0], [-s, c, 0], [0, 0, 1.5]], dtype=np.float64
        ),
        "kinds": ["domain", "domain", "domain"],
        "centerings": ["cell", "cell", "cell"],
        "space units": ["mm", "mm", "mm"],
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "centerings", "space units",
    ]
    return "scalar_3d_oblique.nrrd", data, header, fields


def case_ct_with_transforms():
    """LPS, uint16, content field, sample_units='HU'."""
    data = np.random.default_rng(44).integers(0, 4096, (4, 5, 6), dtype=np.uint16)
    header = {
        "space": "left-posterior-superior",
        "space origin": np.array([0.0, 0.0, 0.0]),
        "space directions": np.array(
            [[1, 0, 0], [0, 1, 0], [0, 0, 2]], dtype=np.float64
        ),
        "kinds": ["domain", "domain", "domain"],
        "centerings": ["cell", "cell", "cell"],
        "space units": ["mm", "mm", "mm"],
        "content": "CT scan",
        "sample units": "HU",
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "centerings", "space units",
        "content", "sample units",
    ]
    return "ct_with_transforms.nrrd", data, header, fields


def case_tensor_4d():
    """3 spatial + 3D-symmetric-matrix (size 6)."""
    # C shape (3,4,5,6):  C0=z(3), C1=y(4), C2=x(5), C3=tensor(6)
    # NRRD order: [tensor(6), x(5), y(4), z(3)]
    data = np.random.default_rng(45).standard_normal((3, 4, 5, 6)).astype(np.float32)
    nan3 = [np.nan, np.nan, np.nan]
    header = {
        "space": "right-anterior-superior",
        "space origin": np.array([0.0, 0.0, 0.0]),
        "space directions": np.array(
            [nan3, [1, 0, 0], [0, 1, 0], [0, 0, 1]]
        ),
        "kinds": ["3D-symmetric-matrix", "domain", "domain", "domain"],
        "centerings": ["???", "cell", "cell", "cell"],
        "space units": ["mm", "mm", "mm"],
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "centerings", "space units",
    ]
    return "tensor_4d.nrrd", data, header, fields


def case_rgba_image():
    """2D spatial + RGBA-color (size 4), no space metadata."""
    # C shape (8,10,4):  C0=y(8), C1=x(10), C2=rgba(4)
    # NRRD order: [rgba(4), x(10), y(8)]
    data = np.random.default_rng(46).integers(0, 256, (8, 10, 4), dtype=np.uint8)
    header = {
        "kinds": ["RGBA-color", "domain", "domain"],
        "encoding": "gzip",
    }
    fields = ["kinds"]
    return "rgba_image.nrrd", data, header, fields


def case_time_series_4d():
    """3 spatial + time axis with labels."""
    # C shape (3,4,5,6):  C0=t(3), C1=z(4), C2=y(5), C3=x(6)
    # NRRD order: [x(6), y(5), z(4), t(3)]
    data = np.random.default_rng(47).standard_normal((3, 4, 5, 6)).astype(np.float32)
    nan3 = [np.nan, np.nan, np.nan]
    header = {
        "space": "right-anterior-superior",
        "space origin": np.array([0.0, 0.0, 0.0]),
        "space directions": np.array(
            [[1, 0, 0], [0, 1, 0], [0, 0, 1], nan3]
        ),
        "kinds": ["domain", "domain", "domain", "time"],
        "centerings": ["cell", "cell", "cell", "???"],
        "space units": ["mm", "mm", "mm"],
        "labels": ["x", "y", "z", "t"],
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "centerings", "space units", "labels",
    ]
    return "time_series_4d.nrrd", data, header, fields


def case_vector_field_4d():
    """3 spatial + 3-vector (size 3)."""
    # C shape (4,5,6,3):  C0=z(4), C1=y(5), C2=x(6), C3=vec(3)
    # NRRD order: [vec(3), x(6), y(5), z(4)]
    data = np.random.default_rng(48).standard_normal((4, 5, 6, 3)).astype(np.float64)
    nan3 = [np.nan, np.nan, np.nan]
    header = {
        "space": "right-anterior-superior",
        "space origin": np.array([0.0, 0.0, 0.0]),
        "space directions": np.array(
            [nan3, [1, 0, 0], [0, 1, 0], [0, 0, 1]]
        ),
        "kinds": ["3-vector", "domain", "domain", "domain"],
        "space units": ["mm", "mm", "mm"],
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "space units",
    ]
    return "vector_field_4d.nrrd", data, header, fields


def case_measurement_frame():
    """RAS with non-identity measurement frame (30-degree rotation)."""
    data = np.random.default_rng(49).standard_normal((4, 5, 6)).astype(np.float64)
    c30 = np.cos(np.pi / 6)
    s30 = np.sin(np.pi / 6)
    mf = np.array([[c30, -s30, 0], [s30, c30, 0], [0, 0, 1]])
    header = {
        "space": "right-anterior-superior",
        "space origin": np.array([0.0, 0.0, 0.0]),
        "space directions": np.array(
            [[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64
        ),
        "kinds": ["domain", "domain", "domain"],
        "centerings": ["cell", "cell", "cell"],
        "space units": ["mm", "mm", "mm"],
        "measurement frame": mf,
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "centerings", "space units", "measurement frame",
    ]
    return "measurement_frame.nrrd", data, header, fields


def case_thickness():
    """Spatial axes with thickness different from spacing."""
    data = np.arange(4 * 5 * 6, dtype=np.float32).reshape(4, 5, 6)
    header = {
        "space": "right-anterior-superior",
        "space origin": np.array([0.0, 0.0, 0.0]),
        "space directions": np.array(
            [[1, 0, 0], [0, 1, 0], [0, 0, 2]], dtype=np.float64
        ),
        "kinds": ["domain", "domain", "domain"],
        "centerings": ["cell", "cell", "cell"],
        "space units": ["mm", "mm", "mm"],
        "thicknesses": [0.8, 0.8, 1.5],
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "centerings", "space units", "thicknesses",
    ]
    return "thickness.nrrd", data, header, fields


def case_minimal():
    """No space, no origin, no kinds, no labels — bare-minimum NRRD."""
    data = np.arange(24, dtype=np.int32).reshape(2, 3, 4)
    header = {"encoding": "gzip"}
    fields: list[str] = []
    return "minimal.nrrd", data, header, fields


def case_mixed_centering():
    """Different centerings per axis (cell, node, cell) in NRRD order."""
    data = np.arange(4 * 5 * 6, dtype=np.float64).reshape(4, 5, 6)
    header = {
        "space": "right-anterior-superior",
        "space origin": np.array([0.0, 0.0, 0.0]),
        "space directions": np.array(
            [[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64
        ),
        "kinds": ["domain", "domain", "domain"],
        "centerings": ["cell", "node", "cell"],
        "space units": ["mm", "mm", "mm"],
        "encoding": "gzip",
    }
    fields = [
        "space", "space origin", "space directions",
        "kinds", "centerings", "space units",
    ]
    return "mixed_centering.nrrd", data, header, fields


def case_keyvalues():
    """Custom key/value pairs (`:=` lines) round-trip via extensions.keyvalues."""
    data = np.arange(4 * 5 * 6, dtype=np.float32).reshape(4, 5, 6)
    header = {
        "space": "left-posterior-superior",
        "space origin": np.array([0.0, 0.0, 0.0]),
        "space directions": np.array(
            [[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64
        ),
        "kinds": ["domain", "domain", "domain"],
        "encoding": "gzip",
        # These are key/value pairs — not NRRD spec fields
        "modality": "DWMRI",
        "DWMRI_b-value": "1000",
        "DWMRI_gradient_0000": "0 0 0",
        "DWMRI_gradient_0001": "1 0 0",
        "my_custom_field": "hello world",
    }
    fields = [
        "space", "space origin", "space directions", "kinds",
        "modality", "DWMRI_b-value",
        "DWMRI_gradient_0000", "DWMRI_gradient_0001",
        "my_custom_field",
    ]
    return "keyvalues.nrrd", data, header, fields


ALL_CASES = [
    case_scalar_3d_ras,
    case_scalar_3d_lps_aniso,
    case_scalar_3d_oblique,
    case_ct_with_transforms,
    case_tensor_4d,
    case_rgba_image,
    case_time_series_4d,
    case_vector_field_4d,
    case_measurement_frame,
    case_thickness,
    case_minimal,
    case_mixed_centering,
    case_keyvalues,
]

# Fields the converter is expected to round-trip for real-world files.
_ROUND_TRIP_FIELDS = [
    "space", "space dimension", "space origin", "space directions",
    "kinds", "centerings", "space units", "labels",
    "measurement frame", "thicknesses", "content", "sample units",
]


# ---------------------------------------------------------------------------
# Round-trip helpers
# ---------------------------------------------------------------------------


def round_trip(orig_path: Path, tmp: Path) -> Path:
    """NRRD -> Zarr -> NRRD round trip.  Returns path to round-tripped NRRD."""
    zarr_path = tmp / (orig_path.stem + ".zarr")
    rt_path = tmp / (orig_path.stem + "_rt.nrrd")
    if zarr_path.exists():
        shutil.rmtree(zarr_path)
    if rt_path.exists():
        rt_path.unlink()
    nrrd_to_zarr(orig_path, zarr_path, overwrite=True)
    zarr_to_nrrd(zarr_path, rt_path, overwrite=True)
    return rt_path


def round_trip_zerocopy(orig_path: Path, tmp: Path) -> Path:
    """Zero-copy NRRD -> Zarr -> NRRD round trip.  Returns path to round-tripped NRRD."""
    zarr_path = tmp / (orig_path.stem + "_zc.zarr")
    rt_path = tmp / (orig_path.stem + "_zc_rt.nrrd")
    if zarr_path.exists():
        shutil.rmtree(zarr_path)
    if rt_path.exists():
        rt_path.unlink()
    nrrd_to_zarr_zerocopy(orig_path, zarr_path, overwrite=True)
    zarr_to_nrrd_zerocopy(zarr_path, rt_path, overwrite=True)
    return rt_path


def round_trip_headers(orig_path: Path, tmp: Path) -> dict[str, Any]:
    """Header-only round trip: parse NRRD header, build nrrdz metadata,
    reconstruct NRRD header — without touching data.  Returns the
    reconstructed header dict."""
    from nrrdz.convert import (
        _NRRD_SPEC_FIELDS,
        _clean_float_list,
        _is_nan_vector,
        _transpose_matrix,
    )
    from nrrdz.models import NrrdMetadata, SpaceName, _SPACE_ABBREVS, _SPACE_DIMENSIONS

    header = nrrd.read_header(str(orig_path))
    ndim = header["dimension"]

    # ---- replicate nrrd_to_zarr header logic (no data) ----
    def _rev(arr):
        if arr is None:
            return None
        return list(reversed(arr))

    kinds = _rev(header.get("kinds"))
    centerings = _rev(header.get("centerings"))
    space_dirs = _rev(header.get("space directions"))
    thicknesses = _rev(header.get("thicknesses"))
    labels = _rev(header.get("labels"))
    units = _rev(header.get("units"))
    space_units_raw = header.get("space units")

    space_name_raw = header.get("space")
    space_dim_raw = header.get("space dimension")
    space_origin_raw = header.get("space origin")
    mf_raw = header.get("measurement frame")

    space_name = None
    space_dimension = None
    if space_name_raw:
        normalized = _SPACE_ABBREVS.get(space_name_raw, space_name_raw)
        space_name = SpaceName(normalized)
    elif space_dim_raw:
        space_dimension = int(space_dim_raw)

    spatial_axis_indices = []
    if space_dirs is not None:
        for i in range(ndim):
            if not _is_nan_vector(space_dirs[i]):
                spatial_axis_indices.append(i)

    from nrrdz.models import AxisMetadata
    axes = []
    spatial_count = 0
    for i in range(ndim):
        ax_kwargs: dict[str, Any] = {}
        if kinds is not None and i < len(kinds):
            k = kinds[i]
            if k and k != "???" and k.lower() != "none":
                ax_kwargs["kind"] = k
        if centerings is not None and i < len(centerings):
            c = centerings[i]
            if c and c != "???" and c.lower() != "none":
                ax_kwargs["centering"] = c
        is_spatial = i in spatial_axis_indices
        if space_dirs is not None and i < len(space_dirs) and is_spatial:
            ax_kwargs["space_direction"] = _clean_float_list(space_dirs[i])
        if thicknesses is not None and i < len(thicknesses):
            t = thicknesses[i]
            if t is not None and not (isinstance(t, float) and math.isnan(t)):
                ax_kwargs["thickness"] = float(t)
        if is_spatial and space_units_raw is not None:
            if spatial_count < len(space_units_raw):
                su = space_units_raw[spatial_count]
                if su and su != "???":
                    ax_kwargs["unit"] = su
            spatial_count += 1
        elif units is not None and i < len(units):
            u = units[i]
            if u and u != "???":
                ax_kwargs["unit"] = u
        axes.append(AxisMetadata(**ax_kwargs))

    meta_kwargs: dict[str, Any] = {"version": "1.0", "axes": axes}
    if space_name:
        meta_kwargs["space"] = space_name
    elif space_dimension:
        meta_kwargs["space_dimension"] = space_dimension
    if space_origin_raw is not None:
        meta_kwargs["space_origin"] = _clean_float_list(space_origin_raw)
    if mf_raw is not None:
        mf_list = [_clean_float_list(row) for row in mf_raw]
        meta_kwargs["measurement_frame"] = _transpose_matrix(mf_list)
    sample_units_raw = header.get("sample units")
    if sample_units_raw:
        meta_kwargs["sample_units"] = sample_units_raw

    keyvalues: dict[str, str] = {}
    for k, v in header.items():
        if k not in _NRRD_SPEC_FIELDS:
            keyvalues[k] = str(v)
    if keyvalues:
        meta_kwargs["extensions"] = {"keyvalues": keyvalues}

    meta = NrrdMetadata(**meta_kwargs)

    # ---- replicate zarr_to_nrrd header reconstruction ----
    out: dict[str, Any] = {}
    if meta.space is not None:
        out["space"] = meta.space.value
    elif meta.space_dimension is not None:
        out["space dimension"] = meta.space_dimension
    if meta.space_origin is not None:
        out["space origin"] = np.array(meta.space_origin)
    if meta.measurement_frame is not None:
        mf_rows = _transpose_matrix(meta.measurement_frame)
        out["measurement frame"] = np.array(mf_rows)

    nrrd_axes = list(reversed(meta.axes or []))
    if nrrd_axes:
        kinds_out = [ax.kind if ax.kind else "???" for ax in nrrd_axes]
        if any(k != "???" for k in kinds_out):
            out["kinds"] = kinds_out
        centerings_out = [ax.centering if ax.centering else "???" for ax in nrrd_axes]
        if any(c != "???" for c in centerings_out):
            out["centerings"] = centerings_out
        space_dim = meta._get_space_dim()
        has_any_dir = any(ax.space_direction is not None for ax in nrrd_axes)
        if has_any_dir and space_dim is not None:
            dirs_out = []
            for ax in nrrd_axes:
                if ax.space_direction is not None:
                    dirs_out.append(np.array(ax.space_direction))
                else:
                    dirs_out.append(np.full(space_dim, np.nan))
            out["space directions"] = np.array(dirs_out)
        thicknesses_out = []
        has_thickness = False
        for ax in nrrd_axes:
            if ax.thickness is not None:
                thicknesses_out.append(ax.thickness)
                has_thickness = True
            else:
                thicknesses_out.append(np.nan)
        if has_thickness:
            out["thicknesses"] = thicknesses_out
        if space_dim is not None:
            spatial_units_out: list[str] = []
            for ax in nrrd_axes:
                if ax.space_direction is not None:
                    u = ax.unit
                    if isinstance(u, str):
                        spatial_units_out.append(u)
                    elif u is not None:
                        spatial_units_out.append(u.symbol)
                    else:
                        spatial_units_out.append("")
            if any(spatial_units_out):
                out["space units"] = spatial_units_out

    # labels via dimension_names path
    if labels:
        clean_labels = [lb if lb and lb != "???" else "" for lb in labels]
        if any(clean_labels):
            nrrd_labels = list(reversed(clean_labels))
            out["labels"] = nrrd_labels

    content = header.get("content")
    if content:
        out["content"] = content
    if meta.sample_units is not None:
        if isinstance(meta.sample_units, str):
            out["sample units"] = meta.sample_units
        else:
            out["sample units"] = meta.sample_units.symbol

    # keyvalues
    if meta.extensions and "keyvalues" in meta.extensions:
        for k, v in meta.extensions["keyvalues"].items():
            out[k] = v

    return out


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def _norm_str(v: Any) -> str:
    """Normalize a per-axis string value: treat '???', '', and None as empty."""
    if v is None or str(v) in ("???", "", "none"):
        return ""
    return str(v)


def _compare_field(name: str, val_orig: Any, val_rt: Any) -> str | None:
    """Compare a single header field.  Returns an error message or None."""
    if val_orig is None and val_rt is None:
        return None
    if val_orig is None:
        return f"  {name}: missing in original, present in round-trip"
    if val_rt is None:
        return f"  {name}: present in original, missing in round-trip"

    # Numeric / ndarray fields
    if isinstance(val_orig, np.ndarray) or isinstance(val_rt, np.ndarray):
        try:
            a = np.asarray(val_orig, dtype=np.float64)
            b = np.asarray(val_rt, dtype=np.float64)
            if a.shape != b.shape:
                return f"  {name}: shape {a.shape} vs {b.shape}"
            if not np.allclose(a, b, equal_nan=True, atol=1e-10):
                return f"  {name}: values differ\n    orig: {a}\n    rt:   {b}"
            return None
        except (ValueError, TypeError):
            pass

    # Lists (kinds, centerings, labels, space units, thicknesses)
    if isinstance(val_orig, list) and isinstance(val_rt, list):
        # Try numeric comparison first (thicknesses, space directions)
        try:
            a = np.asarray(val_orig, dtype=np.float64)
            b = np.asarray(val_rt, dtype=np.float64)
            if a.shape == b.shape and np.allclose(a, b, equal_nan=True, atol=1e-10):
                return None
        except (ValueError, TypeError):
            pass

        # String list comparison with normalization
        if len(val_orig) != len(val_rt):
            return f"  {name}: length {len(val_orig)} vs {len(val_rt)}"
        if [_norm_str(x) for x in val_orig] != [_norm_str(x) for x in val_rt]:
            return (
                f"  {name}: list mismatch\n"
                f"    orig: {val_orig}\n"
                f"    rt:   {val_rt}"
            )
        return None

    # Scalar string / int comparison
    if str(val_orig) != str(val_rt):
        return f"  {name}: {val_orig!r} vs {val_rt!r}"
    return None


def compare_nrrds(
    orig_path: Path, rt_path: Path, fields: list[str]
) -> list[str]:
    """Compare original and round-tripped NRRDs.  Returns list of errors."""
    data_orig, header_orig = nrrd.read(str(orig_path), index_order="C")
    data_rt, header_rt = nrrd.read(str(rt_path), index_order="C")

    errors: list[str] = []

    # Data
    if data_orig.shape != data_rt.shape:
        errors.append(f"  data shape: {data_orig.shape} vs {data_rt.shape}")
    elif data_orig.dtype != data_rt.dtype:
        errors.append(f"  data dtype: {data_orig.dtype} vs {data_rt.dtype}")
    elif not np.array_equal(data_orig, data_rt):
        diff = np.max(np.abs(data_orig.astype(np.float64) - data_rt.astype(np.float64)))
        errors.append(f"  data values differ (max diff: {diff})")

    # Header fields
    for field in fields:
        err = _compare_field(field, header_orig.get(field), header_rt.get(field))
        if err:
            errors.append(err)

    return errors


def compare_headers(
    orig_path: Path, rt_header: dict[str, Any], fields: list[str]
) -> list[str]:
    """Compare original NRRD header against a reconstructed header dict."""
    header_orig = nrrd.read_header(str(orig_path))
    errors: list[str] = []
    for field in fields:
        err = _compare_field(field, header_orig.get(field), rt_header.get(field))
        if err:
            errors.append(err)
    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _discover_real_world_files() -> list[Path]:
    """Find .nrrd files in the real-world data directory."""
    if not REAL_WORLD_DIR.is_dir():
        return []
    return sorted(REAL_WORLD_DIR.glob("*.nrrd"))


def main() -> int:
    headers_only = "--headers-only" in sys.argv

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="nrrdz_test_"))

    passed = 0
    failed = 0

    # --- Synthetic tests ---
    print("--- Synthetic tests ---")
    for case_fn in ALL_CASES:
        name, data, header, fields = case_fn()
        path = DATA_DIR / name

        # Write the synthetic NRRD
        nrrd.write(str(path), data, header, index_order="C")

        # Round-trip through Zarr
        try:
            rt_path = round_trip(path, tmp)
        except Exception as e:
            print(f"FAIL  {name}: round-trip error: {e}")
            failed += 1
            continue

        # Compare original and round-tripped
        errors = compare_nrrds(path, rt_path, fields)
        if errors:
            print(f"FAIL  {name}:")
            for err in errors:
                print(err)
            failed += 1
        else:
            print(f"PASS  {name}")
            passed += 1

    # --- Zero-copy synthetic tests ---
    # All synthetic cases use gzip encoding, so zero-copy should work for all.
    print("\n--- Zero-copy synthetic tests ---")
    for case_fn in ALL_CASES:
        name, data, header, fields = case_fn()
        path = DATA_DIR / name
        zc_name = name.replace(".nrrd", " [zero-copy]")

        try:
            rt_path = round_trip_zerocopy(path, tmp)
        except Exception as e:
            print(f"FAIL  {zc_name}: zero-copy round-trip error: {e}")
            failed += 1
            continue

        errors = compare_nrrds(path, rt_path, fields)
        if errors:
            print(f"FAIL  {zc_name}:")
            for err in errors:
                print(err)
            failed += 1
        else:
            print(f"PASS  {zc_name}")
            passed += 1

    # --- Zero-copy byte-for-byte verification ---
    # For gzip files, verify the chunk file bytes match the NRRD data section.
    print("\n--- Zero-copy byte-for-byte verification ---")
    for case_fn in ALL_CASES:
        name, data, header, fields = case_fn()
        path = DATA_DIR / name
        bb_name = name.replace(".nrrd", " [byte-exact]")

        zarr_path = tmp / (path.stem + "_bb.zarr")
        if zarr_path.exists():
            shutil.rmtree(zarr_path)

        try:
            nrrd_to_zarr_zerocopy(path, zarr_path, overwrite=True)
        except Exception as e:
            print(f"FAIL  {bb_name}: error: {e}")
            failed += 1
            continue

        # Read the original NRRD data section
        with open(path, "rb") as fh:
            nrrd.read_header(fh)
            orig_blob = fh.read()

        # Read the chunk file
        ndim = len(header.get("sizes", data.shape))
        chunk_path = zarr_path / "c"
        for _ in range(data.ndim):
            chunk_path = chunk_path / "0"

        if not chunk_path.exists():
            print(f"FAIL  {bb_name}: chunk file not found")
            failed += 1
            continue

        chunk_blob = chunk_path.read_bytes()

        if orig_blob == chunk_blob:
            print(f"PASS  {bb_name}")
            passed += 1
        else:
            print(f"FAIL  {bb_name}: blobs differ (orig={len(orig_blob)}, chunk={len(chunk_blob)})")
            failed += 1

    # --- Real-world tests (zero-copy, both paths) ---
    real_files = _discover_real_world_files()
    if real_files:
        print(f"\n--- Real-world tests ({len(real_files)} files, zero-copy) ---")
    for path in real_files:
        name = path.name
        mb = path.stat().st_size / 1e6

        # Check if encoding supports zero-copy
        header = nrrd.read_header(str(path))
        enc = header.get("encoding", "raw").lower().strip()
        if enc not in ("raw", "gzip", "gz"):
            print(f"      {name} ({mb:.0f} MB) ... SKIP (encoding={enc})")
            continue

        endian = header.get("endian", "little").lower().strip()
        dtype_size = np.dtype(header.get("type", "uint8")).itemsize
        if dtype_size > 1 and endian != "little":
            print(f"      {name} ({mb:.0f} MB) ... SKIP (endian={endian})")
            continue

        print(f"      {name} ({mb:.0f} MB) ...", end="", flush=True)

        fields = [f for f in _ROUND_TRIP_FIELDS if f in header]
        for k in header:
            if k not in _NRRD_SPEC_FIELDS and k not in fields:
                fields.append(k)

        t0 = time.monotonic()
        try:
            rt_path = round_trip_zerocopy(path, tmp)
            errors = compare_nrrds(path, rt_path, fields)
        except Exception as e:
            print(f"\nFAIL  {name}: error: {e}")
            failed += 1
            continue

        elapsed = time.monotonic() - t0
        if errors:
            print(f"\nFAIL  {name} ({elapsed:.1f}s):")
            for err in errors:
                print(err)
            failed += 1
        else:
            print(f" PASS ({elapsed:.1f}s)")
            passed += 1

    # Clean up temp directory
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{passed} passed, {failed} failed out of {passed + failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
