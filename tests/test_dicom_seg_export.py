"""Tests for zarr_to_dicom_seg (duckn labelmap → DICOM LABELMAP Segmentation).

The export writes a LABELMAP-type Segmentation, where each voxel value *is*
a segment number. DICOM requires segment numbers to start at 1 and increase
monotonically (PS3.3 C.8.20.2.1), while duckn label values are arbitrary, so
the exporter remaps the voxel data — these tests pin that contract down.
"""

from __future__ import annotations

import numpy as np
import pytest
import zarr

pydicom = pytest.importorskip("pydicom")

from duckn.dicom_convert import zarr_to_dicom_seg  # noqa: E402
from duckn.models import SEG_EXTENSION_VERSION  # noqa: E402


def _write_labelmap(path, data, segments, *, extra_ext=None):
    """Write a minimal 3D duckn labelmap store with a seg extension."""
    arr = zarr.create_array(
        store=str(path),
        shape=data.shape,
        dtype=data.dtype,
        chunks=data.shape,
        zarr_format=3,
    )
    arr[:] = data
    ext = {"version": SEG_EXTENSION_VERSION, "source_representation": "binary-labelmap"}
    if segments is not None:
        ext["segments"] = segments
    if extra_ext:
        ext.update(extra_ext)
    arr.attrs["duckn"] = {
        "version": "1.0",
        "space": "left-posterior-superior",
        "space_origin": [0.0, 0.0, 0.0],
        "intent": "label-map",
        "axes": [
            {"kind": "space", "centering": "cell", "space_direction": [0, 0, 2.0], "unit": "mm"},
            {"kind": "space", "centering": "cell", "space_direction": [0, 1.0, 0], "unit": "mm"},
            {"kind": "space", "centering": "cell", "space_direction": [1.0, 0, 0], "unit": "mm"},
        ],
        "extensions": {"seg": ext} if segments is not None or extra_ext else {},
    }
    return path


def _seg_numbers(ds):
    return [int(item.SegmentNumber) for item in ds.SegmentSequence]


class TestSegmentNumbering:
    def test_sparse_labels_are_renumbered_from_one(self, tmp_path):
        """Atlas-style sparse ids become 1..N, and the voxels follow."""
        data = np.zeros((2, 4, 4), dtype=np.uint16)
        data[0, 0, 0] = 68
        data[0, 1, 1] = 667
        src = _write_labelmap(
            tmp_path / "in.zarr",
            data,
            [
                {"id": "a", "name": "Layer 1", "label_value": 68},
                {"id": "b", "name": "Layer 2/3", "label_value": 667},
            ],
        )
        out = tmp_path / "out.dcm"
        zarr_to_dicom_seg(src, out)

        ds = pydicom.dcmread(str(out))
        assert _seg_numbers(ds) == [1, 2]

        pixels = np.frombuffer(ds.PixelData, dtype=np.uint8).reshape(data.shape)
        assert pixels[0, 0, 0] == 1  # was 68
        assert pixels[0, 1, 1] == 2  # was 667
        assert pixels.max() == 2

    def test_numbering_is_monotonic_regardless_of_segment_order(self, tmp_path):
        """A high label listed first must not produce a descending sequence."""
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 3
        data[0, 0, 1] = 1
        src = _write_labelmap(
            tmp_path / "in.zarr",
            data,
            [
                {"id": "high", "name": "Three", "label_value": 3},
                {"id": "low", "name": "One", "label_value": 1},
            ],
        )
        out = tmp_path / "out.dcm"
        zarr_to_dicom_seg(src, out)

        ds = pydicom.dcmread(str(out))
        numbers = _seg_numbers(ds)
        assert numbers == [1, 2]
        assert numbers == sorted(numbers)
        labels = [str(i.SegmentLabel) for i in ds.SegmentSequence]
        assert labels == ["Three", "One"]

        # The voxel that was label 3 now carries this segment's new number.
        pixels = np.frombuffer(ds.PixelData, dtype=np.uint8).reshape(data.shape)
        assert pixels[0, 0, 0] == 1
        assert pixels[0, 0, 1] == 2

    def test_identity_mapping_leaves_data_untouched(self, tmp_path):
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 1
        data[0, 0, 1] = 2
        src = _write_labelmap(
            tmp_path / "in.zarr",
            data,
            [
                {"id": "a", "name": "A", "label_value": 1},
                {"id": "b", "name": "B", "label_value": 2},
            ],
        )
        out = tmp_path / "out.dcm"
        zarr_to_dicom_seg(src, out)

        ds = pydicom.dcmread(str(out))
        assert _seg_numbers(ds) == [1, 2]
        pixels = np.frombuffer(ds.PixelData, dtype=np.uint8).reshape(data.shape)
        np.testing.assert_array_equal(pixels, data)


