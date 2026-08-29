"""Parse .seg.nrrd key/value pairs into SegmentationExtension."""

from __future__ import annotations

import re
import warnings
from typing import Any

from .models import (
    SEG_EXTENSION_VERSION,
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
    """Parse ``scheme^code^meaning``.  Returns None without scheme + code.

    The meaning is optional (§4.1), so a two-part ``scheme^code`` is
    accepted rather than discarding the code along with it.
    """
    parts = triplet.split("^", 2)
    if len(parts) < 2:
        return None
    scheme, code = parts[0].strip(), parts[1].strip()
    meaning = parts[2].strip() if len(parts) > 2 else ""
    if not scheme or not code:
        return None
    return CodedEntry(scheme=scheme, code=code, meaning=meaning)


def _parse_terminology_entry(
    raw: str,
) -> tuple[DicomClassification | None, list[Designation] | None, set[str]]:
    """Parse a TerminologyEntry value (``~``-delimited, 7 slots).

    Returns (dicom, designations, schemes_seen). The designation is built
    from the type entry (the primary concept) plus its type_modifier.
    """
    slots = raw.split("~")
    # Pad to 7 slots
    while len(slots) < 7:
        slots.append("^^")

    # slots: 0=context1, 1=category, 2=type, 3=type_modifier,
    #         4=context2, 5=anatomic_region, 6=anatomic_region_modifier
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

    # Build a designation from the type entry (the primary concept).
    designations: list[Designation] | None = None
    if type_entry is not None and type_entry.scheme and type_entry.code:
        modifier: Designation | None = None
        if (
            type_modifier is not None
            and type_modifier.scheme
            and type_modifier.code
        ):
            modifier = Designation(
                scheme=type_modifier.scheme,
                code=type_modifier.code,
                meaning=type_modifier.meaning,
            )
        designations = [
            Designation(
                scheme=type_entry.scheme,
                code=type_entry.code,
                meaning=type_entry.meaning,
                modifier=modifier,
            )
        ]

    return dicom, designations, schemes


def _parse_tags(
    raw: str,
) -> tuple[dict[str, str] | None, DicomClassification | None, list[Designation] | None, set[str]]:
    """Parse ``SegmentN_Tags`` value.

    Returns (tags, dicom, designations, schemes_seen).
    """
    tags: dict[str, str] = {}
    dicom: DicomClassification | None = None
    designations: list[Designation] | None = None
    all_schemes: set[str] = set()

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
            dicom, designations, schemes = _parse_terminology_entry(value)
            all_schemes |= schemes
        else:
            # Strip "Segmentation." prefix from tag keys
            tag_key = key.removeprefix("Segmentation.")
            tags[tag_key] = value

    return tags or None, dicom, designations, all_schemes


def _parse_segment(
    index: int, kv: dict[str, str]
) -> tuple[Segment, set[str]]:
    """Build a Segment from ``SegmentN_*`` keys.  Returns (segment, schemes)."""
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
    if name is not None:
        kwargs["name"] = name
    if color_raw is not None:
        kwargs["color"] = _parse_float_list(color_raw)
    if label_raw is not None:
        kwargs["label_value"] = _parse_label_value(label_raw)
    else:
        # Slicer omits LabelValue only for segments with no binary labelmap
        # representation. 0 is the background value, so claiming it would
        # claim every unwritten voxel; use the 1-based ordinal instead.
        kwargs["label_value"] = index + 1
        warnings.warn(
            f"segment {seg_id!r} has no {prefix}LabelValue; "
            f"assigning label value {index + 1}",
            stacklevel=3,
        )
    if layer_raw is not None and int(layer_raw) != 0:
        # Layer 0 is the implicit default. Carrying it explicitly would claim
        # a `list` axis that an ordinary 3D .seg.nrrd does not have (§5 rule 2).
        kwargs["layer"] = int(layer_raw)
    if extent_raw is not None:
        kwargs["extent"] = _parse_int_list(extent_raw)

    schemes: set[str] = set()
    slicer_meta: dict[str, Any] = {}

    if tags_raw is not None:
        tags, dicom, designations, tag_schemes = _parse_tags(tags_raw)
        schemes = tag_schemes
        if designations is not None:
            kwargs["designations"] = designations
        if dicom is not None:
            kwargs["dicom"] = dicom
        if tags is not None:
            slicer_meta["tags"] = tags

    if name_auto is not None:
        slicer_meta["name_auto_generated"] = _parse_bool(name_auto)
    if color_auto is not None:
        slicer_meta["color_auto_generated"] = _parse_bool(color_auto)

    if slicer_meta:
        kwargs["metadata"] = {"slicer": slicer_meta}

    return Segment(**kwargs), schemes


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

    # Partition keys into consumed (segmentation) vs remaining
    consumed: dict[str, str] = {}
    remaining: dict[str, str] = {}
    for key in keyvalues:
        if key.startswith("Segmentation_") or _SEG_KEY_RE.match(key):
            consumed[key] = keyvalues[key]
        else:
            remaining[key] = keyvalues[key]

    # --- Global fields ---
    ext_kwargs: dict[str, Any] = {"version": SEG_EXTENSION_VERSION}
    slicer_meta: dict[str, Any] = {}

    master_rep = keyvalues.get("Segmentation_MasterRepresentation")
    source_rep = keyvalues.get("Segmentation_SourceRepresentation")
    rep_raw = source_rep or master_rep
    if rep_raw is not None:
        ext_kwargs["source_representation"] = _normalize_representation(rep_raw)

    contained_raw = keyvalues.get("Segmentation_ContainedRepresentationNames")
    if contained_raw is not None:
        reps = [_normalize_representation(r) for r in contained_raw.split("|") if r.strip()]
        if reps:
            slicer_meta["contained_representations"] = reps

    conv_raw = keyvalues.get("Segmentation_ConversionParameters")
    if conv_raw is not None:
        params = _parse_conversion_parameters(conv_raw)
        if params:
            slicer_meta["conversion_parameters"] = {
                name: p.model_dump(exclude_none=True) for name, p in params.items()
            }

    ref_offset_raw = keyvalues.get("Segmentation_ReferenceImageExtentOffset")
    if ref_offset_raw is not None:
        slicer_meta["reference_extent_offset"] = _parse_int_list(ref_offset_raw)

    if slicer_meta:
        ext_kwargs["metadata"] = {"slicer": slicer_meta}

    # --- Per-segment ---
    all_schemes: set[str] = set()
    segments: list[Segment] = []
    for idx in seg_indices:
        seg, schemes = _parse_segment(idx, keyvalues)
        segments.append(seg)
        all_schemes |= schemes

    ext_kwargs["segments"] = segments

    # --- Terminologies registry ---
    if all_schemes:
        terminologies: dict[str, TerminologyEntry] = {}
        for scheme in sorted(all_schemes):
            name = _KNOWN_SCHEMES.get(scheme)
            terminologies[scheme] = TerminologyEntry(name=name)
        ext_kwargs["terminologies"] = terminologies

    # --- Legacy: stash original key/value strings for lossless back-conversion ---
    ext_kwargs["legacy"] = {"keyvalues": consumed}

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

# 3D Slicer renamed Master → Source around 5.3; both spell the same field.
_REPRESENTATION_KEYS = (
    "Segmentation_MasterRepresentation",
    "Segmentation_SourceRepresentation",
)


def _denormalize_representation(kebab: str) -> str:
    """``"binary-labelmap"`` → ``"Binary labelmap"``."""
    return _REPR_TITLE.get(kebab, kebab.replace("-", " ").capitalize())


def _coded_entry_triplet(entry: CodedEntry | None) -> str:
    """CodedEntry → ``"scheme^code^meaning"`` (or ``"^^"`` for None)."""
    if entry is None:
        return "^^"
    return f"{entry.scheme}^{entry.code}^{entry.meaning or ''}"


def _serialize_conversion_parameters(params: dict[str, Any]) -> str:
    """Dict of ``{value, description}`` dicts → ``&``-delimited string."""
    parts: list[str] = []
    for name, param in params.items():
        desc = param.get("description")
        desc = desc.replace("\n", "\\n") if desc is not None else ""
        parts.append(f"{name}|{param.get('value', '')}|{desc}")
    return "&".join(parts) + "&"


def _serialize_terminology_entry(
    dicom: DicomClassification | None,
    designation: Designation | None,
) -> str:
    """Reconstruct the ``~``-delimited TerminologyEntry value."""
    if dicom is None and designation is None:
        return ""

    category = _coded_entry_triplet(dicom.category if dicom else None)
    anatomic_region = _coded_entry_triplet(dicom.anatomic_region if dicom else None)
    anatomic_region_modifier = _coded_entry_triplet(
        dicom.anatomic_region_modifier if dicom else None
    )

    if dicom and dicom.type:
        type_entry = _coded_entry_triplet(dicom.type)
        # The DICOM classification is authoritative for the type, but it may
        # carry no modifier while the designation does. Falling back keeps
        # post-coordinated laterality (§4.1) from being dropped on write.
        modifier = dicom.type_modifier
        if modifier is None and designation is not None and designation.modifier is not None:
            modifier = designation.modifier
        type_modifier = _coded_entry_triplet(modifier)
    elif designation:
        type_entry = _coded_entry_triplet(
            CodedEntry(scheme=designation.scheme, code=designation.code, meaning=designation.meaning)
        )
        type_modifier = _coded_entry_triplet(designation.modifier)
    else:
        type_entry = "^^"
        type_modifier = "^^"

    ctx1 = "Segmentation category and type"
    ctx2 = "Anatomic codes"
    return f"{ctx1}~{category}~{type_entry}~{type_modifier}~{ctx2}~{anatomic_region}~{anatomic_region_modifier}"


def _slicer_metadata(obj: Segment | SegmentationExtension) -> dict[str, Any]:
    """The ``metadata.slicer`` dict of a segment or extension (may be empty)."""
    return (obj.metadata or {}).get("slicer") or {}


def _serialize_tags(seg: Segment) -> str:
    """Build the ``|``-delimited Tags value for a segment."""
    pairs: list[str] = []

    for key, val in _slicer_metadata(seg).get("tags", {}).items():
        pairs.append(f"Segmentation.{key}:{val}")

    designation = seg.designations[0] if seg.designations else None
    term_val = _serialize_terminology_entry(seg.dicom, designation)
    if term_val:
        pairs.append(f"TerminologyEntry:{term_val}")

    if not pairs:
        return ""
    return "|".join(pairs) + "|"


def _generate_from_model(ext: SegmentationExtension) -> dict[str, str]:
    """Generate flat key/value pairs from model data (no legacy)."""
    kv: dict[str, str] = {}
    ext_slicer = _slicer_metadata(ext)

    if ext.source_representation is not None:
        kv["Segmentation_MasterRepresentation"] = _denormalize_representation(
            str(ext.source_representation)
        )

    contained = ext_slicer.get("contained_representations")
    if contained:
        kv["Segmentation_ContainedRepresentationNames"] = (
            "|".join(_denormalize_representation(r) for r in contained) + "|"
        )

    conv_params = ext_slicer.get("conversion_parameters")
    if conv_params:
        kv["Segmentation_ConversionParameters"] = _serialize_conversion_parameters(conv_params)

    ref_offset = ext_slicer.get("reference_extent_offset")
    if ref_offset is not None:
        kv["Segmentation_ReferenceImageExtentOffset"] = " ".join(str(x) for x in ref_offset)

    for i, seg in enumerate(ext.segments):
        p = f"Segment{i}_"
        seg_slicer = _slicer_metadata(seg)
        kv[f"{p}ID"] = seg.id
        if seg.name is not None:
            kv[f"{p}Name"] = seg.name
        name_auto = seg_slicer.get("name_auto_generated")
        if name_auto is not None:
            kv[f"{p}NameAutoGenerated"] = "1" if name_auto else "0"
        if seg.color is not None:
            kv[f"{p}Color"] = " ".join(str(c) for c in seg.color)
        color_auto = seg_slicer.get("color_auto_generated")
        if color_auto is not None:
            kv[f"{p}ColorAutoGenerated"] = "1" if color_auto else "0"
        lv = seg.label_value
        if isinstance(lv, str) or (
            isinstance(lv, list) and any(isinstance(v, str) for v in lv)
        ):
            raise ValueError(
                f"segment {seg.id!r}: string label_value references cannot be "
                "represented in .seg.nrrd"
            )
        if isinstance(lv, list):
            kv[f"{p}LabelValue"] = " ".join(str(v) for v in lv)
        else:
            kv[f"{p}LabelValue"] = str(lv)
        if seg.layer is not None:
            kv[f"{p}Layer"] = str(seg.layer)
        if seg.extent is not None:
            kv[f"{p}Extent"] = " ".join(str(x) for x in seg.extent)

        tags_str = _serialize_tags(seg)
        if tags_str:
            kv[f"{p}Tags"] = tags_str

    return kv


def serialize_seg_extension(ext: SegmentationExtension) -> dict[str, str]:
    """Convert a SegmentationExtension back to flat NRRD key/value pairs.

    Generates key/value pairs from the model, then for each key checks
    whether the legacy dict has an original value that would parse back
    to the same model data.  If so, the original string is used to
    preserve formatting.  If not (or no legacy), the generated string
    is used.
    """
    generated = _generate_from_model(ext)

    legacy_kv: dict[str, str] = {}
    if ext.legacy and "keyvalues" in ext.legacy:
        legacy_kv = ext.legacy["keyvalues"]

    if not legacy_kv:
        return generated

    # Re-parse legacy to see if it still matches the current model.
    # If the legacy parses to an identical extension (ignoring legacy itself),
    # use the original strings for any key the legacy has.
    try:
        legacy_ext, _ = parse_seg_keyvalues(legacy_kv)
    except Exception:
        return generated

    if legacy_ext is None:
        return generated

    # Compare model data (excluding legacy) to check equivalence
    current_dump = ext.model_dump(exclude={"legacy"}, exclude_none=True)
    legacy_dump = legacy_ext.model_dump(exclude={"legacy"}, exclude_none=True)

    if current_dump != legacy_dump:
        # Model was modified — generate fresh
        return generated

    # Model unchanged — replay the original strings verbatim. Starting from
    # the legacy dict (rather than from the generated keys) also preserves
    # the original Master/Source spelling and any Segment*/Segmentation_*
    # keys this model does not represent, which would otherwise be consumed
    # on parse and dropped here.
    result = dict(legacy_kv)
    for key, val in generated.items():
        if key in result:
            continue
        if key in _REPRESENTATION_KEYS and any(k in result for k in _REPRESENTATION_KEYS):
            continue  # the original used the other spelling
        result[key] = val
    return result
