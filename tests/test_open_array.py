"""Tests for ``open_array`` and the ``DucknArray`` wrapper."""

from __future__ import annotations

import numpy as np
import pytest
import zarr

from duckn import DucknArray, open_array, read_metadata
from duckn.models import (
    AxisKind,
    AxisMetadata,
    DucknMetadata,
    SpaceName,
    ValueTransform,
)
from duckn.zarr_io import open_store


def _write_test_store(path, data: np.ndarray, value_transforms=None):
    axes = [
        AxisMetadata(kind=AxisKind.SPACE, space_direction=[1.0, 0.0, 0.0], unit="mm"),
        AxisMetadata(kind=AxisKind.SPACE, space_direction=[0.0, 1.0, 0.0], unit="mm"),
        AxisMetadata(kind=AxisKind.SPACE, space_direction=[0.0, 0.0, 1.0], unit="mm"),
    ]
    meta = DucknMetadata(
        space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
        space_origin=[0.0, 0.0, 0.0],
        axes=axes,
        value_transforms=value_transforms,
    )
    with open_store(path, mode="w") as store:
        arr = zarr.create_array(
            store=store,
            shape=data.shape,
            dtype=data.dtype,
            chunks=data.shape,
            overwrite=True,
        )
        arr[:] = data
        arr.attrs["duckn"] = meta.model_dump(exclude_none=True)


class TestSlicingApplication:
    def test_no_transforms_preserves_dtype(self, tmp_path):
        data = np.arange(2 * 3 * 4, dtype=np.int16).reshape(2, 3, 4)
        store_path = tmp_path / "raw.zarr"
        _write_test_store(store_path, data)

        arr = open_array(store_path)
        assert arr.dtype == np.int16
        np.testing.assert_array_equal(arr[:], data)

    def test_linear_transform_applied(self, tmp_path):
        data = np.arange(24, dtype=np.int16).reshape(2, 3, 4)
        vt = [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 5.0})]
        store_path = tmp_path / "linear.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path)
        assert arr.dtype == np.float32
        np.testing.assert_allclose(arr[:], data.astype(np.float32) * 2.0 + 5.0)

    def test_partial_slice_also_transformed(self, tmp_path):
        data = np.arange(24, dtype=np.int16).reshape(2, 3, 4)
        vt = [ValueTransform(name="linear", parameters={"slope": 1.0, "intercept": -1024.0})]
        store_path = tmp_path / "partial.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path)
        out = arr[1, :, :]
        np.testing.assert_allclose(out, data[1].astype(np.float32) - 1024.0)

    def test_identity_transform_preserves_dtype(self, tmp_path):
        data = np.arange(6, dtype=np.int16).reshape(2, 3, 1)
        vt = [ValueTransform(name="linear", parameters={"slope": 1.0, "intercept": 0.0})]
        store_path = tmp_path / "identity.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path)
        assert arr.dtype == np.int16
        np.testing.assert_array_equal(arr[:], data)

    def test_two_linear_transforms_compose(self, tmp_path):
        data = np.array([[[1.0, 2.0]]], dtype=np.float32)
        vt = [
            ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 1.0}),
            ValueTransform(name="linear", parameters={"slope": 3.0, "intercept": 4.0}),
        ]
        store_path = tmp_path / "two.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path)
        # Composed: 3*(2x+1)+4 = 6x+7
        np.testing.assert_allclose(arr[:], np.array([[[13.0, 19.0]]]))

    def test_unknown_transform_warns_and_skips(self, tmp_path):
        data = np.array([[[1.0, 2.0]]], dtype=np.float32)
        vt = [
            ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 0.0}),
            ValueTransform(name="custom-nonlinear", parameters={"k": 1.0}),
        ]
        store_path = tmp_path / "unknown.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        with pytest.warns(UserWarning, match="custom-nonlinear"):
            arr = open_array(store_path)
        np.testing.assert_allclose(arr[:], np.array([[[2.0, 4.0]]]))


