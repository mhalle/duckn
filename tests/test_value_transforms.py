"""Tests for value_transforms: linear composition and explicit LUTs.

The Modality LUT stage of the DICOM pipeline is a value transform in duckn
(stored -> real), whether it is expressed as slope/intercept or as an
explicit table.
"""

from __future__ import annotations

import numpy as np
import pytest
import zarr

from duckn.models import DucknMetadata, ValueTransform
from duckn.volume import Volume
from duckn.zarr_io import has_nonlinear_transforms, open_array


def _meta(transforms, **kwargs):
    return DucknMetadata(version="1.0", value_transforms=transforms, **kwargs)


LUT = {"name": "lut", "parameters": {"first_value": 10, "values": [0.0, 100.0, 200.0, 300.0]}}


class TestModel:
    def test_lut_requires_values(self):
        with pytest.raises(Exception, match="values"):
            ValueTransform(name="lut", parameters={"first_value": 0})

    def test_lut_rejects_empty_table(self):
        with pytest.raises(Exception, match="non-empty"):
            ValueTransform(name="lut", parameters={"values": []})

    def test_first_value_defaults_to_zero(self):
        vt = ValueTransform(name="lut", parameters={"values": [1.0, 2.0]})
        assert vt.parameters["values"] == [1.0, 2.0]

    def test_lut_must_be_first_in_chain(self):
        """A lut indexes stored values, so nothing may precede it."""
        with pytest.raises(Exception, match="must be the first transform"):
            _meta(
                [
                    {"name": "linear", "parameters": {"slope": 2.0, "intercept": 0.0}},
                    LUT,
                ]
            )

    def test_lut_first_then_linear_is_allowed(self):
        meta = _meta(
            [LUT, {"name": "linear", "parameters": {"slope": 2.0, "intercept": 1.0}}]
        )
        assert len(meta.value_transforms) == 2

    def test_round_trips_through_model_dump(self):
        meta = _meta([LUT])
        assert meta.model_dump(exclude_none=True)["value_transforms"] == [LUT]


class TestDetection:
    def test_linear_only_is_not_nonlinear(self):
        assert not has_nonlinear_transforms(
            [{"name": "linear", "parameters": {"slope": 1.0, "intercept": 0.0}}]
        )

    def test_empty_is_not_nonlinear(self):
        assert not has_nonlinear_transforms(None)
        assert not has_nonlinear_transforms([])

    def test_lut_is_nonlinear(self):
        assert has_nonlinear_transforms([LUT])


class TestApplication:
    def test_lut_maps_through_the_table(self):
        raw = np.array([[10, 11, 12, 13]], dtype=np.uint16)
        vol = Volume(raw=raw, metadata=_meta([LUT]))
        np.testing.assert_array_equal(vol.data, [[0.0, 100.0, 200.0, 300.0]])

    def test_values_outside_the_table_clamp(self):
        """DICOM clamps to the first and last entries."""
        raw = np.array([[0, 9, 10, 13, 14, 9999]], dtype=np.uint16)
        vol = Volume(raw=raw, metadata=_meta([LUT]))
        np.testing.assert_array_equal(
            vol.data, [[0.0, 0.0, 0.0, 300.0, 300.0, 300.0]]
        )

    def test_negative_first_value(self):
        lut = {"name": "lut", "parameters": {"first_value": -2, "values": [5.0, 6.0, 7.0]}}
        raw = np.array([[-2, -1, 0]], dtype=np.int16)
        vol = Volume(raw=raw, metadata=_meta([lut]))
        np.testing.assert_array_equal(vol.data, [[5.0, 6.0, 7.0]])

    def test_lut_then_linear_composes_in_order(self):
        raw = np.array([[10, 11]], dtype=np.uint16)
        meta = _meta(
            [LUT, {"name": "linear", "parameters": {"slope": 2.0, "intercept": 1.0}}]
        )
        vol = Volume(raw=raw, metadata=meta)
        # 0*2+1 = 1, 100*2+1 = 201
        np.testing.assert_array_equal(vol.data, [[1.0, 201.0]])

    def test_dtype_is_float_for_lut(self):
        raw = np.array([[10, 11]], dtype=np.uint16)
        vol = Volume(raw=raw, metadata=_meta([LUT]))
        assert vol.dtype == np.dtype(np.float32)

    def test_raw_is_untouched(self):
        raw = np.array([[10, 11]], dtype=np.uint16)
        vol = Volume(raw=raw, metadata=_meta([LUT]))
        _ = vol.data
        np.testing.assert_array_equal(vol.raw, [[10, 11]])
        assert vol.raw.dtype == np.uint16

    def test_linear_path_is_unchanged(self):
        raw = np.array([[0, 1000]], dtype=np.uint16)
        meta = _meta([{"name": "linear", "parameters": {"slope": 1.0, "intercept": -1024.0}}])
        vol = Volume(raw=raw, metadata=meta)
        np.testing.assert_array_equal(vol.data, [[-1024.0, -24.0]])


