"""Tests for NIfTI to nrrdz Zarr conversion.

Uses synthetic nibabel NIfTI images — no real .nii files needed for unit tests.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

nibabel = pytest.importorskip("nibabel")

import nibabel as nib

from nrrdz.models import NiftiExtension, NrrdMetadata
from nrrdz.nifti_convert import nifti_to_zarr, zarr_to_nifti


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nifti(
    shape: tuple[int, ...] = (8, 8, 4),
    affine: np.ndarray | None = None,
    dtype: np.dtype | type = np.int16,
    *,
    sform_code: int = 1,
    qform_code: int = 0,
    data: np.ndarray | None = None,
    nifti2: bool = False,
) -> nib.Nifti1Image | nib.Nifti2Image:
    """Create a minimal synthetic NIfTI image."""
    if affine is None:
        affine = np.eye(4)
    if data is None:
        data = np.arange(np.prod(shape), dtype=dtype).reshape(shape)
    ImageClass = nib.Nifti2Image if nifti2 else nib.Nifti1Image
    img = ImageClass(data, affine)
    hdr = img.header
    hdr.set_sform(affine, code=sform_code)
    if qform_code > 0:
        hdr.set_qform(affine, code=qform_code)
    return img


def _save_nifti(img: nib.Nifti1Image, path: Path) -> Path:
    """Save NIfTI image and return path."""
    nib.save(img, str(path))
    return path


def _patch_slope_inter(path: Path, slope: float, intercept: float, *, nifti2: bool = False) -> None:
    """Patch scl_slope and scl_inter in a saved NIfTI file.

    nibabel's Nifti1Image.update_header() resets these to NaN during save,
    so we write them directly into the raw header bytes afterward.
    """
    offset = 176 if nifti2 else 112
    fmt = "<d" if nifti2 else "<f"
    with open(str(path), "r+b") as fh:
        fh.seek(offset)
        fh.write(struct.pack(fmt, slope))
        fh.write(struct.pack(fmt, intercept))


def _read_raw_slope_inter(path: Path, *, nifti2: bool = False) -> tuple[float, float]:
    """Read scl_slope and scl_inter from raw NIfTI header bytes."""
    offset = 176 if nifti2 else 112
    fmt = "<d" if nifti2 else "<f"
    size = 8 if nifti2 else 4
    with open(str(path), "rb") as fh:
        fh.seek(offset)
        slope = struct.unpack(fmt, fh.read(size))[0]
        inter = struct.unpack(fmt, fh.read(size))[0]
    return slope, inter


# ---------------------------------------------------------------------------
# Sform decomposition tests
# ---------------------------------------------------------------------------


class TestSformDecomposition:
    def test_identity_affine(self, tmp_path):
        """Identity affine → unit space_directions, zero origin."""
        nii_path = tmp_path / "test.nii"
        img = _make_nifti(affine=np.eye(4), sform_code=1)
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "test.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])

        assert meta.space == "scanner-xyz"
        np.testing.assert_allclose(meta.space_origin, [0, 0, 0])
        np.testing.assert_allclose(meta.axes[0].space_direction, [1, 0, 0])
        np.testing.assert_allclose(meta.axes[1].space_direction, [0, 1, 0])
        np.testing.assert_allclose(meta.axes[2].space_direction, [0, 0, 1])

    def test_oblique_affine(self, tmp_path):
        """Oblique affine decomposed correctly."""
        affine = np.array([
            [0.5, 0.1, 0.0, -10.0],
            [0.0, 0.5, 0.1, -20.0],
            [0.0, 0.0, 2.0, -30.0],
            [0.0, 0.0, 0.0, 1.0],
        ])
        nii_path = tmp_path / "oblique.nii"
        img = _make_nifti(affine=affine, sform_code=2)
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "oblique.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])

        assert meta.space == "right-anterior-superior"
        np.testing.assert_allclose(meta.space_origin, [-10, -20, -30])
        np.testing.assert_allclose(meta.axes[0].space_direction, [0.5, 0, 0])
        np.testing.assert_allclose(meta.axes[1].space_direction, [0.1, 0.5, 0])
        np.testing.assert_allclose(meta.axes[2].space_direction, [0, 0.1, 2.0])

    def test_sform_code_mapping(self, tmp_path):
        """sform_code 1 → scanner-xyz, 4 → RAS with tag."""
        # code 1 → scanner-xyz
        nii_path = tmp_path / "scanner.nii"
        img = _make_nifti(sform_code=1)
        _save_nifti(img, nii_path)
        zarr_path = tmp_path / "scanner.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        assert meta.space == "scanner-xyz"

        # code 4 → RAS + sform_code tag
        nii_path2 = tmp_path / "mni.nii"
        img2 = _make_nifti(sform_code=4)
        _save_nifti(img2, nii_path2)
        zarr_path2 = tmp_path / "mni.zarr"
        nifti_to_zarr(nii_path2, zarr_path2)

        store2 = zarr.storage.LocalStore(str(zarr_path2))
        arr2 = zarr.open_array(store2, mode="r")
        meta2 = NrrdMetadata(**arr2.attrs["nrrd"])
        assert meta2.space == "right-anterior-superior"
        nifti_ext = NiftiExtension(**meta2.extensions["nifti"])
        assert nifti_ext.tags.sform_code == 4


# ---------------------------------------------------------------------------
# Value transforms tests
# ---------------------------------------------------------------------------


class TestValueTransforms:
    def test_slope_intercept_preserved(self, tmp_path):
        """scl_slope/scl_inter → value_transforms linear."""
        nii_path = tmp_path / "scaled.nii"
        img = _make_nifti()
        _save_nifti(img, nii_path)
        # Patch slope/intercept in raw header (nibabel resets them on save)
        _patch_slope_inter(nii_path, 2.5, 10.0)

        zarr_path = tmp_path / "scaled.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])

        assert meta.value_transforms is not None
        assert len(meta.value_transforms) == 1
        assert meta.value_transforms[0].name == "linear"
        assert meta.value_transforms[0].parameters["slope"] == pytest.approx(2.5)
        assert meta.value_transforms[0].parameters["intercept"] == pytest.approx(10.0)

    def test_slope_zero_means_unset(self, tmp_path):
        """Default NIfTI (no slope set) → no value_transforms."""
        nii_path = tmp_path / "unscaled.nii"
        img = _make_nifti()
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "unscaled.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        assert meta.value_transforms is None


# ---------------------------------------------------------------------------
# NIfTI tags tests
# ---------------------------------------------------------------------------


class TestNiftiTags:
    def test_intent_code_and_params(self, tmp_path):
        """intent_code + params preserved in extension."""
        nii_path = tmp_path / "tstat.nii"
        img = _make_nifti(sform_code=4)
        img.header["intent_code"] = 3  # TTEST
        img.header["intent_p1"] = 42.0
        img.header["intent_name"] = b"ttest"
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "tstat.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])

        assert meta.intent == "statistical-map"
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])
        assert nifti_ext.tags.intent.code == 3
        assert nifti_ext.tags.intent.name == "ttest"
        assert nifti_ext.tags.intent.p1 == 42.0

    def test_dim_info(self, tmp_path):
        """dim_info byte → freq/phase/slice dims."""
        nii_path = tmp_path / "diminfo.nii"
        img = _make_nifti()
        # freq=1, phase=2, slice=3: byte = 1 | (2<<2) | (3<<4) = 1+8+48 = 57
        img.header["dim_info"] = 57
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "diminfo.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])

        assert nifti_ext.tags.dim_info.freq_dim == 1
        assert nifti_ext.tags.dim_info.phase_dim == 2
        assert nifti_ext.tags.dim_info.slice_dim == 3

    def test_slice_timing(self, tmp_path):
        """slice_code + start/end/duration preserved."""
        nii_path = tmp_path / "slicetime.nii"
        img = _make_nifti()
        img.header["slice_code"] = 3  # alternating-increasing
        img.header["slice_start"] = 0
        img.header["slice_end"] = 35
        img.header["slice_duration"] = 0.0556
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "slicetime.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])

        assert nifti_ext.tags.slice_timing.code == "alternating-increasing"
        assert nifti_ext.tags.slice_timing.end == 35
        assert nifti_ext.tags.slice_timing.duration == pytest.approx(0.0556)

    def test_descrip(self, tmp_path):
        """descrip string preserved."""
        nii_path = tmp_path / "descrip.nii"
        img = _make_nifti()
        img.header["descrip"] = b"FSL5.0"
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "descrip.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])
        assert nifti_ext.tags.descrip == "FSL5.0"

    def test_cal_min_max(self, tmp_path):
        """Display calibration preserved."""
        nii_path = tmp_path / "cal.nii"
        img = _make_nifti()
        img.header["cal_min"] = -8.0
        img.header["cal_max"] = 8.0
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "cal.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])
        assert nifti_ext.tags.cal.min == -8.0
        assert nifti_ext.tags.cal.max == 8.0

    def test_qform_code_preserved(self, tmp_path):
        """qform_code stored as integer when non-zero."""
        nii_path = tmp_path / "qform.nii"
        img = _make_nifti(sform_code=2, qform_code=1)
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "qform.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])

        assert nifti_ext.tags.qform_code == 1
        assert nifti_ext.tags.sform_code == 2

    def test_qform_code_roundtrip(self, tmp_path):
        """qform_code round-trips: different code reconstructed on write-back."""
        affine = np.diag([2.0, 2.0, 2.0, 1.0])
        affine[:3, 3] = [-100, -100, -50]
        nii_path = tmp_path / "qfrt.nii"
        img = _make_nifti(affine=affine, sform_code=4, qform_code=1)
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "qfrt.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        rt_path = tmp_path / "qfrt_rt.nii"
        zarr_to_nifti(zarr_path, rt_path)

        # Read raw header to check codes
        with open(str(rt_path), "rb") as fh:
            rt_hdr = nib.Nifti1Header.from_fileobj(fh)
        assert int(rt_hdr["sform_code"]) == 4
        assert int(rt_hdr["qform_code"]) == 1

        # Both affines reconstructed from convention fields (same matrix)
        rt_img = nib.load(str(rt_path))
        np.testing.assert_allclose(rt_img.get_sform(), affine, atol=1e-6)
        np.testing.assert_allclose(rt_img.get_qform(), affine, atol=1e-6)

    def test_different_qform_sform_roundtrip(self, tmp_path):
        """When sform and qform differ, both round-trip from legacy."""
        sform_affine = np.diag([2.0, 2.0, 2.0, 1.0])
        sform_affine[:3, 3] = [-100, -100, -50]

        qform_affine = np.diag([2.0, 2.0, 2.0, 1.0])
        qform_affine[:3, 3] = [-99, -99, -49]  # different origin

        nii_path = tmp_path / "dual.nii"
        img = _make_nifti(affine=sform_affine, sform_code=4)
        img.header.set_qform(qform_affine, code=1)
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "dual.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        rt_path = tmp_path / "dual_rt.nii"
        zarr_to_nifti(zarr_path, rt_path)

        rt_img = nib.load(str(rt_path))
        # sform from convention fields
        np.testing.assert_allclose(rt_img.get_sform(), sform_affine, atol=1e-6)
        # qform restored from legacy — different origin preserved
        rt_qform = rt_img.get_qform()
        np.testing.assert_allclose(rt_qform[:3, 3], [-99, -99, -49], atol=1e-4)

    def test_legacy_matrices_stored(self, tmp_path):
        """Original sform and qform 4x4 matrices stored in legacy.tags."""
        sform_affine = np.diag([2.0, 2.0, 3.0, 1.0])
        sform_affine[:3, 3] = [-100, -120, -60]

        nii_path = tmp_path / "legacy.nii"
        img = _make_nifti(affine=sform_affine, sform_code=4, qform_code=1)
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "legacy.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])

        # legacy.tags.sform is the original 4x4
        assert nifti_ext.legacy is not None
        assert nifti_ext.legacy.tags is not None
        assert nifti_ext.legacy.tags.sform is not None
        np.testing.assert_allclose(nifti_ext.legacy.tags.sform, sform_affine.tolist())

        # legacy.tags.qform also present (qform_code > 0)
        assert nifti_ext.legacy.tags.qform is not None

    def test_legacy_matrices_absent_when_code_zero(self, tmp_path):
        """No legacy qform when qform_code is 0."""
        nii_path = tmp_path / "nolegacy.nii"
        img = _make_nifti(sform_code=1, qform_code=0)
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "nolegacy.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])

        # sform present (code=1), qform absent (code=0)
        assert nifti_ext.legacy is not None
        assert nifti_ext.legacy.tags.sform is not None
        assert nifti_ext.legacy.tags.qform is None


# ---------------------------------------------------------------------------
# 4D time series tests
# ---------------------------------------------------------------------------


class TestFourD:
    def test_fmri_time_axis(self, tmp_path):
        """4D fMRI: time axis with kind=time, unit from xyzt_units."""
        nii_path = tmp_path / "fmri.nii"
        data = np.zeros((8, 8, 4, 10), dtype=np.int16)
        affine = np.diag([3.0, 3.0, 3.0, 1.0])
        img = nib.Nifti1Image(data, affine)
        hdr = img.header
        hdr.set_sform(affine, code=1)
        # xyzt_units: spatial=mm(2), temporal=s(8)
        hdr["xyzt_units"] = 2 | 8
        hdr["pixdim"][4] = 2.0  # TR = 2s
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "fmri.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])

        assert arr.shape == (8, 8, 4, 10)
        assert len(meta.axes) == 4
        assert meta.axes[3].kind == "time"
        assert meta.axes[3].unit == "s"
        assert meta.axes[3].thickness == 2.0

        # dimension names
        dim_names = arr.metadata.dimension_names
        assert dim_names == ("i", "j", "k", "t")


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_nifti_zarr_nifti(self, tmp_path):
        """NIfTI → Zarr → NIfTI: pixel-exact data + header fidelity."""
        # Create source NIfTI
        affine = np.diag([2.0, 2.0, 3.0, 1.0])
        affine[:3, 3] = [-100, -120, -60]
        data = np.arange(8 * 8 * 4, dtype=np.int16).reshape(8, 8, 4)

        nii_path = tmp_path / "source.nii"
        img = nib.Nifti1Image(data, affine)
        hdr = img.header
        hdr.set_sform(affine, code=4)
        hdr.set_qform(affine, code=4)
        hdr["descrip"] = b"round-trip test"
        hdr["intent_code"] = 3  # ttest
        hdr["intent_p1"] = 42.0
        _save_nifti(img, nii_path)

        # NIfTI → Zarr
        zarr_path = tmp_path / "rt.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        # Zarr → NIfTI
        rt_path = tmp_path / "roundtrip.nii"
        zarr_to_nifti(zarr_path, rt_path)

        # Verify
        rt_img = nib.load(str(rt_path))
        rt_data = rt_img.dataobj.get_unscaled()

        # Read raw header for fields nibabel sanitizes
        with open(str(rt_path), "rb") as fh:
            rt_raw_hdr = nib.Nifti1Header.from_fileobj(fh)

        # Pixel-exact data
        np.testing.assert_array_equal(data, rt_data)

        # Sform
        np.testing.assert_allclose(rt_img.get_sform(), affine, atol=1e-6)
        assert int(rt_raw_hdr["sform_code"]) == 4

        # descrip
        descrip = bytes(rt_raw_hdr["descrip"]).decode("ascii", errors="ignore").strip("\x00 ")
        assert descrip == "round-trip test"

        # intent
        assert int(rt_raw_hdr["intent_code"]) == 3
        assert float(rt_raw_hdr["intent_p1"]) == 42.0

    def test_4d_roundtrip(self, tmp_path):
        """4D NIfTI round-trip preserves time axis metadata."""
        affine = np.diag([3.0, 3.0, 3.0, 1.0])
        data = np.zeros((4, 4, 3, 5), dtype=np.float32)
        data[1, 1, 1, :] = np.arange(5, dtype=np.float32)

        nii_path = tmp_path / "fmri4d.nii"
        img = nib.Nifti1Image(data, affine)
        hdr = img.header
        hdr.set_sform(affine, code=1)
        hdr["xyzt_units"] = 2 | 8  # mm + seconds
        hdr["pixdim"][4] = 2.0
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "fmri4d.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        rt_path = tmp_path / "fmri4d_rt.nii"
        zarr_to_nifti(zarr_path, rt_path)

        rt_img = nib.load(str(rt_path))
        rt_data = np.asarray(rt_img.dataobj)
        np.testing.assert_array_equal(data, rt_data)

        rt_hdr = rt_img.header
        assert int(rt_hdr["xyzt_units"]) & 0x07 == 2  # mm
        assert int(rt_hdr["xyzt_units"]) & 0x38 == 8  # seconds
        assert float(rt_hdr["pixdim"][4]) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# NIfTI-2 tests
# ---------------------------------------------------------------------------


class TestNifti2:
    def test_nifti2_roundtrip(self, tmp_path):
        """NIfTI-2 round-trips with nifti_version=2."""
        affine = np.eye(4)
        data = np.zeros((4, 4, 4), dtype=np.int16)

        nii_path = tmp_path / "nifti2.nii"
        img = _make_nifti(affine=affine, sform_code=1, nifti2=True)
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "nifti2.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        meta = NrrdMetadata(**arr.attrs["nrrd"])
        nifti_ext = NiftiExtension(**meta.extensions["nifti"])
        assert nifti_ext.nifti_version == 2

        # Round-trip back
        rt_path = tmp_path / "nifti2_rt.nii"
        zarr_to_nifti(zarr_path, rt_path)

        rt_img = nib.load(str(rt_path))
        assert isinstance(rt_img, nib.Nifti2Image)


# ---------------------------------------------------------------------------
# Fresh export (no NIfTI extension — derive from convention fields)
# ---------------------------------------------------------------------------


class TestFreshExport:
    def test_no_nifti_extension(self, tmp_path):
        """Zarr with no NIfTI extension → valid NIfTI from convention fields."""
        import zarr
        from nrrdz.models import AxisKind, AxisMetadata, Centering, NrrdMetadata, SpaceName

        data = np.arange(8 * 8 * 4, dtype=np.int16).reshape(8, 8, 4)
        affine = np.diag([2.0, 2.0, 3.0, 1.0])
        affine[:3, 3] = [-100, -120, -60]

        meta = NrrdMetadata(
            version="1.0",
            space=SpaceName.RIGHT_ANTERIOR_SUPERIOR,
            space_origin=[-100.0, -120.0, -60.0],
            axes=[
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[2.0, 0.0, 0.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.0, 2.0, 0.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.0, 0.0, 3.0], unit="mm"),
            ],
        )

        zarr_path = tmp_path / "no_nifti_ext.zarr"
        store = zarr.storage.LocalStore(str(zarr_path))
        zarr.create_array(
            store,
            data=data,
            chunks=(8, 8, 4),
            dimension_names=["i", "j", "k"],
            attributes={"nrrd": meta.model_dump(exclude_none=True)},
            fill_value=0,
        )

        nii_path = tmp_path / "fresh.nii"
        zarr_to_nifti(zarr_path, nii_path)

        img = nib.load(str(nii_path))
        np.testing.assert_array_equal(np.asarray(img.dataobj), data)
        np.testing.assert_allclose(img.get_sform(), affine, atol=1e-6)
        np.testing.assert_allclose(img.get_qform(), affine, atol=1e-6)

        with open(str(nii_path), "rb") as fh:
            raw_hdr = nib.Nifti1Header.from_fileobj(fh)
        # RAS → sform_code=2 (aligned_anat)
        assert int(raw_hdr["sform_code"]) == 2
        assert int(raw_hdr["qform_code"]) == 2

    def test_scanner_xyz_maps_to_code_1(self, tmp_path):
        """scanner-xyz space → sform_code=1."""
        import zarr
        from nrrdz.models import AxisKind, AxisMetadata, Centering, NrrdMetadata, SpaceName

        data = np.zeros((4, 4, 4), dtype=np.float32)
        meta = NrrdMetadata(
            version="1.0",
            space=SpaceName.SCANNER_XYZ,
            space_origin=[0.0, 0.0, 0.0],
            axes=[
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[1.0, 0.0, 0.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.0, 1.0, 0.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.0, 0.0, 1.0], unit="mm"),
            ],
        )

        zarr_path = tmp_path / "scanner.zarr"
        store = zarr.storage.LocalStore(str(zarr_path))
        zarr.create_array(
            store,
            data=data,
            chunks=(4, 4, 4),
            dimension_names=["i", "j", "k"],
            attributes={"nrrd": meta.model_dump(exclude_none=True)},
            fill_value=0,
        )

        nii_path = tmp_path / "scanner.nii"
        zarr_to_nifti(zarr_path, nii_path)

        with open(str(nii_path), "rb") as fh:
            raw_hdr = nib.Nifti1Header.from_fileobj(fh)
        assert int(raw_hdr["sform_code"]) == 1
        assert int(raw_hdr["qform_code"]) == 1

    def test_oblique_affine_from_convention(self, tmp_path):
        """Oblique space_directions → correct oblique sform."""
        import zarr
        from nrrdz.models import AxisKind, AxisMetadata, Centering, NrrdMetadata, SpaceName

        data = np.zeros((4, 4, 4), dtype=np.float32)
        meta = NrrdMetadata(
            version="1.0",
            space=SpaceName.RIGHT_ANTERIOR_SUPERIOR,
            space_origin=[-50.0, -60.0, -30.0],
            axes=[
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.9, 0.3, -0.1], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[-0.3, 0.8, 0.2], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.1, -0.2, 2.5], unit="mm"),
            ],
        )

        zarr_path = tmp_path / "oblique.zarr"
        store = zarr.storage.LocalStore(str(zarr_path))
        zarr.create_array(
            store,
            data=data,
            chunks=(4, 4, 4),
            dimension_names=["i", "j", "k"],
            attributes={"nrrd": meta.model_dump(exclude_none=True)},
            fill_value=0,
        )

        nii_path = tmp_path / "oblique.nii"
        zarr_to_nifti(zarr_path, nii_path)

        img = nib.load(str(nii_path))
        sform = img.get_sform()
        # Check columns match space_directions
        np.testing.assert_allclose(sform[:3, 0], [0.9, 0.3, -0.1], atol=1e-6)
        np.testing.assert_allclose(sform[:3, 1], [-0.3, 0.8, 0.2], atol=1e-6)
        np.testing.assert_allclose(sform[:3, 2], [0.1, -0.2, 2.5], atol=1e-6)
        np.testing.assert_allclose(sform[:3, 3], [-50, -60, -30], atol=1e-6)

    def test_value_transforms_from_convention(self, tmp_path):
        """value_transforms without NIfTI extension → scl_slope/scl_inter."""
        import zarr
        from nrrdz.models import (
            AxisKind, AxisMetadata, Centering, NrrdMetadata, SpaceName, ValueTransform,
        )

        data = np.arange(64, dtype=np.int16).reshape(4, 4, 4)
        meta = NrrdMetadata(
            version="1.0",
            space=SpaceName.SCANNER_XYZ,
            space_origin=[0.0, 0.0, 0.0],
            value_transforms=[
                ValueTransform(name="linear", parameters={"slope": 0.5, "intercept": -100.0}),
            ],
            axes=[
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[1.0, 0.0, 0.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.0, 1.0, 0.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.0, 0.0, 1.0], unit="mm"),
            ],
        )

        zarr_path = tmp_path / "vt.zarr"
        store = zarr.storage.LocalStore(str(zarr_path))
        zarr.create_array(
            store,
            data=data,
            chunks=(4, 4, 4),
            dimension_names=["i", "j", "k"],
            attributes={"nrrd": meta.model_dump(exclude_none=True)},
            fill_value=0,
        )

        nii_path = tmp_path / "vt.nii"
        zarr_to_nifti(zarr_path, nii_path)

        slope, inter = _read_raw_slope_inter(nii_path)
        assert slope == pytest.approx(0.5)
        assert inter == pytest.approx(-100.0)

        # Raw data preserved
        img = nib.load(str(nii_path))
        np.testing.assert_array_equal(img.dataobj.get_unscaled(), data)

    def test_dicom_sourced_zarr_to_nifti(self, tmp_path):
        """Zarr from DICOM conversion (LPS, no NIfTI extension) → NIfTI."""
        import zarr
        from nrrdz.models import AxisKind, AxisMetadata, Centering, NrrdMetadata, SpaceName

        data = np.zeros((30, 512, 512), dtype=np.int16)
        meta = NrrdMetadata(
            version="1.0",
            space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
            space_origin=[0.0, 0.0, 0.0],
            axes=[
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.0, 0.0, 3.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.0, 0.5, 0.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                             space_direction=[0.5, 0.0, 0.0], unit="mm"),
            ],
        )

        zarr_path = tmp_path / "dicom_src.zarr"
        store = zarr.storage.LocalStore(str(zarr_path))
        zarr.create_array(
            store,
            data=data,
            chunks=(30, 64, 64),
            dimension_names=["k", "j", "i"],
            attributes={"nrrd": meta.model_dump(exclude_none=True)},
            fill_value=0,
        )

        nii_path = tmp_path / "dicom_src.nii"
        zarr_to_nifti(zarr_path, nii_path)

        img = nib.load(str(nii_path))
        assert img.shape == (30, 512, 512)
        sform = img.get_sform()
        # Space directions as sform columns
        np.testing.assert_allclose(sform[:3, 0], [0, 0, 3], atol=1e-6)
        np.testing.assert_allclose(sform[:3, 1], [0, 0.5, 0], atol=1e-6)
        np.testing.assert_allclose(sform[:3, 2], [0.5, 0, 0], atol=1e-6)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_overwrite_protection(self, tmp_path):
        """Zarr → NIfTI raises when output exists and overwrite=False."""
        nii_path = tmp_path / "test.nii"
        img = _make_nifti()
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "test.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        out_path = tmp_path / "out.nii"
        zarr_to_nifti(zarr_path, out_path)

        # Second call should fail
        with pytest.raises(FileExistsError):
            zarr_to_nifti(zarr_path, out_path)

        # With overwrite should succeed
        zarr_to_nifti(zarr_path, out_path, overwrite=True)

    def test_nii_gz_output(self, tmp_path):
        """Writing to .nii.gz produces compressed output."""
        nii_path = tmp_path / "test.nii"
        img = _make_nifti()
        _save_nifti(img, nii_path)

        zarr_path = tmp_path / "test.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        gz_path = tmp_path / "out.nii.gz"
        zarr_to_nifti(zarr_path, gz_path)

        assert gz_path.exists()
        rt_img = nib.load(str(gz_path))
        assert rt_img.shape == (8, 8, 4)

    def test_raw_data_not_scaled(self, tmp_path):
        """Data stored is raw (not scaled by scl_slope/scl_inter)."""
        nii_path = tmp_path / "raw.nii"
        data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]]], dtype=np.int16)
        img = nib.Nifti1Image(data, np.eye(4))
        _save_nifti(img, nii_path)
        # Patch slope/intercept in raw header
        _patch_slope_inter(nii_path, 2.0, 100.0)

        zarr_path = tmp_path / "raw.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        stored_data = arr[:]

        # Should be raw stored values, NOT 2*data+100
        np.testing.assert_array_equal(stored_data, data)

    def test_value_transforms_roundtrip(self, tmp_path):
        """scl_slope/scl_inter round-trips through Zarr."""
        nii_path = tmp_path / "vt.nii"
        img = _make_nifti()
        _save_nifti(img, nii_path)
        # Patch slope/intercept in raw header
        _patch_slope_inter(nii_path, 3.5, -50.0)

        zarr_path = tmp_path / "vt.zarr"
        nifti_to_zarr(nii_path, zarr_path)

        rt_path = tmp_path / "vt_rt.nii"
        zarr_to_nifti(zarr_path, rt_path)

        slope, inter = _read_raw_slope_inter(rt_path)
        assert slope == pytest.approx(3.5)
        assert inter == pytest.approx(-50.0)