class TestToggle:
    def test_toggle_at_open(self, tmp_path):
        data = np.arange(6, dtype=np.int16).reshape(2, 3)
        vt = [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 5.0})]
        store_path = tmp_path / "toggle.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        raw = open_array(store_path, apply_value_transforms=False)
        assert raw.dtype == np.int16
        np.testing.assert_array_equal(raw[:], data)

    def test_runtime_toggle_changes_output(self, tmp_path):
        data = np.arange(6, dtype=np.int16).reshape(2, 3)
        vt = [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 5.0})]
        store_path = tmp_path / "runtime.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path)
        np.testing.assert_allclose(arr[:], data * 2.0 + 5.0)
        assert arr.dtype == np.float32

        arr.apply_value_transforms = False
        np.testing.assert_array_equal(arr[:], data)
        assert arr.dtype == np.int16

        arr.apply_value_transforms = True
        np.testing.assert_allclose(arr[:], data * 2.0 + 5.0)


class TestProperties:
    def test_shape_and_chunks_forwarded(self, tmp_path):
        data = np.arange(24, dtype=np.uint16).reshape(2, 3, 4)
        store_path = tmp_path / "props.zarr"
        _write_test_store(store_path, data)

        arr = open_array(store_path)
        assert arr.shape == (2, 3, 4)
        assert arr.chunks == (2, 3, 4)
        assert arr.ndim == 3
        assert arr.size == 24

    def test_metadata_property(self, tmp_path):
        data = np.zeros((2, 2, 2), dtype=np.uint8)
        vt = [ValueTransform(name="linear", parameters={"slope": 1.5, "intercept": -10.0})]
        store_path = tmp_path / "meta.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path)
        assert isinstance(arr.metadata, DucknMetadata)
        assert arr.metadata.value_transforms[0].parameters == {
            "slope": 1.5,
            "intercept": -10.0,
        }

    def test_zarr_metadata_distinct_from_metadata(self, tmp_path):
        """arr.zarr.metadata gives zarr-level array info, distinct from arr.metadata."""
        data = np.zeros((2, 2), dtype=np.uint8)
        store_path = tmp_path / "zarr_meta.zarr"
        _write_test_store(store_path, data)

        arr = open_array(store_path)
        # Underlying zarr metadata exists and has zarr-level fields
        zarr_meta = arr.zarr.metadata
        assert hasattr(zarr_meta, "shape")

    def test_attrs_forwards_to_zarr(self, tmp_path):
        data = np.zeros((2, 2), dtype=np.uint8)
        store_path = tmp_path / "attrs.zarr"
        _write_test_store(store_path, data)

        arr = open_array(store_path)
        assert "duckn" in dict(arr.attrs)

    def test_zarr_returns_underlying_array(self, tmp_path):
        data = np.arange(6, dtype=np.uint16).reshape(2, 3)
        vt = [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 0.0})]
        store_path = tmp_path / "zarr_prop.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path)
        # arr.zarr should bypass transforms — original dtype, unscaled values
        np.testing.assert_array_equal(arr.zarr[:], data)
        assert arr.zarr.dtype == np.uint16


class TestNumpyInterop:
    def test_np_asarray(self, tmp_path):
        data = np.arange(6, dtype=np.int16).reshape(2, 3)
        vt = [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 1.0})]
        store_path = tmp_path / "asarray.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path)
        out = np.asarray(arr)
        assert out.dtype == np.float32
        np.testing.assert_allclose(out, data * 2.0 + 1.0)

    def test_len(self, tmp_path):
        data = np.zeros((5, 3), dtype=np.uint8)
        store_path = tmp_path / "len.zarr"
        _write_test_store(store_path, data)
        arr = open_array(store_path)
        assert len(arr) == 5


