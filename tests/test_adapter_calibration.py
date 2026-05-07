"""Verify that adapter exports (SITK, NIfTI) get calibrated values.

Volume's contract is that ``vol.data`` returns calibrated (transform-applied)
values. Adapters use ``vol.data``, so external libraries see physical units
(e.g., HU for CT) regardless of how the Volume was constructed.
"""

from __future__ import annotations

import numpy as np
import pytest
import zarr

sitk = pytest.importorskip("SimpleITK")

from duckn import open_array
from duckn.io import read
from duckn.models import (
    AxisKind,
    AxisMetadata,
    Centering,
    DucknMetadata,
    SpaceName,
    ValueTransform,
)
from duckn.sitk_adapter import to_sitk
from duckn.volume import Volume
from duckn.zarr_io import open_store


def _write_ct_store(path):
    """Write a synthetic CT store: uint16 data with HU intercept=-1024."""
    raw_data = np.array([[[0, 1024, 2048]]], dtype=np.uint16)  # raw → -1024, 0, 1024 HU
    axes = [
        AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                     space_direction=[1.0, 0.0, 0.0], unit="mm"),
        AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                     space_direction=[0.0, 1.0, 0.0], unit="mm"),
        AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                     space_direction=[0.0, 0.0, 1.0], unit="mm"),
    ]
    meta = DucknMetadata(
        space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
        space_origin=[0.0, 0.0, 0.0],
        axes=axes,
        value_transforms=[
            ValueTransform(name="linear", parameters={"slope": 1.0, "intercept": -1024.0})
        ],
        sample_units="HU",
    )
    with open_store(path, mode="w") as store:
        arr = zarr.create_array(
            store=store, shape=raw_data.shape, dtype=raw_data.dtype,
            chunks=raw_data.shape, overwrite=True,
        )
        arr[:] = raw_data
        arr.attrs["duckn"] = meta.model_dump(exclude_none=True)


def test_to_sitk_via_io_read_gets_hu(tmp_path):
    """io.read → to_sitk: SITK image contains HU values, not raw uint16."""
    store_path = tmp_path / "ct.zarr"
    _write_ct_store(store_path)

    vol = read(store_path)

    # Volume's raw is uint16 stored values
    assert vol.raw.dtype == np.uint16
    np.testing.assert_array_equal(vol.raw, [[[0, 1024, 2048]]])
    # value_transforms preserved on metadata
    assert vol.metadata.value_transforms is not None

    # vol.data is calibrated HU
    assert vol.data.dtype == np.float32
    np.testing.assert_allclose(vol.data, [[[-1024.0, 0.0, 1024.0]]])

    # SITK image gets the calibrated HU values
    img = to_sitk(vol)
    sitk_arr = sitk.GetArrayFromImage(img)
    assert sitk_arr.dtype == np.float32
    np.testing.assert_allclose(sitk_arr, [[[-1024.0, 0.0, 1024.0]]])


def test_to_sitk_via_open_array_gets_hu(tmp_path):
    """open_array().to_volume() → to_sitk: same calibrated output."""
    store_path = tmp_path / "ct2.zarr"
    _write_ct_store(store_path)

    arr = open_array(store_path)
    vol = arr.to_volume()
    img = to_sitk(vol)

    sitk_arr = sitk.GetArrayFromImage(img)
    np.testing.assert_allclose(sitk_arr, [[[-1024.0, 0.0, 1024.0]]])


def test_volume_raw_preserves_for_round_trip(tmp_path):
    """vol.raw gives stored uint16 for preservation/re-write."""
    store_path = tmp_path / "ct3.zarr"
    _write_ct_store(store_path)

    vol = read(store_path)
    # Re-write to a new zarr store using vol.raw — should preserve source
    out_path = tmp_path / "ct3_copy.zarr"
    from duckn.io import write
    write(vol, out_path, format="zarr")

    # Read back; raw values match
    vol2 = read(out_path)
    np.testing.assert_array_equal(vol2.raw, vol.raw)
    # value_transforms round-tripped
    assert vol2.metadata.value_transforms is not None
