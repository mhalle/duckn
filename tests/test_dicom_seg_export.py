"""Tests for the DICOM Segmentation mapping of seg 0.8 (spec §6.2): export as
BINARY by default, LABELMAP on request, FRACTIONAL; import; and the round trip."""

from __future__ import annotations

import numpy as np
import pytest
import zarr

pydicom = pytest.importorskip("pydicom")

from duckn.diagnostics import About  # noqa: E402
from duckn.dicom_convert import dicom_to_zarr, zarr_to_dicom_seg  # noqa: E402
from duckn.dicom_seg import LABELMAP_SEG_SOP_CLASS_UID, SEG_SOP_CLASS_UID  # noqa: E402

BODY = {"scheme": "SCT", "code": "123037004", "meaning": "Body structure"}
KIDNEY = {"scheme": "SCT", "code": "64033007", "meaning": "Kidney"}
MASS = {"scheme": "SCT", "code": "4147007", "meaning": "Mass"}
DICOM = {"category": BODY, "type": KIDNEY, "algorithm_type": "MANUAL"}
DEFAULTS = dict(default_dicom={"category": BODY, "type": MASS}, algorithm_type="MANUAL")

_SPACE = [
    {"kind": "space", "centering": "cell", "space_direction": [0, 0, 2.0], "unit": "mm"},
    {"kind": "space", "centering": "cell", "space_direction": [0, 1.0, 0], "unit": "mm"},
    {"kind": "space", "centering": "cell", "space_direction": [1.0, 0, 0], "unit": "mm"},
]


def _write(path, data, segments, *, version="0.8", layered=False, fill_value=0, **ext):
    """Write a minimal duckn segmentation store."""
    arr = zarr.create_array(store=str(path), shape=data.shape, dtype=data.dtype,
                            chunks=data.shape, zarr_format=3, fill_value=fill_value)
    arr[:] = data
    duckn = {
        "version": "1.0",
        "space": "left-posterior-superior",
        "space_origin": [0.0, 0.0, 0.0],
        "axes": ([{"kind": "list"}] if layered else []) + _SPACE,
    }
    if segments is not None:
        ext.setdefault("terminologies", {"SCT": {"name": "SNOMED Clinical Terms",
                                                 "uri": "http://snomed.info/sct"}})
        duckn["extensions"] = {"seg": {"version": version, "segments": segments, **ext}}
    arr.attrs["duckn"] = duckn
    return path


def _export(tmp_path, data, segments, **kwargs):
    store_kwargs = {k: kwargs.pop(k) for k in list(kwargs)
                    if k in ("version", "layered", "fill_value", "implicit_background",
                             "source_representation", "metadata")}
    n = len(list(tmp_path.glob("in*.zarr")))
    src = _write(tmp_path / f"in{n}.zarr", data, segments, **store_kwargs)
    out = tmp_path / "out.dcm"
    kwargs.setdefault("overwrite", True)
    reported = zarr_to_dicom_seg(src, out, **kwargs)
    return pydicom.dcmread(str(out)), reported


def _codes(diagnostics):
    return [(d.code, d.about) for d in diagnostics]


def _frames(ds):
    """(segment number, frame) pairs of a BINARY object, unpacked."""
    n = int(ds.NumberOfFrames)
    bits = np.unpackbits(np.frombuffer(ds.PixelData, dtype=np.uint8), bitorder="little")
    frames = bits[: n * ds.Rows * ds.Columns].reshape(n, ds.Rows, ds.Columns)
    numbers = [int(fg.SegmentIdentificationSequence[0].ReferencedSegmentNumber)
               for fg in ds.PerFrameFunctionalGroupsSequence]
    return list(zip(numbers, frames))


LIVER_TUMOR = [
    {"id": "liver", "name": "Liver", "label_values": [10, 30], "color": "#dd8265", "dicom": DICOM},
    {"id": "tumor", "name": "Tumor", "label_values": [20, 30], "dicom": {**DICOM, "type": MASS}},
]


