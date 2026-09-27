"""Convention 1.2 (2026-09-26): ``axis_linear``, finite linear parameters, and whether a
calibration is STATED (an absent ``value_transforms`` means "not stated" from 1.2, identity in
1.0/1.1 files; ``[]`` states identity)."""
from __future__ import annotations

import numpy as np
import pytest
import zarr

from duckn.models import DucknMetadata, validate_against_shape
from duckn.volume import Volume
from duckn.zarr_io import DucknArray


def _meta(transforms, *, version="1.2", ndim=3):
    return DucknMetadata(version=version, axes=[{"kind": "domain"}] * ndim,
                         value_transforms=transforms)


def _axis_linear(axis=2, slope=(1.0, 2.0, 0.5, -1.0), intercept=(0.0, 10.0, -3.0, 4.0)):
    return [{"name": "axis_linear",
             "parameters": {"axis": axis, "slope": list(slope), "intercept": list(intercept)}}]


def _truth(stored, axis, slope, intercept):
    shape = [1] * stored.ndim
    shape[axis] = -1
    return (stored.astype(np.float64) * np.asarray(slope).reshape(shape)
            + np.asarray(intercept).reshape(shape))


STORED = np.arange(2 * 3 * 4, dtype=np.int8).reshape(2, 3, 4) - 12


def test_axis_linear_calibrates_each_index_with_its_own_scale():
    meta = _meta(_axis_linear())
    got = Volume(raw=STORED, metadata=meta).data
    np.testing.assert_allclose(got, _truth(STORED, 2, (1, 2, .5, -1), (0, 10, -3, 4)))


@pytest.mark.parametrize("key", [
    (slice(None), slice(None), slice(1, 3)),     # a range along the axis
    (0, slice(None), 2),                         # an integer ON the axis drops it
    (slice(None), 1),                            # the axis untouched
    (Ellipsis, slice(1, None, 2)),               # strided along the axis
    (slice(None), slice(None), [3, 0, 3]),       # fancy, with a repeat
])
def test_a_partial_read_uses_the_parameters_of_the_indices_it_selects(tmp_path, key):
    """The parameters follow the selected indices, whatever the key - never the result's own
    positions (a slice [1:3] must use slope[1], slope[2], not slope[0], slope[1])."""
    arr = zarr.create_array(str(tmp_path / "a.zarr"), shape=STORED.shape, dtype="i1",
                            chunks=(1, 3, 2))
    arr[:] = STORED
    meta = _meta(_axis_linear())
    got = DucknArray(arr, meta)[key]
    want = _truth(STORED, 2, (1, 2, .5, -1), (0, 10, -3, 4))[key]
    np.testing.assert_allclose(got, want)


def test_scalar_parameters_are_the_same_for_every_index():
    meta = _meta([{"name": "axis_linear", "parameters": {"axis": 0, "slope": 2.0, "intercept": [1.0, -1.0]}}])
    got = Volume(raw=STORED, metadata=meta).data
    np.testing.assert_allclose(got, _truth(STORED, 0, (2.0, 2.0), (1.0, -1.0)))


@pytest.mark.parametrize("params,match", [
    ({"axis": -1, "slope": [1.0], "intercept": [0.0]}, "non-negative"),
    ({"axis": 0, "slope": [1.0, 2.0], "intercept": [0.0]}, "one length"),
    ({"axis": 0, "slope": [], "intercept": 0.0}, "empty"),
    ({"axis": 0, "slope": [float("nan")], "intercept": [0.0]}, "finite"),
    ({"axis": 0, "slope": [1.0]}, "intercept"),
])
def test_axis_linear_parameters_are_checked(params, match):
    with pytest.raises(ValueError, match=match):
        _meta([{"name": "axis_linear", "parameters": params}])


def test_axis_linear_must_name_an_axis_the_array_has_and_fit_it():
    with pytest.raises(ValueError, match="names axis 3"):
        _meta(_axis_linear(axis=3))
    meta = _meta(_axis_linear())
    validate_against_shape(meta, (2, 3, 4))
    with pytest.raises(ValueError, match="states 4 values for axis 2, which has 5"):
        validate_against_shape(meta, (2, 3, 5))


