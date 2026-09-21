"""Tests for the seg extension's models: field constraints, and reading the
shapes that versions before 0.6 wrote (seg spec §6.3, step 1).

The rules are tested in test_seg_model.py, migration in test_seg_read.py.
"""

from __future__ import annotations

import pytest

from duckn.models import Designation
from duckn.seg_model import SEG_VERSION, SegmentationExtension
from duckn.seg_read import read_seg_extension


def test_extra_field_rejected() -> None:
    """Extra fields should be rejected (extra='forbid')."""
    with pytest.raises(Exception):
        SegmentationExtension(
            version=SEG_VERSION,
            segments=[{"id": "S1", "label_values": [1]}],
            bogus="nope",
        )


def test_missing_required_field() -> None:
    """Missing required fields should raise."""
    with pytest.raises(Exception):
        SegmentationExtension(version=SEG_VERSION)  # missing segments
    with pytest.raises(Exception):
        SegmentationExtension(version=SEG_VERSION, segments=[{"id": "S1"}])  # no label_values


def test_designation_meaning_optional() -> None:
    """Designations need only scheme + code; empty meanings normalize to None."""
    d = Designation(scheme="SCT", code="64033007")
    assert d.meaning is None
    d2 = Designation(scheme="SCT", code="64033007", meaning="")
    assert d2.meaning is None


def test_dicom_entry_rejects_a_nested_modifier() -> None:
    """A Designation in a dicom slot would lose its modifier silently."""
    kidney = {"scheme": "SCT", "code": "64033007",
              "modifier": {"scheme": "SCT", "code": "24028007"}}
    for value in (kidney, Designation(**kidney)):
        with pytest.raises(Exception, match="modifier"):
            SegmentationExtension(
                version=SEG_VERSION,
                segments=[{"id": "S1", "label_values": [1], "dicom": {"type": value}}],
            )


def test_designation_modifier_depth_one() -> None:
    """Modifiers must not carry their own modifiers."""
    inner = {"scheme": "SCT", "code": "24028007", "meaning": "Right"}
    with pytest.raises(Exception):
        Designation(
            scheme="SCT",
            code="64033007",
            modifier={**inner, "modifier": inner},
        )


def test_extent_is_six_bounds() -> None:
    with pytest.raises(Exception):
        SegmentationExtension(
            version=SEG_VERSION, segments=[{"id": "a", "label_values": [1], "extent": [0, 1, 2]}]
        )


# -- Backward compatibility with seg extension 0.5 and earlier ----------------

EXAMPLE_0_5 = {
    "version": "0.5",
    "source_representation": "binary-labelmap",
    "contained_representations": ["binary-labelmap", "closed-surface"],
    "conversion_parameters": {"Smoothing factor": {"value": "0.5", "description": "d"}},
    "reference_extent_offset": [100, 50, 0],
    "segments": [
        {
            "id": "Segment_1",
            "name": "Right kidney",
            "label_value": 1,
            "name_auto_generated": True,
            "color_auto_generated": False,
            "tags": {"Status": "reviewed"},
            "identifiers": {
                "SCT": {"id": "64033007", "name": "Kidney"},
                "FMA": {"id": "7205", "name": "Right kidney"},
            },
            "metadata": {
                "dicom": {
                    "category": {"id": "123037004", "name": "Body structure"},
                    "type": {"id": "64033007", "name": "Kidney"},
                    "anatomic_region_modifier": {"id": "24028007", "name": "Right"},
                }
            },
        }
    ],
}



def _read_0_5():
    ext, _ = read_seg_extension(EXAMPLE_0_5)
    return ext


def test_pre_0_6_extension_fields_migrate_to_slicer_metadata() -> None:
    ext = _read_0_5()
    assert ext.version == SEG_VERSION
    slicer = ext.metadata["slicer"]
    assert slicer["contained_representations"] == ["binary-labelmap", "closed-surface"]
    assert slicer["conversion_parameters"]["Smoothing factor"]["value"] == "0.5"
    assert slicer["reference_extent_offset"] == [100, 50, 0]


def test_pre_0_6_segment_fields_migrate_to_slicer_metadata() -> None:
    seg = _read_0_5().segments[0]
    assert seg.label_values == [1]
    slicer = seg.metadata["slicer"]
    assert slicer["name_auto_generated"] is True
    assert slicer["color_auto_generated"] is False
    assert slicer["tags"] == {"Status": "reviewed"}


def test_pre_0_6_identifiers_migrate_to_designations() -> None:
    seg = _read_0_5().segments[0]
    assert [(d.scheme, d.code, d.meaning) for d in seg.designations] == [
        ("SCT", "64033007", "Kidney"),
        ("FMA", "7205", "Right kidney"),
    ]


def test_pre_0_6_metadata_dicom_becomes_first_class() -> None:
    """The dangerous case: it used to load fine and silently export nothing."""
    seg = _read_0_5().segments[0]
    assert seg.dicom is not None
    assert seg.dicom.category.scheme == "SCT"  # scheme was implicit in 0.5
    assert seg.dicom.category.code == "123037004"
    assert seg.dicom.category.meaning == "Body structure"
    assert seg.dicom.anatomic_region_modifier.code == "24028007"
    assert "dicom" not in (seg.metadata or {})


def test_pre_0_6_dropped_coded_entry_fields_are_ignored() -> None:
    ext, _ = read_seg_extension(
        {
            "version": "0.5",
            "segments": [
                {
                    "id": "S1",
                    "label_value": 1,
                    "designations": [
                        {
                            "scheme": "TA2",
                            "code": "5767",
                            "meaning": "Right kidney",
                            "url": "http://example.invalid/5767",
                            "display": {"la": "Ren dexter"},
                        }
                    ],
                }
            ],
        }
    )
    des = ext.segments[0].designations[0]
    assert (des.scheme, des.code, des.meaning) == ("TA2", "5767", "Right kidney")
