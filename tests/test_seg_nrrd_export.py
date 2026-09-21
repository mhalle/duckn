"""Tests for the 0.8 .seg.nrrd mapping: roles, ids, colors, dense indices, and
materialization (seg spec §6.1)."""

from __future__ import annotations

import nrrd
import numpy as np
import pytest
import zarr

from duckn.convert import nrrd_to_zarr, zarr_to_nrrd, zarr_to_nrrd_zerocopy
from duckn.diagnostics import About
from duckn.models import AxisMetadata, DucknMetadata, duckn_attrs
from duckn.seg_model import SegmentationExtension
from duckn.seg_nrrd import (
    export_seg_nrrd,
    needs_materialization,
    parse_seg_keyvalues,
    serialize_seg_extension,
)


def _ext(segments, **kwargs):
    return SegmentationExtension(version="0.8", segments=segments, **kwargs)


def _codes(diagnostics):
    return [(d.code, d.about) for d in diagnostics]


def _seg(id):
    return About.segment(id)


class TestImport:
    def test_role_tag(self):
        kv = {"Segment0_ID": "a", "Segment0_LabelValue": "1",
              "Segment0_Tags": "duckn.role:unknown|Segmentation.Status:done|",
              "Segment1_ID": "b", "Segment1_LabelValue": "2",
              "Segment1_Tags": "duckn.role:foreground|"}
        found = []
        ext, _ = parse_seg_keyvalues(kv, diagnostics=found)
        a, b = ext.segments
        assert a.role == "unknown" and a.metadata == {"slicer": {"tags": {"Status": "done"}}}
        assert b.role is None and b.metadata["slicer"]["tags"] == {"duckn.role": "foreground"}
        assert _codes(found) == [("role-tag-invalid", _seg("b"))]

    def test_ids_become_tokens_and_come_back(self):
        kv = {"Segment0_ID": "2.25.123", "Segment0_LabelValue": "1",
              "Segment1_ID": "Left kidney", "Segment1_LabelValue": "2",
              "Segment1_Tags": "TerminologyEntry:c~SCT^1^Cat~SCT^64033007^Kidney~^^~c~^^~^^|"}
        found = []
        ext, _ = parse_seg_keyvalues(kv, diagnostics=found)
        assert [s.id for s in ext.segments] == ["2_25_123", "Left_kidney"]
        assert ext.segments[0].metadata == {"duckn": {"id": "2.25.123"}}
        assert _codes(found) == [("designation-unverified", _seg("Left_kidney")),
                                 ("id-changed", _seg("2_25_123")),
                                 ("id-changed", _seg("Left_kidney"))]
        assert ext.terminologies["SCT"].uri == "http://snomed.info/sct"
        fresh = ext.model_copy(update={"legacy": None})
        out = serialize_seg_extension(fresh)
        assert (out["Segment0_ID"], out["Segment1_ID"]) == ("2.25.123", "Left kidney")

    def test_ids_restore_all_or_nothing(self):
        ext = _ext([{"id": "a_b", "label_values": [1], "metadata": {"duckn": {"id": "a b"}}},
                    {"id": "a_b_2", "label_values": [2], "metadata": {"duckn": {"id": "a b"}}}])
        out = serialize_seg_extension(ext)
        assert (out["Segment0_ID"], out["Segment1_ID"]) == ("a_b", "a_b_2")

    def test_colliding_designations_are_set_aside(self):
        tags = "TerminologyEntry:c~SCT^1^Cat~SCT^64033007^Kidney~^^~c~^^~^^|"
        kv = {"Segment0_ID": "a", "Segment0_LabelValue": "1", "Segment0_Tags": tags,
              "Segment1_ID": "b", "Segment1_LabelValue": "2", "Segment1_Tags": tags}
        found = []
        ext, _ = parse_seg_keyvalues(kv, diagnostics=found)
        assert ext.segments[0].designations and ext.segments[1].designations is None
        assert ("designation-set-aside", _seg("b")) in _codes(found)

    def test_colors(self):
        kv = {"Segment0_ID": "a", "Segment0_LabelValue": "1",
              "Segment0_Color": "0.501961 0.682353 0.501961",
              "Segment1_ID": "b", "Segment1_LabelValue": "2",
              "Segment1_Color": "0.5 0.333333 0.0784314"}
        ext, _ = parse_seg_keyvalues(kv)
        assert [s.color for s in ext.segments] == ["#80ae80", "color(srgb 0.5 0.333333 0.0784314)"]
        out = serialize_seg_extension(ext.model_copy(update={"legacy": None}))
        assert out["Segment0_Color"] == kv["Segment0_Color"]
        assert out["Segment1_Color"] == kv["Segment1_Color"]


