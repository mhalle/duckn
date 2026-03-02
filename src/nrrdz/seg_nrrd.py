"""Parse .seg.nrrd key/value pairs into SegmentationExtension."""

from __future__ import annotations

import re
from typing import Any

from .models import (
    CodedEntry,
    ConversionParameter,
    Designation,
    DicomClassification,
    Segment,
    SegmentationExtension,
    TerminologyEntry,
)

# Known terminology scheme → human-readable name
_KNOWN_SCHEMES: dict[str, str] = {
    "SCT": "SNOMED Clinical Terms",
    "SRT": "DICOM SR Coding Scheme",
}

_SEG_KEY_RE = re.compile(r"^Segment(\d+)_(.+)$")


def _normalize_representation(raw: str) -> str:
    """Normalize title-case representation name to kebab-case.

    ``"Binary labelmap"`` → ``"binary-labelmap"``
    """
    return raw.strip().lower().replace(" ", "-")


def _parse_bool(val: str) -> bool:
    return val.strip() == "1"


def _parse_int_list(val: str) -> list[int]:
    return [int(x) for x in val.split()]


def _parse_float_list(val: str) -> list[float]:
    return [float(x) for x in val.split()]


def _parse_label_value(val: str) -> int | list[int]:
    parts = val.split()
    if len(parts) == 1:
        return int(parts[0])
    return [int(x) for x in parts]