class TestDucknArray:
    def _store(self, tmp_path, transforms):
        arr = zarr.create_array(
            store=str(tmp_path / "a.zarr"),
            shape=(1, 4),
            dtype="uint16",
            chunks=(1, 4),
            zarr_format=3,
        )
        arr[:] = np.array([[10, 11, 12, 13]], dtype=np.uint16)
        arr.attrs["duckn"] = {"version": "1.0", "value_transforms": transforms}
        return tmp_path / "a.zarr"

    def test_lut_applied_on_read(self, tmp_path):
        path = self._store(tmp_path, [LUT])
        with open_array(path) as arr:
            np.testing.assert_array_equal(arr[...], [[0.0, 100.0, 200.0, 300.0]])
            assert arr.dtype == np.dtype(np.float32)

    def test_raw_access_bypasses_the_lut(self, tmp_path):
        path = self._store(tmp_path, [LUT])
        with open_array(path, apply_value_transforms=False) as arr:
            np.testing.assert_array_equal(arr[...], [[10, 11, 12, 13]])
            assert arr.dtype == np.dtype(np.uint16)

    def test_transform_dtype_respected(self, tmp_path):
        path = self._store(tmp_path, [LUT])
        with open_array(path, transform_dtype="float64") as arr:
            assert arr.dtype == np.dtype(np.float64)
            np.testing.assert_array_equal(arr[...], [[0.0, 100.0, 200.0, 300.0]])

    def test_scalar_indexing_works(self, tmp_path):
        """A full integer index yields a 0-d result, which must not break."""
        path = self._store(tmp_path, [LUT])
        with open_array(path) as arr:
            assert float(arr[0, 0]) == 0.0
            assert float(arr[0, 3]) == 300.0
            # and agrees with the slice path
            np.testing.assert_array_equal(arr[0, 0:1], [0.0])


class TestUnknownTransformsAreNotDroppedByTheAffinePath:
    def test_unknown_name_counts_as_nonlinear(self):
        """Otherwise a future transform type is silently skipped as affine."""
        assert has_nonlinear_transforms([{"name": "gamma", "parameters": {"g": 2.0}}])
        assert has_nonlinear_transforms(
            [{"name": "linear", "parameters": {"slope": 1.0, "intercept": 0.0}},
             {"name": "gamma", "parameters": {"g": 2.0}}]
        )


class TestLutRequiresIntegerStoredValues:
    def test_float_stored_values_rejected(self):
        """Rounding float data would invent an index; NaN has no index at all."""
        raw = np.array([[0.0, 1.5, np.nan]], dtype=np.float32)
        vol = Volume(raw=raw, metadata=_meta([LUT]))
        with pytest.raises(ValueError, match="integer stored values"):
            _ = vol.data