class TestBinary:
    def test_default_type_numbers_from_one_and_allows_overlap(self, tmp_path):
        data = np.zeros((1, 2, 4), dtype=np.uint8)
        data[0, 0] = [0, 10, 20, 30]
        ds, reported = _export(tmp_path, data, LIVER_TUMOR)
        assert ds.SegmentationType == "BINARY" and ds.SOPClassUID == SEG_SOP_CLASS_UID
        assert ds.BitsAllocated == 1
        assert [int(i.SegmentNumber) for i in ds.SegmentSequence] == [1, 2]
        assert [str(i.SegmentLabel) for i in ds.SegmentSequence] == ["Liver", "Tumor"]
        assert ds.SegmentsOverlap == "YES"
        frames = dict(_frames(ds))
        assert frames[1][0].tolist() == [0, 1, 0, 1] and frames[2][0].tolist() == [0, 0, 1, 1]
        assert reported == []

    def test_segments_overlap_values(self, tmp_path):
        data = np.zeros((1, 2, 4), dtype=np.uint8)
        data[0, 0] = [0, 10, 20, 0]            # the shared value 30 does not occur
        ds, _ = _export(tmp_path, data, LIVER_TUMOR)
        assert ds.SegmentsOverlap == "UNDEFINED"
        disjoint = [{**LIVER_TUMOR[0], "label_values": [10]}, {**LIVER_TUMOR[1], "label_values": [20]}]
        ds, _ = _export(tmp_path, data, disjoint)
        assert ds.SegmentsOverlap == "NO"

    def test_layers_are_written_not_refused(self, tmp_path):
        data = np.zeros((2, 1, 2, 2), dtype=np.uint8)
        data[0, 0, 0, 0] = data[1, 0, 0, 0] = 1
        segs = [{"id": "a", "label_values": [1], "dicom": DICOM},
                {"id": "b", "label_values": [1], "layer": 1, "dicom": DICOM}]
        ds, _ = _export(tmp_path, data, segs, layered=True)
        assert [n for n, _ in _frames(ds)] == [1, 2] and ds.SegmentsOverlap == "UNDEFINED"

    def test_roles(self, tmp_path):
        data = np.array([[[0, 1, 9, 9]]], dtype=np.uint8)
        segs = [{"id": "bg", "name": "Air", "label_values": [0], "role": "background"},
                {"id": "a", "label_values": [1], "dicom": DICOM},
                {"id": "unk", "label_values": [9], "role": "unknown", "dicom": DICOM}]
        ds, reported = _export(tmp_path, data, segs)
        assert [str(i.SegmentLabel) for i in ds.SegmentSequence] == ["a", "unk"]
        assert _codes(reported) == [("background-not-written", About.segment("bg")),
                                    ("role-lost", About.segment("unk"))]

    def test_an_older_store_exports(self, tmp_path):
        data = np.array([[[0, 5]]], dtype=np.uint8)
        segs = [{"id": "a", "name": "A", "label_value": 5, "color": [1.0, 0.0, 0.0],
                 "dicom": {"category": BODY, "type": KIDNEY}}]
        ds, _ = _export(tmp_path, data, segs, version="0.7", algorithm_type="MANUAL")
        assert [int(i.SegmentNumber) for i in ds.SegmentSequence] == [1]

    def test_no_seg_extension_synthesizes_segments(self, tmp_path):
        data = np.array([[[0, 3, 7]]], dtype=np.uint8)
        ds, _ = _export(tmp_path, data, None, **DEFAULTS)
        assert [str(i.SegmentLabel) for i in ds.SegmentSequence] == ["Segment 3", "Segment 7"]

    def test_nothing_to_write_raises(self, tmp_path):
        with pytest.raises(ValueError, match="at least one segment"):
            _export(tmp_path, np.zeros((1, 2, 2), dtype=np.uint8), None, **DEFAULTS)

    def test_label_falls_back_and_is_truncated(self, tmp_path):
        data = np.array([[[0, 1, 2]]], dtype=np.uint8)
        segs = [{"id": "a", "label_values": [1], "dicom": DICOM,
                 "designations": [{"scheme": "SCT", "code": "1", "meaning": "x" * 70}]},
                {"id": "only-an-id", "label_values": [2],
                 "dicom": {"category": BODY, "type": {"scheme": "SCT", "code": "2", "meaning": "m"},
                           "algorithm_type": "MANUAL"}}]
        ds, reported = _export(tmp_path, data, segs)
        assert [str(i.SegmentLabel) for i in ds.SegmentSequence] == ["x" * 64, "only-an-id"]
        assert _codes(reported) == [("label-truncated", About.segment("a"))]


