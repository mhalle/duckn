"""Metadata must never disagree with the bytes it describes.

duckn-spec §4.1 states the invariant: stored values interpreted through
`value_transforms` must equal the intended quantity. These tests pin down
the places where that invariant was previously broken.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from duckn.models import AxisMetadata, DucknMetadata
from duckn.volume import Volume


def _ct_volume(raw=None):
    """A CT-like volume: uint16 stored, HU via a linear transform."""
    meta = DucknMetadata(
        version="1.0",
        space="left-posterior-superior",
        space_origin=[0.0, 0.0, 0.0],
        sample_units="HU",
        value_transforms=[
            {"name": "linear", "parameters": {"slope": 1.0, "intercept": -1024.0}}
        ],
        axes=[
            AxisMetadata(kind="space", space_direction=[1.0, 0, 0], unit="mm"),
            AxisMetadata(kind="space", space_direction=[0, 1.0, 0], unit="mm"),
            AxisMetadata(kind="space", space_direction=[0, 0, 1.0], unit="mm"),
        ],
    )
    if raw is None:
        raw = np.full((2, 2, 2), 1000, dtype=np.uint16)
    return Volume(raw=raw, metadata=meta)


class TestAdapterRoundTripPreservesQuantity:
    """`to_*` emits calibrated values, so `from_*` must not re-apply them."""

    def test_sitk_round_trip(self):
        pytest.importorskip("SimpleITK")
        from duckn.sitk_adapter import from_sitk, to_sitk

        vol = _ct_volume()
        rt = from_sitk(to_sitk(vol), metadata=vol.metadata)
        np.testing.assert_allclose(rt.data, vol.data)
        assert rt.metadata.value_transforms is None
        # the quantity is unchanged, so its name must survive
        assert rt.metadata.sample_units == "HU"

    def test_nibabel_round_trip(self):
        pytest.importorskip("nibabel")
        from duckn.nibabel_adapter import from_nifti, to_nifti

        vol = _ct_volume()
        rt = from_nifti(to_nifti(vol), metadata=vol.metadata)
        np.testing.assert_allclose(rt.data, vol.data)
        assert rt.metadata.value_transforms is None

    def test_vtk_round_trip(self):
        pytest.importorskip("vtk")
        from duckn.vtk_adapter import from_vtk, to_vtk

        vol = _ct_volume()
        rt = from_vtk(to_vtk(vol), metadata=vol.metadata)
        np.testing.assert_allclose(rt.data, vol.data)
        assert rt.metadata.value_transforms is None

    def test_error_would_have_been_exactly_the_intercept(self):
        """Regression guard: the old bug shifted CT values by 1024 HU."""
        pytest.importorskip("SimpleITK")
        from duckn.sitk_adapter import from_sitk, to_sitk

        vol = _ct_volume()
        rt = from_sitk(to_sitk(vol), metadata=vol.metadata)
        assert float(np.max(np.abs(rt.data - vol.data))) == 0.0


class TestWritersAgreeWithTheirMetadata:
    """A file must not declare units over values that are not in them."""

    LINEAR = [{"name": "linear", "parameters": {"slope": 1.0, "intercept": -1024.0}}]
    LUT = [{"name": "lut", "parameters": {"first_value": 1000, "values": [-24.0, -23.0, -22.0, -21.0]}}]

    def _store(self, tmp_path, name, transforms):
        import zarr

        raw = np.arange(1000, 1008, dtype=np.uint16).reshape(2, 2, 2)
        path = tmp_path / name
        arr = zarr.create_array(
            store=str(path), shape=raw.shape, dtype="uint16",
            chunks=raw.shape, zarr_format=3,
        )
        arr[:] = raw
        arr.attrs["duckn"] = {
            "version": "1.1",
            "space": "left-posterior-superior",
            "space_origin": [0.0, 0.0, 0.0],
            "sample_units": "HU",
            "value_transforms": transforms,
            "axes": [
                {"kind": "space", "space_direction": v, "unit": "mm"}
                for v in ([0, 0, 1.0], [0, 1.0, 0], [1.0, 0, 0])
            ],
        }
        return path

    def test_nrrd_materializes(self, tmp_path):
        """NRRD cannot carry a transform, so it must write calibrated values."""
        nrrd = pytest.importorskip("nrrd")
        from duckn.convert import zarr_to_nrrd

        src = self._store(tmp_path, "a.zarr", self.LINEAR)
        zarr_to_nrrd(src, tmp_path / "a.nrrd", overwrite=True)
        values, header = nrrd.read(str(tmp_path / "a.nrrd"), index_order="C")
        np.testing.assert_allclose(values.ravel()[:3], [-24.0, -23.0, -22.0])
        assert header.get("sample units") == "HU"

    def test_nifti_preserves_a_single_linear_via_scl(self, tmp_path):
        nib = pytest.importorskip("nibabel")
        from duckn.nifti_convert import zarr_to_nifti

        src = self._store(tmp_path, "b.zarr", self.LINEAR)
        zarr_to_nifti(src, tmp_path / "b.nii", overwrite=True)
        img = nib.load(str(tmp_path / "b.nii"))
        assert float(img.dataobj.slope) == 1.0
        assert float(img.dataobj.inter) == -1024.0
        np.testing.assert_allclose(img.get_fdata().ravel()[:3], [-24.0, -23.0, -22.0])

    def test_nifti_materializes_a_lut(self, tmp_path):
        """scl_slope/inter cannot express a table, so write the values."""
        nib = pytest.importorskip("nibabel")
        from duckn.nifti_convert import zarr_to_nifti

        src = self._store(tmp_path, "c.zarr", self.LUT)
        zarr_to_nifti(src, tmp_path / "c.nii", overwrite=True)
        img = nib.load(str(tmp_path / "c.nii"))
        np.testing.assert_allclose(img.get_fdata().ravel()[:3], [-24.0, -23.0, -22.0])

    def test_dicom_derives_rescale_from_value_transforms(self, tmp_path):
        pydicom = pytest.importorskip("pydicom")
        from duckn.dicom_convert import zarr_to_dicom

        src = self._store(tmp_path, "d.zarr", self.LINEAR)
        zarr_to_dicom(src, tmp_path / "d.dcm", overwrite=True)
        ds = pydicom.dcmread(str(tmp_path / "d.dcm"))
        assert float(ds.RescaleSlope) == 1.0
        assert float(ds.RescaleIntercept) == -1024.0
        assert str(ds.RescaleType) == "HU"

    def test_dicom_writes_a_representable_lut_as_a_sequence(self, tmp_path):
        pydicom = pytest.importorskip("pydicom")
        from duckn.dicom_convert import zarr_to_dicom

        lut = [{"name": "lut", "parameters": {"first_value": 1000, "values": [10.0, 20.0, 30.0, 40.0]}}]
        src = self._store(tmp_path, "e.zarr", lut)
        zarr_to_dicom(src, tmp_path / "e.dcm", overwrite=True)
        ds = pydicom.dcmread(str(tmp_path / "e.dcm"))
        assert "ModalityLUTSequence" in ds
        # mutually exclusive with a linear rescale (PS3.3 C.11.1.1.2)
        assert "RescaleSlope" not in ds

    def test_dicom_refuses_an_unrepresentable_lut(self, tmp_path):
        """LUT Data is unsigned, so negative HU has no representation."""
        pytest.importorskip("pydicom")
        from duckn.dicom_convert import zarr_to_dicom

        src = self._store(tmp_path, "f.zarr", self.LUT)
        with pytest.raises(ValueError, match=r"\[0, 65535\]"):
            zarr_to_dicom(src, tmp_path / "f.dcm", overwrite=True)

    def test_dicom_refuses_a_chain_it_cannot_express(self, tmp_path):
        pytest.importorskip("pydicom")
        from duckn.dicom_convert import zarr_to_dicom

        chain = [
            {"name": "linear", "parameters": {"slope": 2.0, "intercept": 0.0}},
            {"name": "linear", "parameters": {"slope": 1.0, "intercept": -100.0}},
        ]
        src = self._store(tmp_path, "g.zarr", chain)
        with pytest.raises(ValueError, match="Modality LUT"):
            zarr_to_dicom(src, tmp_path / "g.dcm", overwrite=True)


class TestPerInstanceRescaleVariation:
    """One value_transform cannot describe a series that disagrees."""

    def _series(self, intercepts):
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset

        out = []
        for i, intercept in enumerate(intercepts):
            ds = Dataset()
            ds.Rows = ds.Columns = 2
            ds.ImagePositionPatient = [0.0, 0.0, float(i)]
            ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
            ds.PixelSpacing = [1.0, 1.0]
            ds.SliceThickness = 1.0
            ds.BitsAllocated = ds.BitsStored = 16
            ds.HighBit = 15
            ds.PixelRepresentation = 0
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.SeriesInstanceUID = "1.2.3"
            ds.Modality = "CT"
            ds.RescaleSlope = 1.0
            if intercept is not None:
                ds.RescaleIntercept = intercept
            out.append(ds)
        return out

    def test_uniform_series_is_calibrated(self):
        from duckn.dicom_convert import _uniform_rescale_value

        datasets = self._series([-1024.0, -1024.0])
        assert _uniform_rescale_value(datasets, "RescaleIntercept", float) == -1024.0

    def test_varying_series_warns_and_declines(self):
        """Adopting slice 0's mapping would misreport every other slice."""
        from duckn.dicom_convert import _uniform_rescale_value

        datasets = self._series([-1024.0, -2048.0])
        with pytest.warns(UserWarning, match="varies across the series"):
            result = _uniform_rescale_value(datasets, "RescaleIntercept", float)
        assert result is None

    def test_partially_present_warns_and_declines(self):
        from duckn.dicom_convert import _uniform_rescale_value

        datasets = self._series([-1024.0, None])
        with pytest.warns(UserWarning, match="varies across the series"):
            assert _uniform_rescale_value(datasets, "RescaleIntercept", float) is None

    def test_absent_everywhere_is_silent(self):
        from duckn.dicom_convert import _uniform_rescale_value

        datasets = self._series([None, None])
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert _uniform_rescale_value(datasets, "RescaleIntercept", float) is None