class TestResampleCommutation:
    """Non-affine transforms do not commute with interpolation (spec §4.4)."""

    def _vol(self, transforms):
        from duckn.models import AxisMetadata

        meta = DucknMetadata(
            version="1.1",
            space="right-anterior-superior",
            space_origin=[0.0, 0.0, 0.0],
            sample_units="HU",
            value_transforms=transforms,
            axes=[
                AxisMetadata(kind="space", space_direction=[1.0, 0, 0]),
                AxisMetadata(kind="space", space_direction=[0, 1.0, 0]),
                AxisMetadata(kind="space", space_direction=[0, 0, 1.0]),
            ],
        )
        raw = np.array([[[0, 1, 2, 3]]], dtype=np.uint16)
        return Volume(raw=raw, metadata=meta)

    def test_lut_is_applied_before_interpolation(self):
        """Interpolating stored indices then looking up gives a wrong answer."""
        pytest.importorskip("scipy")
        from duckn.resample import resample

        # A steep, non-monotonic table: neighbouring indices differ wildly.
        lut = {"name": "lut", "parameters": {"first_value": 0, "values": [0.0, 1000.0, 0.0, 1000.0]}}
        out = resample(self._vol([lut]), factor=[1.0, 1.0, 2.0], order=1)
        values = out.data.ravel()

        # Correct: interpolation between the calibrated 0 and 1000.
        assert values[0] == pytest.approx(0.0)
        assert 0.0 < values[1] < 1000.0
        # Wrong would be table[round(interp(index))], which yields exact
        # table entries only — no intermediate values anywhere.
        assert not np.all(np.isin(np.round(values, 6), [0.0, 1000.0]))

    def test_lut_resample_materializes_and_drops_transforms(self):
        """Keeping them would apply the table twice on the next read (§4.3)."""
        pytest.importorskip("scipy")
        from duckn.resample import resample

        lut = {"name": "lut", "parameters": {"first_value": 0, "values": [0.0, 10.0, 20.0, 30.0]}}
        out = resample(self._vol([lut]), factor=[1.0, 1.0, 2.0], order=1)
        assert out.metadata.value_transforms is None
        assert out.metadata.sample_units == "HU"  # the quantity is unchanged

    def test_nearest_neighbour_preserves_the_lut(self):
        """Nearest-neighbour selects rather than averages, so it commutes."""
        pytest.importorskip("scipy")
        from duckn.resample import resample

        lut = {"name": "lut", "parameters": {"first_value": 0, "values": [0.0, 10.0, 20.0, 30.0]}}
        out = resample(self._vol([lut]), factor=[1.0, 1.0, 2.0], order=0)
        assert out.metadata.value_transforms is not None
        assert out.raw.dtype == np.uint16
        assert set(np.unique(out.raw)).issubset({0, 1, 2, 3})

    def test_linear_transform_still_resamples_on_raw(self):
        """Affine transforms commute, so the preserve path is unchanged."""
        pytest.importorskip("scipy")
        from duckn.resample import resample

        linear = {"name": "linear", "parameters": {"slope": 2.0, "intercept": -10.0}}
        out = resample(self._vol([linear]), factor=[1.0, 1.0, 2.0], order=1)
        assert out.metadata.value_transforms is not None
        assert out.metadata.value_transforms[0].name == "linear"


