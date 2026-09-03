"""Defects an adversarial review of seg extension 0.7 reproduced (2026-09-03), pinned.

Each test is the review's minimal input and the corrected outcome.
"""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from duckn.extensions import SegAccessor
from duckn.models import (
    SEG_EXTENSION_VERSION,
    AxisMetadata,
    Segment,
    SegmentationExtension,
    coverage_report,
    validate_seg_data,
    validate_seg_extension,
)


def _ext(segments, **kw):
    return SegmentationExtension(version=SEG_EXTENSION_VERSION, segments=segments, **kw)


class TestAccessorAnswersFromOneShape:
    def test_a_0_6_union_is_a_group_everywhere_the_accessor_looks(self):
        raw = {"version": "0.6", "segments": [
            {"id": "liver", "name": "Liver", "label_value": [1, 3]},
            {"id": "tumor", "name": "Tumor", "label_value": [2, 3]}]}
        a = SegAccessor(raw)
        assert a.version == "0.7"
        by = {s.id: s for s in a.segments}
        assert by["liver"].is_group and by["liver"].members == ["label_1", "label_3"]
        assert by["liver"].label_value is None
        assert a.name_for(1) == "label 1" and a.name_for(3) == "label 3"
        assert a.effective_label_values("liver") == {(0, 1), (0, 3)}
        assert set(a.members_of("tumor")) == {"label_2", "label_3"}    # segments order
        assert raw["segments"][0]["label_value"] == [1, 3]          # the caller's dict is untouched

    def test_an_unreadable_dict_is_kept_raw_and_model_says_why(self):
        a = SegAccessor({"segments": [{"id": "a", "label_value": 1}]})   # no version
        assert a.segments[0].id == "a"
        with pytest.raises(Exception, match="version"):
            a.model


class TestVersion:
    def test_version_is_required(self):
        with pytest.raises(Exception, match="version"):
            SegmentationExtension.model_validate({"segments": [{"id": "a", "label_value": 1}]})

    def test_unparseable_version_is_refused_not_migrated(self):
        with pytest.raises(Exception, match="unparseable"):
            SegmentationExtension.model_validate(
                {"version": "banana", "segments": [{"id": "a", "label_value": 1}]})

    def test_a_future_or_prerelease_version_is_left_alone(self):
        ext = SegmentationExtension.model_validate(
            {"version": "0.8-rc1", "segments": [{"id": "a", "label_value": 1}]})
        assert ext.version == "0.8-rc1"                       # not stamped down to 0.7

    def test_a_v_prefixed_old_version_still_migrates(self):
        ext = SegmentationExtension.model_validate(
            {"version": "v0.6", "segments": [{"id": "g", "label_value": ["a"]},
                                             {"id": "a", "label_value": 1}]})
        assert ext.version == "0.7" and ext.segments[0].members == ["a"]


class TestFieldConstraints:
    def test_a_boolean_label_value_is_refused_not_coerced(self):
        with pytest.raises(Exception, match="boolean"):
            Segment(id="a", label_value=True)

    def test_color_is_three_components_in_range(self):
        Segment(id="a", label_value=1, color=[0.1, 0.2, 0.3])
        with pytest.raises(Exception):
            Segment(id="a", label_value=1, color=[1, 2, 3, 4])
        with pytest.raises(Exception):
            Segment(id="a", label_value=1, color=[0.0, 0.0, 1.5])

    def test_extent_is_six_bounds(self):
        with pytest.raises(Exception):
            Segment(id="a", label_value=1, extent=[1, 2, 3])


class TestRule4:
    def test_an_omitted_layer_and_layer_zero_are_the_same_layer(self):
        ext = _ext([{"id": "a", "label_value": 1}, {"id": "b", "label_value": 2, "layer": 0}],
                   source_representation="fractional-labelmap")
        with pytest.raises(ValueError, match="distinct layer per segment"):
            validate_seg_extension(ext, axes=[AxisMetadata(kind=k) for k in ("list", "space", "space", "space")],
                                   shape=(2, 4, 4, 4))


class TestDataChecks:
    def _two_layers(self):
        return _ext([
            {"id": "bg0", "label_value": 0, "background": True},
            {"id": "a", "label_value": 1,
             "designations": [{"scheme": "S", "code": "L"}]},
            {"id": "bg1", "label_value": 9, "layer": 1, "background": True},
            {"id": "b", "label_value": 1, "layer": 1},
            {"id": "c", "label_value": 2, "layer": 1},
            {"id": "g", "members": ["b", "c"], "exhaustive": True,
             "designations": [{"scheme": "S", "code": "L"}]},
        ])

    def test_validate_seg_data_refuses_to_guess_the_layering(self):
        data = np.zeros((2, 3, 3, 3), np.uint8)
        data[1] = 9                                                    # layer 1's background
        with pytest.raises(ValueError, match="list_axis"):
            validate_seg_data(self._two_layers(), data)
        validate_seg_data(self._two_layers(), data, list_axis=0)     # fine when told

    def test_coverage_report_never_compares_a_group_with_its_own_member(self):
        ext = _ext([{"id": "a", "label_value": 1, "designations": [{"scheme": "S", "code": "L"}]},
                    {"id": "b", "label_value": 2},
                    {"id": "g", "members": ["a", "b"], "exhaustive": True,
                     "designations": [{"scheme": "S", "code": "L"}]}])
        data = np.array([[[1, 2, 0]]], np.uint8)
        assert coverage_report(ext, data) == {}                       # nothing else to compare with

    def test_coverage_report_says_none_when_nothing_was_compared(self):
        ext = _ext([{"id": "whole", "label_value": 5, "designations": [{"scheme": "S", "code": "L"}]},
                    {"id": "p", "label_value": 1}, {"id": "q", "label_value": 2},
                    {"id": "g", "members": ["p", "q"], "exhaustive": True,
                     "designations": [{"scheme": "S", "code": "L"}]}])
        rep = coverage_report(ext, np.zeros((2, 2, 2), np.uint8))
        assert rep["g"]["jaccard"] is None and rep["g"]["group_voxels"] == 0

    def test_coverage_report_refuses_data_missing_a_layer_it_needs(self):
        with pytest.raises(ValueError, match="layer 1"):
            coverage_report(self._two_layers(), np.zeros((3, 3, 3), np.uint8))
        data = np.zeros((2, 3, 3, 3), np.uint8)
        data[1] = 9
        rep = coverage_report(self._two_layers(), data, list_axis=0)
        assert rep["g"]["leaf"] == "a" and rep["g"]["jaccard"] is None