class TestContextManager:
    def test_works_with_zip_store(self, tmp_path):
        data = np.arange(24, dtype=np.uint16).reshape(2, 3, 4)
        vt = [ValueTransform(name="linear", parameters={"slope": 1.0, "intercept": -100.0})]

        # Write a .zarr.zip
        zip_path = tmp_path / "test.zarr.zip"
        with open_store(zip_path, mode="w") as store:
            arr = zarr.create_array(
                store=store, shape=data.shape, dtype=data.dtype, chunks=data.shape
            )
            arr[:] = data
            axes = [
                AxisMetadata(kind=AxisKind.SPACE, space_direction=[1.0, 0.0, 0.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, space_direction=[0.0, 1.0, 0.0], unit="mm"),
                AxisMetadata(kind=AxisKind.SPACE, space_direction=[0.0, 0.0, 1.0], unit="mm"),
            ]
            meta = DucknMetadata(
                space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
                space_origin=[0.0, 0.0, 0.0],
                axes=axes,
                value_transforms=vt,
            )
            arr.attrs["duckn"] = meta.model_dump(exclude_none=True)

        with open_array(zip_path) as h:
            np.testing.assert_allclose(h[:], data.astype(np.float32) - 100.0)
            assert h.dtype == np.float32


class TestReadMetadataStillWorks:
    def test_read_metadata_unchanged(self, tmp_path):
        data = np.zeros((2, 2, 2), dtype=np.uint8)
        vt = [ValueTransform(name="linear", parameters={"slope": 3.0, "intercept": 7.0})]
        store_path = tmp_path / "rm.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        meta = read_metadata(store_path)
        assert isinstance(meta, DucknMetadata)
        assert meta.value_transforms[0].parameters == {"slope": 3.0, "intercept": 7.0}


class TestTransformDtype:
    def test_float64_output(self, tmp_path):
        data = np.arange(6, dtype=np.int16).reshape(2, 3)
        vt = [ValueTransform(name="linear", parameters={"slope": 0.1, "intercept": 0.0})]
        store_path = tmp_path / "f64.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path, transform_dtype=np.float64)
        out = arr[:]
        assert out.dtype == np.float64
        np.testing.assert_allclose(out, data * 0.1, rtol=1e-12)

    def test_int16_output_rounds(self, tmp_path):
        # slope/intercept produce values that need rounding (not truncation)
        data = np.array([[0, 1, 2, 3, 4]], dtype=np.uint8)
        vt = [ValueTransform(name="linear", parameters={"slope": 0.7, "intercept": 0.0})]
        store_path = tmp_path / "i16.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path, transform_dtype=np.int16)
        out = arr[:]
        assert out.dtype == np.int16
        # data * 0.7 = [0, 0.7, 1.4, 2.1, 2.8] → rint → [0, 1, 1, 2, 3]
        np.testing.assert_array_equal(out, [[0, 1, 1, 2, 3]])

    def test_int8_output(self, tmp_path):
        data = np.array([[0, 50, 100, 150, 200]], dtype=np.uint8)
        # 0.5*x - 50: [-50, -25, 0, 25, 50] — fits in int8
        vt = [ValueTransform(name="linear", parameters={"slope": 0.5, "intercept": -50.0})]
        store_path = tmp_path / "i8.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path, transform_dtype=np.int8)
        out = arr[:]
        assert out.dtype == np.int8
        np.testing.assert_array_equal(out, [[-50, -25, 0, 25, 50]])

    def test_transform_dtype_overrides_identity_bypass(self, tmp_path):
        data = np.arange(6, dtype=np.int16).reshape(2, 3)
        vt = [ValueTransform(name="linear", parameters={"slope": 1.0, "intercept": 0.0})]
        store_path = tmp_path / "id_override.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        # Default: identity bypass keeps int16
        arr = open_array(store_path)
        assert arr.dtype == np.int16

        # With transform_dtype set: bypass disabled, output respects the override
        arr2 = open_array(store_path, transform_dtype=np.float64)
        assert arr2.dtype == np.float64
        out = arr2[:]
        assert out.dtype == np.float64
        np.testing.assert_array_equal(out, data.astype(np.float64))

    def test_transform_dtype_ignored_when_apply_false(self, tmp_path):
        data = np.arange(6, dtype=np.int16).reshape(2, 3)
        vt = [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 0.0})]
        store_path = tmp_path / "ignore.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(
            store_path,
            apply_value_transforms=False,
            transform_dtype=np.float64,
        )
        # apply_value_transforms=False wins — raw output, native dtype
        assert arr.dtype == np.int16
        np.testing.assert_array_equal(arr[:], data)


class TestRepr:
    def test_repr_shows_mode(self, tmp_path):
        data = np.zeros((2, 2), dtype=np.int16)
        vt = [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 0.0})]
        store_path = tmp_path / "repr.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        arr = open_array(store_path)
        r = repr(arr)
        assert "DucknArray" in r
        assert "transformed" in r

        arr.apply_value_transforms = False
        assert "raw" in repr(arr)


class TestDucknArrayDirectConstruction:
    def test_construct_from_zarr_array(self, tmp_path):
        data = np.arange(6, dtype=np.uint16).reshape(2, 3)
        vt = [ValueTransform(name="linear", parameters={"slope": 4.0, "intercept": 0.0})]
        store_path = tmp_path / "direct.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        with open_store(store_path, mode="r") as store:
            zarr_arr = zarr.open_array(store=store, mode="r")
            handle = DucknArray(zarr_arr)
            np.testing.assert_allclose(handle[:], data * 4.0)
