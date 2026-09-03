"""Tests for segment reference resolution and §5 consistency validation."""

from __future__ import annotations

import pytest

from duckn.models import (
    SEG_EXTENSION_VERSION,
    AxisMetadata,
    SegmentationExtension,
    effective_label_values,
    validate_seg_extension,
)


def _ext(segments, **kwargs):
    return SegmentationExtension(
        version=SEG_EXTENSION_VERSION, segments=segments, **kwargs
    )


def _axes(*kinds):
    return [AxisMetadata(kind=k) for k in kinds]


# ---------------------------------------------------------------------------
# effective_label_values
# ---------------------------------------------------------------------------


class TestEffectiveLabelValues:
    def test_leaf(self):
        ext = _ext([{"id": "S1", "label_value": 1}])
        assert effective_label_values(ext, "S1") == {(0, 1)}

    def test_islands_and_groups(self):
        """§7.3: overlapping structures are groups sharing an island leaf."""
        ext = _ext(
            [
                {"id": "liver_only", "label_value": 1},
                {"id": "tumor_only", "label_value": 2},
                {"id": "overlap", "label_value": 3},
                {"id": "tumor", "members": ["tumor_only", "overlap"]},
                {"id": "liver", "members": ["liver_only", "overlap"]},
            ]
        )
        assert effective_label_values(ext, "tumor") == {(0, 2), (0, 3)}
        assert effective_label_values(ext, "liver") == {(0, 1), (0, 3)}

    def test_transitive_membership(self):
        """§7.5: an interior node is the union of its descendants."""
        ext = _ext(
            [
                {"id": "root", "members": ["mid", "leaf-c"]},
                {"id": "mid", "members": ["leaf-a", "leaf-b"]},
                {"id": "leaf-a", "label_value": 1},
                {"id": "leaf-b", "label_value": 2},
                {"id": "leaf-c", "label_value": 3},
            ]
        )
        assert effective_label_values(ext, "root") == {(0, 1), (0, 2), (0, 3)}
        assert effective_label_values(ext, "mid") == {(0, 1), (0, 2)}
        assert effective_label_values(ext, "leaf-a") == {(0, 1)}

    def test_member_keeps_its_own_layer(self):
        """Layer comes from the leaf that owns the voxels, not the group."""
        ext = _ext(
            [
                {"id": "parent", "members": ["a", "b"]},
                {"id": "a", "label_value": 1, "layer": 0},
                {"id": "b", "label_value": 1, "layer": 1},
            ]
        )
        assert effective_label_values(ext, "parent") == {(0, 1), (1, 1)}

    def test_diamond_is_not_a_cycle(self):
        """Multiple inheritance is legal as long as the graph is acyclic."""
        ext = _ext(
            [
                {"id": "top", "members": ["left", "right"]},
                {"id": "left", "members": ["shared"]},
                {"id": "right", "members": ["shared"]},
                {"id": "shared", "label_value": 5},
            ]
        )
        assert effective_label_values(ext, "top") == {(0, 5)}

    def test_self_cycle_raises(self):
        ext = _ext([{"id": "A", "members": ["A"]}])
        with pytest.raises(ValueError, match="circular"):
            effective_label_values(ext, "A")

    def test_indirect_cycle_raises(self):
        ext = _ext(
            [
                {"id": "A", "members": ["B"]},
                {"id": "B", "members": ["C"]},
                {"id": "C", "members": ["A"]},
            ]
        )
        with pytest.raises(ValueError, match="circular"):
            effective_label_values(ext, "A")

    def test_dangling_member_raises(self):
        ext = _ext([{"id": "A", "members": ["nope"]}])
        with pytest.raises(KeyError, match="does not resolve"):
            effective_label_values(ext, "A")

    def test_unknown_segment_raises(self):
        ext = _ext([{"id": "A", "label_value": 1}])
        with pytest.raises(KeyError, match="no segment with id"):
            effective_label_values(ext, "missing")