def _parse_conversion_parameters(raw: str) -> dict[str, ConversionParameter]:
    """Parse ``&``-delimited conversion parameters.

    Each param is ``name|value|description``.  Backslash-escaped newlines
    in descriptions are unescaped.
    """
    params: dict[str, ConversionParameter] = {}
    for chunk in raw.split("&"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split("|", 2)
        name = parts[0]
        value = parts[1] if len(parts) > 1 else ""
        desc: str | None = None
        if len(parts) > 2 and parts[2]:
            desc = parts[2].replace("\\n", "\n")
        params[name] = ConversionParameter(value=value, description=desc)
    return params


def _parse_coded_entry(triplet: str) -> CodedEntry | None:
    """Parse ``scheme^code^meaning``.  Returns None if all empty."""
    parts = triplet.split("^", 2)
    if len(parts) < 3:
        return None
    scheme, code, meaning = parts[0].strip(), parts[1].strip(), parts[2].strip()
    if not scheme and not code and not meaning:
        return None
    return CodedEntry(scheme=scheme, code=code, meaning=meaning)


def _parse_terminology_entry(
    raw: str,
) -> tuple[DicomClassification | None, Designation | None, set[str], str, str]:
    """Parse a TerminologyEntry value (``~``-delimited, 7 slots).

    Returns (dicom, designation, schemes_seen, context1, context2).
    """
    slots = raw.split("~")
    # Pad to 7 slots
    while len(slots) < 7:
        slots.append("^^")

    # slots: 0=context1, 1=category, 2=type, 3=type_modifier,
    #         4=context2, 5=anatomic_region, 6=anatomic_region_modifier
    context1 = slots[0]
    context2 = slots[4]

    category = _parse_coded_entry(slots[1])
    type_entry = _parse_coded_entry(slots[2])
    type_modifier = _parse_coded_entry(slots[3])
    anatomic_region = _parse_coded_entry(slots[5])
    anatomic_region_modifier = _parse_coded_entry(slots[6])

    schemes: set[str] = set()
    for entry in (category, type_entry, type_modifier, anatomic_region, anatomic_region_modifier):
        if entry is not None and entry.scheme:
            schemes.add(entry.scheme)

    dicom: DicomClassification | None = None
    if any(x is not None for x in (category, type_entry, type_modifier, anatomic_region, anatomic_region_modifier)):
        dicom = DicomClassification(
            category=category,
            type=type_entry,
            type_modifier=type_modifier,
            anatomic_region=anatomic_region,
            anatomic_region_modifier=anatomic_region_modifier,
        )

    designation: Designation | None = None
    if type_entry is not None:
        designation = Designation(
            scheme=type_entry.scheme,
            code=type_entry.code,
            meaning=type_entry.meaning,
            modifier=type_modifier,
        )

    return dicom, designation, schemes, context1, context2


def _parse_tags(
    raw: str,
) -> tuple[dict[str, str] | None, DicomClassification | None, list[Designation] | None, set[str], dict[str, str]]:
    """Parse ``SegmentN_Tags`` value.

    Returns (tags, dicom, designations, schemes_seen, legacy).
    """
    tags: dict[str, str] = {}
    dicom: DicomClassification | None = None
    designations: list[Designation] | None = None
    all_schemes: set[str] = set()
    legacy: dict[str, str] = {}

    for pair in raw.split("|"):
        pair = pair.strip()
        if not pair:
            continue
        colon_idx = pair.find(":")
        if colon_idx < 0:
            continue
        key = pair[:colon_idx]
        value = pair[colon_idx + 1 :]

        if key == "TerminologyEntry":
            dicom, designation, schemes, ctx1, ctx2 = _parse_terminology_entry(value)
            all_schemes |= schemes
            if designation is not None:
                designations = [designation]
            legacy["terminology_context1"] = ctx1
            legacy["terminology_context2"] = ctx2
        else:
            # Strip "Segmentation." prefix from tag keys
            tag_key = key.removeprefix("Segmentation.")
            tags[tag_key] = value

    return tags or None, dicom, designations, all_schemes, legacy


def _parse_segment(
    index: int, kv: dict[str, str]
) -> tuple[Segment, set[str], dict[str, str]]:
    """Build a Segment from ``SegmentN_*`` keys.

    Returns (segment, schemes, legacy).
    """
    prefix = f"Segment{index}_"

    seg_id = kv[f"{prefix}ID"]
    name = kv.get(f"{prefix}Name")
    name_auto = kv.get(f"{prefix}NameAutoGenerated")
    color_raw = kv.get(f"{prefix}Color")
    color_auto = kv.get(f"{prefix}ColorAutoGenerated")
    label_raw = kv.get(f"{prefix}LabelValue")
    layer_raw = kv.get(f"{prefix}Layer")
    extent_raw = kv.get(f"{prefix}Extent")
    tags_raw = kv.get(f"{prefix}Tags")

    kwargs: dict[str, Any] = {"id": seg_id}
    seg_legacy: dict[str, str] = {}

    if name is not None:
        kwargs["name"] = name
    if name_auto is not None:
        kwargs["name_auto_generated"] = _parse_bool(name_auto)
    if color_raw is not None:
        kwargs["color"] = _parse_float_list(color_raw)
    if color_auto is not None:
        kwargs["color_auto_generated"] = _parse_bool(color_auto)
    if label_raw is not None:
        kwargs["label_value"] = _parse_label_value(label_raw)
    else:
        kwargs["label_value"] = 0
    if layer_raw is not None:
        kwargs["layer"] = int(layer_raw)
    if extent_raw is not None:
        kwargs["extent"] = _parse_int_list(extent_raw)

    schemes: set[str] = set()
    if tags_raw is not None:
        tags, dicom, designations, tag_schemes, tag_legacy = _parse_tags(tags_raw)
        schemes = tag_schemes
        seg_legacy.update(tag_legacy)
        if tags is not None:
            kwargs["tags"] = tags
        if dicom is not None:
            kwargs["dicom"] = dicom
        if designations is not None:
            kwargs["designations"] = designations

    return Segment(**kwargs), schemes, seg_legacy


def parse_seg_keyvalues(
    keyvalues: dict[str, str],
) -> tuple[SegmentationExtension | None, dict[str, str]]:
    """Parse segmentation key/value pairs into a SegmentationExtension.

    Parameters
    ----------
    keyvalues:
        Non-spec key/value pairs from an NRRD header.

    Returns
    -------
    (extension, remaining)
        The parsed SegmentationExtension (or None if no segmentation keys
        found) and a dict of remaining non-segmentation key/value pairs.
    """
    # Detect segment indices
    seg_indices: list[int] = []
    for key in keyvalues:
        m = _SEG_KEY_RE.match(key)
        if m and m.group(2) == "ID":
            seg_indices.append(int(m.group(1)))
    seg_indices.sort()

    if not seg_indices:
        return None, keyvalues

    # Partition keys into segmentation vs remaining
    remaining: dict[str, str] = {}
    for key in keyvalues:
        if not key.startswith("Segmentation_") and not _SEG_KEY_RE.match(key):
            remaining[key] = keyvalues[key]

    # --- Legacy: track original key name ---
    legacy: dict[str, Any] = {}
    if "Segmentation_SourceRepresentation" in keyvalues:
        legacy["representation_key"] = "source"
    elif "Segmentation_MasterRepresentation" in keyvalues:
        legacy["representation_key"] = "master"

    # --- Global fields ---
    ext_kwargs: dict[str, Any] = {"version": "1.0"}

    master_rep = keyvalues.get("Segmentation_MasterRepresentation")
    source_rep = keyvalues.get("Segmentation_SourceRepresentation")
    rep_raw = source_rep or master_rep
    if rep_raw is not None:
        ext_kwargs["source_representation"] = _normalize_representation(rep_raw)

    contained_raw = keyvalues.get("Segmentation_ContainedRepresentationNames")
    if contained_raw is not None:
        reps = [_normalize_representation(r) for r in contained_raw.split("|") if r.strip()]
        if reps:
            ext_kwargs["contained_representations"] = reps

    conv_raw = keyvalues.get("Segmentation_ConversionParameters")
    if conv_raw is not None:
        params = _parse_conversion_parameters(conv_raw)
        if params:
            ext_kwargs["conversion_parameters"] = params

    ref_offset_raw = keyvalues.get("Segmentation_ReferenceImageExtentOffset")
    if ref_offset_raw is not None:
        ext_kwargs["reference_extent_offset"] = _parse_int_list(ref_offset_raw)

    # --- Per-segment ---
    all_schemes: set[str] = set()
    segments: list[Segment] = []
    seg_legacy_list: list[dict[str, str]] = []
    for idx in seg_indices:
        seg, schemes, seg_leg = _parse_segment(idx, keyvalues)
        segments.append(seg)
        all_schemes |= schemes
        seg_legacy_list.append(seg_leg)

    ext_kwargs["segments"] = segments

    # --- Terminologies registry ---
    if all_schemes:
        terminologies: dict[str, TerminologyEntry] = {}
        for scheme in sorted(all_schemes):
            name = _KNOWN_SCHEMES.get(scheme)
            terminologies[scheme] = TerminologyEntry(name=name)
        ext_kwargs["terminologies"] = terminologies

    # --- Collect legacy ---
    # Only store non-empty per-segment legacy entries
    non_empty = [lg for lg in seg_legacy_list if lg]
    if non_empty:
        legacy["segments"] = seg_legacy_list
    if legacy:
        ext_kwargs["legacy"] = legacy

    return SegmentationExtension(**ext_kwargs), remaining


# ---------------------------------------------------------------------------
# Reverse: SegmentationExtension → flat NRRD key/value pairs
# ---------------------------------------------------------------------------

_REPR_TITLE: dict[str, str] = {
    "binary-labelmap": "Binary labelmap",
    "fractional-labelmap": "Fractional labelmap",
    "closed-surface": "Closed surface",
    "planar-contour": "Planar contour",
}


def _denormalize_representation(kebab: str) -> str:
    """``"binary-labelmap"`` → ``"Binary labelmap"``."""
    return _REPR_TITLE.get(kebab, kebab.replace("-", " ").capitalize())


def _coded_entry_triplet(entry: CodedEntry | None) -> str:
    """CodedEntry → ``"scheme^code^meaning"`` (or ``"^^"`` for None)."""
    if entry is None:
        return "^^"
    return f"{entry.scheme}^{entry.code}^{entry.meaning}"


def _serialize_conversion_parameters(
    params: dict[str, ConversionParameter],
) -> str:
    """Dict of ConversionParameter → ``&``-delimited string."""
    parts: list[str] = []
    for name, param in params.items():
        desc = ""
        if param.description is not None:
            desc = param.description.replace("\n", "\\n")
        parts.append(f"{name}|{param.value}|{desc}")
    return "&".join(parts) + "&"


def _serialize_terminology_entry(
    dicom: DicomClassification | None,
    designation: Designation | None,
    seg_legacy: dict[str, str] | None = None,
) -> str:
    """Reconstruct the ``~``-delimited TerminologyEntry value.

    Uses original context strings from legacy if available, otherwise
    falls back to generic names.
    """
    if dicom is None and designation is None:
        return ""

    category = _coded_entry_triplet(dicom.category if dicom else None)
    anatomic_region = _coded_entry_triplet(dicom.anatomic_region if dicom else None)
    anatomic_region_modifier = _coded_entry_triplet(
        dicom.anatomic_region_modifier if dicom else None
    )

    # type and type_modifier: prefer dicom fields, fall back to designation
    if dicom and dicom.type:
        type_entry = _coded_entry_triplet(dicom.type)
        type_modifier = _coded_entry_triplet(dicom.type_modifier)
    elif designation:
        type_entry = _coded_entry_triplet(
            CodedEntry(scheme=designation.scheme, code=designation.code, meaning=designation.meaning)
        )
        type_modifier = _coded_entry_triplet(designation.modifier)
    else:
        type_entry = "^^"
        type_modifier = "^^"

    # Context strings: use legacy if available
    if seg_legacy:
        ctx1 = seg_legacy.get("terminology_context1", "Segmentation category and type")
        ctx2 = seg_legacy.get("terminology_context2", "Anatomic codes")
    else:
        ctx1 = "Segmentation category and type"
        ctx2 = "Anatomic codes"

    return f"{ctx1}~{category}~{type_entry}~{type_modifier}~{ctx2}~{anatomic_region}~{anatomic_region_modifier}"


def _serialize_tags(
    seg: Segment,
    seg_legacy: dict[str, str] | None = None,
) -> str:
    """Build the ``|``-delimited Tags value for a segment."""
    pairs: list[str] = []

    if seg.tags:
        for key, val in seg.tags.items():
            pairs.append(f"Segmentation.{key}:{val}")

    # Reconstruct TerminologyEntry if we have dicom or designations
    designation = seg.designations[0] if seg.designations else None
    term_val = _serialize_terminology_entry(seg.dicom, designation, seg_legacy)
    if term_val:
        pairs.append(f"TerminologyEntry:{term_val}")

    if not pairs:
        return ""
    return "|".join(pairs) + "|"


def serialize_seg_extension(ext: SegmentationExtension) -> dict[str, str]:
    """Convert a SegmentationExtension back to flat NRRD key/value pairs.

    Uses ``ext.legacy`` (if present) to reproduce original formatting
    for context strings, color values, and representation key names.
    """
    kv: dict[str, str] = {}
    legacy = ext.legacy or {}

    # --- Global fields ---
    if ext.source_representation is not None:
        rep_key_suffix = legacy.get("representation_key", "master")
        if rep_key_suffix == "source":
            key_name = "Segmentation_SourceRepresentation"
        else:
            key_name = "Segmentation_MasterRepresentation"
        kv[key_name] = _denormalize_representation(str(ext.source_representation))

    if ext.contained_representations:
        kv["Segmentation_ContainedRepresentationNames"] = (
            "|".join(_denormalize_representation(r) for r in ext.contained_representations) + "|"
        )

    if ext.conversion_parameters:
        kv["Segmentation_ConversionParameters"] = _serialize_conversion_parameters(
            ext.conversion_parameters
        )

    if ext.reference_extent_offset is not None:
        kv["Segmentation_ReferenceImageExtentOffset"] = " ".join(
            str(x) for x in ext.reference_extent_offset
        )

    # --- Per-segment fields ---
    seg_legacy_list: list[dict[str, str]] = legacy.get("segments", [])

    for i, seg in enumerate(ext.segments):
        p = f"Segment{i}_"
        seg_leg = seg_legacy_list[i] if i < len(seg_legacy_list) else None

        kv[f"{p}ID"] = seg.id
        if seg.name is not None:
            kv[f"{p}Name"] = seg.name
        if seg.name_auto_generated is not None:
            kv[f"{p}NameAutoGenerated"] = "1" if seg.name_auto_generated else "0"
        if seg.color is not None:
            kv[f"{p}Color"] = " ".join(str(c) for c in seg.color)
        if seg.color_auto_generated is not None:
            kv[f"{p}ColorAutoGenerated"] = "1" if seg.color_auto_generated else "0"
        if isinstance(seg.label_value, list):
            kv[f"{p}LabelValue"] = " ".join(str(v) for v in seg.label_value)
        else:
            kv[f"{p}LabelValue"] = str(seg.label_value)
        if seg.layer is not None:
            kv[f"{p}Layer"] = str(seg.layer)
        if seg.extent is not None:
            kv[f"{p}Extent"] = " ".join(str(x) for x in seg.extent)

        tags_str = _serialize_tags(seg, seg_leg)
        if tags_str:
            kv[f"{p}Tags"] = tags_str

    return kv