class TestRequiredContent:
    def test_nothing_is_invented(self, tmp_path):
        data = np.array([[[0, 1]]], dtype=np.uint8)
        no_algorithm = [{"id": "a", "name": "A", "label_values": [1],
                         "dicom": {"category": BODY, "type": KIDNEY}}]
        with pytest.raises(ValueError, match="algorithm_type"):
            _export(tmp_path, data, no_algorithm)
        with pytest.raises(ValueError, match="SegmentAlgorithmName"):
            _export(tmp_path, data, no_algorithm, algorithm_type="AUTOMATIC", overwrite=True)
        with pytest.raises(ValueError, match="category"):
            _export(tmp_path, data, [{"id": "a", "label_values": [1]}],
                    algorithm_type="MANUAL", overwrite=True)

    def test_a_bad_algorithm_type_fails(self, tmp_path):
        segs = [{"id": "a", "label_values": [1], "dicom": {**DICOM, "algorithm_type": "AUTO"}}]
        with pytest.raises(ValueError, match="not DICOM's"):
            _export(tmp_path, np.array([[[0, 1]]], dtype=np.uint8), segs)

    def test_the_caller_fills_what_the_file_lacks(self, tmp_path):
        segs = [{"id": "a", "name": "A", "label_values": [1]}]
        ds, _ = _export(tmp_path, np.array([[[0, 1]]], dtype=np.uint8), segs,
                        default_dicom={"category": BODY, "type": MASS},
                        algorithm_type="AUTOMATIC", algorithm_name="nnU-Net")
        item = ds.SegmentSequence[0]
        assert item.SegmentAlgorithmType == "AUTOMATIC" and item.SegmentAlgorithmName == "nnU-Net"
        assert item.SegmentedPropertyTypeCodeSequence[0].CodeValue == "4147007"

    def test_code_meaning_falls_back_to_the_name_never_the_id(self, tmp_path):
        bare = {"category": {"scheme": "SCT", "code": "1"}, "type": {"scheme": "SCT", "code": "2"},
                "algorithm_type": "MANUAL"}
        data = np.array([[[0, 1]]], dtype=np.uint8)
        ds, _ = _export(tmp_path, data, [{"id": "a", "name": "Kidney", "label_values": [1],
                                          "dicom": bare}])
        assert ds.SegmentSequence[0].SegmentedPropertyTypeCodeSequence[0].CodeMeaning == "Kidney"
        with pytest.raises(ValueError, match="CodeMeaning"):
            _export(tmp_path, data, [{"id": "SEG_ID_7", "label_values": [1], "dicom": bare}],
                    overwrite=True)

    def test_modifiers_description_and_tracking(self, tmp_path):
        segs = [{"id": "a", "name": "A", "label_values": [1],
                 "dicom": {**DICOM, "type_modifier": {"scheme": "SCT", "code": "24028007",
                                                      "meaning": "Right"}},
                 "metadata": {"dicom": {"SegmentDescription": "d", "TrackingID": "t"}}}]
        ds, _ = _export(tmp_path, np.array([[[0, 1]]], dtype=np.uint8), segs)
        item = ds.SegmentSequence[0]
        modifier = item.SegmentedPropertyTypeCodeSequence[0].SegmentedPropertyTypeModifierCodeSequence[0]
        assert modifier.CodeValue == "24028007" and item.SegmentDescription == "d"
        assert "TrackingID" not in item                      # only as a pair


class TestColor:
    def _color(self, tmp_path, color, **kwargs):
        segs = [{"id": "a", "name": "A", "label_values": [1], "color": color, "dicom": DICOM}]
        ds, reported = _export(tmp_path, np.array([[[0, 1]]], dtype=np.uint8), segs, **kwargs)
        return ds, [int(v) for v in ds.SegmentSequence[0].RecommendedDisplayCIELabValue], reported

    def test_d65_by_default_and_marked(self, tmp_path):
        ds, values, _ = self._color(tmp_path, "color(xyz-d65 0.2459822 0.2813858 0.1198888)")
        assert values == [39330, 30580, 41942]
        assert "cielab-d65" in list(ds.SoftwareVersions) or ds.SoftwareVersions == "cielab-d65"

    def test_the_standard_encoding_is_an_option(self, tmp_path):
        ds, values, _ = self._color(tmp_path, "lab(60.0137 -9.0117 35.1984)", cielab="d50")
        assert values == [39330, 30580, 41942] and "SoftwareVersions" not in ds

    def test_no_color_no_marker(self, tmp_path):
        segs = [{"id": "a", "name": "A", "label_values": [1], "dicom": DICOM}]
        ds, _ = _export(tmp_path, np.array([[[0, 1]]], dtype=np.uint8), segs)
        assert "SoftwareVersions" not in ds
        assert "RecommendedDisplayCIELabValue" not in ds.SegmentSequence[0]

    def test_far_out_of_range_is_clamped_and_reported(self, tmp_path):
        _, values, reported = self._color(tmp_path, "lab(150 300 -300)", cielab="d50")
        assert values == [65535, 65535, 0]
        assert _codes(reported) == [("color-clamped", About.segment("a"))]


