"""Bidirectional conversion between NIfTI files and duckn Zarr stores.

Reads NIfTI-1/NIfTI-2 files via nibabel and writes duckn Zarr v3 stores
with the NIfTI provenance extension. Also converts back from Zarr to NIfTI.
Also builds ZMP manifests for zero-copy slice access to .nii files.

Requires nibabel: install with ``pip install duckn[nifti]``.
"""

from __future__ import annotations

import json
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
    DucknMetadata,
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
            "Install it with: pip install duckn[nifti]"
        ) from None


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

# sform_code / qform_code → space name. NIfTI defines the world as RAS+ (+x Right, +y Anterior,
# +z Superior) for EVERY code; a code says which RAS frame (the scanner's, an aligned one, a
# template), which the `nifti` extension keeps as `sform_code`/`qform_code`. Code 1 was mapped
# to `scanner-xyz` - a frame with no directions - and code 5 (TEMPLATE_OTHER, added to NIfTI in
# 2019) not at all, so both lost their orientation on import (found 2026-09-27).
_SFORM_CODE_TO_SPACE: dict[int, SpaceName] = {
    1: SpaceName.RIGHT_ANTERIOR_SUPERIOR,
    2: SpaceName.RIGHT_ANTERIOR_SUPERIOR,
    3: SpaceName.RIGHT_ANTERIOR_SUPERIOR,
    4: SpaceName.RIGHT_ANTERIOR_SUPERIOR,
    5: SpaceName.RIGHT_ANTERIOR_SUPERIOR,
}

# space name → default sform_code (for writing back). Anatomical spaces
# (RAS/LAS/LPS and their -time variants) are all reframed to RAS+ on export
# and written with code 2 (aligned_anat); scanner spaces keep code 1.
_SPACE_TO_SFORM_CODE: dict[str, int] = {
    "scanner-xyz": 1,
    "right-anterior-superior": 2,
    "left-anterior-superior": 2,
    "left-posterior-superior": 2,
}


def _space_to_ras_signs(space_value: str) -> np.ndarray:
    """Per-axis sign flips to reframe a duckn anatomical space into NIfTI RAS+.

    NIfTI sform/qform are *defined* to be RAS+ (``+x`` → Right, ``+y`` →
    Anterior, ``+z`` → Superior); there is no code for any other anatomical
    frame. duckn stores carry their native frame in ``space`` (e.g. DICOM is
    ``left-posterior-superior``), so on export the affine must be reframed to
    RAS by negating each axis whose positive direction is opposite RAS — e.g.
    LPS → negate x (L→R) and y (P→A). This relabels the world coordinate
    frame only; the voxel data is never touched.

    Returns a length-3 array of ``±1``. Non-anatomical spaces (``scanner-xyz``)
    are assumed already NIfTI-compatible and get no flip.
    """
    toks = space_value.split("-")
    anatomical = {
        "right", "left", "anterior", "posterior", "superior", "inferior",
    }
    if len(toks) < 3 or not all(t in anatomical for t in toks[:3]):
        return np.array([1.0, 1.0, 1.0])
    ras_positive = ("right", "anterior", "superior")
    return np.array([
        1.0 if tok == want else -1.0
        for tok, want in zip(toks[:3], ras_positive)
    ])

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
    32: "Hz",
    40: "ppm",
    48: "rad/s",
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

# Temporal unit codes that make the 4th dimension something other than time (nifti1.h: the
# "temporal" unit bits also carry frequency, chemical shift and angular frequency).
_NON_TIME_UNITS = frozenset({"Hz", "ppm", "rad/s"})

# Intents whose values are vectors or matrices: nifti1.h puts the components in the 5th
# dimension (dim[5]), with dim[4] = 1; some writers put them in the 4th instead.
def _open_header_file(path):
    """The header's bytes as a file: gzip judged by its magic number, not its name (an
    uppercase ``.NII.GZ`` read raw failed before 0.6.4)."""
    import gzip
    with open(str(path), "rb") as fh:
        magic = fh.read(2)
    return gzip.open(str(path), "rb") if magic == b"\x1f\x8b" else open(str(path), "rb")


def _load_repaired(nib, path):
    """``nib.load`` for a single-file NIfTI nibabel refuses where nifti1_io, NIfTI's reference
    library, reads it: a usable ``scl_slope`` beside an intercept that is not finite (read as
    0), and a quaternion whose b, c, d have a squared norm above 1 (nifti1_io normalizes them
    and takes a = 0). Its header, so repaired, over the file's own data."""
    from nibabel.arrayproxy import ArrayProxy

    for klass in (nib.Nifti1Image, nib.Nifti2Image):
        with _open_header_file(path) as fh:
            try:
                header = klass.header_class.from_fileobj(fh)
            except Exception:                    # noqa: BLE001 - not this NIfTI version
                continue
        if not np.isfinite(float(header["scl_inter"])):
            header["scl_inter"] = 0
        bcd = np.array([float(header["quatern_b"]), float(header["quatern_c"]),
                        float(header["quatern_d"])])
        norm = float(np.linalg.norm(bcd))
        if norm > 1:
            for k, v in zip(("quatern_b", "quatern_c", "quatern_d"), bcd / norm):
                header[k] = v
        return klass(ArrayProxy(str(path), header), header.get_best_affine(), header)
    raise ValueError(f"{path}: not a NIfTI-1 or NIfTI-2 file")


# CIFTI-2's intents (nifti2.h: 3000-3099): a matrix of brain-ordinates described by an XML
# extension, not an image on a grid. NIfTI-MRS: header extension code 44. Neither is refused
# silently converted: their meaning lives outside what this converter models (nifti-spec §4.4).
_CIFTI_INTENTS = range(3000, 3100)
_NIFTI_MRS_ECODE = 44


