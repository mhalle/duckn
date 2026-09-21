"""Defects an adversarial review of seg extension 0.7 reproduced (2026-09-03), pinned.

Each test is the review's minimal input and the outcome under 0.8. Tests of
what 0.8 removed (groups, coverage reports) went with it.
"""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from duckn.diagnostics import DiagnosticsError
from duckn.extensions import SegAccessor
from duckn.models import AxisMetadata
from duckn.seg_model import Segment, SegmentationExtension, validate_seg_extension
from duckn.seg_read import read_seg_extension


def _ext(segments, **kw):
    return SegmentationExtension(version="0.8", segments=segments, **kw)


class TestAccessorAnswersFromOneShape:
    def test_a_0_6_union_is_shared_values_everywhere_the_accessor_looks(self):
        raw = {"version": "0.6", "segments": [
            {"id": "liver", "name": "Liver", "label_value": [1, 3]},
            {"id": "tumor", "name": "Tumor", "label_value": [2, 3]}]}
        a = SegAccessor(raw)
        assert a.version == "0.8" and a.file_version == "0.6"
        by = {s.id: s for s in a.segments}
        assert by["liver"].label_values == [1, 3] and a.label_for("Tumor") == [2, 3]
        assert a.name_for(1) == "Liver"
        assert [s.id for s in a.segments_for(3)] == ["liver", "tumor"]
        assert a.name_for(3) == "Tumor"                              # the topmost answers
        assert a.segment(label_value=3).id == "tumor" and a.segment(label_value=3, layer=1) is None
        assert a.model.segments[0].effective_value_set == {(0, 1), (0, 3)}
        assert a.diagnostics == []
        assert raw["segments"][0]["label_value"] == [1, 3]          # the caller's dict is untouched

    def test_an_unreadable_dict_is_kept_raw_and_model_says_why(self):
        a = SegAccessor({"segments": [{"id": "a", "label_value": 1}]})   # no version
        assert a.segments[0].id == "a"
        with pytest.raises(Exception, match="version"):
            a.model


class TestVersion:
    def test_version_is_required(self):
        with pytest.raises(ValueError, match="version"):
            read_seg_extension({"segments": [{"id": "a", "label_value": 1}]})

    def test_unparseable_version_is_refused_not_migrated(self):
        with pytest.raises(DiagnosticsError, match="rule-1"):
            read_seg_extension({"version": "banana", "segments": [{"id": "a", "label_value": 1}]})

    def test_a_future_or_prerelease_version_is_refused_not_stamped_down(self):
        raw = {"version": "0.9-rc1", "segments": [{"id": "a", "label_value": 1}]}
        with pytest.raises(DiagnosticsError, match="rule-1"):
            read_seg_extension(raw)
        assert raw["version"] == "0.9-rc1"

    def test_a_v_prefixed_old_version_still_migrates(self):
        ext, _ = read_seg_extension(
            {"version": "v0.6", "segments": [{"id": "g", "label_value": ["a"]},
                                             {"id": "a", "label_value": 1}]})
        assert ext.version == "0.8" and ext.segments[0].label_values == [1]


class TestFieldConstraints:
    def test_a_boolean_label_value_is_refused_not_coerced(self):
        with pytest.raises(Exception, match="boolean"):
            Segment(id="a", label_values=[True])

    def test_an_older_color_outside_the_range_is_clamped_not_refused(self):
        ext, _ = read_seg_extension(
            {"version": "0.7", "segments": [{"id": "a", "label_value": 1, "color": [0.0, 0.0, 1.5]}]})
        assert ext.segments[0].color == "color(srgb 0 0 1)"


class TestRule17:
    def test_an_omitted_layer_and_layer_zero_are_the_same_layer(self):
        ext = _ext([{"id": "a", "label_values": [1]}, {"id": "b", "label_values": [1], "layer": 0}],
                   source_representation="fractional-labelmap")
        found = validate_seg_extension(
            ext, axes=[AxisMetadata(kind=k) for k in ("list", "space", "space", "space")],
            shape=(2, 4, 4, 4))
        assert [d.code for d in found] == ["rule-17"]