ATLAS = [
    {"id": "bg", "name": "Background", "label_values": [0], "role": "background", "dicom": DICOM},
    {"id": "184", "name": "Frontal pole", "label_values": [68, 184, 667], "dicom": DICOM},
    {"id": "68", "name": "Layer 1", "label_values": [68], "dicom": DICOM},
    {"id": "667", "name": "Layer 2/3", "label_values": [667], "dicom": DICOM},
]


class TestLabelmap:
    def test_pixel_data_is_unchanged_and_items_are_per_value(self, tmp_path):
        data = np.array([[[0, 68, 184, 667]]], dtype=np.uint16)
        ds, reported = _export(tmp_path, data, ATLAS, segmentation_type="LABELMAP")
        assert ds.SOPClassUID == LABELMAP_SEG_SOP_CLASS_UID and ds.SegmentsOverlap == "NO"
        assert [int(i.SegmentNumber) for i in ds.SegmentSequence] == [0, 68, 184, 667]
        assert [str(i.SegmentLabel) for i in ds.SegmentSequence] == [
            "Background", "Layer 1", "Frontal pole", "Layer 2/3"]
        assert int(ds.PixelPaddingValue) == 0
        assert ds.pixel_array.reshape(data.shape).tolist() == data.tolist()
        assert _codes(reported) == [("segment-partly-represented", About.segment("184"))]

    def test_unrepresented_segment(self, tmp_path):
        segs = [ATLAS[0], {**ATLAS[1], "label_values": [68]}, ATLAS[2]]
        _, reported = _export(tmp_path, np.array([[[0, 68]]], dtype=np.uint8), segs,
                              segmentation_type="LABELMAP")
        assert _codes(reported) == [("segment-not-represented", About.segment("184"))]

    @pytest.mark.parametrize(
        "segments, data, match",
        [
            (LIVER_TUMOR, [0, 10], "nested"),
            ([{"id": "a", "label_values": [70000], "dicom": DICOM}], [70000, 70000], "65535"),
            ([ATLAS[0], ATLAS[2]], [0, 5], "undescribed"),
            ([ATLAS[2]], [0, 68], "background"),
        ],
    )
    def test_fails_rather_than_guess(self, tmp_path, segments, data, match):
        with pytest.raises(ValueError, match=match):
            _export(tmp_path, np.array([[data]], dtype=np.uint32), segments,
                    segmentation_type="LABELMAP")

    def test_layers_are_not_eligible(self, tmp_path):
        segs = [{"id": "a", "label_values": [1], "dicom": DICOM},
                {"id": "b", "label_values": [1], "layer": 1, "dicom": DICOM}]
        with pytest.raises(ValueError, match="single layer"):
            _export(tmp_path, np.ones((2, 1, 1, 2), dtype=np.uint8), segs, layered=True,
                    segmentation_type="LABELMAP")

    def test_the_caller_may_describe_an_implicit_background(self, tmp_path):
        ds, _ = _export(tmp_path, np.array([[[0, 68]]], dtype=np.uint8), [ATLAS[2]],
                        segmentation_type="LABELMAP", background_dicom=DICOM)
        assert [int(i.SegmentNumber) for i in ds.SegmentSequence] == [0, 68]
        assert int(ds.PixelPaddingValue) == 0

    def test_no_background_no_padding_value(self, tmp_path):
        segs = [{"id": "Unknown", "name": "Unknown", "label_values": [0], "role": "unknown",
                 "dicom": DICOM}, {**ATLAS[2], "label_values": [17]}]
        ds, reported = _export(tmp_path, np.array([[[0, 17]]], dtype=np.uint8), segs,
                               implicit_background=False, segmentation_type="LABELMAP")
        assert "PixelPaddingValue" not in ds
        assert _codes(reported) == [("role-lost", About.segment("Unknown"))]


class TestFractional:
    SEGS = [{"id": "fg", "name": "Tumor", "label_values": [1], "dicom": DICOM},
            {"id": "none", "name": "None", "label_values": [1], "layer": 1,
             "role": "background", "dicom": DICOM}]

    def test_quantized_with_roles_written(self, tmp_path):
        data = np.zeros((2, 1, 1, 3), dtype=np.float32)
        data[0, 0, 0] = [0.0, 0.5, 1.2]
        data[1, 0, 0] = [1.0, 0.5, 0.0]
        ds, reported = _export(tmp_path, data, self.SEGS, layered=True,
                               source_representation="fractional-labelmap",
                               fractional_type="PROBABILITY")
        assert ds.SegmentationType == "FRACTIONAL" and int(ds.MaximumFractionalValue) == 255
        assert ds.SegmentationFractionalType == "PROBABILITY" and ds.BitsAllocated == 8
        frames = ds.pixel_array.reshape(2, 1, 3)
        assert frames.tolist() == [[[0, 128, 255]], [[255, 128, 0]]]
        assert _codes(reported) == [("role-lost", About.segment("none"))]

    def test_the_fractional_type_is_not_guessed(self, tmp_path):
        with pytest.raises(ValueError, match="SegmentationFractionalType"):
            _export(tmp_path, np.zeros((2, 1, 1, 3), dtype=np.float32), self.SEGS, layered=True,
                    source_representation="fractional-labelmap")