class TestSegmentSequenceIsNeverEmpty:
    def test_no_seg_extension_synthesizes_segments(self, tmp_path):
        """Segment Sequence is Type 1, so a bare labelmap still gets rows."""
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 1
        data[0, 0, 1] = 5
        src = _write_labelmap(tmp_path / "in.zarr", data, None)
        out = tmp_path / "out.dcm"
        zarr_to_dicom_seg(src, out)

        ds = pydicom.dcmread(str(out))
        assert _seg_numbers(ds) == [1, 2]
        assert len(ds.SegmentSequence) == 2

    def test_all_reference_only_segments_raises(self, tmp_path):
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        src = _write_labelmap(
            tmp_path / "in.zarr",
            data,
            [
                {"id": "A", "members": ["B"]},
                {"id": "B", "members": ["A"]},
            ],
        )
        with pytest.raises(ValueError, match="reference"):
            zarr_to_dicom_seg(src, tmp_path / "out.dcm")

    def test_reference_only_segment_is_skipped_but_leaves_survive(self, tmp_path):
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 1
        src = _write_labelmap(
            tmp_path / "in.zarr",
            data,
            [
                {"id": "parent", "name": "Parent", "members": ["leaf"]},
                {"id": "leaf", "name": "Leaf", "label_value": 1},
            ],
        )
        out = tmp_path / "out.dcm"
        zarr_to_dicom_seg(src, out)

        ds = pydicom.dcmread(str(out))
        assert _seg_numbers(ds) == [1]
        assert str(ds.SegmentSequence[0].SegmentLabel) == "Leaf"


class TestUnrepresentableSegmentations:
    def test_island_group_exports_its_islands_not_itself(self, tmp_path):
        """The island model (§7.3): a structure that is a group over islands has
        no LABELMAP row of its own; its islands export as the segments."""
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 2
        data[0, 1, 1] = 3
        src = _write_labelmap(
            tmp_path / "in.zarr",
            data,
            [
                {"id": "tumor_only", "name": "Tumor only", "label_value": 2},
                {"id": "overlap", "name": "Overlap", "label_value": 3},
                {"id": "tumor", "name": "Tumor", "members": ["tumor_only", "overlap"]},
            ],
        )
        out = tmp_path / "out.dcm"
        zarr_to_dicom_seg(src, out)
        ds = pydicom.dcmread(str(out))
        assert [str(s.SegmentLabel) for s in ds.SegmentSequence] == ["Tumor only", "Overlap"]

    def test_layered_segment_raises(self, tmp_path):
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 1
        src = _write_labelmap(
            tmp_path / "in.zarr",
            data,
            [
                {"id": "a", "name": "A", "label_value": 1, "layer": 0},
                {"id": "b", "name": "B", "label_value": 1, "layer": 1},
            ],
        )
        with pytest.raises(ValueError, match="layer"):
            zarr_to_dicom_seg(src, tmp_path / "out.dcm")

    def test_duplicate_label_in_same_layer_raises(self, tmp_path):
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 1
        src = _write_labelmap(
            tmp_path / "in.zarr",
            data,
            [
                {"id": "a", "name": "A", "label_value": 1},
                {"id": "b", "name": "B", "label_value": 1},
            ],
        )
        with pytest.raises(ValueError, match="both claim label"):
            zarr_to_dicom_seg(src, tmp_path / "out.dcm")

    def test_undescribed_labels_warn_and_become_background(self, tmp_path):
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 1
        data[0, 0, 1] = 9  # described by no segment
        src = _write_labelmap(
            tmp_path / "in.zarr",
            data,
            [{"id": "a", "name": "A", "label_value": 1}],
        )
        out = tmp_path / "out.dcm"
        with pytest.warns(UserWarning, match=r"\[9\]"):
            zarr_to_dicom_seg(src, out)

        ds = pydicom.dcmread(str(out))
        pixels = np.frombuffer(ds.PixelData, dtype=np.uint8).reshape(data.shape)
        assert pixels[0, 0, 1] == 0