class TestConverters:
    def test_a_slicer_file_with_a_label_union_round_trips_through_legacy_replay(self):
        from duckn.seg_nrrd import parse_seg_keyvalues, serialize_seg_extension
        kv = {"Segment0_ID": "S1", "Segment0_Name": "Liver", "Segment0_LabelValue": "1 3"}
        ext, _ = parse_seg_keyvalues(kv)
        assert ext.segments[0].members == ["label_1", "label_3"]      # read as 0.7
        out = serialize_seg_extension(ext)
        assert out["Segment0_LabelValue"] == "1 3"                      # written back verbatim
        renamed = ext.model_copy(update={"segments": [
            ext.segments[0].model_copy(update={"name": "Hepar"}), *ext.segments[1:]]})
        with pytest.raises(ValueError, match="group"):
            serialize_seg_extension(renamed)                          # changed: cannot be generated

    def test_dicom_export_warns_when_it_drops_a_named_group(self, tmp_path):
        pytest.importorskip("pydicom")
        from test_dicom_seg_export import _write_labelmap
        from duckn.dicom_convert import zarr_to_dicom_seg
        data = np.zeros((1, 4, 4), np.uint8)
        data[0, 0, 0] = 1
        data[0, 1, 1] = 3
        src = _write_labelmap(tmp_path / "in.zarr", data, [
            {"id": "label_1", "name": "label 1", "label_value": 1},
            {"id": "label_3", "name": "label 3", "label_value": 3},
            {"id": "liver", "name": "Liver", "members": ["label_1", "label_3"],
             "designations": [{"scheme": "SCT", "code": "10200004"}]}])
        with pytest.warns(UserWarning, match="'liver' \\(Liver\\) is a group"):
            zarr_to_dicom_seg(src, tmp_path / "out.dcm")
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)                # an unnamed group is silent
            src2 = _write_labelmap(tmp_path / "in2.zarr", data, [
                {"id": "label_1", "name": "label 1", "label_value": 1},
                {"id": "label_3", "name": "label 3", "label_value": 3},
                {"id": "u", "members": ["label_1", "label_3"]}])
            zarr_to_dicom_seg(src2, tmp_path / "out2.dcm")


class TestSecondRound:
    def test_a_numpy_bool_label_value_is_refused_and_numpy_ints_are_fine(self):
        with pytest.raises(Exception, match="boolean"):
            Segment(id="a", label_value=np.True_)
        assert Segment(id="a", label_value=np.int64(3)).label_value == 3
        assert Segment(id="a", label_value=np.uint8(3)).label_value == 3

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
        assert a.version == "0.7" and a.file_version == "0.5"
        assert a.segments[0].dicom["category"]["code"] == "1"       # the view hands dicts back
        assert a.segments[0].metadata == {"slicer": {"tags": {"x": "y"}}}
        m = a.model.segments[0]
        assert m.dicom.category.code == "1" and m.metadata == {"slicer": {"tags": {"x": "y"}}}
        assert raw["segments"][0]["metadata"] == {"dicom": {"category": {"scheme": "SCT", "code": "1"}}}

    def test_a_numeric_version_is_refused(self):
        with pytest.raises(Exception, match="string"):
            SegmentationExtension.model_validate({"version": 0.6, "segments": [{"id": "a", "label_value": 1}]})
        with pytest.raises(Exception, match="string"):
            SegmentationExtension.model_validate({"version": 0.10, "segments": [{"id": "a", "label_value": 1}]})

    def test_coverage_report_takes_one_layers_volume(self):
        ext = _ext([
            {"id": "bg0", "label_value": 0, "background": True},
            {"id": "whole", "label_value": 5, "designations": [{"scheme": "S", "code": "L"}]},
            {"id": "bg1", "label_value": 0, "layer": 1, "background": True},
            {"id": "p", "label_value": 1, "layer": 1}, {"id": "q", "label_value": 2, "layer": 1},
            {"id": "g", "members": ["p", "q"], "exhaustive": True,
             "designations": [{"scheme": "S", "code": "L"}]},
        ])
        layer1 = np.array([[[1, 2, 0]]], np.uint8)
        with pytest.raises(ValueError, match="layer="):
            coverage_report(ext, layer1)                       # "this is everything": no it is not
        assert coverage_report(ext, layer1, layer=1) == {}     # one layer, on purpose: nothing comparable
        both = np.zeros((2, 1, 1, 3), np.uint8)
        both[0, 0, 0, :2] = 5
        both[1] = layer1
        rep = coverage_report(ext, both, list_axis=0)
        assert rep["g"]["leaf"] == "whole" and rep["g"]["jaccard"] == 1.0
