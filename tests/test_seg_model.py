"""Tests for the seg 0.8 models, consistency rules (spec §5), and lookups."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from duckn.diagnostics import About
from duckn.models import AxisMetadata
from duckn.seg_model import (
    SEG_VERSION,
    Segment,
    SegmentationExtension,
    background_value,
    color_map,
    is_fractional,
    is_label_table,
    is_nested,
    normalized_for_writing,
    provably_disjoint,
    refusals,
    rgb8_color_table,
    segments_by_designation,
    segments_for,
    topmost_for,
    undescribed_values,
    validate_seg_data,
    validate_seg_extension,
)

SPEC_PATH = Path(__file__).parent.parent / "docs" / "segmentation-ext-spec.md"


def _ext(segments, **kwargs):
    return SegmentationExtension(version=SEG_VERSION, segments=segments, **kwargs)


def _axes(*kinds):
    return [AxisMetadata(kind=k) for k in kinds]


def _codes(diagnostics):
    return [(d.code, d.about) for d in diagnostics]


def _seg(id):
    return About.segment(id)


LIVER_TUMOR = [
    {"id": "liver", "name": "Liver", "label_values": [1, 3], "color": "#dd8265"},
    {"id": "tumor", "name": "Tumor", "label_values": [2, 3], "color": "#cc3333"},
    {"id": "artifact", "label_values": [9], "role": "unknown"},
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModel:
    def test_minimal(self):
        ext = _ext([{"id": "S1", "label_values": [1]}])
        assert ext.segments[0].effective_value_set == {(0, 1)}
        assert ext.model_dump(exclude_none=True) == {
            "version": "0.9",
            "segments": [{"id": "S1", "label_values": [1]}],
        }

    @pytest.mark.parametrize(
        "values", [1, [], [True], [1.0], ["1"], [2**53], [np.bool_(True)], None]
    )
    def test_label_values_refused(self, values):
        with pytest.raises(ValidationError):
            Segment(id="S", label_values=values)

    def test_numpy_integers_accepted(self):
        seg = Segment(id="S", label_values=[np.uint8(3), np.int64(1)])
        assert seg.label_values == [3, 1] and all(type(v) is int for v in seg.label_values)

    def test_role_refused(self):
        with pytest.raises(ValidationError):
            Segment(id="S", label_values=[1], role="foreground")

    @pytest.mark.parametrize("field", ["label_value", "background", "display"])
    def test_0_7_fields_are_gone(self, field):
        with pytest.raises(ValidationError):
            Segment(**{"id": "S", "label_values": [1], field: 1})

    def test_unrecognized_source_representation_is_kept(self):
        ext = _ext([], source_representation="point-cloud")
        assert ext.source_representation == "point-cloud"
        assert validate_seg_extension(ext) == []

    def test_labeling_schemes(self):
        assert _ext([]).labeling_schemes == []
        assert _ext([], labeling_scheme="A").labeling_schemes == ["A"]
        assert _ext([], labeling_scheme=["A", "B", "A"]).labeling_schemes == ["A", "B"]


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


class TestLookups:
    def test_topmost_is_last_in_order(self):
        ext = _ext(LIVER_TUMOR)
        assert [s.id for s in segments_for(ext, 3)] == ["liver", "tumor"]
        assert topmost_for(ext, 3).id == "tumor"
        assert topmost_for(ext, 1).id == "liver"
        assert topmost_for(ext, 5) is None
        assert topmost_for(ext, 3, layer=1) is None

    def test_color_map_takes_topmost_with_a_color(self):
        ext = _ext(
            [
                {"id": "lobe", "label_values": [1, 2, 3], "color": "#112233"},
                {"id": "a", "label_values": [1]},
                {"id": "b", "label_values": [2], "color": "#445566"},
                {"id": "c", "label_values": [3], "color": "rgb(1 2 3)"},
                {"id": "other", "label_values": [1], "layer": 1, "color": "#ffffff"},
            ]
        )
        assert color_map(ext) == {1: "#112233", 2: "#445566", 3: "#112233"}
        assert color_map(ext, layer=1) == {1: "#ffffff"}

    def test_rgb8_table_reports_gamut_mapping(self):
        ext = _ext(
            [
                {"id": "a", "label_values": [1], "color": "lab(64.0631 33.8785 31.5159)"},
                {"id": "b", "label_values": [2], "color": "lab(50 100 -100)"},
            ]
        )
        table, diagnostics = rgb8_color_table(ext)
        assert table[1] == (0xDD, 0x82, 0x65)
        assert _codes(diagnostics) == [("color-gamut-mapped", _seg("b"))]

    def test_background_value(self):
        assert background_value(_ext(LIVER_TUMOR)) == 0
        assert background_value(_ext(LIVER_TUMOR, implicit_background=False)) is None
        ext = _ext([{"id": "air", "label_values": [7], "role": "background"}],
                   implicit_background=False)
        assert background_value(ext) == 7
        assert background_value(ext, layer=1) is None

    def test_by_designation_joins_on_uri_and_is_per_layer(self):
        kidney = {"scheme": "SCT", "code": "64033007"}
        ext = _ext(
            [
                {"id": "r1", "label_values": [1], "designations": [kidney]},
                {"id": "r2", "label_values": [1], "layer": 1,
                 "designations": [{"scheme": "TA2", "code": "5765"},
                                  {"scheme": "SNOMED", "code": "64033007",
                                   "modifier": {"scheme": "SCT", "code": "7771000"}}]},
            ],
            terminologies={"SCT": {"system_uri": "http://snomed.info/sct"},
                           "SNOMED": {"system_uri": "http://snomed.info/sct"}, "TA2": {}},
        )
        assert [s.id for s in segments_by_designation(ext, "SCT", "64033007")] == ["r1", "r2"]
        left = ext.segments[1].designations[1].modifier
        assert [s.id for s in segments_by_designation(ext, "SCT", "64033007", modifier=left)] == ["r2"]
        assert segments_by_designation(ext, "TA2", "64033007") == []

    def test_reader_properties(self):
        ext = _ext(LIVER_TUMOR)
        assert not is_label_table(ext) and not is_nested(ext)
        liver, tumor, artifact = ext.segments
        assert not provably_disjoint(liver, tumor)
        assert provably_disjoint(liver, artifact)
        atlas = _ext(
            [
                {"id": "184", "label_values": [68, 184, 667]},
                {"id": "68", "label_values": [68]},
                {"id": "667", "label_values": [667]},
            ]
        )
        assert is_nested(atlas) and not is_label_table(atlas)
        table = _ext([{"id": "a", "label_values": [1]}, {"id": "b", "label_values": [2]}])
        assert is_label_table(table) and is_nested(table)
        assert is_label_table(_ext([])) and is_nested(_ext([]))

    def test_is_fractional(self):
        assert is_fractional(_ext([], source_representation="fractional-labelmap"))
        assert is_fractional(_ext([]), "float32")
        assert not is_fractional(_ext([]), "uint8")
        assert not is_fractional(_ext([], source_representation="closed-surface"), "float32")
        assert not is_fractional(_ext([]))


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class TestRules:
    def test_valid(self):
        ext = _ext(LIVER_TUMOR)
        assert validate_seg_extension(
            ext, axes=_axes("space", "space", "space"), shape=(4, 4, 4),
            dtype="uint8", fill_value=0,
        ) == []

    @pytest.mark.parametrize("version", ["0.10", "1.0", "0.09", "v0.9", "0.9.1", " 0.9", "0.9 "])
    def test_rule_1(self, version):
        ext = SegmentationExtension(version=version, segments=[])
        found = validate_seg_extension(ext)
        assert _codes(found) == [("rule-1", About.extension())]
        assert refusals(found) == found

    def test_rule_1_needs_a_string(self):
        with pytest.raises(ValidationError):
            SegmentationExtension(version=0.8, segments=[])

    def test_rule_2(self):
        ext = _ext([{"id": "a", "label_values": [1], "layer": 0},
                    {"id": "b", "label_values": [1], "layer": 2}])
        assert _codes(validate_seg_extension(ext, axes=_axes("space", "space", "space"))) == [
            ("rule-2", _seg("a")), ("rule-2", _seg("b"))]
        layered = _axes("list", "space", "space", "space")
        assert _codes(validate_seg_extension(ext, axes=layered, shape=(2, 4, 4, 4))) == [
            ("rule-2", _seg("b"))]
        assert validate_seg_extension(ext, axes=layered, shape=(3, 4, 4, 4)) == []
        assert validate_seg_extension(ext) == []

    def test_rule_3(self):
        ext = _ext([], labeling_scheme=["A", "A"], implicit_background=True,
                   terminologies={"A": {"system_uri": "u", "version": "1"}})
        assert _codes(validate_seg_extension(ext)) == [
            ("rule-3a", About.extension()), ("rule-3b", About.extension())]
        frac = _ext([], implicit_background=False)
        assert _codes(validate_seg_extension(frac, dtype="float32")) == [
            ("rule-3c", About.extension())]
        assert refusals(validate_seg_extension(ext)) == []

    def test_rule_4(self):
        ext = _ext([{"id": "a", "label_values": [1]}, {"id": "a", "label_values": [2]},
                    {"id": "2.25.1", "label_values": [3]}, {"id": "", "label_values": [4]}])
        found = validate_seg_extension(ext)
        assert _codes(found) == [("rule-4a", About.segment("a", 1)),
                                 ("rule-4b", _seg("2.25.1")), ("rule-4b", _seg(""))]
        assert [d.code for d in refusals(found)] == ["rule-4a"]

    def test_rule_5(self):
        ext = _ext(
            [{"id": "a", "label_values": [1],
              "designations": [{"scheme": "TS", "code": "liver"},
                               {"scheme": "TA2", "code": "1",
                                "modifier": {"scheme": "SCT", "code": "7771000"}}],
              "dicom": {"type": {"scheme": "DCM", "code": "x"}}}],
            labeling_scheme="TS",
            terminologies={"TS": {"system_uri": "u"}, "TA2": {}},
        )
        assert _codes(validate_seg_extension(ext)) == [
            ("rule-5", About.scheme("SCT")), ("rule-5", About.scheme("DCM")),
            ("rule-5", About.scheme("TS"))]

    def test_rules_6_and_7(self):
        ts = {"system_uri": "u", "version": "2.4"}
        ext = _ext(
            [{"id": "two", "label_values": [1],
              "designations": [{"scheme": "TS", "code": "a"}, {"scheme": "TS", "code": "b"}]},
             {"id": "none", "label_values": [2]},
             {"id": "unk", "label_values": [3], "role": "unknown"}],
            labeling_scheme="TS", terminologies={"TS": ts},
        )
        found = validate_seg_extension(ext)
        assert _codes(found) == [("rule-6", _seg("two")), ("rule-7", _seg("none"))]
        assert [d.severity for d in found] == ["error", "warning"]

    def test_rule_8b_is_carried_as_found(self):
        ext = _ext([{"id": "a", "label_values": [1], "dicom": {"algorithm_type": "AUTO"}}])
        found = validate_seg_extension(ext)
        assert _codes(found) == [("rule-8b", _seg("a"))] and refusals(found) == []
        assert ext.segments[0].dicom.algorithm_type == "AUTO"

    def test_rule_9_is_per_layer_and_by_uri(self):
        reg = {"SCT": {"system_uri": "http://snomed.info/sct"},
               "SNOMED": {"system_uri": "http://snomed.info/sct"}}
        ext = _ext(
            [{"id": "a", "label_values": [1],
              "designations": [{"scheme": "SCT", "code": "1", "meaning": "x"}]},
             {"id": "b", "label_values": [2],
              "designations": [{"scheme": "SNOMED", "code": "1", "meaning": "y"}]},
             {"id": "c", "label_values": [1], "layer": 1,
              "designations": [{"scheme": "SCT", "code": "1"}]},
             {"id": "d", "label_values": [3],
              "designations": [{"scheme": "SCT", "code": "1",
                                "modifier": {"scheme": "SCT", "code": "7771000"}}]}],
            terminologies=reg,
        )
        assert _codes(validate_seg_extension(ext)) == [("rule-9", _seg("b"))]

    def test_rule_9_within_a_segment(self):
        d = {"scheme": "SCT", "code": "1"}
        ext = _ext([{"id": "a", "label_values": [1], "designations": [d, dict(d, meaning="x")]}],
                   terminologies={"SCT": {}})
        assert _codes(validate_seg_extension(ext)) == [("rule-9", _seg("a"))]

    def test_rule_5_for_a_declared_scheme_nothing_uses(self):
        ext = _ext([], labeling_scheme="TS")
        found = validate_seg_extension(ext)
        assert _codes(found) == [("rule-5", About.scheme("TS"))]
        assert "not registered" in found[0].message

    def test_rule_11(self):
        ext = _ext([{"id": "a", "label_values": [3, 1, 3]}, {"id": "b", "label_values": [300]},
                    {"id": "c", "label_values": [-1]}])
        assert _codes(validate_seg_extension(ext)) == [("rule-11b", _seg("a"))]
        assert _codes(validate_seg_extension(ext, dtype="uint8")) == [
            ("rule-11b", _seg("a")), ("rule-11a", _seg("b")), ("rule-11a", _seg("c"))]
        assert _codes(validate_seg_extension(ext, dtype="int16")) == [("rule-11b", _seg("a"))]

    def test_rule_12(self):
        ext = _ext([{"id": "68", "label_values": [68]}, {"id": "667", "label_values": [667]},
                    {"id": "184", "label_values": [68, 184, 667]},
                    {"id": "same", "label_values": [68]},
                    {"id": "elsewhere", "label_values": [68, 184, 667, 900], "layer": 1}])
        assert _codes(validate_seg_extension(ext)) == [("rule-12", _seg("184"))]

    def test_rule_13(self):
        ext = _ext([{"id": "bg", "label_values": [5, 6], "role": "background"},
                    {"id": "bg2", "label_values": [7], "role": "background"},
                    {"id": "bg3", "label_values": [7], "role": "background", "layer": 1}])
        found = validate_seg_extension(ext)
        assert _codes(found) == [("rule-13", _seg("bg2")), ("rule-13", _seg("bg"))]
        assert refusals(found) == found

    def test_rule_14(self):
        ext = _ext([{"id": "zero", "label_values": [0, 1]},
                    {"id": "unk", "label_values": [1, 9], "role": "unknown"},
                    {"id": "unk2", "label_values": [9], "role": "unknown"},
                    {"id": "fine", "label_values": [0], "layer": 1, "role": "background"}])
        assert _codes(validate_seg_extension(ext)) == [
            ("rule-14", About.value(0, 0)), ("rule-14", About.value(0, 1)),
            ("rule-14", About.value(0, 9))]
        relaxed = _ext([{"id": "zero", "label_values": [0, 1]}], implicit_background=False)
        assert validate_seg_extension(relaxed) == []

    def test_rule_15(self):
        ext = _ext([{"id": "a", "label_values": [1]},
                    {"id": "b", "label_values": [1], "layer": 1}], implicit_background=False)
        assert _codes(validate_seg_extension(ext, fill_value=0)) == [
            ("rule-15", About.value(0, 0)), ("rule-15", About.value(1, 0))]
        assert validate_seg_extension(ext, fill_value=1) == []
        assert validate_seg_extension(ext) == []
        assert validate_seg_extension(_ext([], implicit_background=False), fill_value=0) == []

    def test_rule_16(self):
        ext = _ext(LIVER_TUMOR)
        data = np.array([[[0, 1, 2], [3, 9, 5]]], dtype=np.uint8)
        assert _codes(validate_seg_data(ext, data)) == [("rule-16", About.value(0, 5))]
        assert undescribed_values(ext, data) == {(0, 5)}
        no_bg = _ext(LIVER_TUMOR, implicit_background=False)
        assert undescribed_values(no_bg, data) == {(0, 0), (0, 5)}

    def test_rule_16_layers(self):
        ext = _ext([{"id": "a", "label_values": [1]},
                    {"id": "b", "label_values": [2], "layer": 1}])
        data = np.zeros((2, 2, 2, 2), dtype=np.uint8)
        data[0, 0, 0, 0] = 1
        data[1, 0, 0, 0] = 1
        assert undescribed_values(ext, data, list_axis=0) == {(1, 1)}
        assert undescribed_values(ext, data[1], layer=1) == {(1, 1)}
        with pytest.raises(ValueError, match="list_axis"):
            validate_seg_data(ext, data)

    def test_rule_17(self):
        segs = [{"id": "a", "label_values": [1]},
                {"id": "b", "label_values": [1], "layer": 1},
                {"id": "c", "label_values": [1], "layer": 1},
                {"id": "d", "label_values": [2], "layer": 2},
                {"id": "none", "label_values": [1], "layer": 3, "role": "background"}]
        ext = _ext(segs, source_representation="fractional-labelmap")
        assert _codes(validate_seg_extension(ext, axes=_axes("space", "space", "space"))) == [
            *[("rule-2", _seg(i)) for i in "bcd"], ("rule-2", _seg("none")),
            ("rule-17", About.extension()), ("rule-17", _seg("c")), ("rule-17", _seg("d"))]
        layered = _axes("list", "space", "space", "space")
        assert _codes(validate_seg_extension(ext, axes=layered, shape=(4, 2, 2, 2))) == [
            ("rule-17", _seg("c")), ("rule-17", _seg("d"))]

    def test_fractional_skips_the_binary_rules(self):
        ext = _ext([{"id": "a", "label_values": [1], "role": "background"},
                    {"id": "b", "label_values": [1], "layer": 1, "role": "background"}],
                   source_representation="fractional-labelmap")
        assert validate_seg_extension(ext, fill_value=0.0, dtype="float32") == []

    def test_color_unreadable(self):
        ext = _ext([{"id": "a", "label_values": [1], "color": "rgb(1 2 3)"}])
        found = validate_seg_extension(ext)
        assert _codes(found) == [("color-unreadable", _seg("a"))]
        assert found[0].severity == "warning"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_writer_spelling(self):
        ext = _ext(
            [{"id": "a", "label_values": [3, 1, 3], "layer": 0, "color": "#DD8265",
              "designations": []},
             {"id": "b", "label_values": [2], "color": "color(srgb 1.5 0 0)"},
             {"id": "c", "label_values": [4], "color": "red"}],
            implicit_background=True, labeling_scheme=["TS", "TS"], terminologies={},
        )
        out, diagnostics = normalized_for_writing(ext)
        assert out.model_dump(exclude_none=True) == {
            "version": "0.9",
            "labeling_scheme": "TS",
            "segments": [
                {"id": "a", "label_values": [1, 3], "color": "#dd8265"},
                {"id": "b", "label_values": [2], "color": "color(srgb 1 0 0)"},
                {"id": "c", "label_values": [4]},
            ],
        }
        assert _codes(diagnostics) == [("color-clamped", _seg("b")),
                                       ("color-unreadable", _seg("c"))]
        assert ext.segments[0].label_values == [3, 1, 3]  # the input is untouched
        later = SegmentationExtension(version="0.10", segments=[])
        with pytest.raises(ValueError, match="0.10"):
            normalized_for_writing(later)


# ---------------------------------------------------------------------------
# The spec's own examples
# ---------------------------------------------------------------------------


def _spec_examples() -> list[dict]:
    blocks = re.findall(r"```json\n(.*?)```", SPEC_PATH.read_text(), re.S)
    found = []
    for block in blocks:
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "segments" in obj and "version" in obj:
            found.append(obj)
    return found


def test_spec_examples_conform():
    examples = _spec_examples()
    assert len(examples) >= 6
    for raw in examples:
        ext = SegmentationExtension.model_validate(raw)
        found = validate_seg_extension(ext, dtype="uint16", fill_value=0)
        assert [d for d in found if d.severity == "error"] == [], (raw["segments"][0], found)
        # A conforming example is already in a writer's spelling.
        out, diagnostics = normalized_for_writing(ext)
        assert diagnostics == []
        assert out.model_dump(exclude_none=True) == raw


# ---------------------------------------------------------------------------
# members: a union stated once
# ---------------------------------------------------------------------------


ATLAS = [
    {"id": "184", "name": "Frontal pole", "members": ["68", "667", "rest"], "color": "#268f45"},
    {"id": "68", "label_values": [68]},
    {"id": "667", "label_values": [667]},
    {"id": "rest", "label_values": [184]},
]


class TestMembers:
    def test_at_least_one_spelling_per_segment(self):
        with pytest.raises(ValidationError, match="or both"):
            Segment(id="a")
        both = Segment(id="a", label_values=[1], members=["b"])
        assert both.values == frozenset({1}) and not both.is_resolved
        with pytest.raises(ValidationError):
            Segment(id="a", members=[])

    def test_a_union_resolves_transitively_and_is_a_value_set_to_every_reader(self):
        ext = _ext([{"id": "top", "members": ["184"]}, *ATLAS])
        assert ext.segments[0].sorted_values == [68, 184, 667]
        assert ext.segments[1].effective_value_set == {(0, 68), (0, 184), (0, 667)}
        assert [s.id for s in segments_for(ext, 667)] == ["top", "184", "667"]
        assert topmost_for(ext, 184).id == "rest"
        assert color_map(ext) == {68: "#268f45", 184: "#268f45", 667: "#268f45"}
        assert is_nested(ext) and not is_label_table(ext)
        assert validate_seg_extension(ext, dtype="uint16", fill_value=0) == []
        # the file keeps the spelling it was given
        assert ext.model_dump(exclude_none=True)["segments"][1] == ATLAS[0]
        out, _ = normalized_for_writing(ext)
        assert out.model_dump(exclude_none=True)["segments"][1] == ATLAS[0]

    @pytest.mark.parametrize(
        "segments, message",
        [
            ([{"id": "g", "members": ["nobody"]}], "no segment"),
            ([{"id": "g", "members": ["a"]}, {"id": "a", "label_values": [1], "layer": 1}], "layer 1"),
            ([{"id": "g", "members": ["bg"]}, {"id": "bg", "label_values": [0], "role": "background"}], "role"),
            ([{"id": "g", "members": ["h"]}, {"id": "h", "members": ["g"]}], "returns to"),
            ([{"id": "g", "members": ["a", "a"]}, {"id": "a", "label_values": [1]}], "distinct"),
        ],
    )
    def test_rule_11c_refuses(self, segments, message):
        ext = _ext(segments)
        found = validate_seg_extension(ext)
        rule = [d for d in found if d.code == "rule-11c"]
        assert rule[0].about == _seg("g") and message in rule[0].message   # a cycle names both
        assert refusals(found) == rule
        assert ext.segments[0].values == frozenset()          # unresolved: no values

    def test_rule_12_and_the_reader_properties_see_the_resolved_set(self):
        ext = _ext([{"id": "68", "label_values": [68]}, {"id": "184", "members": ["68"]}])
        assert _codes(validate_seg_extension(ext)) == []      # equal sets: not strict containment
        ext = _ext([{"id": "68", "label_values": [68]}, {"id": "667", "label_values": [667]},
                    {"id": "184", "members": ["68", "667"]}])
        assert _codes(validate_seg_extension(ext)) == [("rule-12", _seg("184"))]

    def test_a_members_segment_has_no_role(self):
        with pytest.raises(ValidationError, match="no role"):
            Segment(id="bg", members=["a"], role="background")

    def test_membership_three_deep_and_first_of_a_repeated_id_wins(self):
        ext = _ext([{"id": "top", "members": ["mid"]}, {"id": "mid", "members": ["low"]},
                    {"id": "low", "members": ["a"]}, {"id": "a", "label_values": [1]},
                    {"id": "a", "label_values": [2]}])
        assert ext.segments[0].sorted_values == [1]
        assert [d.code for d in refusals(validate_seg_extension(ext))] == ["rule-4a"]

    def test_values_are_re_resolved_by_validation_and_by_writing(self):
        ext = _ext([{"id": "a", "label_values": [1]}, {"id": "u", "members": ["a"]}])
        assert not Segment(id="x", members=["a"]).is_resolved and ext.segments[1].is_resolved
        ext.segments[0].label_values = [1, 7]
        assert ext.segments[1].sorted_values == [1]              # stale until re-resolved
        validate_seg_extension(ext)
        assert ext.segments[1].sorted_values == [1, 7]
        ext.segments[0].label_values = [1, 7, 9]
        out, _ = normalized_for_writing(ext)
        assert out.segments[1].sorted_values == [1, 7, 9]

    def test_own_values_and_members_are_a_union(self):
        """An interior structure is its own value plus its children: no segment is
        invented for the voxels no child claims."""
        ext = _ext([{"id": "184", "label_values": [184], "members": ["68", "667"],
                     "color": "#268f45"},
                    {"id": "68", "label_values": [68]}, {"id": "667", "label_values": [667]}])
        top = ext.segments[0]
        assert top.sorted_values == [68, 184, 667] and top.is_resolved
        assert topmost_for(ext, 184).id == "184" and topmost_for(ext, 68).id == "68"
        assert color_map(ext) == {68: "#268f45", 184: "#268f45", 667: "#268f45"}
        assert validate_seg_extension(ext, dtype="uint16", fill_value=0) == []
        assert ext.model_dump(exclude_none=True)["segments"][0] == {
            "id": "184", "label_values": [184], "members": ["68", "667"], "color": "#268f45"}
        # rules 11a/11b still apply to the values it lists
        bad = _ext([{"id": "u", "label_values": [3, 1], "members": ["a"]},
                    {"id": "a", "label_values": [2]}])
        assert _codes(validate_seg_extension(bad)) == [("rule-11b", _seg("u"))]

    def test_fractional_has_no_members(self):
        ext = _ext([{"id": "a", "label_values": [1]}, {"id": "u", "members": ["a"], "layer": 1}],
                   source_representation="fractional-labelmap")
        assert ("rule-17", _seg("u")) in _codes(validate_seg_extension(ext))
