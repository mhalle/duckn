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
