"""The .seg.nrrd mapping of the seg extension (spec §6.1): key/value pairs to
a SegmentationExtension and back, materializing voxel data where 3D Slicer's
one-value-per-segment layout cannot hold the file as it is."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .diagnostics import About, Diagnostic
from .models import CodedEntry, ConversionParameter, Designation
from .seg_color import format_color, from_slicer_floats, parse_color, slicer_floats_text, to_srgb
from .seg_model import (
    SEG_VERSION,
    DicomContent,
    Segment,
    SegmentationExtension,
    derive_token_ids,
    is_fractional,
    is_label_table,
    layers_of,
)

# Known terminology scheme → registry entry
_KNOWN_SCHEMES: dict[str, dict[str, str]] = {
    "SCT": {"name": "SNOMED Clinical Terms", "system_uri": "http://snomed.info/sct"},
    "SRT": {"name": "DICOM SR Coding Scheme"},
    "DCM": {"name": "DICOM Controlled Terminology",
            "system_uri": "http://dicom.nema.org/resources/ontology/DCM"},
}

ROLE_TAG = "duckn.role"
_ROLES = ("background", "unknown")


def _warn(code: str, about: About, message: str = "") -> Diagnostic:
    return Diagnostic(code, "warning", about, message)

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


def _parse_label_values(val: str) -> list[int]:
    # 3D Slicer writes one integer; a space-separated list is what an earlier
    # version of this library wrote for a union of islands.
    return sorted({int(x) for x in val.split()})


def _reverse_extent(extent: list[int]) -> list[int]:
    """``SegmentN_Extent`` runs over NRRD's axes, fastest first; the model's
    ``extent`` over the array's spatial axes in storage order, the reverse.
    The three (min, max) pairs swap ends; a malformed list is left for the
    model to refuse."""
    if len(extent) != 6:
        return extent
    return [*extent[4:6], *extent[2:4], *extent[0:2]]


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
) -> tuple[DicomContent | None, list[Designation] | None, set[str]]:
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

    dicom: DicomContent | None = None
    if any(x is not None for x in (category, type_entry, type_modifier, anatomic_region, anatomic_region_modifier)):
        dicom = DicomContent(
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
) -> tuple[dict[str, str] | None, DicomContent | None, list[Designation] | None, set[str]]:
    """Parse ``SegmentN_Tags`` value.

    Returns (tags, dicom, designations, schemes_seen).
    """
    tags: dict[str, str] = {}
    dicom: DicomContent | None = None
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
        elif key.startswith("Segmentation.") and "." not in key.removeprefix("Segmentation."):
            tags[key.removeprefix("Segmentation.")] = value
        else:
            # A tag without the prefix keeps its key verbatim, so that
            # serialization can put back exactly what it found.
            tags[key] = value

    return tags or None, dicom, designations, all_schemes


def _parse_segment(
    index: int, kv: dict[str, str], diagnostics: list[Diagnostic]
) -> tuple[dict[str, Any], set[str]]:
    """Build a segment dict from ``SegmentN_*`` keys.  Returns (segment, schemes).

    ``label_values`` is left out when the file has no ``LabelValue``; the
    caller assigns one. Diagnostics name the segment by its source id, and
    the caller re-points them if the id changes."""
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

    seg: dict[str, Any] = {"id": seg_id}
    if name is not None:
        seg["name"] = name
    if label_raw is not None:
        seg["label_values"] = _parse_label_values(label_raw)
    if layer_raw is not None and int(layer_raw) != 0:
        # Layer 0 is the implicit default. Carrying it explicitly would claim
        # a `list` axis that an ordinary 3D .seg.nrrd does not have (§5 rule 2).
        seg["layer"] = int(layer_raw)
    if extent_raw is not None:
        seg["extent"] = _reverse_extent(_parse_int_list(extent_raw))
    if color_raw is not None:
        seg["color"] = format_color(from_slicer_floats(_parse_float_list(color_raw)))[0]

    schemes: set[str] = set()
    slicer_meta: dict[str, Any] = {}

    if tags_raw is not None:
        tags, dicom, designations, tag_schemes = _parse_tags(tags_raw)
        schemes = tag_schemes
        if tags is not None and ROLE_TAG in tags:
            if tags[ROLE_TAG] in _ROLES:
                seg["role"] = tags.pop(ROLE_TAG)
            else:
                diagnostics.append(
                    _warn("role-tag-invalid", About.segment(seg_id), repr(tags[ROLE_TAG]))
                )
        if designations is not None:
            seg["designations"] = [d.model_dump(exclude_none=True) for d in designations]
            diagnostics.append(_warn("designation-unverified", About.segment(seg_id)))
        if dicom is not None:
            seg["dicom"] = dicom.model_dump(exclude_none=True)
        if tags:
            slicer_meta["tags"] = tags

    if name_auto is not None:
        slicer_meta["name_auto_generated"] = _parse_bool(name_auto)
    if color_auto is not None:
        slicer_meta["color_auto_generated"] = _parse_bool(color_auto)

    if slicer_meta:
        seg["metadata"] = {"slicer": slicer_meta}

    return seg, schemes


def parse_seg_keyvalues(
    keyvalues: dict[str, str],
    *,
    diagnostics: list[Diagnostic] | None = None,
) -> tuple[SegmentationExtension | None, dict[str, str]]:
    """Parse segmentation key/value pairs into a SegmentationExtension.

    Parameters
    ----------
    keyvalues:
        Non-spec key/value pairs from an NRRD header.
    diagnostics:
        A list to which what the import reports is appended (§10).

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
    found: list[Diagnostic] = []
    ext_kwargs: dict[str, Any] = {"version": SEG_VERSION}
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
    segments: list[dict[str, Any]] = []
    for idx in seg_indices:
        seg, schemes = _parse_segment(idx, keyvalues, found)
        segments.append(seg)
        all_schemes |= schemes

    # Slicer omits LabelValue only for segments with no binary labelmap
    # representation. 0 is the background value, so claiming it would claim
    # every unwritten voxel: each such segment, in order, takes the smallest
    # positive integer that no segment of its layer has.
    used: dict[int, set[int]] = {}
    for seg in segments:
        used.setdefault(seg.get("layer", 0), set()).update(seg.get("label_values", ()))
    for seg in segments:
        if "label_values" not in seg:
            taken = used[seg.get("layer", 0)]
            value = next(v for v in range(1, len(taken) + 2) if v not in taken)
            taken.add(value)
            seg["label_values"] = [value]

    # Two segments of a layer carrying the same designation (rule 9)
    from .seg_read import _set_aside_colliding_designations

    found.extend(_set_aside_colliding_designations(segments))

    # Ids are made tokens, the original kept (§6.1)
    new_ids = derive_token_ids([seg["id"] for seg in segments])
    for seg, new_id in zip(segments, new_ids):
        if new_id != seg["id"]:
            old_id = seg["id"]
            metadata = seg.setdefault("metadata", {})
            metadata.setdefault("duckn", {})["id"] = old_id
            seg["id"] = new_id
            found = [
                Diagnostic(d.code, d.severity, About.segment(new_id), d.message)
                if d.about == About.segment(old_id) else d
                for d in found
            ]
            found.append(_warn("id-changed", About.segment(new_id), f"was {old_id!r}"))

    ext_kwargs["segments"] = segments

    # --- Terminologies registry ---
    if all_schemes:
        ext_kwargs["terminologies"] = {
            scheme: dict(_KNOWN_SCHEMES.get(scheme, {})) for scheme in sorted(all_schemes)
        }

    # --- Legacy: stash original key/value strings for lossless back-conversion ---
    ext_kwargs["legacy"] = {"keyvalues": consumed}

    if diagnostics is not None:
        diagnostics.extend(found)
    return SegmentationExtension.model_validate(ext_kwargs), remaining


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
    dicom: DicomContent | None,
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
        # Slicer's own tags are stored with the "Segmentation." prefix
        # stripped; anything else kept its key verbatim on parse (and
        # "TerminologyEntry" is reconstructed below, not carried as a tag).
        if key == "TerminologyEntry":
            continue
        pairs.append(f"Segmentation.{key}:{val}" if "." not in key else f"{key}:{val}")

    if seg.role is not None:
        pairs.append(f"{ROLE_TAG}:{seg.role}")

    designation = seg.designations[0] if seg.designations else None
    term_val = _serialize_terminology_entry(seg.dicom, designation)
    if term_val:
        pairs.append(f"TerminologyEntry:{term_val}")

    if not pairs:
        return ""
    return "|".join(pairs) + "|"