class TestAsItIs:
    def test_roles_indices_and_background(self):
        ext = _ext([{"id": "bg", "name": "Air", "label_values": [0], "role": "background"},
                    {"id": "a", "label_values": [1]},
                    {"id": "unk", "label_values": [9], "role": "unknown",
                     "metadata": {"slicer": {"tags": {"Status": "x"}}}}])
        assert not needs_materialization(ext)
        result = export_seg_nrrd(ext)
        kv = result.keyvalues
        assert not result.materialized
        assert [kv[f"Segment{i}_ID"] for i in range(2)] == ["a", "unk"]   # no gap, no bg
        assert "Segment2_ID" not in kv
        assert kv["Segment1_Tags"] == "Segmentation.Status:x|duckn.role:unknown|"
        assert _codes(result.diagnostics) == [("background-not-written", _seg("bg"))]
        back, _ = parse_seg_keyvalues(kv)
        assert back.segments[1].role == "unknown"

    def test_a_lab_color_is_converted_and_a_wide_one_mapped(self):
        ext = _ext([{"id": "a", "label_values": [1], "color": "lab(64.0631 33.8785 31.5159)"},
                    {"id": "b", "label_values": [2], "color": "lab(50 100 -100)"},
                    {"id": "c", "label_values": [3], "color": "color(srgb 1.2 0 0)"}])
        result = export_seg_nrrd(ext)
        r, g, b = (float(x) for x in result.keyvalues["Segment0_Color"].split())
        assert (round(r * 255), round(g * 255), round(b * 255)) == (0xDD, 0x82, 0x65)
        assert result.keyvalues["Segment2_Color"] == "1 0 0"
        assert _codes(result.diagnostics) == [("color-gamut-mapped", _seg("b")),
                                              ("color-clamped", _seg("c"))]


LIVER_TUMOR = [
    {"id": "liver", "name": "Liver", "label_values": [1, 3], "color": "#dd8265"},
    {"id": "tumor", "name": "Tumor", "label_values": [2, 3], "extent": [0, 0, 0, 0, 0, 0]},
    {"id": "artifact", "label_values": [9], "role": "unknown"},
]