# ---------------------------------------------------------------------------
# validate_seg_extension
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_segmentation_passes(self):
        ext = _ext(
            [
                {"id": "S1", "label_value": 1},
                {"id": "S2", "label_value": 2},
                {"id": "S3", "members": ["S1", "S2"], "disjoint": True},
            ]
        )
        validate_seg_extension(ext)  # does not raise

    def test_empty_segmentation_passes(self):
        validate_seg_extension(_ext([]))

    def test_duplicate_ids_rejected(self):
        ext = _ext([{"id": "dup", "label_value": 1}, {"id": "dup", "label_value": 2}])
        with pytest.raises(ValueError, match="duplicate segment id"):
            validate_seg_extension(ext)

    def test_a_leaf_or_a_group_never_both(self):
        """Rule 7 is enforced by the model itself."""
        with pytest.raises(ValueError, match="exactly one of"):
            _ext([{"id": "S1", "label_value": 1, "members": ["S1"]}])
        with pytest.raises(ValueError, match="exactly one of"):
            _ext([{"id": "S1"}])
        with pytest.raises(ValueError, match="claims a group"):
            _ext([{"id": "S1", "label_value": 1, "disjoint": True}])
        with pytest.raises(ValueError, match="leaf's role"):
            _ext([{"id": "g", "members": ["S1"], "background": True},
                  {"id": "S1", "label_value": 1}])
        with pytest.raises(ValueError, match="no layer"):
            _ext([{"id": "g", "members": ["S1"], "layer": 0}, {"id": "S1", "label_value": 1}])

    def test_one_leaf_per_value_in_a_layer(self):
        """Rule 8: a value resolves to exactly one leaf. The copy-paste duplicate
        the old rule 8 was aimed at is caught here, structurally."""
        ext = _ext([{"id": "a", "label_value": 5}, {"id": "b", "label_value": 5}])
        with pytest.raises(ValueError, match="all claim label value 5 in layer 0"):
            validate_seg_extension(ext)

    def test_same_label_in_different_layers_is_allowed(self):
        validate_seg_extension(
            _ext(
                [
                    {"id": "a", "label_value": 1, "layer": 0},
                    {"id": "b", "label_value": 1, "layer": 1},
                ]
            )
        )

    def test_identical_effective_sets_are_allowed(self):
        """Identity is the id, never the voxels: a group of one member coincides
        with its leaf and is its own statement (the vertebral column on a scan
        that shows one vertebra); an alias is a group over the leaf."""
        validate_seg_extension(
            _ext(
                [
                    {"id": "vertebra_L5", "label_value": 26},
                    {"id": "vertebral_column", "members": ["vertebra_L5"]},
                    {"id": "hepar", "members": ["liver"]},
                    {"id": "liver", "label_value": 5},
                ]
            )
        )

    def test_background_default_is_zero(self):
        """Rule 10: with no background segment, 0 is the background and no leaf
        may claim it."""
        ext = _ext([{"id": "S1", "label_value": 0}])
        with pytest.raises(ValueError, match="the background of layer 0 \\(0 by default\\)"):
            validate_seg_extension(ext)

    def test_a_declared_background_frees_zero_and_binds_its_own_value(self):
        """FreeSurfer's Unknown at 0 is a named background; then 0 belongs to it
        and a fill value elsewhere is the background of another layer."""
        validate_seg_extension(
            _ext(
                [
                    {"id": "unknown", "name": "Unknown", "label_value": 0, "background": True},
                    {"id": "fill", "label_value": 255, "layer": 1, "background": True},
                    {"id": "zero_is_a_label_here", "label_value": 0, "layer": 1},
                ]
            )
        )
        with pytest.raises(ValueError, match="the background of layer 1"):
            validate_seg_extension(
                _ext(
                    [
                        {"id": "fill", "label_value": 255, "layer": 1, "background": True},
                        {"id": "oops", "label_value": 255, "layer": 1},
                    ]
                )
            )

    def test_at_most_one_background_per_layer(self):
        ext = _ext(
            [
                {"id": "a", "label_value": 0, "background": True},
                {"id": "b", "label_value": 9, "background": True},
            ]
        )
        with pytest.raises(ValueError, match="2 background segments"):
            validate_seg_extension(ext)

    def test_disjoint_claim_is_checked(self):
        """Rule 13: a group claiming disjoint members must have them."""
        ok = _ext(
            [
                {"id": "a", "label_value": 1},
                {"id": "b", "label_value": 2},
                {"id": "g", "members": ["a", "b"], "disjoint": True},
            ]
        )
        validate_seg_extension(ok)
        bad = _ext(
            [
                {"id": "liver_only", "label_value": 1},
                {"id": "overlap", "label_value": 3},
                {"id": "tumor_only", "label_value": 2},
                {"id": "liver", "members": ["liver_only", "overlap"]},
                {"id": "tumor", "members": ["tumor_only", "overlap"]},
                {"id": "both", "members": ["liver", "tumor"], "disjoint": True},
            ]
        )
        with pytest.raises(ValueError, match="'liver' and 'tumor' share \\[\\(0, 3\\)\\]"):
            validate_seg_extension(bad)

    def test_exhaustive_is_not_checked_from_metadata(self):
        validate_seg_extension(
            _ext([{"id": "a", "label_value": 1}, {"id": "g", "members": ["a"], "exhaustive": True}])
        )

    def test_cycle_reported(self):
        ext = _ext(
            [
                {"id": "A", "members": ["B"]},
                {"id": "B", "members": ["A"]},
            ]
        )
        with pytest.raises(ValueError, match="circular"):
            validate_seg_extension(ext)

    def test_dangling_member_reported(self):
        ext = _ext([{"id": "A", "members": ["ghost"]}])
        with pytest.raises(ValueError, match="does not resolve"):
            validate_seg_extension(ext)

    def test_all_problems_reported_together(self):
        ext = _ext(
            [
                {"id": "dup", "label_value": 0},
                {"id": "dup", "members": ["ghost"]},
            ]
        )
        with pytest.raises(ValueError) as exc:
            validate_seg_extension(ext)
        message = str(exc.value)
        assert "duplicate segment id" in message
        assert "the background of layer 0" in message
        assert "does not resolve" in message