def _is_nrrd_background(seg: Segment) -> bool:
    """A background segment at 0 is .seg.nrrd's own background: not written."""
    return seg.role == "background" and list(seg.label_values) == [0]


def _written(ext: SegmentationExtension) -> list[Segment]:
    return [seg for seg in ext.segments if not _is_nrrd_background(seg)]


def needs_materialization(ext: SegmentationExtension, dtype: Any = None) -> bool:
    """Whether the file cannot be written as it is (§6.1): some layer is not a
    label table, or a written segment lists a value that is not positive."""
    if is_fractional(ext, dtype):
        return False
    if any(v <= 0 for seg in _written(ext) for v in seg.label_values):
        return True
    return not all(is_label_table(ext, layer) for layer in layers_of(ext))


def _generate_from_model(
    ext: SegmentationExtension, diagnostics: list[Diagnostic]
) -> dict[str, str]:
    """Generate flat key/value pairs for a file that can be written as it is."""
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

    for seg in ext.segments:
        if _is_nrrd_background(seg) and (seg.name or seg.designations or seg.dicom):
            diagnostics.append(_warn("background-not-written", About.segment(seg.id)))

    # The source ids come back only if restoring all of them keeps ids distinct.
    written = _written(ext)
    originals = [((s.metadata or {}).get("duckn") or {}).get("id", s.id) for s in written]
    ids = originals if len(set(originals)) == len(originals) else [s.id for s in written]

    # Dense indices: 3D Slicer stops reading at the first missing one.
    for i, seg in enumerate(written):
        p = f"Segment{i}_"
        seg_slicer = _slicer_metadata(seg)
        kv[f"{p}ID"] = ids[i]
        if seg.name is not None:
            kv[f"{p}Name"] = seg.name
        name_auto = seg_slicer.get("name_auto_generated")
        if name_auto is not None:
            kv[f"{p}NameAutoGenerated"] = "1" if name_auto else "0"
        # an unreadable color is absent, and the reader has already said so
        color = parse_color(seg.color) if seg.color is not None else None
        if color is not None:
            srgb = to_srgb(color)
            kv[f"{p}Color"] = slicer_floats_text(srgb.rgb)
            if srgb.clamped:
                diagnostics.append(_warn("color-clamped", About.segment(seg.id)))
            if srgb.gamut_mapped:
                diagnostics.append(_warn("color-gamut-mapped", About.segment(seg.id)))
        color_auto = seg_slicer.get("color_auto_generated")
        if color_auto is not None:
            kv[f"{p}ColorAutoGenerated"] = "1" if color_auto else "0"
        kv[f"{p}LabelValue"] = " ".join(str(v) for v in seg.label_values)
        if seg.layer is not None:
            kv[f"{p}Layer"] = str(seg.layer)
        if seg.extent is not None:
            kv[f"{p}Extent"] = " ".join(str(x) for x in _reverse_extent(seg.extent))

        tags_str = _serialize_tags(seg)
        if tags_str:
            kv[f"{p}Tags"] = tags_str

    return kv