class TestMaterialize:
    def test_needs_data(self):
        with pytest.raises(ValueError, match="voxel"):
            export_seg_nrrd(_ext(LIVER_TUMOR))

    def test_shared_values(self):
        data = np.array([[[0, 1, 2, 3, 9, 9]]], dtype=np.uint8)
        result = export_seg_nrrd(_ext(LIVER_TUMOR), data)
        kv = result.keyvalues
        assert result.list_axis == 3 and result.data.shape == (1, 1, 6, 2)
        # liver keeps nothing (two values) -> 1; tumor overlaps it -> layer 1;
        # the artifact keeps 9 in layer 0
        assert [(kv[f"Segment{i}_LabelValue"], kv.get(f"Segment{i}_Layer")) for i in range(3)] == [
            ("1", None), ("1", "1"), ("9", None)]
        assert result.data[0, 0, :, 0].tolist() == [0, 1, 0, 1, 9, 9]
        assert result.data[0, 0, :, 1].tolist() == [0, 0, 1, 1, 0, 0]
        assert kv["Segment0_Extent"] == "1 3 0 0 0 0" and kv["Segment1_Extent"] == "2 3 0 0 0 0"
        assert _codes(result.diagnostics) == [("values-renumbered", About.extension())]

    def test_nesting_is_one_layer_per_level(self):
        ext = _ext([{"id": "184", "label_values": [68, 184, 667]},
                    {"id": "68", "label_values": [68]}, {"id": "667", "label_values": [667]}])
        data = np.array([[[68, 184, 667, 0]]], dtype=np.uint16)
        result = export_seg_nrrd(ext, data)
        assert result.data[0, 0, :, 0].tolist() == [1, 1, 1, 0]
        assert result.data[0, 0, :, 1].tolist() == [68, 0, 667, 0]
        assert ("nesting-exported-as-layers", About.extension()) in _codes(result.diagnostics)

    def test_an_unknown_at_zero_takes_a_positive_value(self):
        ext = _ext([{"id": "Unknown", "label_values": [0], "role": "unknown"},
                    {"id": "hip", "label_values": [17]}], implicit_background=False)
        data = np.array([[[0, 17, 0]]], dtype=np.uint8)
        result = export_seg_nrrd(ext, data)
        assert result.list_axis is None
        assert result.data.tolist() == [[[1, 17, 1]]]
        assert "duckn.role:unknown" in result.keyvalues["Segment0_Tags"]

    def test_source_layers_are_merged_where_voxels_allow(self):
        ext = _ext([{"id": "a", "label_values": [1, 2]},
                    {"id": "b", "label_values": [1], "layer": 1},
                    {"id": "c", "label_values": [2], "layer": 1}])
        data = np.zeros((1, 1, 4, 2), dtype=np.uint8)
        data[0, 0, :, 0] = [1, 2, 0, 0]
        data[0, 0, :, 1] = [0, 1, 2, 2]      # b overlaps a; c does not
        result = export_seg_nrrd(ext, data, list_axis=3)
        kv = result.keyvalues
        assert [(kv[f"Segment{i}_LabelValue"], kv.get(f"Segment{i}_Layer")) for i in range(3)] == [
            ("1", None), ("1", "1"), ("2", None)]
        assert result.data[0, 0, :, 0].tolist() == [1, 1, 2, 2]
        assert result.data[0, 0, :, 1].tolist() == [0, 1, 0, 0]

    def test_every_mutual_overlap_gets_its_own_layer(self):
        ext = _ext([{"id": f"s{i}", "label_values": [1, i + 2]} for i in range(3)])
        result = export_seg_nrrd(ext, np.zeros((1, 1, 1), dtype=np.uint8))
        assert result.data.shape == (1, 1, 1, 3) and result.data.dtype == np.uint8