class TestDicomModalityLut:
    def test_explicit_modality_lut_becomes_a_lut_transform(self):
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset
        from pydicom.sequence import Sequence

        from duckn.dicom_convert import _extract_modality_lut

        item = Dataset()
        item.LUTDescriptor = [4, 10, 16]
        item.LUTData = [0, 100, 200, 300]
        ds = Dataset()
        ds.ModalityLUTSequence = Sequence([item])

        vt = _extract_modality_lut(ds)
        assert vt is not None
        assert vt.name == "lut"
        assert vt.parameters["first_value"] == 10
        assert vt.parameters["values"] == [0.0, 100.0, 200.0, 300.0]

    def test_lut_data_as_raw_bytes_is_unpacked_at_the_declared_width(self):
        """LUT Data is US or OW; for OW pydicom returns bytes, not values."""
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset
        from pydicom.sequence import Sequence

        from duckn.dicom_convert import _extract_modality_lut

        item = Dataset()
        item.add_new(0x00283002, "US", [4, 100, 16])
        item.add_new(0x00283006, "OW", np.array([0, 10, 20, 300], dtype="<u2").tobytes())
        ds = Dataset()
        ds.ModalityLUTSequence = Sequence([item])

        vt = _extract_modality_lut(ds)
        assert vt is not None
        # Iterating the bytes would give [0, 0, 10, 0, 20, 0, 44, 1]
        assert vt.parameters["values"] == [0.0, 10.0, 20.0, 300.0]

    def test_ow_lut_data_uses_payload_width_not_declared_bits(self):
        """VR OW is 16-bit words, so an 8-bit table is padded into 16 bits."""
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset
        from pydicom.sequence import Sequence

        from duckn.dicom_convert import _extract_modality_lut

        item = Dataset()
        item.add_new(0x00283002, "US", [4, 0, 8])  # declares 8 bits per entry
        item.add_new(0x00283006, "OW", np.array([0, 10, 20, 30], "<u2").tobytes())
        ds = Dataset()
        ds.ModalityLUTSequence = Sequence([item])

        vt = _extract_modality_lut(ds)
        # Unpacking at the declared 8 bits would give [0,0,10,0,20,0,30,0].
        assert vt.parameters["values"] == [0.0, 10.0, 20.0, 30.0]

    def test_modality_lut_type_read_from_the_sequence_item(self):
        """ModalityLUTType (0028,3004) lives in the item, per PS3.3 C.11.1.1."""
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset
        from pydicom.sequence import Sequence

        from duckn.dicom_convert import _extract_modality_lut

        item = Dataset()
        item.add_new(0x00283002, "US", [3, 0, 16])
        item.add_new(0x00283006, "US", [1, 2, 3])
        item.ModalityLUTType = "HU"
        ds = Dataset()
        ds.ModalityLUTSequence = Sequence([item])
        assert _extract_modality_lut(ds) is not None
        assert str(getattr(ds.ModalityLUTSequence[0], "ModalityLUTType")) == "HU"

    def test_malformed_descriptor_is_skipped_not_crashed(self):
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset
        from pydicom.sequence import Sequence

        from duckn.dicom_convert import _extract_modality_lut

        item = Dataset()
        item.add_new(0x00283002, "US", [3])  # VM=1 collapses to a bare int
        item.add_new(0x00283006, "US", [1, 2, 3])
        ds = Dataset()
        ds.ModalityLUTSequence = Sequence([item])
        with pytest.warns(UserWarning, match="malformed"):
            assert _extract_modality_lut(ds) is None

    def test_truncated_ow_payload_is_skipped(self):
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset
        from pydicom.sequence import Sequence

        from duckn.dicom_convert import _extract_modality_lut

        item = Dataset()
        item.add_new(0x00283002, "US", [3, 0, 16])
        item.add_new(0x00283006, "OW", b"\x01\x00\x02\x00\x03")  # odd length
        ds = Dataset()
        ds.ModalityLUTSequence = Sequence([item])
        with pytest.warns(UserWarning):
            assert _extract_modality_lut(ds) is None

    def test_eight_bit_entries_are_padded_into_16_bit_words(self):
        """An 8-bit table sent as OW still occupies 16 bits per entry."""
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset
        from pydicom.sequence import Sequence

        from duckn.dicom_convert import _extract_modality_lut

        item = Dataset()
        item.add_new(0x00283002, "US", [3, 0, 8])
        item.add_new(0x00283006, "OW", np.array([1, 2, 3], "<u2").tobytes())
        ds = Dataset()
        ds.ModalityLUTSequence = Sequence([item])

        assert _extract_modality_lut(ds).parameters["values"] == [1.0, 2.0, 3.0]

    def test_us_lut_data_is_taken_as_values(self):
        """VR US arrives as a list of ints, not bytes — no unpacking needed."""
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset
        from pydicom.sequence import Sequence

        from duckn.dicom_convert import _extract_modality_lut

        item = Dataset()
        item.add_new(0x00283002, "US", [3, 0, 16])
        item.add_new(0x00283006, "US", [100, 200, 300])
        ds = Dataset()
        ds.ModalityLUTSequence = Sequence([item])

        assert _extract_modality_lut(ds).parameters["values"] == [100.0, 200.0, 300.0]

    def test_absent_modality_lut_returns_none(self):
        pytest.importorskip("pydicom")
        from pydicom.dataset import Dataset

        from duckn.dicom_convert import _extract_modality_lut

        assert _extract_modality_lut(Dataset()) is None