def _replayed_legacy(ext: SegmentationExtension) -> dict[str, str] | None:
    """The file's original key/values, if the model still says what they say."""
    legacy_kv = (ext.legacy or {}).get("keyvalues")
    if not legacy_kv:
        return None
    # Only what 3D Slicer reads is replayed: not a LabelValue of several integers
    if any(k.endswith("_LabelValue") and len(str(v).split()) != 1 for k, v in legacy_kv.items()):
        return None
    try:
        legacy_ext, _ = parse_seg_keyvalues(legacy_kv)
    except Exception:
        return None
    if legacy_ext is None:
        return None
    current_dump = ext.model_dump(exclude={"legacy"}, exclude_none=True)
    if current_dump != legacy_ext.model_dump(exclude={"legacy"}, exclude_none=True):
        return None  # the model was modified - generate fresh
    # Starting from the legacy dict (rather than from generated keys) also
    # preserves the original Master/Source spelling and any Segment*/
    # Segmentation_* keys this model does not represent. Generation only
    # fills keys the legacy lacks.
    result = dict(legacy_kv)
    for key, val in _generate_from_model(ext, []).items():
        if key in result:
            continue
        if key in _REPRESENTATION_KEYS and any(k in result for k in _REPRESENTATION_KEYS):
            continue  # the original used the other spelling
        result[key] = val
    return result


@dataclass
class SegNrrdExport:
    """What :func:`export_seg_nrrd` produced."""

    keyvalues: dict[str, str]
    #: New voxel data when the export materialized, else None (write the source's)
    data: Any = None
    #: Index of the ``list`` axis in ``data``, or None when it has a single layer
    list_axis: int | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def materialized(self) -> bool:
        return self.data is not None