class TestAxisDependentRules:
    def test_layer_without_list_axis_rejected(self):
        """Rule 2 — the violation duckn's own converter used to emit."""
        ext = _ext([{"id": "S1", "label_value": 1, "layer": 0}])
        with pytest.raises(ValueError, match="no 'list' axis"):
            validate_seg_extension(ext, axes=_axes("space", "space", "space"))

    def test_layer_with_list_axis_passes(self):
        ext = _ext([{"id": "S1", "label_value": 1, "layer": 1}])
        validate_seg_extension(
            ext, axes=_axes("list", "space", "space", "space"), shape=(2, 4, 4, 4)
        )

    def test_layer_out_of_range_rejected(self):
        ext = _ext([{"id": "S1", "label_value": 1, "layer": 5}])
        with pytest.raises(ValueError, match="out of range"):
            validate_seg_extension(
                ext, axes=_axes("list", "space", "space", "space"), shape=(2, 4, 4, 4)
            )

    def test_omitted_layer_passes_without_list_axis(self):
        ext = _ext([{"id": "S1", "label_value": 1}])
        validate_seg_extension(ext, axes=_axes("space", "space", "space"))

    def test_fractional_requires_list_axis(self):
        ext = _ext(
            [{"id": "S1", "label_value": 1}],
            source_representation="fractional-labelmap",
        )
        with pytest.raises(ValueError, match="fractional labelmap requires a 'list' axis"):
            validate_seg_extension(ext, axes=_axes("space", "space", "space"))

    def test_fractional_requires_distinct_layers(self):
        ext = _ext(
            [
                {"id": "S1", "label_value": 1, "layer": 0},
                {"id": "S2", "label_value": 1, "layer": 0},
            ],
            source_representation="fractional-labelmap",
        )
        with pytest.raises(ValueError, match="distinct layer per segment"):
            validate_seg_extension(
                ext, axes=_axes("list", "space", "space", "space"), shape=(2, 4, 4, 4)
            )

    def test_valid_fractional_passes(self):
        ext = _ext(
            [
                {"id": "S1", "label_value": 1, "layer": 0},
                {"id": "S2", "label_value": 1, "layer": 1},
            ],
            source_representation="fractional-labelmap",
        )
        validate_seg_extension(
            ext, axes=_axes("list", "space", "space", "space"), shape=(2, 4, 4, 4)
        )


