"""Tests for SegAccessor and SegmentView against seg 0.8."""

from __future__ import annotations

import numpy as np
import pytest

from duckn.diagnostics import DiagnosticsError
from duckn.extensions import Extensions, SegAccessor
from duckn.models import AxisMetadata, DucknMetadata
from duckn.volume import Volume

SEG = {
    "version": "0.8",
    "labeling_scheme": "TS",
    "terminologies": {"TS": {"uri": "u", "version": "2.4"},
                      "SCT": {"url_template": "http://snomed.info/id/{code}"}},
    "segments": [
        {"id": "liver", "name": "Liver", "label_values": [1, 3], "color": "#dd8265",
         "designations": [{"scheme": "TS", "code": "liver"},
                          {"scheme": "SCT", "code": "10200004"}]},
        {"id": "tumor", "name": "Tumor", "label_values": [2, 3], "role": None,
         "dicom": {"type": {"scheme": "SCT", "code": "4147007"}}},
        {"id": "artifact", "label_values": [9], "role": "unknown", "color": "#808080"},
        {"id": "liver-2", "name": "Liver", "label_values": [1], "layer": 1,
         "designations": [{"scheme": "sct", "code": "10200004"}]},
    ],
}


class TestViews:
    def test_segment_view(self):
        a = SegAccessor(SEG)
        liver, tumor, artifact, second = a.segments
        assert (liver.id, liver.name, liver.label_values, liver.layer) == ("liver", "Liver", [1, 3], 0)
        assert liver.color == "#dd8265" and liver.role is None
        assert artifact.role == "unknown" and second.layer == 1
        assert repr(artifact) == "Segment(None, labels=[9], role='unknown')"

    @pytest.mark.parametrize(
        "gone", ["label_value", "members", "is_group", "background", "disjoint", "exhaustive"])
    def test_0_7_properties_are_gone(self, gone):
        with pytest.raises(AttributeError):
            getattr(SegAccessor(SEG).segments[0], gone)

    @pytest.mark.parametrize("gone", ["effective_label_values", "members_of", "parents_of"])
    def test_0_7_graph_questions_are_gone(self, gone):
        assert not hasattr(SegAccessor(SEG), gone)


class TestLookups:
    def test_by_value_is_per_layer_and_topmost(self):
        a = SegAccessor(SEG)
        assert [s.id for s in a.segments_for(3)] == ["liver", "tumor"]
        assert a.segment(label_value=3).id == "tumor"
        assert a.segment(label_value=1, layer=1).id == "liver-2"
        assert a.name_for(1) == "Liver" and a.name_for(7) is None
        assert a.label_for("Tumor") == [2, 3] and a.label_for("nobody") == []
        assert a.label_values == [[1, 3], [2, 3], [9], [1]]

    def test_by_id_name_and_snomed(self):
        a = SegAccessor(SEG)
        assert a.segment(id="artifact").label_values == [9]
        assert a.segment(name="Liver").id == "liver"
        assert a.segment(snomed="10200004").id == "liver"      # lowest layer
        assert a.segment(snomed="4147007").id == "tumor"       # the DICOM type
        assert a.segment(snomed="0") is None

    def test_colors_background_schemes(self):
        a = SegAccessor(SEG)
        assert a.color_map() == {1: "#dd8265", 3: "#dd8265", 9: "#808080"}
        assert a.color_map(layer=1) == {}
        assert a.background_value() == 0
        assert SegAccessor({**SEG, "implicit_background": False}).background_value() is None
        assert a.labeling_schemes == ["TS"]
        assert a.concept_url("SCT", "1") == "http://snomed.info/id/1"


class TestReading:
    def test_diagnostics_are_offered(self):
        a = SegAccessor({"version": "0.7", "segments": [
            {"id": "bg", "label_value": 0, "background": True}, {"id": "a b", "label_value": 1}]})
        assert [s.id for s in a.segments] == ["bg", "a_b"]
        assert [d.code for d in a.diagnostics] == ["migrated-background", "id-changed"]
        assert a.model.segments[0].role == "background"

    def test_refusal_surfaces_in_model_not_in_construction(self):
        a = SegAccessor({"version": "0.8", "segments": [
            {"id": "a", "label_values": [1]}, {"id": "a", "label_values": [2]}]})
        assert len(a.segments) == 2
        assert [d.code for d in a.diagnostics] == ["rule-4a"]
        for _ in range(2):
            with pytest.raises(DiagnosticsError):
                a.model

    def test_a_later_version_is_refused_but_still_viewable(self):
        a = SegAccessor({"version": "0.9", "segments": [{"id": "a", "label_values": [1]}]})
        assert a.version == "0.9" and a.segments[0].id == "a"
        with pytest.raises(DiagnosticsError):
            a.model

    def test_a_volume_supplies_the_array_context(self):
        meta = DucknMetadata(
            axes=[AxisMetadata(kind="space")] * 3,
            extensions={"seg": {"version": "0.8", "segments": [
                {"id": "a", "label_values": [300], "layer": 1}]}},
        )
        vol = Volume(np.zeros((2, 2, 2), dtype=np.uint8), meta)
        assert sorted(d.code for d in vol.extensions.seg.diagnostics) == ["rule-11a", "rule-2"]
        assert sorted(d.code for d in vol.extensions["seg"].diagnostics) == ["rule-11a", "rule-2"]
        # without the array, neither rule can be checked
        assert Extensions(meta.extensions).seg.diagnostics == []
