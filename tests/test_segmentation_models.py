"""Smoke tests for segmentation extension Pydantic models.

Validates that each JSON example from §7 of the segmentation-ext-spec
can be parsed and round-tripped through model_dump(exclude_none=True).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from duckn.models import SEG_EXTENSION_VERSION, Designation, SegmentationExtension

SPEC_PATH = Path(__file__).parent.parent / "docs" / "segmentation-ext-spec.md"


# -- §7.1 Non-Overlapping Labelmap with Multi-Ontology Designations ----------

EXAMPLE_7_1 = {
    "version": "0.6",
    "source_representation": "binary-labelmap",
    "terminologies": {
        "SCT": {
            "name": "SNOMED Clinical Terms",
            "version": "2024-09-01",
            "url": "https://browser.ihtsdotools.org",
            "url_template": "https://browser.ihtsdotools.org/?perspective=full&conceptId1={code}",
        },
        "FMA": {
            "name": "Foundational Model of Anatomy",
            "url": "http://purl.org/sig/ont/fma/",
            "url_template": "http://purl.org/sig/ont/fma/fma{code}",
        },
        "TA2": {
            "name": "Terminologia Anatomica 2nd Edition",
            "url": "https://ta2viewer.openanatomy.org",
        },
    },
    "segments": [
        {
            "id": "Segment_1",
            "name": "Right kidney",
            "label_value": 1,
            "color": [0.89, 0.85, 0.78],
            "extent": [90, 170, 100, 180, 40, 100],
            "designations": [
                {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                    "modifier": {
                        "scheme": "SCT",
                        "code": "24028007",
                        "meaning": "Right",
                    },
                },
                {"scheme": "FMA", "code": "7205", "meaning": "Right kidney"},
                {"scheme": "TA2", "code": "5767", "meaning": "Right kidney"},
            ],
            "dicom": {
                "category": {
                    "scheme": "SCT",
                    "code": "123037004",
                    "meaning": "Body structure",
                },
                "type": {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                },
                "anatomic_region": {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                },
                "anatomic_region_modifier": {
                    "scheme": "SCT",
                    "code": "24028007",
                    "meaning": "Right",
                },
            },
        },
        {
            "id": "Segment_2",
            "name": "Left kidney",
            "label_value": 2,
            "color": [0.90, 0.82, 0.72],
            "extent": [85, 165, 60, 140, 38, 98],
            "designations": [
                {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                    "modifier": {
                        "scheme": "SCT",
                        "code": "7771000",
                        "meaning": "Left",
                    },
                },
                {"scheme": "FMA", "code": "7204", "meaning": "Left kidney"},
                {"scheme": "TA2", "code": "5766", "meaning": "Left kidney"},
            ],
            "dicom": {
                "category": {
                    "scheme": "SCT",
                    "code": "123037004",
                    "meaning": "Body structure",
                },
                "type": {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                },
                "anatomic_region": {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                },
                "anatomic_region_modifier": {
                    "scheme": "SCT",
                    "code": "7771000",
                    "meaning": "Left",
                },
            },
        },
    ],
    "metadata": {
        "slicer": {
            "contained_representations": ["binary-labelmap", "closed-surface"],
        }
    },
}

# -- §7.2 Overlapping Segments with Layers -----------------------------------

EXAMPLE_7_2 = {
    "version": "0.6",
    "source_representation": "binary-labelmap",
    "segments": [
        {
            "id": "Segment_1",
            "name": "Tumor",
            "label_value": 1,
            "layer": 0,
            "color": [0.8, 0.2, 0.2],
            "designations": [
                {"scheme": "SCT", "code": "108369006", "meaning": "Neoplasm"}
            ],
        },
        {
            "id": "Segment_2",
            "name": "Liver",
            "label_value": 1,
            "layer": 1,
            "color": [0.2, 0.6, 0.8],
            "designations": [
                {"scheme": "SCT", "code": "10200004", "meaning": "Liver"}
            ],
        },
    ],
}

# -- §7.3 Overlapping Segments with Label Unions ------------------------------

EXAMPLE_7_3 = {
    "version": "0.6",
    "source_representation": "binary-labelmap",
    "segments": [
        {
            "id": "Segment_1",
            "name": "Tumor",
            "label_value": [2, 3],
            "color": [0.8, 0.2, 0.2],
            "designations": [
                {"scheme": "SCT", "code": "108369006", "meaning": "Neoplasm"}
            ],
        },
        {
            "id": "Segment_2",
            "name": "Liver",
            "label_value": [1, 3],
            "color": [0.2, 0.6, 0.8],
            "designations": [
                {"scheme": "SCT", "code": "10200004", "meaning": "Liver"}
            ],
        },
    ],
}

# -- §7.4 Research Segmentation Without DICOM ---------------------------------

EXAMPLE_7_4 = {
    "version": "0.6",
    "source_representation": "binary-labelmap",
    "terminologies": {
        "FMA": {
            "name": "Foundational Model of Anatomy",
            "url": "http://purl.org/sig/ont/fma/",
            "url_template": "http://purl.org/sig/ont/fma/fma{code}",
        }
    },
    "segments": [
        {
            "id": "S1",
            "name": "Left ventricle",
            "label_value": 1,
            "designations": [
                {"scheme": "FMA", "code": "7101", "meaning": "Left ventricle"}
            ],
        },
        {
            "id": "S2",
            "name": "Right ventricle",
            "label_value": 2,
            "designations": [
                {"scheme": "FMA", "code": "7098", "meaning": "Right ventricle"}
            ],
        },
    ],
}

# -- §7.5 Hierarchical Ontology --------------------------------------------
# Trimmed from the spec's excerpt so that every referenced id resolves within
# this fixture; the spec's own listing is checked verbatim by
# test_spec_document_examples_validate below.

EXAMPLE_7_5 = {
    "version": "0.6",
    "source_representation": "binary-labelmap",
    "terminologies": {
        "CCF": {
            "name": "Allen Mouse Brain Common Coordinate Framework",
            "version": "3.0",
            "url": "http://atlas.brain-map.org",
        }
    },
    "segments": [
        {
            "id": "184",
            "name": "Frontal pole, cerebral cortex",
            "label_value": ["68", "667"],
            "color": [0.149, 0.561, 0.271],
            "designations": [
                {
                    "scheme": "CCF",
                    "code": "184",
                    "meaning": "Frontal pole, cerebral cortex",
                }
            ],
        },
        {
            "id": "68",
            "name": "Frontal pole, layer 1",
            "label_value": 68,
            "color": [0.149, 0.561, 0.271],
            "designations": [
                {"scheme": "CCF", "code": "68", "meaning": "Frontal pole, layer 1"}
            ],
        },
        {
            "id": "667",
            "name": "Frontal pole, layer 2/3",
            "label_value": 667,
            "color": [0.149, 0.561, 0.271],
            "designations": [
                {"scheme": "CCF", "code": "667", "meaning": "Frontal pole, layer 2/3"}
            ],
        },
    ],
}

# -- §7.6 Minimal --------------------------------------------------------------

EXAMPLE_7_6 = {
    "version": "0.6",
    "segments": [
        {"id": "S1", "label_value": 1, "name": "Liver"},
        {"id": "S2", "label_value": 2, "name": "Spleen"},
    ],
}


# -- Tests --------------------------------------------------------------------

EXAMPLES = [
    ("7.1", EXAMPLE_7_1),
    ("7.2", EXAMPLE_7_2),
    ("7.3", EXAMPLE_7_3),
    ("7.4", EXAMPLE_7_4),
    ("7.5", EXAMPLE_7_5),
    ("7.6", EXAMPLE_7_6),
]


@pytest.mark.parametrize("name,data", EXAMPLES, ids=[e[0] for e in EXAMPLES])
def test_spec_example_round_trip(name: str, data: dict) -> None:
    """Parse a spec example and verify model_dump round-trips cleanly."""
    model = SegmentationExtension(**data)
    dumped = model.model_dump(exclude_none=True)
    assert dumped == data


def test_spec_version_constant_matches_examples() -> None:
    """The version written by converters should match the spec examples."""
    assert SEG_EXTENSION_VERSION == "0.6"


def _spec_seg_extensions() -> list[tuple[int, dict]]:
    """Every seg extension object appearing in a ```json block of the spec."""
    blocks = re.findall(r"```json\n(.*?)```", SPEC_PATH.read_text(), re.S)
    found: list[tuple[int, dict]] = []
    for i, block in enumerate(blocks, 1):
        text = block.strip()
        for candidate in (text, "{" + text + "}"):  # whole docs and fragments
            try:
                obj = json.loads(candidate)
                break
            except json.JSONDecodeError:
                obj = None
        assert obj is not None, f"spec json block {i} does not parse: {text[:80]!r}"
        if not isinstance(obj, dict):
            continue
        extensions = obj.get("extensions")
        if not isinstance(extensions, dict):
            duckn = obj.get("attributes", {})
            duckn = duckn.get("duckn", {}) if isinstance(duckn, dict) else {}
            extensions = duckn.get("extensions") if isinstance(duckn, dict) else None
        if isinstance(extensions, dict) and isinstance(extensions.get("seg"), dict):
            found.append((i, extensions["seg"]))
    return found


def test_spec_document_examples_validate() -> None:
    """The spec's own §7 listings must parse and round-trip exactly.

    Guards against the fixtures above drifting from the document they claim
    to mirror — and against the document drifting from the models.
    """
    examples = _spec_seg_extensions()
    assert len(examples) >= 6, f"expected the §7 examples, found {len(examples)}"
    for block_number, data in examples:
        model = SegmentationExtension(**data)
        assert model.model_dump(exclude_none=True) == data, (
            f"spec json block {block_number} does not round-trip"
        )
        assert model.version == SEG_EXTENSION_VERSION


def test_extra_field_rejected() -> None:
    """Extra fields should be rejected (extra='forbid')."""
    with pytest.raises(Exception):
        SegmentationExtension(
            version="0.6",
            segments=[{"id": "S1", "label_value": 1}],
            bogus="nope",
        )


def test_missing_required_field() -> None:
    """Missing required fields should raise."""
    with pytest.raises(Exception):
        SegmentationExtension(version="0.6")  # missing segments


def test_designation_meaning_optional() -> None:
    """Designations need only scheme + code; empty meanings normalize to None."""
    d = Designation(scheme="SCT", code="64033007")
    assert d.meaning is None
    d2 = Designation(scheme="SCT", code="64033007", meaning="")
    assert d2.meaning is None


def test_designation_modifier_depth_one() -> None:
    """Modifiers must not carry their own modifiers."""
    inner = {"scheme": "SCT", "code": "24028007", "meaning": "Right"}
    with pytest.raises(Exception):
        Designation(
            scheme="SCT",
            code="64033007",
            modifier={**inner, "modifier": inner},
        )


def test_label_value_string_references() -> None:
    """String entries in label_value reference other segments by id."""
    ext = SegmentationExtension(
        version="0.6",
        segments=[
            {"id": "parent", "label_value": [1, "child"]},
            {"id": "child", "label_value": 2},
        ],
    )
    assert ext.segments[0].label_value == [1, "child"]


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


def test_pre_0_6_extension_fields_migrate_to_slicer_metadata() -> None:
    ext = SegmentationExtension(**EXAMPLE_0_5)
    slicer = ext.metadata["slicer"]
    assert slicer["contained_representations"] == ["binary-labelmap", "closed-surface"]
    assert slicer["conversion_parameters"]["Smoothing factor"]["value"] == "0.5"
    assert slicer["reference_extent_offset"] == [100, 50, 0]


def test_pre_0_6_segment_fields_migrate_to_slicer_metadata() -> None:
    seg = SegmentationExtension(**EXAMPLE_0_5).segments[0]
    slicer = seg.metadata["slicer"]
    assert slicer["name_auto_generated"] is True
    assert slicer["color_auto_generated"] is False
    assert slicer["tags"] == {"Status": "reviewed"}


def test_pre_0_6_identifiers_migrate_to_designations() -> None:
    seg = SegmentationExtension(**EXAMPLE_0_5).segments[0]
    assert [(d.scheme, d.code, d.meaning) for d in seg.designations] == [
        ("SCT", "64033007", "Kidney"),
        ("FMA", "7205", "Right kidney"),
    ]


def test_pre_0_6_metadata_dicom_becomes_first_class() -> None:
    """The dangerous case: it used to load fine and silently export nothing."""
    seg = SegmentationExtension(**EXAMPLE_0_5).segments[0]
    assert seg.dicom is not None
    assert seg.dicom.category.scheme == "SCT"  # scheme was implicit in 0.5
    assert seg.dicom.category.code == "123037004"
    assert seg.dicom.category.meaning == "Body structure"
    assert seg.dicom.anatomic_region_modifier.code == "24028007"
    assert "dicom" not in (seg.metadata or {})


def test_pre_0_6_dropped_coded_entry_fields_are_ignored() -> None:
    ext = SegmentationExtension(
        version="0.5",
        segments=[
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
    )
    des = ext.segments[0].designations[0]
    assert (des.scheme, des.code, des.meaning) == ("TA2", "5767", "Right kidney")


def test_0_6_data_passes_through_migration_unchanged() -> None:
    for _, data in EXAMPLES:
        assert SegmentationExtension(**data).model_dump(exclude_none=True) == data