def export_seg_nrrd(
    ext: SegmentationExtension,
    data: Any = None,
    *,
    list_axis: int | None = None,
) -> SegNrrdExport:
    """Write a segmentation as .seg.nrrd key/value pairs (§6.1).

    A file whose model is unchanged since it was read replays its original
    strings. Otherwise it is written as it is when every layer is a label
    table of positive values, and is **materialized** when not: segments are
    placed first-fit into destination layers and the voxels rewritten, which
    needs ``data`` (and ``list_axis``, the index of its ``list`` axis, for a
    layered array). Raises ``ValueError`` when materialization is needed and
    no data was given.
    """
    replayed = _replayed_legacy(ext)
    if replayed is not None:
        return SegNrrdExport(replayed)

    diagnostics: list[Diagnostic] = []
    if not needs_materialization(ext, getattr(data, "dtype", None)):
        return SegNrrdExport(_generate_from_model(ext, diagnostics), diagnostics=diagnostics)
    if data is None:
        raise ValueError(
            "this segmentation shares values between segments, or lists a value that is "
            "not positive, and .seg.nrrd can hold neither: exporting it rewrites the "
            "voxels, so the voxel data is needed (spec §6.1)"
        )
    new_ext, new_data, new_axis = _materialize(ext, data, list_axis, diagnostics)
    return SegNrrdExport(
        _generate_from_model(new_ext, diagnostics), new_data, new_axis, diagnostics
    )


def _materialize(
    ext: SegmentationExtension,
    data: Any,
    list_axis: int | None,
    diagnostics: list[Diagnostic],
) -> tuple[SegmentationExtension, Any, int | None]:
    import numpy as np

    arr = np.asarray(data)
    if list_axis is None and any(seg.effective_layer for seg in ext.segments):
        raise ValueError("the segmentation has layers beyond 0; list_axis is needed")

    def source(layer: int) -> Any:
        return arr if list_axis is None else np.take(arr, layer, axis=list_axis)

    the_ext = About.extension()
    diagnostics.append(_warn("values-renumbered", the_ext))
    for layer in layers_of(ext):
        segs = [s for s in ext.segments if s.effective_layer == layer]
        if any(a.values > b.values for a in segs for b in segs):
            diagnostics.append(_warn("nesting-exported-as-layers", the_ext))
            break

    # Placement: first destination layer in which the segment overlaps nothing
    # placed there. Same source layer: a shared value. Different source
    # layers: a shared voxel, decided from one occupancy mask per (destination,
    # source layer).
    placed: list[list[Segment]] = []
    occupancy: list[dict[int, Any]] = []
    used: list[set[int]] = []
    out_segments: list[Segment] = []
    masks: list[Any] = []
    destinations: list[int] = []
    for seg in ext.segments:
        if _is_nrrd_background(seg):
            out_segments.append(seg)
            continue
        mask = np.isin(source(seg.effective_layer), list(seg.values))
        for dest in range(len(placed) + 1):
            if dest == len(placed):
                placed.append([])
                occupancy.append({})
                used.append(set())
                break
            if any(o.effective_layer == seg.effective_layer and o.values & seg.values
                   for o in placed[dest]):
                continue
            if any(src != seg.effective_layer and bool(np.any(occ & mask))
                   for src, occ in occupancy[dest].items()):
                continue
            break
        placed[dest].append(seg)
        occ = occupancy[dest].get(seg.effective_layer)
        occupancy[dest][seg.effective_layer] = mask if occ is None else (occ | mask)

        values = sorted(seg.values)
        if len(values) == 1 and values[0] > 0 and values[0] not in used[dest]:
            value = values[0]
        else:
            value = next(v for v in range(1, len(used[dest]) + 2) if v not in used[dest])
        used[dest].add(value)

        update: dict[str, Any] = {"label_values": [value], "layer": dest or None, "extent": None}
        if mask.any():
            bounds = [(int(idx.min()), int(idx.max())) for idx in np.nonzero(mask)]
            update["extent"] = [b for pair in bounds for b in pair]
        out_segments.append(seg.model_copy(update=update))
        masks.append(mask)
        destinations.append(dest)

    top = max((max(u) for u in used if u), default=0)
    dtype = arr.dtype if arr.dtype.kind in "iu" and top <= np.iinfo(arr.dtype).max else (
        np.min_scalar_type(top))
    spatial = source(0).shape
    n_layers = max(len(placed), 1)
    new_axis = None if n_layers == 1 else (list_axis if list_axis is not None else len(spatial))
    layers_out = [np.zeros(spatial, dtype=dtype) for _ in range(n_layers)]
    written = [s for s in out_segments if not _is_nrrd_background(s)]
    for seg, mask, dest in zip(written, masks, destinations):
        layers_out[dest][mask] = seg.label_values[0]
    new_data = layers_out[0] if new_axis is None else np.stack(layers_out, axis=new_axis)

    new_ext = ext.model_copy(update={"segments": out_segments, "legacy": None})
    return new_ext, new_data, new_axis


def serialize_seg_extension(ext: SegmentationExtension) -> dict[str, str]:
    """Key/value pairs for a segmentation that can be written without touching
    its voxels. Raises ``ValueError`` for one that must be materialized: use
    :func:`export_seg_nrrd`, which takes the data and returns diagnostics."""
    return export_seg_nrrd(ext).keyvalues