class TestConverters:
    def test_a_label_union_is_one_segment_and_needs_the_voxels_to_export(self):
        from duckn.seg_nrrd import parse_seg_keyvalues, serialize_seg_extension
        kv = {"Segment0_ID": "S1", "Segment0_Name": "Liver", "Segment0_LabelValue": "1 3"}
        ext, _ = parse_seg_keyvalues(kv)
        assert ext.segments[0].label_values == [1, 3]                   # one segment, two values
        with pytest.raises(ValueError, match="voxel"):                  # Slicer reads one value
            serialize_seg_extension(ext)

    def test_dicom_export_writes_a_migrated_group_as_a_segment(self, tmp_path):
        pydicom = pytest.importorskip("pydicom")
        from test_dicom_seg_export import BODY, MASS, _write
        from duckn.dicom_convert import zarr_to_dicom_seg
        data = np.zeros((1, 4, 4), np.uint8)
        data[0, 0, 0] = 1
        data[0, 1, 1] = 3
        src = _write(tmp_path / "in.zarr", data, [
            {"id": "label_1", "name": "label 1", "label_value": 1},
            {"id": "label_3", "name": "label 3", "label_value": 3},
            {"id": "liver", "name": "Liver", "members": ["label_1", "label_3"]}], version="0.7")
        zarr_to_dicom_seg(src, tmp_path / "out.dcm", algorithm_type="MANUAL",
                          default_dicom={"category": BODY, "type": MASS})
        ds = pydicom.dcmread(str(tmp_path / "out.dcm"))
        # 0.7 dropped the group and kept only its islands; 0.8 writes it, overlapping them
        assert [str(i.SegmentLabel) for i in ds.SegmentSequence] == ["Liver", "label 1", "label 3"]
        assert ds.SegmentsOverlap == "YES"


class TestSecondRound:
    def test_a_numpy_bool_label_value_is_refused_and_numpy_ints_are_fine(self):
        with pytest.raises(Exception, match="boolean"):
            Segment(id="a", label_values=[np.True_])
        assert Segment(id="a", label_values=[np.int64(3)]).label_values == [3]
        assert Segment(id="a", label_values=[np.uint8(3)]).label_values == [3]

    def test_a_malformed_segment_is_kept_raw_and_the_model_says_why(self):
        a = SegAccessor({"version": "0.6", "segments": [{"label_value": 1}]})   # no id
        assert a.file_version == "0.6"
        with pytest.raises(Exception, match="id"):
            a.model
        b = SegAccessor({"version": "0.6", "segments": "nope"})
        with pytest.raises(Exception):
            b.model

    def test_a_pre_0_6_store_reads_the_same_through_the_accessor_and_the_model(self):
        raw = {"version": "0.5", "segments": [
            {"id": "a", "label_value": 1, "tags": {"x": "y"},
             "metadata": {"dicom": {"category": {"scheme": "SCT", "code": "1"}}}}]}
        a = SegAccessor(raw)
        assert a.version == "0.8" and a.file_version == "0.5"
        assert a.segments[0].dicom["category"]["code"] == "1"       # the view hands dicts back
        assert a.segments[0].metadata == {"slicer": {"tags": {"x": "y"}}}
        m = a.model.segments[0]
        assert m.dicom.category.code == "1" and m.metadata == {"slicer": {"tags": {"x": "y"}}}
        assert raw["segments"][0]["metadata"] == {"dicom": {"category": {"scheme": "SCT", "code": "1"}}}

    def test_a_numeric_version_is_refused(self):
        for version in (0.6, 0.10):
            with pytest.raises(ValueError, match="version"):
                read_seg_extension({"version": version, "segments": [{"id": "a", "label_value": 1}]})