# Components that are spatial quantities, and so change with the world's frame
_SPATIAL_COMPONENT_KINDS = frozenset({AxisKind.VECTOR, AxisKind.THREE_D_MATRIX,
                                      AxisKind.THREE_D_SYMMETRIC_MATRIX})


def _reframe_components(data: np.ndarray, axis: int, kind: AxisKind, R: np.ndarray):
    """Components on dimension ``axis`` taken through R: a vector R v, a matrix R M R^T. Only
    3-vectors and 3x3 matrices (a component count that is not one of those is left alone)."""
    moved = np.moveaxis(data, axis, -1)
    n = moved.shape[-1]
    work = moved.astype(np.result_type(moved.dtype, np.float32), copy=False)
    if kind == AxisKind.VECTOR and n == 3:
        out = work @ R.T
    elif kind == AxisKind.THREE_D_MATRIX and n == 9:
        m = work.reshape(work.shape[:-1] + (3, 3))
        out = (R @ m @ R.T).reshape(work.shape)
    elif kind == AxisKind.THREE_D_SYMMETRIC_MATRIX and n == 6:
        iu = np.triu_indices(3)                        # xx xy xz yy yz zz
        m = np.zeros(work.shape[:-1] + (3, 3), dtype=work.dtype)
        m[..., iu[0], iu[1]] = work
        m[..., iu[1], iu[0]] = work
        out = (R @ m @ R.T)[..., iu[0], iu[1]]
    else:
        return data
    return np.moveaxis(out.astype(work.dtype, copy=False), -1, axis)


def _components_kind(intent_code: int, size: int, p1: float = 0, p2: float = 0) -> AxisKind:
    if intent_code == 1005:                               # NIFTI_INTENT_SYMMATRIX
        return {6: AxisKind.THREE_D_SYMMETRIC_MATRIX,
                3: AxisKind.TWO_D_SYMMETRIC_MATRIX}.get(size, AxisKind.LIST)
    if intent_code == 1004:                               # NIFTI_INTENT_GENMATRIX
        # an M x N matrix, M and N in intent_p1 and p2: a 1 x 4 is no 2 x 2
        shape = (int(p1), int(p2)) if p1 and p2 else None
        if shape in (None, (3, 3)) and size == 9:
            return AxisKind.THREE_D_MATRIX
        if shape in (None, (2, 2)) and size == 4:
            return AxisKind.TWO_D_MATRIX
        return AxisKind.LIST
    if intent_code in (1006, 1007, 2006):                 # DISPVECT, VECTOR, FNIRT field
        return AxisKind.VECTOR
    if intent_code == 1010 and size == 4:                 # QUATERNION
        return AxisKind.QUATERNION
    if intent_code == 2003 and size == 3:                 # RGB_VECTOR
        return AxisKind.RGB_COLOR
    if intent_code == 2004 and size == 4:                 # RGBA_VECTOR
        return AxisKind.RGBA_COLOR
    return AxisKind.LIST


_VECTOR_INTENTS = frozenset({1004, 1005, 1006, 1007, 1010, 2003, 2004,
                             # FSL's fields (FSL's nifti1.h, as nibabel lists them): an FNIRT
                             # displacement field, and spline, DCT and TOPUP coefficient
                             # fields - components, never time
                             2006, 2007, 2008, 2009, 2016, 2017})
# statistical intents (nifti1.h 2-24): with a 5th dimension, it holds the statistic in plane 0
# and its parameters after (nifti1.h "STATISTICAL PARAMETRIC DATASETS"): a list, never time
_STATISTIC_INTENTS = frozenset(range(2, 25))

# nifti1.h stores NIFTI_INTENT_SYMMATRIX as the lower triangle, row by row: A00 A10 A11 A20 A21
# A22, that is xx xy yy xz yz zz. duckn's 3D-symmetric-matrix is xx xy xz yy yz zz (NRRD's
# order). The permutation is its own inverse. 0.6.3 and earlier took the file's order as duckn's,
# so a tensor read Dyy as Dxz and Dxz as Dyy. (The 2D order, xx xy yy, is the same in both.)
_SYMMATRIX_3D_ORDER = [0, 1, 3, 2, 4, 5]

