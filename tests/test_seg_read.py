"""Tests for reading seg extensions: migration (spec §6.3) and the entry point."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from duckn.diagnostics import About, DiagnosticsError
from duckn.seg_model import SegmentationExtension, derive_token_ids, validate_seg_extension
from duckn.seg_read import migrate_seg_extension, read_seg_extension

OLD_SPEC_PATH = Path(__file__).parent.parent / "docs" / "archive" / "segmentation-ext-v07-spec.md"

# Rules a migrated file satisfies, given a conforming older file (§6.3).
GUARANTEED = {f"rule-{n}" for n in
              ("1", "2", "3a", "3b", "3c", "4a", "4b", "6", "8a", "9",
               "11a", "11b", "12", "13", "14")}


def _old(segments, version="0.7", **kwargs):
    return {"version": version, "segments": segments, **kwargs}


def _codes(diagnostics):
    return [(d.code, d.about) for d in diagnostics]


def _seg(id):
    return About.segment(id)


class TestSimpleFields:
    def test_0_8_is_untouched(self):
        raw = {"version": "0.9", "segments": [{"id": "a", "label_values": [1]}]}
        out, diagnostics = migrate_seg_extension(raw)
        assert out is raw and diagnostics == []

    def test_scalar_list_layer_color_display(self):
        raw = _old(
            [{"id": "a", "label_value": 5, "layer": 0, "color": [0.501961, 0.682353, 0.501961],
              "display": {"de": "Leber"}},
             {"id": "b", "label_value": [3, 1, 3], "color": [0.89, 0.85, 0.78]}],
            version="0.6",
        )
        before = deepcopy(raw)
        out, diagnostics = migrate_seg_extension(raw)
        assert raw == before
        assert diagnostics == []
        assert out == {
            "version": "0.9",
            "segments": [
                {"id": "a", "label_values": [5], "color": "#80ae80",
                 "metadata": {"duckn": {"display": {"de": "Leber"}}}},
                {"id": "b", "label_values": [1, 3], "color": "color(srgb 0.89 0.85 0.78)"},
            ],
        }

    def test_background_is_reported(self):
        out, diagnostics = migrate_seg_extension(
            _old([{"id": "air", "label_value": 7, "background": True},
                  {"id": "a", "label_value": 1, "background": False}])
        )
        assert out["segments"][0] == {"id": "air", "label_values": [7], "role": "background"}
        assert "role" not in out["segments"][1] and "background" not in out["segments"][1]
        assert _codes(diagnostics) == [("migrated-background", _seg("air"))]

    def test_algorithm_attributes_move(self):
        out, _ = migrate_seg_extension(
            _old([{"id": "a", "label_value": 1,
                   "metadata": {"dicom": {"SegmentAlgorithmType": "MANUAL",
                                          "SegmentDescription": "x"}}},
                  {"id": "b", "label_value": 2, "dicom": {"algorithm_type": "AUTOMATIC"},
                   "metadata": {"dicom": {"SegmentAlgorithmType": "MANUAL",
                                          "SegmentAlgorithmName": "n"}}}])
        )
        a, b = out["segments"]
        assert a["dicom"] == {"algorithm_type": "MANUAL"}
        assert a["metadata"] == {"dicom": {"SegmentDescription": "x"}}
        assert b["dicom"] == {"algorithm_type": "AUTOMATIC", "algorithm_name": "n"}
        assert "metadata" not in b

    def test_pre_0_6_shapes(self):
        out, _ = migrate_seg_extension(
            _old([{"id": "a", "label_value": 1,
                   "identifiers": {"SCT": {"id": "64033007", "name": "Kidney"}},
                   "metadata": {"dicom": {"type": {"id": "64033007", "name": "Kidney"}}},
                   "name_auto_generated": True}],
                 version="0.5", contained_representations=["Binary labelmap"])
        )
        assert out["metadata"] == {"slicer": {"contained_representations": ["Binary labelmap"]}}
        assert out["segments"] == [
            {"id": "a", "label_values": [1],
             "designations": [{"scheme": "SCT", "code": "64033007", "meaning": "Kidney"}],
             "dicom": {"type": {"scheme": "SCT", "code": "64033007", "meaning": "Kidney"}},
             "metadata": {"slicer": {"name_auto_generated": True}}}]
        SegmentationExtension.model_validate(out)


class TestGroups:
    def test_0_7_group_is_flattened_and_ordered(self):
        raw = _old(
            [{"id": "68", "label_value": 68}, {"id": "667", "label_value": 667},
             {"id": "184-rest", "label_value": 184},
             {"id": "184", "members": ["68", "667", "184-rest"], "disjoint": True,
              "exhaustive": True, "extent": [0, 1, 0, 1, 0, 1], "color": [1, 0, 0]}]
        )
        out, diagnostics = migrate_seg_extension(raw)
        assert [s["id"] for s in out["segments"]] == ["184", "68", "667", "184-rest"]
        # a union of structures keeps its members; its extent and claims go
        assert out["segments"][0] == {"id": "184", "members": ["68", "667", "184-rest"],
                                      "color": "#ff0000"}
        assert _codes(diagnostics) == [("migrated-claim-dropped", _seg("184"))] * 2
        ext = SegmentationExtension.model_validate(out)
        assert ext.segments[0].sorted_values == [68, 184, 667]

    def test_0_6_references_and_nesting(self):
        raw = _old(
            [{"id": "a", "label_value": 1}, {"id": "b", "label_value": 2, "layer": 0},
             {"id": "ab", "label_value": ["a", "b"]},
             {"id": "abc", "label_value": ["ab", 3]}],
            version="0.6",
        )
        out, _ = migrate_seg_extension(raw)
        # `ab` is a pure union and keeps members; `abc` has an integer of its own (3) and
        # is written out, since a segment has one spelling or the other
        assert [(s["id"], s.get("label_values"), s.get("members")) for s in out["segments"]] == [
            ("abc", [1, 2, 3], None), ("ab", None, ["a", "b"]), ("a", [1], None), ("b", [2], None)]
        ext = SegmentationExtension.model_validate(out)
        assert ext.segments[1].sorted_values == [1, 2]

    def test_group_takes_the_layer_of_its_values(self):
        raw = _old([{"id": "a", "label_value": 1, "layer": 1},
                    {"id": "g", "members": ["a"]}])
        out, _ = migrate_seg_extension(raw)
        assert out["segments"][1] == {"id": "g", "members": ["a"], "layer": 1}

    @pytest.mark.parametrize(
        "group",
        [{"id": "g", "members": ["a", "b"]},            # spans layers
         {"id": "g", "members": ["a", "missing"]},      # does not resolve
         {"id": "g", "members": ["a", "g"]},            # cycle
         {"id": "g", "members": ["bg"]}],               # nothing left
    )
    def test_group_with_no_0_8_form_is_omitted(self, group):
        raw = _old([{"id": "a", "label_value": 1},
                    {"id": "b", "label_value": 1, "layer": 1},
                    {"id": "bg", "label_value": 9, "background": True}, group])
        out, diagnostics = migrate_seg_extension(raw)
        assert [s["id"] for s in out["segments"]] == ["a", "b", "bg"]
        assert out["metadata"]["duckn"]["omitted"] == [group]
        assert ("migrated-group-omitted", About.omitted("g")) in _codes(diagnostics)

    def test_role_values_are_subtracted(self):
        raw = _old([{"id": "bg", "label_value": 9, "background": True},
                    {"id": "a", "label_value": 1},
                    {"id": "all", "members": ["a", "bg"]}])
        out, diagnostics = migrate_seg_extension(raw)
        # a role-bearing member was subtracted, which `members` cannot say: written out
        assert {s["id"]: s.get("label_values") for s in out["segments"]}["all"] == [1]
        assert "members" not in {s["id"]: s for s in out["segments"]}["all"]
        assert ("migrated-background-subtracted", _seg("all")) in _codes(diagnostics)
        ext = SegmentationExtension.model_validate(out)
        assert validate_seg_extension(ext) == []


class TestCollisionsIdsOrder:
    def test_designation_collision(self):
        liver = {"scheme": "SCT", "code": "10200004", "meaning": "Liver"}
        raw = _old([{"id": "rest", "label_value": 1, "designations": [liver]},
                    {"id": "lesion", "label_value": 2},
                    {"id": "liver", "members": ["rest", "lesion"],
                     "designations": [dict(liver, meaning="Liver structure")]},
                    {"id": "other", "label_value": 1, "layer": 1, "designations": [liver]}])
        out, diagnostics = migrate_seg_extension(raw)
        by_id = {s["id"]: s for s in out["segments"]}
        assert by_id["liver"]["designations"][0]["code"] == "10200004"
        assert "designations" not in by_id["rest"]
        assert by_id["rest"]["metadata"]["duckn"]["designations"] == [liver]
        assert by_id["other"]["designations"] == [liver]
        assert _codes(diagnostics) == [("designation-set-aside", _seg("rest"))]

    def test_ids_become_tokens(self):
        assert derive_token_ids(["a b", "a_b", "...", "2.25.1", "a/b", "ok", "a.b"]) == [
            "a_b_2", "a_b", "Segment_2", "2_25_1", "a_b_3", "ok", "a_b_4"]
        raw = _old([{"id": "Left kidney", "label_value": 1, "background": True}])
        out, diagnostics = migrate_seg_extension(raw)
        assert out["segments"][0]["id"] == "Left_kidney"
        assert out["segments"][0]["metadata"] == {"duckn": {"id": "Left kidney"}}
        assert _codes(diagnostics) == [("migrated-background", _seg("Left_kidney")),
                                       ("id-changed", _seg("Left_kidney"))]

    def test_order_keeps_each_layers_positions(self):
        raw = _old([{"id": "x", "label_value": [1]}, {"id": "L1", "label_value": 1, "layer": 1},
                    {"id": "xy", "label_value": [1, 2]}, {"id": "y", "label_value": [2]},
                    {"id": "xyz", "label_value": [1, 2, 3]}], version="0.6")
        out, _ = migrate_seg_extension(raw)
        assert [s["id"] for s in out["segments"]] == ["xyz", "L1", "xy", "x", "y"]

    def test_color_that_resolves_differently_is_reported(self):
        # 0.7: the first colored group containing an uncolored leaf won.
        raw = _old([{"id": "leaf", "label_value": 1}, {"id": "two", "label_value": 2},
                    {"id": "broad", "members": ["leaf", "two"], "color": [1, 0, 0]},
                    {"id": "narrow", "members": ["leaf"], "color": [0, 0, 1]}])
        out, diagnostics = migrate_seg_extension(raw)
        assert [s["id"] for s in out["segments"]] == ["broad", "leaf", "two", "narrow"]
        assert _codes(diagnostics) == [("migrated-color-differs", About.value(0, 1))]


class TestRead:
    def test_reads_and_reports(self):
        ext, diagnostics = read_seg_extension(
            _old([{"id": "bg", "label_value": 0, "background": True},
                  {"id": "a", "label_value": 1, "color": [1, 0, 0]}]),
            dtype="uint8", fill_value=0,
        )
        assert ext.version == "0.9" and ext.segments[1].color == "#ff0000"
        assert _codes(diagnostics) == [("migrated-background", _seg("bg"))]

    def test_non_string_color_is_absent(self):
        ext, diagnostics = read_seg_extension(
            {"version": "0.9", "segments": [{"id": "a", "label_values": [1], "color": [1, 0, 0]}]})
        assert ext.segments[0].color is None
        assert _codes(diagnostics) == [("color-unreadable", _seg("a"))]

    @pytest.mark.parametrize("version", ["0.10", "1.0", "0.9.1", "0.09", "later"])
    def test_refuses_by_version_before_fields(self, version):
        with pytest.raises(DiagnosticsError) as e:
            read_seg_extension({"version": version, "segments": [{"new_field": 1}]})
        assert [d.code for d in e.value.diagnostics] == ["rule-1"]

    @pytest.mark.parametrize(
        "raw, code",
        [({"segments": []}, "rule-1"),
         ({"version": 0.8, "segments": []}, "rule-1"),
         ({"version": "0.9", "segments": [{"id": "a", "label_values": [1], "role": "fg"}]}, "rule-8a"),
         ({"version": "0.9", "segments": [{"id": "a", "label_values": [1.5]}]}, "rule-11a"),
         ({"version": "0.9", "segments": [{"id": "a", "label_values": []}]}, "rule-11a"),
         ({"version": "0.9", "segments": [{"id": "a", "label_values": 1}]}, "rule-11a"),
         ({"version": "0.7", "segments": [{"id": "a", "label_value": True}]}, "rule-11a"),
         ({"version": "0.9", "segments": [{"id": "a", "label_values": [1], "layer": -1}]}, "rule-2"),
         ({"version": "0.9", "segments": [{"id": "a", "label_values": [1], "members": ["b"]}]}, "rule-11a"),
         ({"version": "0.9", "segments": [{"id": "a", "members": ["nobody"]}]}, "rule-11c"),
         ({"version": "0.9", "segments": [{"id": "a", "members": ["b"], "role": "background"},
                                          {"id": "b", "label_values": [1]}]}, "rule-11c"),
         ({"version": "0.9", "segments": [{"id": "a", "members": None}]}, "rule-11a")],
    )
    def test_a_refusal_the_model_enforces_still_has_its_code(self, raw, code):
        with pytest.raises(DiagnosticsError) as e:
            read_seg_extension(raw)
        assert [d.code for d in e.value.diagnostics] == [code]

    @pytest.mark.parametrize("raw", [{"version": "0.9"}, {"version": "0.9", "segments": "no"},
                                     {"version": "0.9", "segments": [{"label_values": [1]}]},
                                     {"version": "0.9", "segments": [], "bogus": 1}])
    def test_not_a_seg_extension(self, raw):
        with pytest.raises(ValueError) as e:
            read_seg_extension(raw)
        assert not isinstance(e.value, DiagnosticsError)

    def test_a_refused_file_still_says_what_migration_changed(self):
        raw = {"version": "0.7", "segments": [{"id": "a b", "label_value": 0, "background": True},
                                               {"id": "s", "label_value": 0}]}
        with pytest.raises(DiagnosticsError) as e:
            read_seg_extension(raw)
        assert [d.code for d in e.value.diagnostics] == ["rule-14"]
        assert [d.code for d in e.value.all_diagnostics] == [
            "migrated-background", "id-changed", "rule-14"]

    def test_refusal_and_strict(self):
        dup = {"version": "0.9", "segments": [{"id": "a", "label_values": [1]},
                                               {"id": "a", "label_values": [2]}]}
        with pytest.raises(DiagnosticsError) as e:
            read_seg_extension(dup)
        assert [d.code for d in e.value.diagnostics] == ["rule-4a"]
        unsorted = {"version": "0.9", "segments": [{"id": "a", "label_values": [2, 1]}]}
        _, diagnostics = read_seg_extension(unsorted)
        assert [d.code for d in diagnostics] == ["rule-11b"]
        with pytest.raises(DiagnosticsError):
            read_seg_extension(unsorted, strict=True)


def _old_spec_examples() -> list[dict]:
    found = []
    for block in re.findall(r"```json\n(.*?)```", OLD_SPEC_PATH.read_text(), re.S):
        obj = None
        for candidate in (block, "{" + block + "}"):  # whole documents and fragments
            try:
                obj = json.loads(candidate)
                break
            except json.JSONDecodeError:
                pass
        seg = obj
        if isinstance(obj, dict) and "attributes" in obj:
            seg = obj["attributes"].get("duckn", {}).get("extensions", {}).get("seg")
        elif isinstance(obj, dict) and "extensions" in obj:
            seg = obj["extensions"].get("seg")
        if isinstance(seg, dict) and "segments" in seg and "version" in seg:
            found.append(seg)
    return found


def test_the_0_7_specs_examples_migrate():
    examples = _old_spec_examples()
    assert len(examples) >= 4
    for raw in examples:
        ext, diagnostics = read_seg_extension(raw)
        broken = {d.code for d in diagnostics} & GUARANTEED
        assert not broken, (raw["segments"][0]["id"], diagnostics)
        assert {d.code for d in diagnostics} <= {
            "rule-5", "migrated-background", "migrated-claim-dropped",
            "migrated-group-omitted", "migrated-background-subtracted",
            "designation-set-aside", "id-changed", "migrated-color-differs"}


def test_migration_carries_an_undefined_algorithm_type_as_found():
    ext, diagnostics = read_seg_extension({"version": "0.7", "segments": [
        {"id": "a", "label_value": 1, "metadata": {"dicom": {"SegmentAlgorithmType": "AUTO"}}}]})
    assert ext.segments[0].dicom.algorithm_type == "AUTO"
    assert [d.code for d in diagnostics] == ["rule-8b"]


def test_a_registry_entrys_url_becomes_definition_url():
    raw = _old([{"id": "a", "label_value": 1}],
               terminologies={"SCT": {"name": "SNOMED CT", "url": "https://browser.ihtsdotools.org"}})
    out, _ = migrate_seg_extension(raw)
    assert out["terminologies"] == {"SCT": {"name": "SNOMED CT",
                                            "definition_url": "https://browser.ihtsdotools.org"}}
    ext, _ = read_seg_extension(raw)
    assert ext.terminologies["SCT"].definition_url == "https://browser.ihtsdotools.org"


def test_an_unresolved_background_union_is_refused_not_crashed():
    raw = {"version": "0.9", "segments": [{"id": "bg", "members": ["nobody"], "role": "background"},
                                          {"id": "a", "label_values": [1]}]}
    with pytest.raises(DiagnosticsError) as e:
        read_seg_extension(raw)
    assert {d.code for d in e.value.diagnostics} == {"rule-11c"}


def test_a_0_8_file_is_a_0_9_file_with_a_new_version():
    raw = {"version": "0.8", "segments": [{"id": "a", "label_values": [1, 3]},
                                          {"id": "b", "label_values": [2, 3], "color": "#cc3333"}]}
    out, diagnostics = migrate_seg_extension(raw)
    assert out == {**raw, "version": "0.9"} and diagnostics == []
    ext, found = read_seg_extension(raw)
    assert ext.version == "0.9" and found == []