class TestCodeMeaning:
    def _segment_with_dicom(self, **overrides):
        seg = {
            "id": "SEG_ID_7",
            "label_value": 1,
            "dicom": {
                "category": {"scheme": "SCT", "code": "123037004", "meaning": "Body structure"},
                "type": {"scheme": "SCT", "code": "64033007", "meaning": "Kidney"},
                "anatomic_region": {"scheme": "SCT", "code": "64033007", "meaning": "Kidney"},
                "anatomic_region_modifier": {"scheme": "SCT", "code": "24028007", "meaning": "Right"},
            },
        }
        seg.update(overrides)
        return seg

    def _export(self, tmp_path, segment):
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 1
        src = _write_labelmap(tmp_path / "in.zarr", data, [segment])
        out = tmp_path / "out.dcm"
        zarr_to_dicom_seg(src, out)
        return pydicom.dcmread(str(out))

    def test_meanings_are_preserved(self, tmp_path):
        ds = self._export(tmp_path, self._segment_with_dicom(name="Right kidney"))
        item = ds.SegmentSequence[0]
        assert str(item.SegmentedPropertyCategoryCodeSequence[0].CodeMeaning) == "Body structure"
        assert str(item.SegmentedPropertyTypeCodeSequence[0].CodeMeaning) == "Kidney"
        assert str(item.AnatomicRegionSequence[0].CodeMeaning) == "Kidney"

    def test_modifier_sequences_are_written(self, tmp_path):
        """Laterality must survive export (§4.1 post-coordination)."""
        seg = self._segment_with_dicom(name="Right kidney")
        seg["dicom"]["type_modifier"] = {"scheme": "SCT", "code": "24028007", "meaning": "Right"}
        ds = self._export(tmp_path, seg)
        item = ds.SegmentSequence[0]

        type_mod = item.SegmentedPropertyTypeCodeSequence[0].SegmentedPropertyTypeModifierCodeSequence[0]
        assert str(type_mod.CodeValue) == "24028007"
        assert str(type_mod.CodeMeaning) == "Right"

        anat_mod = item.AnatomicRegionSequence[0].AnatomicRegionModifierSequence[0]
        assert str(anat_mod.CodeValue) == "24028007"
        assert str(anat_mod.CodeMeaning) == "Right"

    def test_falls_back_to_segment_name(self, tmp_path):
        seg = {
            "id": "S1",
            "name": "Liver",
            "label_value": 1,
            "dicom": {"type": {"scheme": "SCT", "code": "10200004"}},
        }
        ds = self._export(tmp_path, seg)
        assert str(ds.SegmentSequence[0].SegmentedPropertyTypeCodeSequence[0].CodeMeaning) == "Liver"

    def test_raises_rather_than_publishing_the_segment_id_as_a_meaning(self, tmp_path):
        """A segment id is not a concept meaning — §4.2 says fail instead."""
        seg = {
            "id": "SEG_ID_7",
            "label_value": 1,
            "dicom": {"category": {"scheme": "SCT", "code": "123037004"}},
        }
        with pytest.raises(ValueError, match="CodeMeaning"):
            self._export(tmp_path, seg)


class TestRoundTrip:
    def test_classification_survives_export_and_reimport(self, tmp_path):
        from duckn.dicom_convert import _extract_seg_extension

        seg = {
            "id": "S1",
            "name": "Right kidney",
            "label_value": 1,
            "dicom": {
                "category": {"scheme": "SCT", "code": "123037004", "meaning": "Body structure"},
                "type": {"scheme": "SCT", "code": "64033007", "meaning": "Kidney"},
                "type_modifier": {"scheme": "SCT", "code": "24028007", "meaning": "Right"},
            },
        }
        data = np.zeros((1, 4, 4), dtype=np.uint8)
        data[0, 0, 0] = 1
        src = _write_labelmap(tmp_path / "in.zarr", data, [seg])
        out = tmp_path / "out.dcm"
        zarr_to_dicom_seg(src, out)

        ds = pydicom.dcmread(str(out))
        ds.SegmentationType = "LABELMAP"
        ext = _extract_seg_extension(ds)
        assert ext is not None
        seg_back = ext.segments[0]
        assert seg_back.dicom.category.code == "123037004"
        assert seg_back.dicom.category.meaning == "Body structure"
        assert seg_back.dicom.type.code == "64033007"
        assert seg_back.dicom.type_modifier.code == "24028007"
        # And the type code surfaces as the primary designation, with laterality.
        assert seg_back.designations[0].code == "64033007"
        assert seg_back.designations[0].modifier.code == "24028007"
