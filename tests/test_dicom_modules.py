"""dicom-spec §5's named groups in code, and selecting and withholding tags by them (2026-09-27).

The spec's tables are the one statement of which keywords a group names; ``MODULES`` is held
equal to them here, read from the document, so neither can move alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pydicom")

from duckn.dicom_tags import MODULES, keywords_named, select, withhold

SPEC = Path(__file__).resolve().parents[1] / "docs" / "dicom-spec.md"


def _spec_groups() -> dict[str, tuple[str, ...]]:
    text = SPEC.read_text(encoding="utf-8")
    section = text[text.index("## 5. Recommended Tags by Module"):text.index("## 6. ")]
    groups: dict[str, tuple[str, ...]] = {}
    for block in re.split(r"^### ", section, flags=re.M)[1:]:
        heading, _, body = block.partition("\n")
        name = re.search(r"\(`([a-z-]+)`\)\s*$", heading)
        assert name, f"§5 heading without a group name: {heading!r}"
        # the group's FIRST table only: §5.10 goes on to a second one (the attributes in
        # stored-value units), which lists keywords that are not the group's
        table = re.search(r"(?:^\|.*\n)+", body, flags=re.M).group(0)
        rows = re.findall(r"^\| `([A-Za-z0-9]+)` \|", table, flags=re.M)
        groups[name.group(1)] = tuple(rows)
    return groups


def test_modules_are_the_spec_tables():
    assert MODULES == _spec_groups()


def test_every_module_keyword_is_a_ps36_keyword():
    import pydicom.datadict as dd
    for name, keywords in MODULES.items():
        for kw in keywords:
            assert dd.tag_for_keyword(kw) is not None, (name, kw)


TAGS = {
    "PatientName": "Doe^Jane", "PatientID": "123", "Modality": "CT", "KVP": 120,
    "ConvolutionKernel": "B30f", "00091001": "private",
    "ReferencedPatientSequence": [{"PatientName": "Other^One", "Modality": "CT"}],
}


def test_select_by_module_and_keyword():
    assert select(TAGS, ["ct"]) == {"KVP": 120, "ConvolutionKernel": "B30f"}
    assert select(TAGS, ["CT", "Modality"]) == {"KVP": 120, "ConvolutionKernel": "B30f",
                                                "Modality": "CT"}
    assert select(TAGS, ["00091001"]) == {"00091001": "private"}
    assert select(TAGS, ["0009100a"]) == {}            # uppercased, and not held: absent


def test_a_selected_key_not_held_stays_absent():
    assert "PatientAge" not in select(TAGS, ["patient"])


def test_unknown_names_are_refused_not_ignored():
    with pytest.raises(ValueError, match="neither a module"):
        keywords_named(["ct", "Kvp"])                 # PS3.6 spells it KVP
    with pytest.raises(ValueError, match="patient"):
        select(TAGS, ["patients"])


def test_withhold_nulls_present_values_at_every_depth():
    out, removed = withhold(TAGS, ["patient"])
    assert removed is True
    assert out["PatientName"] is None and out["PatientID"] is None
    assert "PatientBirthDate" not in out               # never held: not claimed as removed
    assert out["ReferencedPatientSequence"] == [{"PatientName": None, "Modality": "CT"}]
    assert out["KVP"] == 120
    assert TAGS["PatientName"] == "Doe^Jane"           # the input is not changed


def test_withholding_what_is_not_held_removes_nothing():
    out, removed = withhold({"Modality": "MR"}, ["patient", "ct"])
    assert out == {"Modality": "MR"} and removed is False


def test_an_already_null_value_is_not_a_new_removal():
    out, removed = withhold({"PatientName": None}, ["PatientName"])
    assert out == {"PatientName": None} and removed is False