class TestFiles:
    def _store(self, tmp_path, data, seg, axes):
        path = tmp_path / "seg.zarr"
        meta = DucknMetadata(version="1.0", axes=axes, space="left-posterior-superior",
                             space_origin=[0, 0, 0], extensions={"seg": seg})
        zarr.create_array(str(path), data=data, attributes=duckn_attrs(meta), fill_value=0)
        return path

    def _space_axes(self):
        eye = np.eye(3).tolist()
        return [AxisMetadata(kind="space", space_direction=eye[2 - i]) for i in range(3)]

    def test_zarr_to_nrrd_materializes_and_comes_back(self, tmp_path):
        data = np.zeros((2, 3, 6), dtype=np.uint8)
        data[0, 0] = [0, 1, 2, 3, 9, 9]
        seg = _ext(LIVER_TUMOR).model_dump(exclude_none=True)
        store = self._store(tmp_path, data, seg, self._space_axes())
        out = tmp_path / "out.seg.nrrd"
        reported = zarr_to_nrrd(store, out)
        assert ("values-renumbered", About.extension()) in _codes(reported)

        voxels, header = nrrd.read(str(out), index_order="C")
        assert voxels.shape == (2, 3, 6, 2) and header["kinds"][0] == "list"
        assert voxels[0, 0, :, 1].tolist() == [0, 0, 1, 1, 0, 0]

        back = tmp_path / "back.zarr"
        nrrd_to_zarr(out, back)
        arr = zarr.open_array(str(back), mode="r")
        seg2 = arr.attrs["duckn"]["extensions"]["seg"]
        assert [(s["id"], s["label_values"], s.get("layer"), s.get("role"))
                for s in seg2["segments"]] == [
            ("liver", [1], None, None), ("tumor", [1], 1, None), ("artifact", [9], None, "unknown")]
        liver = np.isin(data, [1, 3])
        assert np.array_equal(arr[..., 0] == 1, liver)

    def test_an_older_store_still_exports(self, tmp_path):
        data = np.zeros((2, 2, 2), dtype=np.uint8)
        seg = {"version": "0.7", "segments": [
            {"id": "a", "label_value": 1, "color": [1.0, 0.0, 0.0]}]}
        store = self._store(tmp_path, data, seg, self._space_axes())
        out = tmp_path / "old.seg.nrrd"
        assert zarr_to_nrrd(store, out) == []
        header = nrrd.read_header(str(out))
        assert header["Segment0_LabelValue"] == "1" and header["Segment0_Color"] == "1 0 0"

    def test_zero_copy_cannot_materialize(self, tmp_path):
        data = np.ones((2, 2, 2), dtype=np.uint8)
        store = self._store(tmp_path, data, _ext(LIVER_TUMOR).model_dump(exclude_none=True),
                            self._space_axes())
        with pytest.raises(ValueError, match="voxel"):
            zarr_to_nrrd_zerocopy(store, tmp_path / "zc.seg.nrrd")


class TestSegConvert:
    def _meta(self, seg, layered):
        axes = [AxisMetadata(kind="space", space_direction=d) for d in np.eye(3).tolist()]
        if layered:
            axes = [AxisMetadata(kind="list"), *axes]
        return DucknMetadata(version="1.0", axes=axes, space="left-posterior-superior",
                             space_origin=[0, 0, 0], extensions={"seg": seg})

    def test_labelmap_to_binary_follows_the_segments(self):
        from duckn.seg_convert import seg_binary_to_labelmap, seg_labelmap_to_binary

        data = np.array([[[0, 1, 2, 3, 9]]], dtype=np.uint8)
        seg = _ext([{"id": "bg", "label_values": [0], "role": "background"}, *LIVER_TUMOR],
                   legacy={"keyvalues": {}}).model_dump(exclude_none=True)
        binary, meta = seg_labelmap_to_binary(data, self._meta(seg, False))
        assert binary[:, 0, 0].tolist() == [[0, 1, 0, 1, 0], [0, 0, 1, 1, 0], [0, 0, 0, 0, 1]]
        out = meta.extensions["seg"]
        assert "legacy" not in out
        assert [(s["id"], s["label_values"], s.get("layer")) for s in out["segments"]] == [
            ("liver", [1], None), ("tumor", [1], 1), ("artifact", [1], 2)]

        labelmap, meta2 = seg_binary_to_labelmap(binary, meta)
        assert labelmap[0, 0].tolist() == [0, 1, 2, 2, 3]        # last writer wins the overlap
        assert [(s["id"], s["label_values"]) for s in meta2.extensions["seg"]["segments"]] == [
            ("liver", [1]), ("tumor", [2]), ("artifact", [3])]
        assert meta2.extensions["seg"]["segments"][2]["role"] == "unknown"

    def test_an_older_extension_is_migrated(self):
        from duckn.seg_convert import seg_binary_to_labelmap

        seg = {"version": "0.7", "segments": [{"id": "a", "label_value": 1},
                                               {"id": "b", "label_value": 1, "layer": 1}]}
        data = np.zeros((2, 1, 1, 2), dtype=np.uint8)
        data[0, 0, 0, 0] = data[1, 0, 0, 1] = 1
        labelmap, meta = seg_binary_to_labelmap(data, self._meta(seg, True))
        assert labelmap[0, 0].tolist() == [1, 2]
        assert meta.extensions["seg"]["version"] == "0.8"