def test_a_linear_slope_or_intercept_must_be_finite():
    for bad in ({"slope": float("nan"), "intercept": 0.0}, {"slope": 1.0, "intercept": float("inf")}):
        with pytest.raises(ValueError, match="finite"):
            _meta([{"name": "linear", "parameters": bad}])


def test_whether_the_calibration_is_stated():
    assert _meta([]).values_stated() is True                      # stated identity
    assert _meta(_axis_linear()).values_stated() is True
    assert _meta(None, version="1.2").values_stated() is False    # 1.2: absent = not stated
    assert _meta(None, version="1.3").values_stated() is False
    assert _meta(None, version="1.1").values_stated() is True     # old meaning: identity
    assert _meta(None, version="1.0").values_stated() is True
    assert DucknMetadata(axes=[{"kind": "domain"}]).values_stated() is True   # no version: oldest


def test_a_reader_that_knows_no_axis_linear_is_told_the_mapping_is_unknown():
    """Why a NEW name rather than a `linear` with arrays: an older reader broadcasting array
    parameters of `linear` would calibrate along the wrong axis silently; an unknown name makes
    the calibrated read refuse (§4.2), which 1.1 readers already do."""
    from duckn.zarr_io import _apply_value_transforms
    with pytest.raises(ValueError, match="unsupported value_transform"):
        _apply_value_transforms(STORED, [{"name": "axis_linear_v9", "parameters": {}}], None)


# -- groups (§3.3) and the seg group form (seg 0.10) ----------------------------------------

def test_a_group_carries_only_version_intent_and_extensions():
    from duckn.models import DucknGroupMetadata
    g = DucknGroupMetadata(version="1.2", intent="label-map",
                           extensions={"seg": {"version": "0.10", "segments": []}})
    assert g.intent == "label-map"
    with pytest.raises(ValueError, match="Extra inputs"):
        DucknGroupMetadata(version="1.2", space="left-posterior-superior")    # an array field
    with pytest.raises(ValueError, match="from convention 1.2"):
        DucknGroupMetadata(version="1.1")
    with pytest.raises(ValueError, match="no version"):
        DucknGroupMetadata(version="1.2", extensions={"haversack": {"task": "x"}})


def _group_seg(layers, segments, version="0.10"):
    from duckn.seg_model import SegmentationExtension
    return SegmentationExtension(version=version, layers=layers, segments=segments)


def test_a_groups_seg_block_names_each_layers_member():
    from duckn.seg_model import validate_seg_extension
    ext = _group_seg([{"path": "parts/0", "labelmap_from": "ranked"},
                      {"path": "parts/1", "labelmap_from": "ranked"}],
                     [{"id": "a", "label_values": [1], "layer": 0},
                      {"id": "b", "label_values": [1], "layer": 1}])
    assert not [d for d in validate_seg_extension(ext) if d.severity == "error"]
    bad = _group_seg([{"path": "parts/0"}, {"path": "parts/1"}],
                     [{"id": "a", "label_values": [1], "layer": 2},
                      {"id": "b", "label_values": [1]}])       # absent = layer 0: fine
    codes = [d.code for d in validate_seg_extension(bad)]
    assert codes.count("rule-2") == 1                       # only the out-of-range one
    twice = _group_seg([{"path": "p"}, {"path": "p"}], [])
    assert "rule-2" in [d.code for d in validate_seg_extension(twice)]


@pytest.mark.parametrize("path", ["", "/abs", "a/../b", "./a", "a//b"])
def test_a_layers_path_stays_inside_the_group(path):
    with pytest.raises(ValueError, match="relative path"):
        _group_seg([{"path": path}], [])


def test_layers_is_a_0_10_field_and_a_group_block_is_written_as_0_10():
    from duckn.seg_model import SEG_VERSION, normalized_for_writing, validate_seg_extension
    old = _group_seg([{"path": "parts/0"}], [], version="0.9")
    assert "rule-1" in [d.code for d in validate_seg_extension(old)]
    written, _ = normalized_for_writing(_group_seg([{"path": "parts/0"}], [], version="0.9"))
    assert written.version == "0.10"
    from duckn.seg_model import SegmentationExtension
    array_block, _ = normalized_for_writing(SegmentationExtension(version="0.9", segments=[]))
    assert array_block.version == SEG_VERSION == "0.9"      # arrays stay readable by 0.9
