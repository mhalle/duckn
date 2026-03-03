"""Bidirectional conversion between NIfTI files and nrrdz Zarr stores.

Reads NIfTI-1/NIfTI-2 files via nibabel and writes nrrdz Zarr v3 stores
with the NIfTI provenance extension. Also converts back from Zarr to NIfTI.

Requires nibabel: install with ``pip install nrrdz[nifti]``.
"""

from __future__ import annotations

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
    NiftiCal,
    NiftiDimInfo,
    NiftiExtension,
    NiftiIntent,
    NiftiLegacy,
    NiftiLegacyTags,
    NiftiSliceTiming,
    NiftiTags,
    NrrdMetadata,
    SpaceName,
    ValueTransform,
)


# ---------------------------------------------------------------------------
# nibabel lazy guard
# ---------------------------------------------------------------------------


def _require_nibabel() -> None:
    """Raise a helpful error if nibabel is not installed."""
    try:
        import nibabel  # noqa: F401
    except ImportError:
        raise ImportError(
            "nibabel is required for NIfTI conversion. "
            "Install it with: pip install nrrdz[nifti]"
        ) from None


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

# sform_code → space name
_SFORM_CODE_TO_SPACE: dict[int, SpaceName] = {
    1: SpaceName.SCANNER_XYZ,
    2: SpaceName.RIGHT_ANTERIOR_SUPERIOR,
    3: SpaceName.RIGHT_ANTERIOR_SUPERIOR,
    4: SpaceName.RIGHT_ANTERIOR_SUPERIOR,
}

# space name → default sform_code (for writing back)
_SPACE_TO_SFORM_CODE: dict[str, int] = {
    "scanner-xyz": 1,
    "right-anterior-superior": 2,
}

# NIfTI spatial unit codes → unit strings
_NIFTI_SPATIAL_UNITS: dict[int, str] = {
    1: "m",
    2: "mm",
    3: "um",
}

# NIfTI temporal unit codes → unit strings
_NIFTI_TEMPORAL_UNITS: dict[int, str] = {
    8: "s",
    16: "ms",
    24: "us",
}

# Reverse mappings for writing
_UNIT_TO_SPATIAL_CODE: dict[str, int] = {v: k for k, v in _NIFTI_SPATIAL_UNITS.items()}
_UNIT_TO_TEMPORAL_CODE: dict[str, int] = {v: k for k, v in _NIFTI_TEMPORAL_UNITS.items()}

# NIfTI slice_code → extension string
_SLICE_CODE_TO_STR: dict[int, str] = {
    1: "sequential-increasing",
    2: "sequential-decreasing",
    3: "alternating-increasing",
    4: "alternating-decreasing",
    5: "alternating-increasing-2",
    6: "alternating-decreasing-2",
}

_STR_TO_SLICE_CODE: dict[str, int] = {v: k for k, v in _SLICE_CODE_TO_STR.items()}

# NIfTI intent codes → convention-level intent strings
_INTENT_CODE_TO_CONVENTION: dict[int, str] = {
    2: "statistical-map",
    3: "statistical-map",
    4: "statistical-map",
    5: "statistical-map",
    1001: "statistical-map",
    1002: "label-map",
    1005: "diffusion-tensor",
    1006: "displacement-field",
}


# ---------------------------------------------------------------------------
# NIfTI → Zarr
# ---------------------------------------------------------------------------


