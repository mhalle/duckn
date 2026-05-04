"""Tests for ``read_array`` and value-transform application."""

from __future__ import annotations

import numpy as np
import pytest
import zarr

from duckn.models import (
    AxisKind,
    AxisMetadata,
    DucknMetadata,
    SpaceName,
    ValueTransform,
)
from duckn.zarr_io import open_store, read_array, read_metadata


def _write_test_store(path, data: np.ndarray, value_transforms=None):
    """Write a minimal duckn store at ``path`` for testing."""
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


class TestReadArrayWithoutTransforms:
    def test_no_transforms_returns_raw_dtype(self, tmp_path):
        data = np.arange(2 * 3 * 4, dtype=np.int16).reshape(2, 3, 4)
        store_path = tmp_path / "raw.zarr"
        _write_test_store(store_path, data)

        out = read_array(store_path)
        assert out.dtype == np.int16
        np.testing.assert_array_equal(out, data)

    def test_opt_out_returns_raw_even_with_transforms(self, tmp_path):
        data = np.arange(2 * 3 * 4, dtype=np.int16).reshape(2, 3, 4)
        vt = [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 5.0})]
        store_path = tmp_path / "opt_out.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        out = read_array(store_path, apply_value_transforms=False)
        assert out.dtype == np.int16
        np.testing.assert_array_equal(out, data)


class TestReadArrayAppliesLinear:
    def test_linear_transform_applied(self, tmp_path):
        data = np.arange(2 * 3 * 4, dtype=np.int16).reshape(2, 3, 4)
        vt = [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 5.0})]
        store_path = tmp_path / "linear.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        out = read_array(store_path)
        assert out.dtype == np.float32
        np.testing.assert_allclose(out, data.astype(np.float32) * 2.0 + 5.0)

    def test_ct_hu_round_trip(self, tmp_path):
        """Realistic CT case: stored uint16 + intercept=-1024 -> HU."""
        data = np.array([[[0, 1024, 2048]]], dtype=np.uint16)
        vt = [ValueTransform(name="linear", parameters={"slope": 1.0, "intercept": -1024.0})]
        store_path = tmp_path / "ct.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        out = read_array(store_path)
        assert out.dtype == np.float32
        np.testing.assert_allclose(out, [[[-1024.0, 0.0, 1024.0]]])

    def test_identity_transform_preserves_dtype(self, tmp_path):
        data = np.arange(6, dtype=np.int16).reshape(2, 3, 1)
        vt = [ValueTransform(name="linear", parameters={"slope": 1.0, "intercept": 0.0})]
        store_path = tmp_path / "identity.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        out = read_array(store_path)
        # Identity transform → no rescale → dtype preserved
        assert out.dtype == np.int16
        np.testing.assert_array_equal(out, data)


class TestReadArrayMultipleTransforms:
    def test_two_linear_transforms_compose(self, tmp_path):
        """Linear transforms compose: y = b * (a*x + c) + d."""
        data = np.array([[[1.0, 2.0]]], dtype=np.float32)
        vt = [
            ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 1.0}),
            ValueTransform(name="linear", parameters={"slope": 3.0, "intercept": 4.0}),
        ]
        store_path = tmp_path / "two.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        out = read_array(store_path)
        # Applied in order: first 2x+1, then 3y+4 → 3*(2x+1)+4 = 6x+7
        np.testing.assert_allclose(out, np.array([[[13.0, 19.0]]]))

    def test_unknown_transform_warns_and_skips(self, tmp_path):
        data = np.array([[[1.0, 2.0]]], dtype=np.float32)
        vt = [
            ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 0.0}),
            # An unknown name still passes pydantic validation since
            # only "linear" is constrained; should be skipped at read.
            ValueTransform(name="custom-nonlinear", parameters={"k": 1.0}),
        ]
        store_path = tmp_path / "unknown.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        with pytest.warns(UserWarning, match="custom-nonlinear"):
            out = read_array(store_path)
        np.testing.assert_allclose(out, np.array([[[2.0, 4.0]]]))


class TestReadMetadata:
    def test_read_metadata_returns_parsed_model(self, tmp_path):
        data = np.zeros((2, 2, 2), dtype=np.uint8)
        vt = [ValueTransform(name="linear", parameters={"slope": 1.5, "intercept": -10.0})]
        store_path = tmp_path / "meta.zarr"
        _write_test_store(store_path, data, value_transforms=vt)

        meta = read_metadata(store_path)
        assert isinstance(meta, DucknMetadata)
        assert meta.value_transforms is not None
        assert meta.value_transforms[0].parameters == {"slope": 1.5, "intercept": -10.0}
