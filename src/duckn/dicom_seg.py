"""The DICOM Segmentation mapping of the seg extension (spec §6.2).

Import turns a Segmentation object's Segment Sequence into a 0.8 extension;
export plans and builds the segment items, frames, and pixel data for the
three Segmentation Types. Reading frames and geometry, and writing the file,
stay in ``dicom_convert``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

import numpy as np

from .diagnostics import About, Diagnostic
from .models import CodedEntry
from .seg_color import (
    DICOM_D65_MARKER,
    dicom_cielab_is_d65,
    format_color,
    from_dicom_cielab,
    parse_color,
    to_dicom_cielab,
)
from .seg_model import (
    ALGORITHM_TYPES,
    SEG_VERSION,
    DicomContent,
    Segment,
    SegmentationExtension,
    background_segment,
    background_value,
    is_fractional,
    is_nested,
    layers_of,
    validate_seg_data,
)

SEG_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.66.4"
LABELMAP_SEG_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.66.7"

CielabReading = Literal["d65", "d50"]
SegmentationType = Literal["BINARY", "FRACTIONAL", "LABELMAP"]

_KNOWN_SCHEMES: dict[str, dict[str, str]] = {
    "SCT": {"name": "SNOMED Clinical Terms", "system_uri": "http://snomed.info/sct"},
    "SRT": {"name": "DICOM SR Coding Scheme"},
    "DCM": {"name": "DICOM Controlled Terminology",
            "system_uri": "http://dicom.nema.org/resources/ontology/DCM"},
    "LN": {"name": "LOINC", "system_uri": "http://loinc.org"},
    "UCUM": {"name": "Unified Code for Units of Measure", "system_uri": "http://unitsofmeasure.org"},
    "FMA": {"name": "Foundational Model of Anatomy"},
    "NCIt": {"name": "NCI Thesaurus"},
    "RADLEX": {"name": "RadLex"},
}


def _warn(code: str, about: About, message: str = "") -> Diagnostic:
    return Diagnostic(code, "warning", about, message)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def seg_import_layout(ds: Any) -> Literal["labelmap", "one-layer", "layers"]:
    """How a Segmentation object's frames become an array (§6.2): a LABELMAP
    is one layer as it stands; a BINARY object that declares its segments
    disjoint is one layer valued by Segment Number; anything else is one
    layer per segment."""
    seg_type = str(getattr(ds, "SegmentationType", "BINARY")).strip().upper()
    if seg_type == "LABELMAP":
        return "labelmap"
    overlap = str(getattr(ds, "SegmentsOverlap", "") or "").strip().upper()
    if seg_type == "BINARY" and overlap == "NO":
        return "one-layer"
    return "layers"


def _coded(item: Any) -> dict[str, str] | None:
    code = str(getattr(item, "CodeValue", "") or getattr(item, "LongCodeValue", "")
               or getattr(item, "URNCodeValue", "") or "").strip()
    scheme = str(getattr(item, "CodingSchemeDesignator", "") or "").strip()
    if not code or not scheme:
        return None
    out = {"scheme": scheme, "code": code}
    meaning = str(getattr(item, "CodeMeaning", "") or "").strip()
    if meaning:
        out["meaning"] = meaning
    return out


def _coded_items(seq: Any) -> list[dict[str, str]]:
    return [c for c in (_coded(item) for item in (seq or [])) if c is not None]


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else list(value)
    return [str(v).strip() for v in values if str(v).strip()]


def _segment_from_item(item: Any, d65: bool, found: list[Diagnostic]) -> dict[str, Any]:
    number = int(item.SegmentNumber)
    seg_id = f"Segment_{number}"
    seg: dict[str, Any] = {"id": seg_id}
    label = str(getattr(item, "SegmentLabel", "") or "")
    if label:
        seg["name"] = label

    cielab = getattr(item, "RecommendedDisplayCIELabValue", None)
    if cielab is not None and len(cielab) == 3:
        seg["color"] = format_color(from_dicom_cielab([int(v) for v in cielab], d65=d65))[0]

    dicom: dict[str, Any] = {}
    kept: dict[str, Any] = {}

    categories = _coded_items(getattr(item, "SegmentedPropertyCategoryCodeSequence", None))
    if categories:
        dicom["category"] = categories[0]
    type_seq = getattr(item, "SegmentedPropertyTypeCodeSequence", None)
    types = _coded_items(type_seq)
    if types:
        dicom["type"] = types[0]
        modifiers = _coded_items(
            getattr(type_seq[0], "SegmentedPropertyTypeModifierCodeSequence", None))
        if modifiers:
            dicom["type_modifier"] = modifiers[0]
        if modifiers[1:]:
            kept["type_modifiers"] = modifiers[1:]
    region_seq = getattr(item, "AnatomicRegionSequence", None)
    regions = _coded_items(region_seq)
    if regions:
        dicom["anatomic_region"] = regions[0]
        modifiers = _coded_items(getattr(region_seq[0], "AnatomicRegionModifierSequence", None))
        if modifiers:
            dicom["anatomic_region_modifier"] = modifiers[0]
        if modifiers[1:]:
            kept["anatomic_region_modifiers"] = modifiers[1:]
        if regions[1:]:
            kept["anatomic_regions"] = regions[1:]

    algorithm_type = str(getattr(item, "SegmentAlgorithmType", "") or "").strip()
    if algorithm_type:
        dicom["algorithm_type"] = algorithm_type
    names = _strings(getattr(item, "SegmentAlgorithmName", None))
    if names:
        dicom["algorithm_name"] = names[0]
    if names[1:]:
        kept["algorithm_names"] = names[1:]
    if kept:
        found.append(_warn("dicom-items-kept", About.segment(seg_id)))

    for keyword in ("SegmentDescription", "TrackingID", "TrackingUID"):
        value = str(getattr(item, keyword, "") or "").strip()
        if value:
            kept[keyword] = value

    if dicom:
        seg["dicom"] = dicom
    if kept:
        seg["metadata"] = {"dicom": kept}

    # The property type code is the primary concept: surface it as a
    # designation so lookup does not require DICOM knowledge. Whether the
    # identification is exact in this extension's sense is not established.
    if "type" in dicom:
        designation = dict(dicom["type"])
        if "type_modifier" in dicom:
            designation["modifier"] = dict(dicom["type_modifier"])
        seg["designations"] = [designation]
        found.append(_warn("designation-unverified", About.segment(seg_id)))
    return seg


def _merge_key(seg: dict[str, Any]) -> tuple:
    def code(entry: Any) -> tuple | None:
        return (entry["scheme"], entry["code"]) if entry else None

    dicom = seg.get("dicom", {})
    kept = seg.get("metadata", {}).get("dicom", {})
    # the whole sequences, by scheme and code: the first items live in `dicom`,
    # the rest were kept under metadata.dicom
    rest = tuple(
        tuple(code(e) for e in kept.get(key, []))
        for key in ("type_modifiers", "anatomic_regions", "anatomic_region_modifiers")
    )
    return (
        seg.get("name"),
        *(code(dicom.get(f)) for f in ("category", "type", "type_modifier",
                                       "anatomic_region", "anatomic_region_modifier")),
        rest,
        dicom.get("algorithm_type"),
        dicom.get("algorithm_name"),
    )


def _schemes_of(segments: list[dict[str, Any]]) -> list[str]:
    keys: set[str] = set()
    for seg in segments:
        for d in seg.get("designations", []):
            keys.add(d["scheme"])
            if "modifier" in d:
                keys.add(d["modifier"]["scheme"])
        keys.update(v["scheme"] for v in seg.get("dicom", {}).values() if isinstance(v, dict))
    return sorted(keys)


def extract_seg_extension(
    ds: Any,
    *,
    cielab: CielabReading | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> SegmentationExtension | None:
    """Build a 0.8 seg extension from a DICOM Segmentation dataset (§6.2).

    ``cielab`` states how ``RecommendedDisplayCIELabValue`` is read — ``"d65"``
    as dcmqi writes it, ``"d50"`` as the standard intends; when None the
    object's ``Manufacturer`` and ``SoftwareVersions`` decide. What the import
    reports is appended to ``diagnostics``. :func:`seg_fill_value` gives the
    ``fill_value`` the array needs.
    """
    seq = getattr(ds, "SegmentSequence", None)
    if seq is None or len(seq) == 0:
        return None

    found: list[Diagnostic] = []
    if cielab is None:
        d65 = dicom_cielab_is_d65(getattr(ds, "Manufacturer", None),
                                  getattr(ds, "SoftwareVersions", None))
    else:
        d65 = cielab == "d65"

    layout = seg_import_layout(ds)
    seg_type = str(getattr(ds, "SegmentationType", "BINARY")).strip().upper()
    items = sorted(seq, key=lambda item: int(item.SegmentNumber))
    segments: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        seg = _segment_from_item(item, d65, found)
        number = int(item.SegmentNumber)
        if layout == "layers":
            seg["label_values"] = [1]
            if index:
                seg["layer"] = index
        else:
            seg["label_values"] = [number]
        segments.append(seg)

    ext: dict[str, Any] = {
        "version": SEG_VERSION,
        "source_representation": (
            "fractional-labelmap" if seg_type == "FRACTIONAL" else "binary-labelmap"),
    }

    if layout == "labelmap":
        # Items an exporter wrote once per value are one segment (§6.2).
        merged: dict[tuple, dict[str, Any]] = {}
        for seg in segments:
            first = merged.setdefault(_merge_key(seg), seg)
            if first is not seg:
                first["label_values"] = sorted({*first["label_values"], *seg["label_values"]})
                found = [d for d in found if d.about != About.segment(seg["id"])]
        for seg in merged.values():
            if len(seg["label_values"]) > 1:
                found.append(_warn("dicom-items-merged", About.segment(seg["id"])))
        segments = list(merged.values())

        # No value is background unless PixelPaddingValue names it.
        ext["implicit_background"] = False
        padding = getattr(ds, "PixelPaddingValue", None)
        if padding is not None:
            padding = int(padding)
            owner = next((s for s in segments if padding in s["label_values"]), None)
            if owner is None:
                segments.insert(0, {"id": f"Segment_{padding}", "label_values": [padding],
                                    "role": "background"})
            elif owner["label_values"] == [padding]:
                owner["role"] = "background"

    from .seg_read import _order_by_containment, _set_aside_colliding_designations

    found.extend(_set_aside_colliding_designations(segments))
    ext["segments"] = _order_by_containment(segments)

    schemes = _schemes_of(segments)
    if schemes:
        ext["terminologies"] = {key: dict(_KNOWN_SCHEMES.get(key, {})) for key in schemes}
    if seg_type == "FRACTIONAL":
        fractional_type = str(getattr(ds, "SegmentationFractionalType", "") or "").strip()
        if fractional_type:
            ext["metadata"] = {"dicom": {"SegmentationFractionalType": fractional_type}}

    if diagnostics is not None:
        diagnostics.extend(found)
    return SegmentationExtension.model_validate(ext)


def seg_fill_value(ext: SegmentationExtension) -> int:
    """The ``fill_value`` an imported array needs so that its layers describe
    it (rule 15): the background value where there is one, otherwise the
    lowest value any segment uses."""
    if is_fractional(ext) or not ext.segments:
        return 0
    value = background_value(ext, 0)
    if value is not None:
        return value
    return min(v for seg in ext.segments for v in seg.values)


def fractional_slope(ds: Any) -> float | None:
    """``1 / MaximumFractionalValue`` for a FRACTIONAL object, else None."""
    if str(getattr(ds, "SegmentationType", "")).strip().upper() != "FRACTIONAL":
        return None
    return 1.0 / int(getattr(ds, "MaximumFractionalValue", 255) or 255)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@dataclass
class SegmentItem:
    """One item of the Segment Sequence to be written."""

    number: int
    segment: Segment
    label: str
    cielab: tuple[int, int, int] | None = None


@dataclass
class DicomSegPlan:
    """What :func:`plan_dicom_seg` decided: the type, the items, the frames.

    ``frames`` is ``(n_items, z, y, x)`` for BINARY and FRACTIONAL, one volume
    per item in item order, and ``(z, y, x)`` for LABELMAP."""

    segmentation_type: SegmentationType
    items: list[SegmentItem]
    frames: np.ndarray
    segments_overlap: str
    pixel_padding_value: int | None = None
    maximum_fractional_value: int | None = None
    fractional_type: str | None = None
    wrote_d65: bool = False
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def sop_class_uid(self) -> str:
        return (LABELMAP_SEG_SOP_CLASS_UID if self.segmentation_type == "LABELMAP"
                else SEG_SOP_CLASS_UID)


def _label(seg: Segment, found: list[Diagnostic]) -> str:
    label = seg.name or (seg.designations[0].meaning if seg.designations else None) or seg.id
    if len(label) > 64:
        found.append(_warn("label-truncated", About.segment(seg.id)))
        label = label[:64]
    return label


def _item(number: int, seg: Segment, cielab: CielabReading, plan: DicomSegPlan) -> SegmentItem:
    item = SegmentItem(number, seg, _label(seg, plan.diagnostics))
    # an unreadable color is absent, and the reader has already said so
    color = parse_color(seg.color) if seg.color is not None else None
    if color is not None:
        item.cielab, clamped = to_dicom_cielab(color, d65=cielab == "d65")
        plan.wrote_d65 = plan.wrote_d65 or cielab == "d65"
        if clamped:
            plan.diagnostics.append(_warn("color-clamped", About.segment(seg.id)))
    return item


def plan_dicom_seg(
    ext: SegmentationExtension,
    data: np.ndarray,
    *,
    list_axis: int | None = None,
    segmentation_type: SegmentationType | None = None,
    cielab: CielabReading = "d65",
    value_scale: tuple[float, float] = (1.0, 0.0),
    maximum_fractional_value: int = 255,
    fractional_type: str | None = None,
    background_dicom: Any = None,
) -> DicomSegPlan:
    """Plan a DICOM Segmentation for ``ext`` and its voxels (§6.2).

    ``BINARY`` is written by default, ``FRACTIONAL`` for a fractional
    labelmap, and ``LABELMAP`` only when asked for and eligible. ``data`` has
    its spatial axes in ``(z, y, x)`` order; ``list_axis`` is the index of its
    ``list`` axis. ``value_scale`` is the ``(slope, intercept)`` of the array's
    ``value_transforms``, applied to a fractional labelmap's values.
    ``background_dicom`` describes a LABELMAP's item for an implicit
    background, which has no segment to take it from. Raises
    ``ValueError`` where the spec says an exporter fails.
    """
    arr = np.asarray(data)
    fractional = is_fractional(ext, arr.dtype)
    if segmentation_type is None:
        segmentation_type = "FRACTIONAL" if fractional else "BINARY"
    if (segmentation_type == "FRACTIONAL") != fractional:
        raise ValueError(
            f"a {'fractional' if fractional else 'binary'} labelmap cannot be written as "
            f"a DICOM {segmentation_type} segmentation")
    if list_axis is None and any(seg.effective_layer for seg in ext.segments):
        raise ValueError("the segmentation has layers beyond 0; list_axis is needed")

    def layer_data(layer: int) -> np.ndarray:
        return arr if list_axis is None else np.take(arr, layer, axis=list_axis)

    plan = DicomSegPlan(segmentation_type, [], np.zeros((0,)), "UNDEFINED")
    found = plan.diagnostics

    if segmentation_type == "LABELMAP":
        _plan_labelmap(ext, layer_data, plan, cielab, background_dicom)
    elif segmentation_type == "FRACTIONAL":
        if not 1 <= maximum_fractional_value <= 255:
            raise ValueError("MaximumFractionalValue must fit 8 bits")
        plan.maximum_fractional_value = maximum_fractional_value
        plan.fractional_type = fractional_type or (
            ((ext.metadata or {}).get("dicom") or {}).get("SegmentationFractionalType"))
        if plan.fractional_type not in ("PROBABILITY", "OCCUPANCY"):
            raise ValueError(
                "SegmentationFractionalType (PROBABILITY or OCCUPANCY) is not recorded in "
                "the extension's metadata.dicom; the caller must give it")
        slope, intercept = value_scale
        volumes = []
        for seg in ext.segments:
            if seg.role is not None:
                found.append(_warn("role-lost", About.segment(seg.id)))
            plan.items.append(_item(len(plan.items) + 1, seg, cielab, plan))
            f = np.clip(layer_data(seg.effective_layer).astype(np.float64) * slope + intercept,
                        0.0, 1.0)
            volumes.append(np.floor(f * maximum_fractional_value + 0.5).astype(np.uint8))
        plan.frames = np.stack(volumes) if volumes else plan.frames
        plan.segments_overlap = _segments_overlap([i.segment for i in plan.items], layer_data)
    else:
        volumes = []
        for seg in ext.segments:
            if seg.role == "background":
                # Absence is this type's background; a background segment has no frames.
                if seg.name or seg.designations or seg.dicom:
                    found.append(_warn("background-not-written", About.segment(seg.id)))
                continue
            if seg.role is not None:
                found.append(_warn("role-lost", About.segment(seg.id)))
            plan.items.append(_item(len(plan.items) + 1, seg, cielab, plan))
            volumes.append(np.isin(layer_data(seg.effective_layer), sorted(seg.values)))
        plan.frames = np.stack(volumes).astype(np.uint8) if volumes else plan.frames
        plan.segments_overlap = _segments_overlap([i.segment for i in plan.items], layer_data)

    if not plan.items:
        raise ValueError(
            "nothing to write: a DICOM Segmentation requires at least one segment "
            "(Segment Sequence is Type 1)")
    return plan


def _segments_overlap(segments: Sequence[Segment], layer_data: Any) -> str:
    """NO when all written segments are in one layer with disjoint value lists;
    YES when two of a layer share a listed value that occurs in the data;
    UNDEFINED otherwise (§6.2)."""
    present: dict[int, set[int]] = {}
    shared_but_absent = False
    for i, a in enumerate(segments):
        for b in segments[i + 1:]:
            if a.effective_layer != b.effective_layer:
                continue
            shared = a.values & b.values
            if not shared:
                continue
            layer = a.effective_layer
            if layer not in present:
                present[layer] = set(np.unique(layer_data(layer)).tolist())
            if shared & present[layer]:
                return "YES"
            shared_but_absent = True
    one_layer = len({s.effective_layer for s in segments}) <= 1
    return "NO" if one_layer and not shared_but_absent else "UNDEFINED"


def _plan_labelmap(
    ext: SegmentationExtension,
    layer_data: Any,
    plan: DicomSegPlan,
    cielab: CielabReading,
    background_dicom: Any = None,
) -> None:
    found = plan.diagnostics
    if len(layers_of(ext)) > 1 or not is_nested(ext, 0):
        raise ValueError(
            "not eligible for a DICOM LABELMAP: that needs a single layer whose value "
            "lists are nested (seg spec §6.2); write BINARY instead")
    if background_segment(ext, 0) is None and background_value(ext, 0) is not None:
        # The implicit background has no segment to describe its item with.
        value = background_value(ext, 0)
        if background_dicom is not None:
            synthetic = Segment(id="background", name="Background", label_values=[value],
                                role="background", dicom=background_dicom)
            ext = ext.model_copy(update={"segments": [synthetic, *ext.segments]})
        elif bool(np.any(layer_data(0) == value)):
            raise ValueError(
                "the layer's background is the implicit 0 and occurs in the data, so a "
                "DICOM LABELMAP needs an item for it, with a category, type, and "
                "algorithm type that DICOM defines no code for: declare a background "
                "segment carrying them in its `dicom` field, or pass background_dicom")
    data = layer_data(0)
    values = sorted({v for seg in ext.segments for v in seg.values})
    if any(not 0 <= v <= 65535 for v in values):
        raise ValueError(
            "a DICOM LABELMAP holds values 0-65535; renumber the segmentation first")
    undescribed = validate_seg_data(ext, data, layer=0)
    if undescribed:
        raise ValueError(
            "every value present must be described to write a DICOM LABELMAP; undescribed: "
            + ", ".join(str(d.about.key[1]) for d in undescribed))

    innermost: dict[int, Segment] = {}
    for seg in ext.segments:
        for v in seg.sorted_values:
            innermost[v] = seg
    represented: dict[str, list[int]] = {}
    for v in values:
        seg = innermost[v]
        represented.setdefault(seg.id, []).append(v)
        if seg.role == "unknown":
            found.append(_warn("role-lost", About.segment(seg.id)))
        plan.items.append(_item(v, seg, cielab, plan))
    for seg in ext.segments:
        if seg.id not in represented:
            found.append(_warn("segment-not-represented", About.segment(seg.id)))
        elif len(seg.values) > 1:
            found.append(_warn("segment-partly-represented", About.segment(seg.id)))
    plan.diagnostics[:] = list(dict.fromkeys(found))

    # The background value is named even where it has no item (an implicit
    # background that does not occur in the data), so that the layer comes
    # back with its background.
    plan.pixel_padding_value = background_value(ext, 0)
    plan.frames = data.astype(np.uint8 if max(values) <= 255 else np.uint16)
    plan.segments_overlap = "NO"


def resolve_item_content(
    item: SegmentItem,
    *,
    default_dicom: Any = None,
    algorithm_type: str | None = None,
    algorithm_name: str | None = None,
) -> DicomContent:
    """The content DICOM requires of a segment item: each field from the
    segment's ``dicom``, else from the caller's ``default_dicom`` (and
    ``algorithm_type`` / ``algorithm_name``). Fails rather than invent one
    (§4.2)."""
    seg = item.segment
    own = seg.dicom.model_dump(exclude_none=True) if seg.dicom is not None else {}
    if default_dicom is not None and not isinstance(default_dicom, DicomContent):
        default_dicom = DicomContent.model_validate(default_dicom)
    defaults = default_dicom.model_dump(exclude_none=True) if default_dicom is not None else {}
    if algorithm_type is not None:
        defaults["algorithm_type"] = algorithm_type
    if algorithm_name is not None:
        defaults["algorithm_name"] = algorithm_name
    if "type" in own:
        # a modifier or region belongs to the type it was written with
        for dependent in ("type_modifier", "anatomic_region", "anatomic_region_modifier"):
            defaults.pop(dependent, None)
    content = DicomContent.model_validate({**defaults, **own})

    if content.algorithm_type is None:
        raise ValueError(
            f"segment {seg.id!r} has no dicom.algorithm_type and none was given; DICOM "
            "requires SegmentAlgorithmType, and it is not invented")
    if content.algorithm_type not in ALGORITHM_TYPES:
        raise ValueError(
            f"segment {seg.id!r}: SegmentAlgorithmType {content.algorithm_type!r} is not DICOM's")
    if content.algorithm_type != "MANUAL" and not content.algorithm_name:
        raise ValueError(
            f"segment {seg.id!r}: SegmentAlgorithmName is required unless the type is MANUAL")
    if content.category is None or content.type is None:
        raise ValueError(
            f"segment {seg.id!r} has no dicom.category or dicom.type and none was given; "
            "DICOM requires both")
    return content


def code_meaning(entry: CodedEntry, seg: Segment, field_name: str) -> str:
    # CodeMeaning is Type 1. It is a property of the *code*, so the only
    # honest fallback is the segment's own name — never its id, which would
    # publish an identifier as if it were the concept's meaning.
    meaning = entry.meaning or seg.name
    if not meaning:
        raise ValueError(
            f"segment {seg.id!r}: dicom.{field_name} ({entry.scheme}:{entry.code}) "
            "has no 'meaning' and the segment has no 'name'; DICOM requires "
            "CodeMeaning for every coded entry")
    return meaning


__all__ = [
    "DICOM_D65_MARKER",
    "DicomSegPlan",
    "SegmentItem",
    "resolve_item_content",
    "code_meaning",
    "extract_seg_extension",
    "fractional_slope",
    "plan_dicom_seg",
    "seg_fill_value",
    "seg_import_layout",
]