class TestRealWorldFilesValidate:
    def test_converted_seg_nrrd_files_satisfy_the_rules(self):
        """Every fixture duckn ships must satisfy its own spec."""
        import warnings
        from pathlib import Path

        import nrrd

        from duckn.seg_nrrd import parse_seg_keyvalues

        data_dir = Path(__file__).parent / "data" / "real-world"
        files = sorted(data_dir.glob("*.seg.nrrd"))
        assert files, "expected real-world fixtures"

        for path in files:
            header = nrrd.read_header(str(path))
            keyvalues = {
                k: str(v)
                for k, v in header.items()
                if not k.startswith(
                    ("type", "dimension", "space", "sizes", "encoding", "endian",
                     "kinds", "data file", "datafile", "content", "sample units",
                     "spacings", "thicknesses", "axis m", "axism", "centerings",
                     "labels", "units", "min", "max", "old", "number", "line", "byte")
                )
            }
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ext, _ = parse_seg_keyvalues(keyvalues)
            validate_seg_extension(ext)


# ---------------------------------------------------------------------------
# 0.7: migration, and the questions a reader asks of leaves and groups
# ---------------------------------------------------------------------------


import numpy as np  # noqa: E402

from duckn.models import (  # noqa: E402
    background_value,
    color_map,
    coverage_report,
    label_values_by_layer,
    leaf_for,
    leaves_of,
    parents_of,
    validate_seg_data,
)


class TestMigrationFrom06:
    def test_string_entries_become_members(self):
        ext = SegmentationExtension(
            version="0.6",
            segments=[{"id": "g", "label_value": ["a", "b"]},
                      {"id": "a", "label_value": 1}, {"id": "b", "label_value": 2}],
        )
        assert ext.version == SEG_EXTENSION_VERSION
        assert ext.segments[0].members == ["a", "b"] and ext.segments[0].label_value is None

    def test_int_lists_become_groups_over_island_leaves(self):
        """§7.3 in its 0.6 spelling: the shared island is synthesized once."""
        ext = SegmentationExtension(
            version="0.6",
            segments=[{"id": "tumor", "label_value": [2, 3]},
                      {"id": "liver", "label_value": [1, 3]}],
        )
        by_id = {s.id: s for s in ext.segments}
        assert by_id["tumor"].members == ["label_2", "label_3"]
        assert by_id["liver"].members == ["label_1", "label_3"]
        assert {s.id for s in ext.segments} == {"tumor", "liver", "label_1", "label_2", "label_3"}
        assert by_id["label_3"].label_value == 3 and by_id["label_3"].name == "label 3"
        validate_seg_extension(ext)
        assert effective_label_values(ext, "liver") == {(0, 1), (0, 3)}

    def test_an_existing_leaf_for_the_value_is_reused_not_duplicated(self):
        ext = SegmentationExtension(
            version="0.6",
            segments=[{"id": "zone", "name": "intersection", "label_value": 3},
                      {"id": "tumor", "label_value": [2, 3]}],
        )
        assert {s.id for s in ext.segments} == {"zone", "tumor", "label_2"}
        assert [s for s in ext.segments if s.id == "tumor"][0].members == ["label_2", "zone"]
        validate_seg_extension(ext)

    def test_layers_are_respected_and_single_lists_collapse(self):
        ext = SegmentationExtension(
            version="0.6",
            segments=[{"id": "u", "label_value": [4, 5], "layer": 2},
                      {"id": "one", "label_value": [7]}],
        )
        by_id = {s.id: s for s in ext.segments}
        assert by_id["u"].members == ["label_4_layer_2", "label_5_layer_2"] and by_id["u"].layer is None
        assert by_id["label_4_layer_2"].layer == 2
        assert by_id["one"].label_value == 7 and by_id["one"].members is None

    def test_a_0_7_file_is_left_alone(self):
        ext = SegmentationExtension(version="0.7", segments=[{"id": "a", "label_value": 1}])
        assert ext.version == "0.7" and len(ext.segments) == 1


