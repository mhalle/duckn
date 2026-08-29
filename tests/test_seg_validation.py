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
    def test_leaf_scalar(self):
        ext = _ext([{"id": "S1", "label_value": 1}])
        assert effective_label_values(ext, "S1") == {(0, 1)}

    def test_scalar_and_single_element_list_are_equivalent(self):
        a = _ext([{"id": "S1", "label_value": 1}])
        b = _ext([{"id": "S1", "label_value": [1]}])
        assert effective_label_values(a, "S1") == effective_label_values(b, "S1")

    def test_island_union(self):
        """§7.3: overlapping structures share an island."""
        ext = _ext(
            [
                {"id": "tumor", "label_value": [2, 3]},
                {"id": "liver", "label_value": [1, 3]},
            ]
        )
        assert effective_label_values(ext, "tumor") == {(0, 2), (0, 3)}
        assert effective_label_values(ext, "liver") == {(0, 1), (0, 3)}

    def test_transitive_reference_union(self):
        """§7.5: an interior node is the union of its descendants."""
        ext = _ext(
            [
                {"id": "root", "label_value": ["mid", "leaf-c"]},
                {"id": "mid", "label_value": ["leaf-a", "leaf-b"]},
                {"id": "leaf-a", "label_value": 1},
                {"id": "leaf-b", "label_value": 2},
                {"id": "leaf-c", "label_value": 3},
            ]
        )
        assert effective_label_values(ext, "root") == {(0, 1), (0, 2), (0, 3)}
        assert effective_label_values(ext, "mid") == {(0, 1), (0, 2)}
        assert effective_label_values(ext, "leaf-a") == {(0, 1)}

    def test_mixed_own_and_referenced(self):
        ext = _ext(
            [
                {"id": "parent", "label_value": [9, "child"]},
                {"id": "child", "label_value": 1},
            ]
        )
        assert effective_label_values(ext, "parent") == {(0, 9), (0, 1)}

    def test_referenced_segment_keeps_its_own_layer(self):
        """Layer comes from the segment that owns the voxels, not the referrer."""
        ext = _ext(
            [
                {"id": "parent", "label_value": ["a", "b"]},
                {"id": "a", "label_value": 1, "layer": 0},
                {"id": "b", "label_value": 1, "layer": 1},
            ]
        )
        assert effective_label_values(ext, "parent") == {(0, 1), (1, 1)}

    def test_diamond_is_not_a_cycle(self):
        """Multiple inheritance is legal as long as the graph is acyclic."""
        ext = _ext(
            [
                {"id": "top", "label_value": ["left", "right"]},
                {"id": "left", "label_value": ["shared"]},
                {"id": "right", "label_value": ["shared"]},
                {"id": "shared", "label_value": 5},
            ]
        )
        assert effective_label_values(ext, "top") == {(0, 5)}

    def test_self_cycle_raises(self):
        ext = _ext([{"id": "A", "label_value": ["A"]}])
        with pytest.raises(ValueError, match="circular"):
            effective_label_values(ext, "A")

    def test_indirect_cycle_raises(self):
        ext = _ext(
            [
                {"id": "A", "label_value": ["B"]},
                {"id": "B", "label_value": ["C"]},
                {"id": "C", "label_value": ["A"]},
            ]
        )
        with pytest.raises(ValueError, match="circular"):
            effective_label_values(ext, "A")

    def test_dangling_reference_raises(self):
        ext = _ext([{"id": "A", "label_value": ["nope"]}])
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
                {"id": "S2", "label_value": [2, 3]},
                {"id": "S3", "label_value": ["S1", "S2"]},
            ]
        )
        validate_seg_extension(ext)  # does not raise

    def test_empty_segmentation_passes(self):
        validate_seg_extension(_ext([]))

    def test_duplicate_ids_rejected(self):
        ext = _ext([{"id": "dup", "label_value": 1}, {"id": "dup", "label_value": 2}])
        with pytest.raises(ValueError, match="duplicate segment id"):
            validate_seg_extension(ext)

    def test_background_label_rejected(self):
        ext = _ext([{"id": "S1", "label_value": 0}])
        with pytest.raises(ValueError, match="reserved for background"):
            validate_seg_extension(ext)

    def test_identical_effective_sets_rejected(self):
        """Rule 8: two segments claiming exactly the same voxels."""
        ext = _ext(
            [
                {"id": "a", "label_value": [1, 3]},
                {"id": "b", "label_value": [3, 1]},
            ]
        )
        with pytest.raises(ValueError, match="identical effective label sets"):
            validate_seg_extension(ext)

    def test_shared_island_is_allowed(self):
        """A shared value is fine; only the whole set must be unique."""
        validate_seg_extension(
            _ext(
                [
                    {"id": "tumor", "label_value": [2, 3]},
                    {"id": "liver", "label_value": [1, 3]},
                ]
            )
        )

    def test_same_label_in_different_layers_is_allowed(self):
        validate_seg_extension(
            _ext(
                [
                    {"id": "a", "label_value": 1, "layer": 0},
                    {"id": "b", "label_value": 1, "layer": 1},
                ]
            )
        )

    def test_cycle_reported(self):
        ext = _ext(
            [
                {"id": "A", "label_value": ["B"]},
                {"id": "B", "label_value": ["A"]},
            ]
        )
        with pytest.raises(ValueError, match="circular"):
            validate_seg_extension(ext)

    def test_dangling_reference_reported(self):
        ext = _ext([{"id": "A", "label_value": ["ghost"]}])
        with pytest.raises(ValueError, match="does not resolve"):
            validate_seg_extension(ext)

    def test_all_problems_reported_together(self):
        ext = _ext(
            [
                {"id": "dup", "label_value": 0},
                {"id": "dup", "label_value": ["ghost"]},
            ]
        )
        with pytest.raises(ValueError) as exc:
            validate_seg_extension(ext)
        message = str(exc.value)
        assert "duplicate segment id" in message
        assert "reserved for background" in message
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