def nifti_to_zarr(
    input_path: str | Path,
    output_path: str | Path,
    *,
    chunks: tuple[int, ...] | None = None,
    compressor: str = "zstd",
    level: int = 3,
    overwrite: bool = False,
) -> None:
    """Convert a NIfTI file to a nrrdz Zarr v3 store.

    Parameters
    ----------
    input_path : path to the input .nii or .nii.gz file
    output_path : path for the output Zarr store (directory)
    chunks : explicit chunk shape, or None for auto-chunking
    compressor : "zstd", "gzip", or "none"
    level : compression level
    overwrite : if True, overwrite existing store
    """
    _require_nibabel()
    import nibabel as nib

    input_path = Path(input_path)
    output_path = Path(output_path)

    img = nib.load(str(input_path))
    hdr = img.header

    # Read raw header directly from file for fields nibabel sanitizes
    # (e.g., scl_slope/scl_inter are reset to NaN by nibabel's image loading)
    if str(input_path).endswith(".gz"):
        import gzip

        with gzip.open(str(input_path), "rb") as fh:
            raw_hdr = type(hdr).from_fileobj(fh)
    else:
        with open(str(input_path), "rb") as fh:
            raw_hdr = type(hdr).from_fileobj(fh)

    # Detect NIfTI version
    is_nifti2 = isinstance(img, nib.Nifti2Image)
    nifti_version = 2 if is_nifti2 else 1

    # Raw stored values (NOT get_fdata which applies scaling)
    data = img.dataobj.get_unscaled()

    ndim = data.ndim
    shape = data.shape

    # --- Affine decomposition ---
    # Prefer sform; fall back to qform
    sform_code = int(hdr["sform_code"])
    qform_code = int(hdr["qform_code"])

    if sform_code > 0:
        affine = img.get_sform()
        active_code = sform_code
    elif qform_code > 0:
        affine = img.get_qform()
        active_code = qform_code
    else:
        affine = img.affine
        active_code = 0

    # space_origin = translation column
    space_origin = affine[:3, 3].tolist()

    # space_directions = rotation-scaling columns
    space_directions: list[list[float]] = []
    for i in range(min(3, ndim)):
        space_directions.append(affine[:3, i].tolist())

    # Map code → space name
    space = _SFORM_CODE_TO_SPACE.get(active_code, SpaceName.RIGHT_ANTERIOR_SUPERIOR)

    # --- Units from xyzt_units ---
    xyzt_units = int(hdr["xyzt_units"])
    spatial_unit_code = xyzt_units & 0x07
    temporal_unit_code = xyzt_units & 0x38
    spatial_unit = _NIFTI_SPATIAL_UNITS.get(spatial_unit_code, "mm")
    temporal_unit = _NIFTI_TEMPORAL_UNITS.get(temporal_unit_code)

    # --- Build axes ---
    axes: list[AxisMetadata] = []
    for i in range(min(3, ndim)):
        axes.append(AxisMetadata(
            kind=AxisKind.SPACE,
            centering=Centering.CELL,
            space_direction=space_directions[i],
            unit=spatial_unit,
        ))

    # Time axis for 4D+
    if ndim >= 4:
        time_kwargs: dict[str, Any] = {"kind": AxisKind.TIME}
        if temporal_unit:
            time_kwargs["unit"] = temporal_unit
        # pixdim[4] as thickness for time axis
        pixdim4 = float(hdr["pixdim"][4])
        if pixdim4 > 0:
            time_kwargs["thickness"] = pixdim4
        axes.append(AxisMetadata(**time_kwargs))

    # Extra axes beyond 4D (rare)
    for i in range(4, ndim):
        axes.append(AxisMetadata())

    # --- Value transforms from scl_slope/scl_inter ---
    # Use raw header because nibabel sanitizes these in the image header
    value_transforms = None
    scl_slope = float(raw_hdr["scl_slope"])
    scl_inter = float(raw_hdr["scl_inter"])
    # Skip if unset (NaN or 0) or identity (slope=1, inter=0)
    slope_set = not (np.isnan(scl_slope) or scl_slope == 0)
    if slope_set and not (scl_slope == 1.0 and (scl_inter == 0.0 or np.isnan(scl_inter))):
        if np.isnan(scl_inter):
            scl_inter = 0.0
        value_transforms = [
            ValueTransform(
                name="linear",
                parameters={"slope": scl_slope, "intercept": scl_inter},
            )
        ]

    # --- Convention-level intent ---
    intent_code = int(hdr["intent_code"])
    convention_intent = _INTENT_CODE_TO_CONVENTION.get(intent_code)

    # --- NIfTI extension tags ---
    tags_kwargs: dict[str, Any] = {}

    # sform_code ≥ 2 → preserve in tags
    if sform_code >= 2:
        tags_kwargs["sform_code"] = sform_code

    # qform_code: preserve when non-zero
    if qform_code > 0:
        tags_kwargs["qform_code"] = qform_code

    # Legacy matrices: store original 4x4 affines for provenance
    legacy_tags_kwargs: dict[str, Any] = {}
    if sform_code > 0:
        legacy_tags_kwargs["sform"] = img.get_sform().tolist()
    if qform_code > 0:
        legacy_tags_kwargs["qform"] = img.get_qform().tolist()

    # dim_info
    dim_info_byte = int(hdr["dim_info"])
    freq_dim = dim_info_byte & 0x03
    phase_dim = (dim_info_byte >> 2) & 0x03
    slice_dim = (dim_info_byte >> 4) & 0x03
    if freq_dim or phase_dim or slice_dim:
        di_kwargs: dict[str, Any] = {}
        if freq_dim:
            di_kwargs["freq_dim"] = freq_dim
        if phase_dim:
            di_kwargs["phase_dim"] = phase_dim
        if slice_dim:
            di_kwargs["slice_dim"] = slice_dim
        tags_kwargs["dim_info"] = NiftiDimInfo(**di_kwargs)

    # intent
    if intent_code != 0:
        intent_kwargs: dict[str, Any] = {"code": intent_code}
        intent_name = bytes(hdr["intent_name"]).decode("ascii", errors="ignore").strip("\x00 ")
        if intent_name:
            intent_kwargs["name"] = intent_name
        p1 = float(hdr["intent_p1"])
        p2 = float(hdr["intent_p2"])
        p3 = float(hdr["intent_p3"])
        if p1 != 0:
            intent_kwargs["p1"] = p1
        if p2 != 0:
            intent_kwargs["p2"] = p2
        if p3 != 0:
            intent_kwargs["p3"] = p3
        tags_kwargs["intent"] = NiftiIntent(**intent_kwargs)

    # slice_timing
    slice_code = int(hdr["slice_code"])
    slice_start = int(hdr["slice_start"])
    slice_end = int(hdr["slice_end"])
    slice_duration = float(hdr["slice_duration"])
    if slice_code or slice_start or slice_end or slice_duration:
        st_kwargs: dict[str, Any] = {}
        if slice_code:
            st_kwargs["code"] = _SLICE_CODE_TO_STR.get(slice_code, str(slice_code))
        if slice_start:
            st_kwargs["start"] = slice_start
        if slice_end:
            st_kwargs["end"] = slice_end
        if slice_duration:
            st_kwargs["duration"] = slice_duration
        tags_kwargs["slice_timing"] = NiftiSliceTiming(**st_kwargs)

    # toffset
    toffset = float(hdr["toffset"])
    if toffset != 0:
        tags_kwargs["toffset"] = toffset

    # cal
    cal_min = float(hdr["cal_min"])
    cal_max = float(hdr["cal_max"])
    if cal_min != 0 or cal_max != 0:
        cal_kwargs: dict[str, Any] = {}
        if cal_min != 0:
            cal_kwargs["min"] = cal_min
        if cal_max != 0:
            cal_kwargs["max"] = cal_max
        tags_kwargs["cal"] = NiftiCal(**cal_kwargs)

    # descrip
    descrip = bytes(hdr["descrip"]).decode("ascii", errors="ignore").strip("\x00 ")
    if descrip:
        tags_kwargs["descrip"] = descrip

    # aux_file
    aux_file = bytes(hdr["aux_file"]).decode("ascii", errors="ignore").strip("\x00 ")
    if aux_file:
        tags_kwargs["aux_file"] = aux_file

    # Build extension
    nifti_ext_kwargs: dict[str, Any] = {
        "version": "1.0",
        "nifti_version": nifti_version,
    }
    if tags_kwargs:
        nifti_ext_kwargs["tags"] = NiftiTags(**tags_kwargs)
    if legacy_tags_kwargs:
        nifti_ext_kwargs["legacy"] = NiftiLegacy(
            tags=NiftiLegacyTags(**legacy_tags_kwargs),
        )

    nifti_ext = NiftiExtension(**nifti_ext_kwargs)
    extensions = {"nifti": nifti_ext.model_dump(exclude_none=True)}

    # --- Build NrrdMetadata ---
    meta = NrrdMetadata(
        version="1.0",
        space=space,
        space_origin=space_origin,
        value_transforms=value_transforms,
        intent=convention_intent,
        axes=axes,
        extensions=extensions,
    )

    # --- Write Zarr ---
    if chunks is None:
        chunks = _auto_chunks(shape, data.dtype)

    compressors_list = _build_compressors(compressor, level)

    dim_names = ["i", "j", "k"]
    if ndim >= 4:
        dim_names.append("t")
    for i in range(4, ndim):
        dim_names.append(f"d{i}")

    attrs = {"nrrd": meta.model_dump(exclude_none=True)}

    is_zip = _is_zip_path(output_path)
    with open_store(output_path, mode="w", overwrite=overwrite) as store:
        zarr.create_array(
            store,
            data=data,
            chunks=chunks,
            compressors=compressors_list,
            dimension_names=dim_names,
            attributes=attrs,
            overwrite=False if is_zip else overwrite,
            fill_value=0,
        )