# NIfTI intent codes → convention-level intent strings
_INTENT_CODE_TO_CONVENTION: dict[int, str] = {
    **{c: "statistical-map" for c in range(2, 25)},       # nifti1.h's statistical codes
    1001: "statistical-map",
    1002: "label-map",
    1003: "label-map",                                    # NEURONAME: labels by name
    1005: "diffusion-tensor",
    1006: "displacement-field",
    2006: "displacement-field",                           # FSL's FNIRT displacement field
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
    affine: str = "sform",
) -> None:
    """Convert a NIfTI file to a duckn Zarr v3 store.

    Parameters
    ----------
    input_path : path to the input .nii or .nii.gz file (or a pair's .hdr or .img)
    output_path : path for the output Zarr store (directory)
    chunks : explicit chunk shape, or None for auto-chunking
    compressor : "zstd", "gzip", or "none"
    level : compression level
    overwrite : if True, overwrite existing store
    affine : which transform is the array's world when both are set: ``"sform"`` (the sform
        when ``sform_code`` > 0, else the qform: nibabel's, FSL's and SPM's rule) or
        ``"qform"`` (the qform when ``qform_code`` > 0, else the sform). nifti1.h leaves the
        choice to the reader; a singular matrix is never used.
    """
    _require_nibabel()
    import nibabel as nib

    input_path = Path(input_path)
    output_path = Path(output_path)

    if affine not in ("sform", "qform"):
        raise ValueError(f'affine {affine!r}: "sform" or "qform"')
    try:
        img = nib.load(str(input_path))
    except (nib.spatialimages.HeaderDataError, ValueError) as e:
        # nibabel refuses what nifti1_io, NIfTI's reference library, reads: a usable scl_slope
        # beside an intercept that is not finite, a quaternion whose b, c, d exceed unit norm.
        try:
            img = _load_repaired(nib, input_path)
        except Exception:                        # noqa: BLE001 - not what nibabel refused
            raise e from None
    if type(img).__name__.startswith("Cifti2"):
        raise ValueError(f"{input_path}: a CIFTI-2 file (brain-ordinates described by its XML "
                         "extension), not an image on a grid; not converted")
    if not isinstance(img, nib.Nifti1Pair):
        raise ValueError(f"{input_path}: not a NIfTI file ({type(img).__name__}; an Analyze "
                         "7.5 header states no sform, qform or units)")
    hdr = img.header
    if int(hdr["intent_code"]) in _CIFTI_INTENTS:
        raise ValueError(f"{input_path}: intent {int(hdr['intent_code'])} is CIFTI's; not an "
                         "image on a grid, not converted")
    if any(e.get_code() == _NIFTI_MRS_ECODE for e in hdr.extensions):
        raise ValueError(f"{input_path}: a NIfTI-MRS file (header extension 44); its dimensions "
                         "are defined by that extension, which this converter does not read")

    # The raw header, for fields nibabel sanitizes on load (scl_slope/scl_inter): read from the
    # file nibabel read the header from - a pair's .hdr, even when given its .img.
    fm = img.file_map.get("header") or img.file_map["image"]
    header_file = fm.filename or str(input_path)
    with _open_header_file(header_file) as fh:
        raw_hdr = type(hdr).from_fileobj(fh)

    # Detect NIfTI version
    is_nifti2 = isinstance(img, nib.Nifti2Image)
    nifti_version = 2 if is_nifti2 else 1

    # Raw stored values (NOT get_fdata which applies scaling)
    data = img.dataobj.get_unscaled()
    # RGB24 / RGBA32 (datatypes 128, 2304): one record of uint8 per voxel. The components become
    # the last dimension, an RGB-color or RGBA-color (nifti1.h: scaling does not apply to them).
    color = None
    if data.dtype.names:
        names = data.dtype.names
        color = AxisKind.RGBA_COLOR if len(names) == 4 else AxisKind.RGB_COLOR
        data = np.stack([np.asarray(data[n]) for n in names], axis=-1)

    ndim = data.ndim - (1 if color is not None else 0)
    shape = data.shape

    # --- The affine ---
    # nifti1.h defines three methods and leaves the choice between the sform (method 3) and the
    # qform (method 2) to the reader, "depending on its purposes". Here it is the caller's:
    # affine="sform" (the default) is nibabel's, FSL's and SPM's rule (get_best_affine), the
    # sform when sform_code > 0; affine="qform" prefers the qform. SimpleITK has rules of its
    # own (it takes the qform for an MNI sform beside a scanner qform), so two readers can place
    # one file differently; a 2.0 writer keeps the transform not chosen (nifti 2.0 §1). The
    # chosen matrix is used as written: method 3 does not use pixdim, so a disagreement is only
    # reported. A singular matrix places nothing, and is passed over (reported). Until 0.6.4
    # duckn took the qform whenever the sform's column lengths disagreed with pixdim and
    # otherwise rescaled the sform to pixdim.
    sform_code = int(hdr["sform_code"])
    qform_code = int(hdr["qform_code"])

    pixdims = np.array(hdr.get_zooms()[:min(3, ndim)], dtype=np.float64)

    import warnings
    matrices = {"sform": (sform_code, img.get_sform), "qform": (qform_code, img.get_qform)}
    order = ("sform", "qform") if affine == "sform" else ("qform", "sform")
    chosen = None
    for which in order:
        code, get = matrices[which]
        if code <= 0:
            continue
        m = np.asarray(get(), dtype=np.float64)
        if not np.all(np.isfinite(m)) or abs(np.linalg.det(m[:3, :3])) < 1e-12:
            warnings.warn(f"NIfTI {which} (code {code}) is singular or not finite; not used",
                          stacklevel=2)
            continue
        chosen, affine_matrix, active_code = which, m, code
        break
    if chosen == "sform":
        mags = np.array([np.linalg.norm(affine_matrix[:3, i]) for i in range(min(3, ndim))])
        if not np.allclose(mags, np.where(pixdims > 0, pixdims, mags), rtol=0.01):
            warnings.warn(
                f"NIfTI sform column lengths {mags.tolist()} disagree with pixdim "
                f"{pixdims.tolist()}; the sform is used as written (method 3 does not use "
                "pixdim)", stacklevel=2)
    if chosen is None:
        # sform_code = qform_code = 0: NIfTI's "method 1", which states only x = pixdim[1] * i
        # and so on - no orientation and no origin in any patient space (NIfTI-1 keeps it for
        # Analyze 7.5 compatibility and nothing else). nibabel's fall-back affine
        # invents both (a flipped x and a centered origin), and 0.5.4 stored it as RAS. The
        # honest statement is an unnamed space (`space_dimension`) with method 1's axes.
        affine_matrix = np.eye(4)
        for i in range(min(3, ndim)):
            affine_matrix[i, i] = pixdims[i] if pixdims[i] > 0 else 1.0
        active_code = 0
    affine = affine_matrix
    # a 2D NIfTI has two spatial axes; the affine is always 3-dimensional
    n_spatial = min(3, ndim)

    # space_origin = translation column
    space_origin = affine[:3, 3].tolist()

    # space_directions: the affine's columns as written (method 3's matrix, or method 2's, which
    # already carries pixdim); a singular matrix was passed over above, so none is zero
    space_directions = [np.array(affine[:3, i], dtype=np.float64).tolist()
                        for i in range(min(3, ndim))]

    # Map code → space name (none for code 0: see above)
    space = _SFORM_CODE_TO_SPACE.get(active_code) if active_code else None

    # --- Units from xyzt_units ---
    # Code 0 is NIFTI_UNITS_UNKNOWN: the unit is left out (absent means unknown), where 0.5.4
    # stated "mm" on every axis.
    xyzt_units = int(hdr["xyzt_units"])
    spatial_unit_code = xyzt_units & 0x07
    temporal_unit_code = xyzt_units & 0x38
    spatial_unit = _NIFTI_SPATIAL_UNITS.get(spatial_unit_code)
    temporal_unit = _NIFTI_TEMPORAL_UNITS.get(temporal_unit_code)

    # --- Build axes ---
    axes: list[AxisMetadata] = []
    for i in range(n_spatial):
        ax_kwargs: dict[str, Any] = {
            "kind": AxisKind.SPACE, "centering": Centering.CELL,
            "space_direction": space_directions[i][:3],
        }
        if spatial_unit:
            ax_kwargs["unit"] = spatial_unit
        axes.append(AxisMetadata(**ax_kwargs))

    # Dimensions 4 and 5. Until 0.6.1 the 4th was always time and the 5th stated nothing, so a
    # vector or tensor file (components in dim 5, dim 4 = 1, as nifti1.h lays them out) gained a
    # time axis, and a spectrum (a Hz or ppm unit) was called time.
    intent_code_hdr = int(hdr["intent_code"])
    ip1, ip2 = float(hdr["intent_p1"]), float(hdr["intent_p2"])
    components_dim = None
    if intent_code_hdr in _VECTOR_INTENTS:
        components_dim = 4 if ndim >= 5 else (3 if ndim == 4 else None)
    elif intent_code_hdr in _STATISTIC_INTENTS and ndim >= 5:
        components_dim = 4
    toffset = float(hdr["toffset"])
    if ndim >= 4:
        if components_dim == 3:
            axes.append(AxisMetadata(kind=_components_kind(intent_code_hdr, shape[3], ip1, ip2)))
        elif components_dim == 4 and shape[3] == 1:
            axes.append(AxisMetadata())                    # nifti1.h's placeholder dim[4] = 1
        else:
            kind = AxisKind.DOMAIN if temporal_unit in _NON_TIME_UNITS else AxisKind.TIME
            time_kwargs: dict[str, Any] = {"kind": kind}
            if temporal_unit:
                time_kwargs["unit"] = temporal_unit
            # pixdim[4] is the sampling interval (TR), which is what `samples[i].position`
            # states on a time axis (duckn-spec §3.2). 0.5.4 wrote it as `thickness` - the
            # extent each sample measures, which is not the interval between samples.
            # nifti1.h: time point m is at toffset + m * pixdim[4], in pixdim[4]'s unit (a
            # spectrum's first bin likewise); 0.6.3 and earlier left toffset out of the positions.
            pixdim4 = float(hdr["pixdim"][4])
            if pixdim4 > 0 and shape[3] >= 2:
                time_kwargs["samples"] = [{"position": toffset + k * pixdim4}
                                          for k in range(shape[3])]
            elif shape[3] == 1 and toffset != 0:
                time_kwargs["samples"] = [{"position": toffset}]
            axes.append(AxisMetadata(**time_kwargs))

    # Dimensions beyond the 4th
    for i in range(4, ndim):
        if i == components_dim:
            kind = (AxisKind.LIST if intent_code_hdr in _STATISTIC_INTENTS
                    else _components_kind(intent_code_hdr, shape[i], ip1, ip2))
            axes.append(AxisMetadata(kind=kind))
        else:
            axes.append(AxisMetadata())
    if color is not None:
        axes.append(AxisMetadata(kind=color))
    if intent_code_hdr == 1005 and components_dim is not None \
            and axes[components_dim].kind == AxisKind.THREE_D_SYMMETRIC_MATRIX:
        data = np.take(np.asarray(data), _SYMMATRIX_3D_ORDER, axis=components_dim)

    # --- Value transforms from scl_slope/scl_inter ---
    # Use raw header because nibabel sanitizes these in the image header
    value_transforms = None
    scl_slope = float(raw_hdr["scl_slope"])
    scl_inter = float(raw_hdr["scl_inter"])
    # As nifti1_io reads them: a slope of 0 or not finite leaves the stored values unscaled,
    # whatever the intercept; a usable slope beside an intercept that is not finite reads that
    # intercept as 0. (0.6.3 took an infinite slope as a slope, which no transform can hold.)
    slope_set = bool(np.isfinite(scl_slope)) and scl_slope != 0 and color is None
    if slope_set and not np.isfinite(scl_inter):
        scl_inter = 0.0
    if slope_set and not (scl_slope == 1.0 and scl_inter == 0.0):
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

    # Both codes, always, 0 included (nifti-spec §4.2): 0.5.4 wrote neither when it was 0,
    # so "absent" meant both "the file had none" and "unknown", and an export gave a qform-only
    # file an sform (and the reverse).
    tags_kwargs["sform_code"] = sform_code
    tags_kwargs["qform_code"] = qform_code

    # A time unit with no time axis to carry it (a 3D file that states one) is kept as the
    # header's value; every other unit is on its axis.
    if temporal_unit_code and ndim < 4:
        tags_kwargs["xyzt_units"] = xyzt_units

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

    # Header extensions (esize > 0 after the header): kept whole, their code and bytes, never
    # interpreted (nifti-spec §4.4); 0.6.3 and earlier dropped them without a word.
    header_extensions = []
    for e in hdr.extensions:
        import base64
        raw = getattr(e, "_raw", None)             # nibabel 5: the bytes as the file held them
        if not isinstance(raw, bytes):
            raw = e.get_content()
            raw = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        header_extensions.append({"code": int(e.get_code()),
                                  "content": base64.b64encode(raw).decode("ascii")})
    if header_extensions:
        tags_kwargs["extensions"] = header_extensions

    # Build extension
    nifti_ext_kwargs: dict[str, Any] = {
        "version": "1.2",   # 1.2: header extensions kept (§4.4); 1.1: codes stated when 0
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

    # --- Build DucknMetadata ---
    meta = DucknMetadata(
        version="1.0",
        space=space,
        space_dimension=None if space is not None else 3,
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

    # "t" only for a dimension that is time; the components dimension is "c".
    dim_names = ["i", "j", "k"][:min(3, ndim)]
    for i in range(3, ndim):
        if i == components_dim:
            dim_names.append("c")
        elif i == 3 and axes[3].kind == AxisKind.TIME:
            dim_names.append("t")
        else:
            dim_names.append(f"d{i}")
    if color is not None:
        dim_names.append("c")

    attrs = {"duckn": meta.model_dump(exclude_none=True)}

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


def _uniform_interval(axis: AxisMetadata) -> float | None:
    """The one step between the axis's sample positions, or None when they state none."""
    samples = axis.samples or []
    positions = [sm.position for sm in samples]
    if len(positions) < 2 or any(p is None for p in positions):
        return None
    step = positions[1] - positions[0]
    if step <= 0:
        return None
    for k, p in enumerate(positions):
        if not np.isclose(p - positions[0], k * step, rtol=1e-9, atol=1e-12):
            return None
    return float(step)


def zarr_to_nifti(
    input_path: str | Path,
    output_path: str | Path,
    *,
    restore_transforms: bool = False,
    overwrite: bool = False,
) -> None:
    """Convert a duckn Zarr v3 store to a NIfTI file.

    Parameters
    ----------
    input_path : path to the input Zarr store
    output_path : path for the output .nii or .nii.gz file
    restore_transforms : if True, restore the original sform/qform from
        the NIfTI legacy extension (if present) instead of using the
        corrected affine reconstructed from space_directions.  Use this
        when you need the original standard-space mapping (e.g. MNI).
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
        duckn_attrs = arr.attrs.get("duckn", {})
        meta = DucknMetadata(**duckn_attrs)

    # NIfTI carries exactly one affine value mapping, in scl_slope/scl_inter.
    # A chain that is a single linear transform round-trips through it
    # exactly; anything else — a lut, several stacked transforms — has no
    # representation, so the calibrated values are written and the chain is
    # dropped (duckn-spec §4.3). Writing stored values and recording only
    # part of the chain would leave the file's values silently misscaled.
    scl_transform = None
    if meta.value_transforms:
        transforms = meta.value_transforms
        if len(transforms) == 1 and transforms[0].name == "linear":
            scl_transform = transforms[0]
        else:
            from .zarr_io import materialize

            data = materialize(data, transforms)
            meta = meta.model_copy(update={"value_transforms": None})

    ndim = data.ndim

    # --- Parse NIfTI extension if present ---
    nifti_ext: NiftiExtension | None = None
    tags: NiftiTags | None = None
    if meta.extensions and "nifti" in meta.extensions:
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])
        tags = nifti_ext.tags

    # --- Reconstruct affine from convention fields ---
    # This affine has the corrected spacing (from pixdim on ingest)
    affine = np.eye(4)
    if meta.axes:
        for i, ax in enumerate(meta.axes[:3]):
            if ax.space_direction is not None:
                affine[:3, i] = ax.space_direction
    if meta.space_origin:
        affine[:3, 3] = meta.space_origin

    # --- Reframe to RAS+ (NIfTI requires it) ---
    # The affine above is in the store's native space (e.g. DICOM LPS). NIfTI
    # sform/qform are RAS+ by definition, so reframe by negating the axes that
    # point opposite RAS. This is a world-frame relabel only — the voxel data
    # is untouched. Skipping it (the historical bug) writes, say, LPS numbers
    # into an RAS-declared sform, which every reader then mis-reads as a 180°
    # rotation about S (left/right + ant/post swapped).
    signs = np.ones(3)
    if meta.space:
        signs = _space_to_ras_signs(meta.space.value)
        if not np.all(signs == 1.0):
            affine = np.diag([signs[0], signs[1], signs[2], 1.0]) @ affine

    # --- Components into the file's world (RAS) ---
    # A vector's or a tensor's components are in the store's world, or in its measurement
    # frame when it states one (world = F @ components). NIfTI's world is RAS, and a reader of
    # the file has no other frame to put them in, so they are written in it: v' = R v and
    # T' = R T R^T, R = diag(signs) @ F. Until 0.6.4 an LPS store's components went out as
    # they were - two of three components negated against the file's own affine.
    comp_axis = next((i for i, ax in enumerate(meta.axes or [])
                      if i >= 3 and ax.kind in _SPATIAL_COMPONENT_KINDS), None)
    frame = meta.measurement_frame_rows()
    R = np.diag(signs) @ (np.array(frame, dtype=np.float64) if frame else np.eye(3))
    if comp_axis is not None and not np.allclose(R, np.eye(3)):
        data = _reframe_components(np.asarray(data), comp_axis, meta.axes[comp_axis].kind, R)

    # --- Determine codes ---
    # After the reframe, anatomical spaces are RAS+ → code 2 (aligned_anat);
    # scanner spaces keep code 1.
    # A store with no named space states no patient orientation, so it gets code 0 unless its
    # nifti tags say otherwise (0.5.4 wrote 2, aligned_anat, over any geometry).
    sform_code = 2 if meta.space else 0
    if tags and tags.sform_code is not None:
        sform_code = tags.sform_code
    elif meta.space:
        sform_code = 1 if meta.space.value.startswith("scanner") else 2

    qform_code_out = sform_code
    if tags and tags.qform_code is not None:
        qform_code_out = tags.qform_code

    # --- A symmetric tensor in nifti1.h's order (see _SYMMATRIX_3D_ORDER) ---
    sym_axis = next((i for i, ax in enumerate(meta.axes or [])
                     if ax.kind == AxisKind.THREE_D_SYMMETRIC_MATRIX and i >= 3), None)
    # The intent a store without nifti tags states by its components: a tensor is SYMMATRIX, a
    # vector VECTOR, or DISPVECT when the store says it is a displacement field (0.6.3 wrote a
    # vector store with intent 0, which states no components at all).
    implied_intent = 0
    if sym_axis is not None:
        implied_intent = 1005
    elif comp_axis is not None and meta.axes[comp_axis].kind == AxisKind.VECTOR:
        implied_intent = 1006 if meta.intent == "displacement-field" else 1007
    out_intent = tags.intent.code if tags and tags.intent is not None else implied_intent
    if sym_axis is not None and out_intent == 1005:
        data = np.take(np.asarray(data), _SYMMATRIX_3D_ORDER, axis=sym_axis)

    # --- RGB24 / RGBA32: a trailing uint8 color dimension goes back into one record a voxel ---
    color_axes = meta.axes or []
    if (data.ndim >= 2 and len(color_axes) == data.ndim
            and color_axes[-1].kind in (AxisKind.RGB_COLOR, AxisKind.RGBA_COLOR)
            and data.dtype == np.uint8 and data.shape[-1] in (3, 4)):
        names = "RGBA"[:data.shape[-1]]
        rec = np.empty(data.shape[:-1], dtype=[(c, "u1") for c in names])
        for k, c in enumerate(names):
            rec[c] = data[..., k]
        data = rec
        ndim = data.ndim

    # --- Choose NIfTI version ---
    use_nifti2 = False
    if nifti_ext and nifti_ext.nifti_version == 2:
        use_nifti2 = True
    # Also use NIfTI-2 if dimensions exceed NIfTI-1 limits
    if any(s > 32767 for s in data.shape):
        use_nifti2 = True

    ImageClass = nib.Nifti2Image if use_nifti2 else nib.Nifti1Image
    # nibabel refuses 64-bit integers unless the type is asked for by name (NIfTI has codes
    # for both, 1024 and 1280); 0.6.3 failed on every int64 store.
    image_kw = ({"dtype": data.dtype} if data.dtype in (np.dtype(np.int64), np.dtype(np.uint64))
                else {})
    # A qform is a rotation, a reflection and pixdim: it cannot hold a shear. Written from a
    # sheared affine it would be a second, different placement; unless the tags ask for one,
    # the file states the sform alone.
    cols = affine[:3, :min(3, ndim)]
    gram = cols.T @ cols
    sheared = not np.allclose(gram, np.diag(np.diag(gram)),
                              atol=1e-6 * max(1.0, float(np.max(np.abs(gram)))))
    if sheared and not (tags and tags.qform_code is not None):
        qform_code_out = 0
    no_codes = sform_code == 0 and qform_code_out == 0
    if no_codes:
        # Neither transform: the file states only pixdim (method 1). An image built with an
        # affine has nibabel rewrite both codes on save (to aligned_anat), so it is built
        # without one and pixdim set from the axes.
        img = ImageClass(data, None, **image_kw)
        hdr = img.header
        zooms = list(hdr.get_zooms())
        for i in range(min(3, ndim)):
            zooms[i] = float(np.linalg.norm(affine[:3, i]))
        hdr.set_zooms(zooms)
    else:
        img = ImageClass(data, affine, **image_kw)
        hdr = img.header

    # --- Set sform and qform ---
    legacy_tags = (
        nifti_ext.legacy.tags
        if nifti_ext and nifti_ext.legacy and nifti_ext.legacy.tags
        else None
    )

    if no_codes:
        hdr.set_sform(None, code=0)
        hdr.set_qform(None, code=0)
    elif restore_transforms and legacy_tags:
        # Restore original matrices verbatim from legacy extension.
        # We must also update pixdim to match the sform, because nibabel's
        # save() rewrites sform from pixdim + the image affine otherwise.
        sform_affine = (
            np.array(legacy_tags.sform) if legacy_tags.sform is not None else affine
        )
        qform_affine = (
            np.array(legacy_tags.qform) if legacy_tags.qform is not None else affine
        )
        # Set pixdim from sform column magnitudes so nibabel doesn't clobber it
        for i in range(min(3, ndim)):
            hdr["pixdim"][i + 1] = np.linalg.norm(sform_affine[:3, i])
        # Recreate image with the sform affine so nibabel's internals are consistent
        img = ImageClass(data, sform_affine, **image_kw)
        hdr = img.header
        hdr.set_sform(sform_affine, code=sform_code)
        hdr.set_qform(qform_affine, code=qform_code_out)
    else:
        # Default: the sform is reconstructed from the convention fields
        # (pixdim-based spacing). The convention stores a single geometry, so
        # a qform that differed from the sform on import survives only in
        # legacy — restore it rather than overwriting it with a copy of the
        # sform, which would silently discard it.
        #
        # Only when the reconstructed geometry still matches what was
        # imported, though: if the store's geometry has since been edited,
        # the stored qform is stale, and a qform contradicting the sform is
        # worse than no separate qform at all.
        hdr.set_sform(affine, code=sform_code)

        qform_affine = affine
        if legacy_tags is not None and legacy_tags.qform is not None:
            # The affine was derived from the sform on import, or from the
            # qform for a qform-only file.
            imported = (
                legacy_tags.sform if legacy_tags.sform is not None else legacy_tags.qform
            )
            if imported is not None and np.allclose(
                np.array(imported, dtype=np.float64), affine, atol=1e-6
            ):
                qform_affine = np.array(legacy_tags.qform, dtype=np.float64)

        hdr.set_qform(qform_affine, code=qform_code_out)
    if not no_codes:
        # a code of 0 is "this transform is absent": its matrix is not written (0.5.4 wrote
        # one with a code, so a qform-only file came back with an sform and the reverse)
        if sform_code == 0:
            hdr.set_sform(None, code=0)
        if qform_code_out == 0:
            hdr.set_qform(None, code=0)

    # --- Restore the single linear transform → scl_slope/scl_inter ---
    # Only set when the whole chain was that one transform; any other chain
    # was materialized above and must leave these unset.
    if scl_transform is not None and scl_transform.parameters:
        hdr["scl_slope"] = scl_transform.parameters.get("slope", 0.0)
        hdr["scl_inter"] = scl_transform.parameters.get("intercept", 0.0)

    # --- Restore xyzt_units ---
    # From the axes; an axis with no unit states none, so the code is 0 (unknown) - 0.5.4
    # wrote mm for a unit nobody stated. A time unit kept in the tags because the store has
    # no time axis to carry it (a 3D file that stated one) goes back as it came.
    spatial_unit_code = 0
    temporal_unit_code = 0
    if meta.axes:
        for ax in meta.axes[:3]:
            if isinstance(ax.unit, str) and ax.unit in _UNIT_TO_SPATIAL_CODE:
                spatial_unit_code = _UNIT_TO_SPATIAL_CODE[ax.unit]
                break
        # Time axis
        for ax in meta.axes[3:]:
            if ax.kind in (AxisKind.TIME, AxisKind.DOMAIN) and isinstance(ax.unit, str):
                temporal_unit_code = _UNIT_TO_TEMPORAL_CODE.get(ax.unit, 0)
                break
    has_time_axis = bool(meta.axes) and any(
        ax.kind in (AxisKind.TIME, AxisKind.DOMAIN) for ax in meta.axes[3:])
    if tags and tags.xyzt_units is not None and not has_time_axis:
        temporal_unit_code = int(tags.xyzt_units) & 0x38
    hdr["xyzt_units"] = spatial_unit_code | temporal_unit_code

    # --- Restore time axis pixdim (the sampling interval) ---
    if ndim >= 4 and meta.axes and len(meta.axes) >= 4:
        time_ax = meta.axes[3]
        interval = _uniform_interval(time_ax)
        if interval is not None:
            hdr["pixdim"][4] = interval
        elif time_ax.thickness is not None and not time_ax.samples:
            # stores written by 0.5.4 and earlier put pixdim[4] here
            hdr["pixdim"][4] = time_ax.thickness
        # nifti1.h: time point 0 is at toffset (0.6.4 reads it into the positions)
        first = (time_ax.samples or [None])[0]
        if (time_ax.kind in (AxisKind.TIME, AxisKind.DOMAIN) and first is not None
                and first.position is not None):
            hdr["toffset"] = first.position

    if out_intent and not (tags and tags.intent is not None):
        hdr["intent_code"] = out_intent
        if out_intent == 1005:
            hdr["intent_p1"] = 3                       # nifti1.h: SYMMATRIX's p1 is its size N

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
                name_bytes = tags.intent.name.encode("ascii", errors="replace")[:16]
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
            hdr["descrip"] = tags.descrip.encode("ascii", errors="replace")[:80]

        # aux_file
        if tags.aux_file is not None:
            hdr["aux_file"] = tags.aux_file.encode("ascii", errors="replace")[:24]

        # header extensions, whole, as the source had them (nifti-spec §4.4)
        if tags.extensions:
            import base64
            for e in tags.extensions:
                hdr.extensions.append(nib.nifti1.Nifti1Extension(
                    int(e["code"]), base64.b64decode(e["content"])))

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
        # the header lives in the .hdr of a pair, whatever name the caller gave (nib.save
        # converts to a pair on its own, so the saved image's file map is not this one's)
        _patch_scaling(_header_file_of(output_path), use_nifti2,
                       _slope_to_patch, _inter_to_patch or 0.0)


def _header_file_of(path: Path) -> Path:
    """The file holding the header nibabel wrote for ``path``: itself for a .nii or .hdr, the
    .hdr of a pair named by its .img (in the .img's case, as nibabel names it)."""
    name = path.name
    for ext in (".img.gz", ".img"):
        if name.lower().endswith(ext):
            img_part = name[len(name) - len(ext):len(name) - len(ext) + 4]     # ".img" / ".IMG"
            hdr_part = ".HDR" if img_part[1:].isupper() else ".hdr"
            return path.with_name(name[:len(name) - len(ext)] + hdr_part + ext[4:])
    return path


def _patch_scaling(header_path: Path, nifti2: bool, slope: float, inter: float) -> None:
    """Write scl_slope and scl_inter into a saved header (NIfTI-1 offset 112 as float32,
    NIfTI-2 176 as float64), compressed or not - judged by the gzip magic number."""
    import gzip
    import struct

    offset, fmt = (176, "<dd") if nifti2 else (112, "<ff")
    with open(header_path, "rb") as fh:
        raw = fh.read()
    gz = raw[:2] == b"\x1f\x8b"
    body = bytearray(gzip.decompress(raw) if gz else raw)
    body[offset:offset + struct.calcsize(fmt)] = struct.pack(fmt, slope, inter)
    with open(header_path, "wb") as fh:
        fh.write(gzip.compress(bytes(body)) if gz else bytes(body))


# ---------------------------------------------------------------------------
# NIfTI ZMP builder
# ---------------------------------------------------------------------------


def build_nifti_zmp(
    input_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Build a ZMP manifest for a NIfTI file with per-slice byte ranges.

    Creates a lightweight Parquet file that maps Zarr chunk paths to
    byte ranges within the NIfTI file. Each chunk is one axial slice,
    readable via byte-range requests (HTTP or local file:// URI).

    The Zarr array shape is (z, y, x) — C-order with Z slowest —
    matching the NIfTI file's on-disk byte layout where X varies fastest.
    Each chunk (1, y, x) is a contiguous byte range.

    Requirements:
    - Uncompressed .nii (not .nii.gz)
    - 3D volume (4D not yet supported)

    Parameters
    ----------
    input_path : path to the .nii file
    output_path : path for the output .zmp file
    overwrite : if True, overwrite existing file

    Returns
    -------
    Path to the created .zmp file
    """
    _require_nibabel()
    import nibabel as nib
    from zarr_zmp import Builder as ZMPBuilder

    input_path = Path(input_path)
    output_path = Path(output_path)

    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists")

    if str(input_path).endswith(".gz"):
        raise ValueError("ZMP requires uncompressed .nii (not .nii.gz)")

    img = nib.load(str(input_path))
    hdr = img.header
    ndim = len(img.shape)

    if ndim != 3:
        raise ValueError(f"ZMP currently supports 3D NIfTI only, got {ndim}D")

    # NIfTI shape is (x_dim, y_dim, z_dim) with x varying fastest on disk
    # Zarr C-order shape is (z_dim, y_dim, x_dim) with x varying fastest
    x_dim, y_dim, z_dim = img.shape
    shape_zyx = [z_dim, y_dim, x_dim]

    # Data offset and dtype
    vox_offset = max(352, int(hdr["vox_offset"]))
    dtype = img.dataobj.dtype
    itemsize = dtype.itemsize

    # Endianness
    byteorder = dtype.byteorder
    if byteorder == "=":
        import sys
        byteorder = "<" if sys.byteorder == "little" else ">"
    endian = "little" if byteorder in ("<", "|") else "big"

    # Slice size: one (y, x) plane
    slice_bytes = y_dim * x_dim * itemsize

    # Build duckn metadata via nifti_to_zarr's logic
    # We do a metadata-only conversion to a temp store
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp())
    tmp_zarr = tmp / "meta.zarr"
    try:
        nifti_to_zarr(input_path, tmp_zarr)
        import zarr
        store = zarr.storage.LocalStore(str(tmp_zarr))
        arr = zarr.open_array(store, mode="r")
        duckn_meta = dict(arr.attrs).get("duckn", {})
    finally:
        shutil.rmtree(tmp)

    # The metadata was built with nibabel's (x,y,z) axis order.
    # We need to reverse the axes for (z,y,x) Zarr order.
    if "axes" in duckn_meta and len(duckn_meta["axes"]) == 3:
        duckn_meta["axes"] = list(reversed(duckn_meta["axes"]))

    # Zarr dtype string
    _dtype_map = {
        "int8": "int8", "uint8": "uint8",
        "int16": "int16", "uint16": "uint16",
        "int32": "int32", "uint32": "uint32",
        "float32": "float32", "float64": "float64",
    }
    dtype_str = _dtype_map.get(str(dtype).replace("<", "").replace(">", ""), "int16")

    # Build zarr.json
    zarr_meta = {
        "zarr_format": 3,
        "node_type": "array",
        "shape": shape_zyx,
        "data_type": dtype_str,
        "chunk_grid": {
            "name": "regular",
            "configuration": {"chunk_shape": [1, y_dim, x_dim]},
        },
        "chunk_key_encoding": {
            "name": "default",
            "configuration": {"separator": "/"},
        },
        "fill_value": 0,
        "codecs": [
            {
                "name": "bytes",
                "configuration": {"endian": endian},
            }
        ],
        "attributes": {"duckn": duckn_meta},
        "dimension_names": ["k", "j", "i"],
    }

    zarr_json_text = json.dumps(zarr_meta)
    uri = input_path.resolve().as_uri()
    file_size = input_path.stat().st_size

    # Build ZMP
    builder = ZMPBuilder()
    builder.add("zarr.json", text=zarr_json_text)

    for k in range(z_dim):
        chunk_path = f"c/{k}/0/0"
        offset = vox_offset + k * slice_bytes
        builder.add(
            chunk_path,
            resolve={"http": {"url": uri, "offset": offset, "length": slice_bytes}},
            size=file_size,
        )

    if output_path.exists() and overwrite:
        output_path.unlink()

    builder.write(output_path)
    return output_path