class TestReaderHelpers:
    def _liver(self):
        return _ext(
            [
                {"id": "bg", "name": "background", "label_value": 0, "background": True},
                {"id": "liver", "name": "liver", "label_value": 5,
                 "designations": [{"scheme": "SCT", "code": "10200004"}]},
                {"id": "c1", "label_value": 1, "layer": 1},
                {"id": "c2", "label_value": 2, "layer": 1, "color": [0.0, 1.0, 0.0]},
                {"id": "couinaud", "name": "liver by Couinaud segment",
                 "members": ["c1", "c2"], "disjoint": True, "exhaustive": True,
                 "color": [1.0, 0.0, 0.0],
                 "designations": [{"scheme": "SCT", "code": "10200004"}]},
                {"id": "everything", "members": ["couinaud"]},
            ]
        )

    def test_background_value(self):
        ext = self._liver()
        assert background_value(ext, 0) == 0 and background_value(ext, 1) == 0
        ext2 = _ext([{"id": "fill", "label_value": 255, "background": True}])
        assert background_value(ext2) == 255

    def test_leaf_for_and_parents_of(self):
        ext = self._liver()
        assert leaf_for(ext, 5).id == "liver"
        assert leaf_for(ext, 1) is None and leaf_for(ext, 1, layer=1).id == "c1"
        assert parents_of(ext, "c1") == ["couinaud"]
        assert parents_of(ext, "couinaud") == ["everything"]
        assert parents_of(ext, "liver") == []

    def test_leaves_of_and_values_by_layer(self):
        ext = self._liver()
        assert leaves_of(ext, "everything") == ["c1", "c2"]
        assert leaves_of(ext, "liver") == ["liver"]
        assert label_values_by_layer(ext, "couinaud") == {1: {1, 2}}

    def test_color_map_inherits_from_the_first_containing_group(self):
        ext = self._liver()
        assert color_map(ext, layer=1) == {1: [1.0, 0.0, 0.0], 2: [0.0, 1.0, 0.0]}
        assert color_map(ext, layer=1, inherit=False) == {2: [0.0, 1.0, 0.0]}
        assert color_map(ext, layer=0) == {}

    def test_validate_seg_data_requires_every_present_value_described(self):
        ext = self._liver()
        data = np.zeros((2, 3, 3, 3), dtype=np.uint8)
        data[0, 0, 0, 0] = 5
        data[1, 0, 0, 0] = 1
        data[1, 1, 1, 1] = 2
        validate_seg_data(ext, data, list_axis=0)
        data[1, 2, 2, 2] = 9
        data[0, 2, 2, 2] = 7
        with pytest.raises(ValueError) as exc:
            validate_seg_data(ext, data, list_axis=0)
        assert "layer 0: values [7]" in str(exc.value) and "layer 1: values [9]" in str(exc.value)

    def test_validate_seg_data_exempts_a_declared_background(self):
        ext = _ext([{"id": "fill", "label_value": 255, "background": True},
                    {"id": "a", "label_value": 1}])
        data = np.full((2, 2, 2), 255, dtype=np.uint8)
        data[0, 0, 0] = 1
        validate_seg_data(ext, data)
        with pytest.raises(ValueError, match="values \\[0\\]"):
            data[1, 1, 1] = 0
            validate_seg_data(ext, data)

    def test_coverage_report_compares_a_group_with_the_leaf_it_designates(self):
        ext = self._liver()
        data = np.zeros((2, 4, 4, 4), dtype=np.uint8)
        data[0, :2] = 5                      # the liver leaf: 32 voxels
        data[1, :1] = 1                      # Couinaud 1: 16 of them
        data[1, 1:2] = 2                     # Couinaud 2: the other 16
        rep = coverage_report(ext, data, list_axis=0)
        assert rep == {"couinaud": {"leaf": "liver", "jaccard": 1.0,
                                    "group_voxels": 32, "leaf_voxels": 32}}
        data[1, 1:2] = 0                     # lose Couinaud 2: half the liver uncovered
        assert coverage_report(ext, data, list_axis=0)["couinaud"]["jaccard"] == 0.5