class TestRoundTrip:
    def _back(self, tmp_path, ds_path):
        out = tmp_path / "back.zarr"
        dicom_to_zarr(ds_path, out)
        arr = zarr.open_array(str(out), mode="r")
        return arr, arr.attrs["duckn"]["extensions"]["seg"], arr.attrs["duckn"]

    def test_binary(self, tmp_path):
        # BINARY omits empty frames, so a slice with no segment would not come back
        data = np.zeros((2, 2, 4), dtype=np.uint8)
        data[0, 0] = [0, 10, 20, 30]
        data[1, 1] = [10, 0, 0, 20]
        segs = [{**LIVER_TUMOR[0], "color": "color(xyz-d65 0.2459822 0.2813858 0.1198888)"},
                LIVER_TUMOR[1]]
        _export(tmp_path, data, segs)
        arr, seg, duckn = self._back(tmp_path, tmp_path / "out.dcm")
        assert seg["version"] == "0.8" and arr.shape == (2, 2, 2, 4)      # overlap: layers
        assert [(s["id"], s["name"], s["label_values"], s.get("layer")) for s in seg["segments"]] == [
            ("Segment_1", "Liver", [1], None), ("Segment_2", "Tumor", [1], 1)]
        assert seg["segments"][0]["color"] == "color(xyz-d65 0.2459822 0.2813858 0.1198888)"
        assert seg["segments"][0]["dicom"]["algorithm_type"] == "MANUAL"
        assert seg["segments"][1]["designations"][0]["code"] == "4147007"
        assert np.array_equal(arr[0] == 1, np.isin(data, [10, 30]))
        assert np.array_equal(arr[1] == 1, np.isin(data, [20, 30]))

    def test_disjoint_binary_is_one_layer(self, tmp_path):
        data = np.zeros((2, 2, 4), dtype=np.uint8)
        data[0, 0] = [0, 10, 20, 20]
        data[1, 0] = [10, 0, 0, 0]
        segs = [{**LIVER_TUMOR[0], "label_values": [10]}, {**LIVER_TUMOR[1], "label_values": [20]}]
        _export(tmp_path, data, segs)
        arr, seg, _ = self._back(tmp_path, tmp_path / "out.dcm")
        assert arr.shape == (2, 2, 4) and arr[0, 0].tolist() == [0, 1, 2, 2]
        assert [s["label_values"] for s in seg["segments"]] == [[1], [2]]

    def test_labelmap_merges_items_and_keeps_values(self, tmp_path):
        data = np.zeros((2, 1, 4), dtype=np.uint16)
        data[0, 0] = [0, 68, 184, 667]
        _export(tmp_path, data, [ATLAS[0], ATLAS[1]], segmentation_type="LABELMAP")
        arr, seg, _ = self._back(tmp_path, tmp_path / "out.dcm")
        assert seg["implicit_background"] is False
        assert [(s["id"], s["label_values"], s.get("role")) for s in seg["segments"]] == [
            ("Segment_0", [0], "background"), ("Segment_68", [68, 184, 667], None)]
        assert arr[0, 0].tolist() == [0, 68, 184, 667] and arr.fill_value == 0

    def test_fractional(self, tmp_path):
        data = np.zeros((2, 2, 1, 3), dtype=np.float32)
        data[0, 0, 0] = [0.0, 0.5, 1.0]
        data[1, 1, 0] = [1.0, 0.5, 0.0]
        _export(tmp_path, data, TestFractional.SEGS, layered=True,
                source_representation="fractional-labelmap", fractional_type="OCCUPANCY")
        arr, seg, duckn = self._back(tmp_path, tmp_path / "out.dcm")
        assert seg["source_representation"] == "fractional-labelmap"
        assert seg["metadata"] == {"dicom": {"SegmentationFractionalType": "OCCUPANCY"}}
        assert arr.dtype == np.uint8 and arr[0, 0, 0].tolist() == [0, 128, 255]
        transform = duckn["value_transforms"][0]
        assert transform["name"] == "linear"
        assert transform["parameters"] == {"slope": 1 / 255, "intercept": 0.0}
        assert "sample_units" not in duckn