# ---------------------------------------------------------------------------
# Zarr → NIfTI
# ---------------------------------------------------------------------------


def zarr_to_nifti(
    input_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> None:
    """Convert a nrrdz Zarr v3 store to a NIfTI file.

    Parameters
    ----------
    input_path : path to the input Zarr store
    output_path : path for the output .nii or .nii.gz file
    overwrite : if True, overwrite existing file
    """
    _require_nibabel()
    import nibabel as nib

    input_path = Path(input_path)
    output_path = Path(output_path)

    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists (use --overwrite)")

    # Read Zarr store
    with open_store(input_path, mode="r") as store:
        arr = zarr.open_array(store, mode="r")
        data = arr[:]
        nrrd_attrs = arr.attrs.get("nrrd", {})
        meta = NrrdMetadata(**nrrd_attrs)

    ndim = data.ndim

    # --- Parse NIfTI extension if present ---
    nifti_ext: NiftiExtension | None = None
    tags: NiftiTags | None = None
    if meta.extensions and "nifti" in meta.extensions:
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])
        tags = nifti_ext.tags

    # --- Reconstruct affine from convention fields ---
    affine = np.eye(4)
    if meta.axes:
        for i, ax in enumerate(meta.axes[:3]):
            if ax.space_direction is not None:
                affine[:3, i] = ax.space_direction
    if meta.space_origin:
        affine[:3, 3] = meta.space_origin

    # --- Determine sform_code ---
    sform_code = 2  # default: aligned_anat
    if tags and tags.sform_code is not None:
        sform_code = tags.sform_code
    elif meta.space:
        sform_code = _SPACE_TO_SFORM_CODE.get(meta.space.value, 2)

    # --- Choose NIfTI version ---
    use_nifti2 = False
    if nifti_ext and nifti_ext.nifti_version == 2:
        use_nifti2 = True
    # Also use NIfTI-2 if dimensions exceed NIfTI-1 limits
    if any(s > 32767 for s in data.shape):
        use_nifti2 = True

    ImageClass = nib.Nifti2Image if use_nifti2 else nib.Nifti1Image
    img = ImageClass(data, affine)
    hdr = img.header

    # --- Set sform ---
    hdr.set_sform(affine, code=sform_code)

    # --- Set qform ---
    # Use legacy qform matrix if available; otherwise reconstruct from convention fields
    qform_code_out = sform_code
    if tags and tags.qform_code is not None:
        qform_code_out = tags.qform_code
    qform_affine = affine
    if nifti_ext and nifti_ext.legacy and nifti_ext.legacy.tags and nifti_ext.legacy.tags.qform:
        qform_affine = np.array(nifti_ext.legacy.tags.qform)
    hdr.set_qform(qform_affine, code=qform_code_out)

    # --- Restore value_transforms → scl_slope/scl_inter ---
    if meta.value_transforms:
        for vt in meta.value_transforms:
            if vt.name == "linear" and vt.parameters:
                hdr["scl_slope"] = vt.parameters.get("slope", 0.0)
                hdr["scl_inter"] = vt.parameters.get("intercept", 0.0)
                break

    # --- Restore xyzt_units ---
    spatial_unit_code = 2  # default mm
    temporal_unit_code = 0
    if meta.axes:
        for ax in meta.axes[:3]:
            if isinstance(ax.unit, str) and ax.unit in _UNIT_TO_SPATIAL_CODE:
                spatial_unit_code = _UNIT_TO_SPATIAL_CODE[ax.unit]
                break
        # Time axis
        for ax in meta.axes[3:]:
            if ax.kind == AxisKind.TIME and isinstance(ax.unit, str):
                temporal_unit_code = _UNIT_TO_TEMPORAL_CODE.get(ax.unit, 0)
                break
    hdr["xyzt_units"] = spatial_unit_code | temporal_unit_code

    # --- Restore time axis pixdim ---
    if ndim >= 4 and meta.axes and len(meta.axes) >= 4:
        time_ax = meta.axes[3]
        if time_ax.thickness is not None:
            hdr["pixdim"][4] = time_ax.thickness

    # --- Restore NIfTI tags ---
    if tags:
        # dim_info
        if tags.dim_info is not None:
            di = tags.dim_info
            freq = di.freq_dim or 0
            phase = di.phase_dim or 0
            slc = di.slice_dim or 0
            hdr["dim_info"] = freq | (phase << 2) | (slc << 4)

        # intent
        if tags.intent is not None:
            hdr["intent_code"] = tags.intent.code
            if tags.intent.name:
                name_bytes = tags.intent.name.encode("ascii")[:16]
                hdr["intent_name"] = name_bytes
            if tags.intent.p1 is not None:
                hdr["intent_p1"] = tags.intent.p1
            if tags.intent.p2 is not None:
                hdr["intent_p2"] = tags.intent.p2
            if tags.intent.p3 is not None:
                hdr["intent_p3"] = tags.intent.p3

        # slice_timing
        if tags.slice_timing is not None:
            st = tags.slice_timing
            if st.code is not None:
                hdr["slice_code"] = _STR_TO_SLICE_CODE.get(st.code, 0)
            if st.start is not None:
                hdr["slice_start"] = st.start
            if st.end is not None:
                hdr["slice_end"] = st.end
            if st.duration is not None:
                hdr["slice_duration"] = st.duration

        # toffset
        if tags.toffset is not None:
            hdr["toffset"] = tags.toffset

        # cal
        if tags.cal is not None:
            if tags.cal.min is not None:
                hdr["cal_min"] = tags.cal.min
            if tags.cal.max is not None:
                hdr["cal_max"] = tags.cal.max

        # descrip
        if tags.descrip is not None:
            hdr["descrip"] = tags.descrip.encode("ascii")[:80]

        # aux_file
        if tags.aux_file is not None:
            hdr["aux_file"] = tags.aux_file.encode("ascii")[:24]

    # --- Save ---
    nib.save(img, str(output_path))

    # Patch scl_slope/scl_inter in the saved file.
    # nibabel's Nifti1Image.update_header() resets these to NaN during save,
    # so we write them directly into the raw header bytes afterward.
    _slope_to_patch: float | None = None
    _inter_to_patch: float | None = None
    if meta.value_transforms:
        for vt in meta.value_transforms:
            if vt.name == "linear" and vt.parameters:
                _slope_to_patch = vt.parameters.get("slope")
                _inter_to_patch = vt.parameters.get("intercept", 0.0)
                break

    if _slope_to_patch is not None:
        import struct as _struct

        actual_path = output_path
        is_gz = str(output_path).endswith(".gz")
        if is_gz:
            import gzip
            import shutil
            import tempfile

            # Decompress, patch, recompress
            tmp_nii = Path(tempfile.mktemp(suffix=".nii"))
            with gzip.open(str(output_path), "rb") as f_in:
                with open(str(tmp_nii), "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            # Determine offset (NIfTI-1: 112, NIfTI-2: 176)
            slope_offset = 176 if use_nifti2 else 112
            fmt = "<d" if use_nifti2 else "<f"
            with open(str(tmp_nii), "r+b") as fh:
                fh.seek(slope_offset)
                fh.write(_struct.pack(fmt, _slope_to_patch))
                fh.write(_struct.pack(fmt, _inter_to_patch or 0.0))
            with open(str(tmp_nii), "rb") as f_in:
                with gzip.open(str(output_path), "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            tmp_nii.unlink()
        else:
            slope_offset = 176 if use_nifti2 else 112
            fmt = "<d" if use_nifti2 else "<f"
            with open(str(output_path), "r+b") as fh:
                fh.seek(slope_offset)
                fh.write(_struct.pack(fmt, _slope_to_patch))
                fh.write(_struct.pack(fmt, _inter_to_patch or 0.0))
