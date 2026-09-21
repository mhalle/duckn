"""The seg extension, version 0.8: models, consistency rules, and lookups.

A segment lists the voxel values that belong to it; values may be shared
between segments of a layer, and the *topmost* segment listing a value — the
last in ``segments`` order — answers for it (spec §2). Validation returns
diagnostics with the codes of the spec's §10 and does not raise; the rules
whose violation makes a reader refuse a file are named in ``REFUSAL_CODES``.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .diagnostics import About, Diagnostic
from .models import AxisMetadata, CodedEntry, Designation
from .seg_color import SegColor, format_color, parse_color, to_rgb8

# Version of the seg extension spec this module reads and writes.
SEG_VERSION = "0.8"

MAX_LABEL_MAGNITUDE = 2**53 - 1

ALGORITHM_TYPES = ("AUTOMATIC", "SEMIAUTOMATIC", "MANUAL")

# Rules a reader refuses a file over (§5); the rest it reports and continues.
REFUSAL_CODES = frozenset(
    {"rule-1", "rule-2", "rule-4a", "rule-8a", "rule-11a", "rule-13", "rule-14", "rule-17"}
)

_VERSION_RE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+\Z")


class TerminologyEntry(BaseModel):
    """Entry in the terminologies registry (§3.1)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    # What identifies the coding system across files; compared byte for byte.
    uri: str | None = None
    version: str | None = None
    url: str | None = None
    # Concept URL template; "{code}" is replaced with a coded entry's code.
    url_template: str | None = None


class DicomContent(BaseModel):
    """DICOM SEG per-segment content that has no other field (§4.2)."""

    model_config = ConfigDict(extra="forbid")

    category: CodedEntry | None = None
    type: CodedEntry | None = None
    type_modifier: CodedEntry | None = None
    anatomic_region: CodedEntry | None = None
    anatomic_region_modifier: CodedEntry | None = None
    # Carried as found; rule 8b reports a value DICOM does not define.
    algorithm_type: str | None = None
    algorithm_name: str | None = None

    def coded_entries(self) -> list[CodedEntry]:
        entries = (
            self.category,
            self.type,
            self.type_modifier,
            self.anatomic_region,
            self.anatomic_region_modifier,
        )
        return [e for e in entries if e is not None]


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    label_values: Annotated[list[int], Field(min_length=1)]
    role: Literal["background", "unknown"] | None = None
    layer: Annotated[int, Field(ge=0)] | None = None
    # [min_i, max_i, min_j, max_j, min_k, max_k], inclusive; a cached index
    extent: Annotated[list[int], Field(min_length=6, max_length=6)] | None = None
    # A CSS color string in one of four forms (§3.2); see seg_color
    color: str | None = None
    designations: list[Designation] | None = None
    dicom: DicomContent | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("label_values", mode="before")
    @classmethod
    def _integers_only(cls, v: Any) -> Any:
        # Rule 11a. pydantic would coerce True -> 1 and 2.0 -> 2; a numpy bool
        # is not a Python bool but is the realistic way one arrives here.
        if not isinstance(v, (list, tuple)):
            raise ValueError("label_values must be an array, even for one value")
        out = []
        for e in v:
            kind = getattr(getattr(e, "dtype", None), "kind", None)
            if isinstance(e, bool) or kind == "b":
                raise ValueError("label_values entries must be integers, not booleans")
            if not isinstance(e, int) and kind not in ("i", "u"):
                raise ValueError(f"label_values entries must be integers, not {e!r}")
            e = int(e)
            if abs(e) > MAX_LABEL_MAGNITUDE:
                raise ValueError(f"label value {e} exceeds 2^53 - 1 in magnitude")
            out.append(e)
        return out

    @property
    def effective_layer(self) -> int:
        return self.layer or 0

    @property
    def values(self) -> frozenset[int]:
        """``label_values`` read as a set (rule 11b)."""
        return frozenset(self.label_values)

    @property
    def effective_value_set(self) -> frozenset[tuple[int, int]]:
        """The ``(layer, value)`` pairs this segment lists (§3.2)."""
        layer = self.effective_layer
        return frozenset((layer, v) for v in self.label_values)


class SegmentationExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    # Unrecognized values may appear; readers do not fail on one (§3.1)
    source_representation: str | None = None
    labeling_scheme: str | list[str] | None = None
    implicit_background: bool | None = None
    terminologies: dict[str, TerminologyEntry] | None = None
    segments: list[Segment]
    metadata: dict[str, Any] | None = None
    legacy: dict[str, Any] | None = None

    @property
    def labeling_schemes(self) -> list[str]:
        """The declared scheme keys, repeats ignored (rule 3a)."""
        raw = self.labeling_scheme
        keys = [raw] if isinstance(raw, str) else list(raw or [])
        return list(dict.fromkeys(keys))


# ---------------------------------------------------------------------------
# Reading a segmentation
# ---------------------------------------------------------------------------


def _is_float_dtype(dtype: Any) -> bool:
    import numpy as np

    return dtype is not None and np.dtype(dtype).kind == "f"


def is_fractional(ext: SegmentationExtension, dtype: Any = None) -> bool:
    """Whether the rules for fractional labelmaps apply (§3.1): the declared
    representation says so, or none is declared and the data is floating point."""
    if ext.source_representation is not None:
        return ext.source_representation == "fractional-labelmap"
    return _is_float_dtype(dtype)


def layers_of(ext: SegmentationExtension) -> list[int]:
    """The layers that have a segment, ascending."""
    return sorted({seg.effective_layer for seg in ext.segments})


def _in_layer(ext: SegmentationExtension, layer: int) -> list[Segment]:
    return [seg for seg in ext.segments if seg.effective_layer == layer]


def background_segment(ext: SegmentationExtension, layer: int = 0) -> Segment | None:
    for seg in _in_layer(ext, layer):
        if seg.role == "background":
            return seg
    return None


def background_value(ext: SegmentationExtension, layer: int = 0) -> int | None:
    """The background value of ``layer`` in a binary labelmap, or None when the
    layer has no background (§3.2 ``role``)."""
    seg = background_segment(ext, layer)
    if seg is not None:
        return seg.label_values[0]
    return None if ext.implicit_background is False else 0


def segments_for(
    ext: SegmentationExtension, value: int, *, layer: int = 0
) -> list[Segment]:
    """Every segment of ``layer`` listing ``value``, in ``segments`` order."""
    return [seg for seg in _in_layer(ext, layer) if value in seg.label_values]


def topmost_for(
    ext: SegmentationExtension, value: int, *, layer: int = 0
) -> Segment | None:
    """The topmost segment for ``(layer, value)``: the last listing it (§2)."""
    found = segments_for(ext, value, layer=layer)
    return found[-1] if found else None


def _scheme_identity(ext: SegmentationExtension, key: str) -> tuple[str, str]:
    entry = (ext.terminologies or {}).get(key)
    if entry is not None and entry.uri is not None:
        return ("uri", entry.uri)
    return ("key", key)


def designation_identity(ext: SegmentationExtension, d: Designation) -> tuple:
    """A key under which two designations are equal exactly when they are *the
    same* (§4.1): same coding system, same code, same modifier. Two keys that
    are equal name one registration, so comparing uri-or-key is the spec's test."""
    modifier = designation_identity(ext, d.modifier) if d.modifier is not None else None
    return (_scheme_identity(ext, d.scheme), d.code, modifier)


def segments_by_designation(
    ext: SegmentationExtension,
    scheme: str,
    code: str,
    *,
    modifier: Designation | None = None,
) -> list[Segment]:
    """Lookup by a portable key (§7.1): at most one segment per layer, the
    topmost carrying the code in that coding system at any position. Without
    ``modifier`` any modifier matches; with one, only an equal modifier."""
    system = _scheme_identity(ext, scheme)
    wanted = designation_identity(ext, modifier) if modifier is not None else None
    by_layer: dict[int, Segment] = {}
    for seg in ext.segments:
        for d in seg.designations or []:
            if _scheme_identity(ext, d.scheme) != system or d.code != code:
                continue
            if wanted is not None and (
                d.modifier is None or designation_identity(ext, d.modifier) != wanted
            ):
                continue
            by_layer[seg.effective_layer] = seg
            break
    return [by_layer[layer] for layer in sorted(by_layer)]


def color_map(ext: SegmentationExtension, *, layer: int = 0) -> dict[int, str]:
    """``{value: CSS color}`` for ``layer``: each value takes the color of the
    topmost segment listing it that has a readable one (§3.2). Values that
    resolve to no color are omitted, and a viewer chooses."""
    colors: dict[int, str] = {}
    for seg in _in_layer(ext, layer):
        if seg.color is None or parse_color(seg.color) is None:
            continue
        for v in seg.label_values:
            colors[v] = seg.color
    return colors


def rgb8_color_table(
    ext: SegmentationExtension, *, layer: int = 0
) -> tuple[dict[int, tuple[int, int, int]], list[Diagnostic]]:
    """:func:`color_map` as 8-bit sRGB, colors outside the gamut mapped into it
    and reported."""
    parsed: dict[int, tuple[Segment, SegColor]] = {}
    for seg in _in_layer(ext, layer):
        color = parse_color(seg.color) if seg.color is not None else None
        if color is None:
            continue
        for v in seg.label_values:
            parsed[v] = (seg, color)
    table: dict[int, tuple[int, int, int]] = {}
    diagnostics: list[Diagnostic] = []
    for v, (seg, color) in parsed.items():
        rgb, clamped, mapped = to_rgb8(color)
        table[v] = rgb
        if mapped:
            diagnostics.append(_warn("color-gamut-mapped", About.segment(seg.id)))
        if clamped:
            diagnostics.append(_warn("color-clamped", About.segment(seg.id)))
    return table, _distinct(diagnostics)


# --- three properties a reader checks (§5) ----------------------------------


def is_label_table(ext: SegmentationExtension, layer: int = 0) -> bool:
    """Every segment of the layer lists one value, and no value is shared."""
    seen: set[int] = set()
    for seg in _in_layer(ext, layer):
        if len(seg.values) != 1 or seg.values & seen:
            return False
        seen |= seg.values
    return True


def provably_disjoint(a: Segment, b: Segment) -> bool:
    """Metadata proves two segments share no voxel: same layer, no common value."""
    return a.effective_layer == b.effective_layer and not (a.values & b.values)


def is_nested(ext: SegmentationExtension, layer: int = 0) -> bool:
    """For every listed value, the segments listing it, in order, each contain
    the next. The last is then the innermost segment for the value."""
    segs = _in_layer(ext, layer)
    for v in set().union(*(s.values for s in segs)) if segs else ():
        chain = [s for s in segs if v in s.values]
        if any(not a.values >= b.values for a, b in zip(chain, chain[1:])):
            return False
    return True


# ---------------------------------------------------------------------------
# Consistency rules (§5)
# ---------------------------------------------------------------------------


def _err(code: str, about: About, message: str = "") -> Diagnostic:
    return Diagnostic(code, "error", about, message)


def _warn(code: str, about: About, message: str = "") -> Diagnostic:
    return Diagnostic(code, "warning", about, message)


def _distinct(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    """One diagnostic per (code, about), the first kept."""
    seen: set[tuple[str, About]] = set()
    out = []
    for d in diagnostics:
        if (d.code, d.about) not in seen:
            seen.add((d.code, d.about))
            out.append(d)
    return out


def refusals(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    """The diagnostics over which a reader refuses the file."""
    return [d for d in diagnostics if d.code in REFUSAL_CODES]


def version_tuple(version: Any) -> tuple[int, int] | None:
    """``(major, minor)`` of a well-formed version string, else None (rule 1)."""
    if not isinstance(version, str):
        return None
    m = _VERSION_RE.match(version)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _used_schemes(ext: SegmentationExtension) -> list[str]:
    keys: list[str] = []
    for seg in ext.segments:
        for d in seg.designations or []:
            keys.append(d.scheme)
            if d.modifier is not None:
                keys.append(d.modifier.scheme)
        if seg.dicom is not None:
            keys.extend(e.scheme for e in seg.dicom.coded_entries())
    return list(dict.fromkeys(keys))


def validate_seg_extension(
    ext: SegmentationExtension,
    *,
    axes: Sequence[AxisMetadata] | None = None,
    shape: Sequence[int] | None = None,
    dtype: Any = None,
    fill_value: Any = None,
) -> list[Diagnostic]:
    """Check a segmentation against the consistency rules of spec §5.

    Returns diagnostics and never raises. The metadata-only rules are always
    checked. Rule 2 and the list-axis part of rule 17 need ``axes`` (and
    ``shape`` for the range of ``layer``); rule 11a's range check and the
    binary/fractional decision need ``dtype``; rule 15 needs ``fill_value``.
    What is not given is not checked. Rule 16 needs the voxels: see
    :func:`validate_seg_data`. Rule 10 cannot be checked.

    Use :func:`refusals` for what a reader refuses the file over, and
    ``diagnostics.raise_on`` for what a writer must not write.
    """
    import numpy as np

    out: list[Diagnostic] = []
    the_ext = About.extension()

    def about(index: int, seg: Segment) -> About:
        return About.segment(seg.id)

    # Rule 1
    version = version_tuple(ext.version)
    if version is None:
        out.append(_err("rule-1", the_ext, f"version {ext.version!r} is not N.N"))
    elif version > version_tuple(SEG_VERSION):  # type: ignore[operator]
        out.append(_err("rule-1", the_ext, f"version {ext.version} is later than {SEG_VERSION}"))

    fractional = is_fractional(ext, dtype)

    # Rule 3
    raw_schemes = ext.labeling_scheme if isinstance(ext.labeling_scheme, list) else []
    if len(set(raw_schemes)) != len(raw_schemes):
        out.append(_err("rule-3a", the_ext, "labeling_scheme repeats a key"))
    if ext.implicit_background is True:
        out.append(_err("rule-3b", the_ext, "implicit_background is never written true"))
    if ext.implicit_background is not None and fractional:
        out.append(_err("rule-3c", the_ext, "implicit_background on a fractional labelmap"))

    # Rule 2
    list_axes = None
    if axes is not None:
        list_axes = [
            i for i, ax in enumerate(axes) if ax.kind is not None and ax.kind.value == "list"
        ]
        for i, seg in enumerate(ext.segments):
            if seg.layer is None:
                continue
            if not list_axes:
                out.append(_err("rule-2", about(i, seg), "layer given, but no 'list' axis"))
            elif shape is not None and not 0 <= seg.layer < shape[list_axes[0]]:
                out.append(
                    _err("rule-2", about(i, seg),
                         f"layer {seg.layer} is out of range for a 'list' axis of "
                         f"size {shape[list_axes[0]]}")
                )

    # Rule 4
    seen_ids: set[str] = set()
    for i, seg in enumerate(ext.segments):
        if seg.id in seen_ids:
            out.append(_err("rule-4a", About.segment(seg.id, i), "id is repeated"))
        seen_ids.add(seg.id)
        if not _TOKEN_RE.match(seg.id):
            out.append(_err("rule-4b", about(i, seg), f"id {seg.id!r} is not a token"))

    # Rule 5
    registry = ext.terminologies or {}
    for key in _used_schemes(ext):
        if key not in registry:
            out.append(_err("rule-5", About.scheme(key), "scheme is not registered"))
    declared = ext.labeling_schemes
    for key in declared:
        entry = registry.get(key)
        if entry is None or entry.uri is None or entry.version is None:
            out.append(
                _err("rule-5", About.scheme(key),
                     "a labeling scheme is registered with a uri and a version")
            )

    # Rules 6 and 7
    if declared:
        systems = {_scheme_identity(ext, key) for key in declared}
        for i, seg in enumerate(ext.segments):
            counts: dict[tuple[str, str], int] = {}
            for d in seg.designations or []:
                system = _scheme_identity(ext, d.scheme)
                if system in systems:
                    counts[system] = counts.get(system, 0) + 1
            if any(n > 1 for n in counts.values()):
                out.append(
                    _err("rule-6", about(i, seg),
                         "more than one designation in a declared labeling scheme")
                )
            if seg.role is None and not counts:
                out.append(
                    _warn("rule-7", about(i, seg),
                          "no designation in a declared labeling scheme")
                )

    # Rule 8 (8a is also enforced by the model; checked for instances built around it)
    for i, seg in enumerate(ext.segments):
        if seg.role not in (None, "background", "unknown"):
            out.append(_err("rule-8a", about(i, seg), f"role {seg.role!r}"))
        if seg.dicom is not None and seg.dicom.algorithm_type not in (None, *ALGORITHM_TYPES):
            out.append(
                _err("rule-8b", about(i, seg),
                     f"dicom.algorithm_type {seg.dicom.algorithm_type!r}")
            )

    # Rule 9
    carried: set[tuple[int, tuple]] = set()
    for i, seg in enumerate(ext.segments):
        mine = {designation_identity(ext, d) for d in seg.designations or []}
        if any((seg.effective_layer, key) in carried for key in mine):
            out.append(
                _err("rule-9", about(i, seg),
                     "carries a designation an earlier segment of the layer carries")
            )
        carried |= {(seg.effective_layer, key) for key in mine}

    # Rule 11
    info = None
    if dtype is not None and np.dtype(dtype).kind in "iu":
        info = np.iinfo(np.dtype(dtype))
    for i, seg in enumerate(ext.segments):
        values = seg.label_values
        bad = (
            not values
            or any(isinstance(v, bool) or not isinstance(v, int) for v in values)
            or any(abs(v) > MAX_LABEL_MAGNITUDE for v in values)
            or (info is not None and not fractional
                and any(not info.min <= v <= info.max for v in values))
        )
        if bad:
            out.append(
                _err("rule-11a", about(i, seg),
                     "label_values is a non-empty array of integers the data type holds")
            )
        elif list(values) != sorted(set(values)):
            out.append(
                _err("rule-11b", about(i, seg), "label_values is distinct and ascending")
            )

    # Rule 12
    for layer in layers_of(ext):
        segs = _in_layer(ext, layer)
        for j, outer in enumerate(segs):
            if any(outer.values > inner.values for inner in segs[:j]):
                out.append(
                    _err("rule-12", About.segment(outer.id),
                         "comes after a segment whose values it strictly contains")
                )

    if fractional:
        # Rule 17
        if list_axes is not None and not list_axes:
            out.append(_err("rule-17", the_ext, "a fractional labelmap has a 'list' axis"))
        used_layers: set[int] = set()
        for i, seg in enumerate(ext.segments):
            if seg.effective_layer in used_layers:
                out.append(
                    _err("rule-17", about(i, seg),
                         f"layer {seg.effective_layer} already holds a segment")
                )
            elif list(seg.label_values) != [1]:
                out.append(_err("rule-17", about(i, seg), "label_values is [1]"))
            used_layers.add(seg.effective_layer)
    else:
        for layer in layers_of(ext):
            segs = _in_layer(ext, layer)

            # Rule 13
            backgrounds = [s for s in segs if s.role == "background"]
            for extra in backgrounds[1:]:
                out.append(
                    _err("rule-13", About.segment(extra.id),
                         f"layer {layer} already has a background segment")
                )
            for bg in backgrounds[:1]:
                if len(bg.values) != 1:
                    out.append(
                        _err("rule-13", About.segment(bg.id),
                             "a background segment lists exactly one value")
                    )

            # Rule 14
            bg_value = background_value(ext, layer)
            bg_seg = backgrounds[0] if backgrounds else None
            for seg in segs:
                if seg is not bg_seg and bg_value in seg.values:
                    out.append(
                        _err("rule-14", About.value(layer, bg_value),  # type: ignore[arg-type]
                             "the background value is listed by another segment")
                    )
            for seg in segs:
                if seg.role is None:
                    continue
                for other in segs:
                    if other is seg:
                        continue
                    for v in sorted(seg.values & other.values):
                        out.append(
                            _err("rule-14", About.value(layer, v),
                                 "a value listed under a role is listed by no other segment")
                        )

            # Rule 15
            if fill_value is not None:
                described = set().union(*(s.values for s in segs))
                try:
                    fill = float(fill_value)
                except (TypeError, ValueError):
                    fill = float("nan")
                if not (fill == bg_value or fill in described):
                    shown = int(fill) if fill == fill and fill.is_integer() else fill_value
                    out.append(
                        _err("rule-15", About.value(layer, shown),
                             "the array's fill_value is described by nothing in the layer")
                    )

    # Reader diagnostics that are not rule violations
    for i, seg in enumerate(ext.segments):
        if seg.color is not None and parse_color(seg.color) is None:
            out.append(
                _warn("color-unreadable", about(i, seg),
                      f"color {seg.color!r} is not one of the four forms")
            )

    return _distinct(out)


def validate_seg_data(
    ext: SegmentationExtension,
    data: Any,
    *,
    list_axis: int | None = None,
    layer: int | None = None,
) -> list[Diagnostic]:
    """Rule 16, against the voxels of a binary labelmap: every value present in
    a layer is its background value or is listed by some segment.

    ``data`` is the label array; ``list_axis`` is the index of its ``list``
    axis (None for a single layer). Pass ``layer`` when ``data`` is one
    layer's volume held separately. Returns one ``rule-16`` diagnostic per
    undescribed ``(layer, value)``.
    """
    import numpy as np

    arr = np.asarray(data)
    if is_fractional(ext, arr.dtype):
        return []
    if layer is not None:
        slices = [(layer, arr)]
    elif list_axis is None:
        if any(seg.effective_layer for seg in ext.segments):
            raise ValueError(
                "the segmentation declares layers beyond 0 but neither list_axis nor "
                "layer was given; a 4-D array checked as one layer would report "
                "false violations"
            )
        slices = [(0, arr)]
    else:
        slices = [(i, np.take(arr, i, axis=list_axis)) for i in range(arr.shape[list_axis])]

    out: list[Diagnostic] = []
    for lay, voxels in slices:
        described: set[Any] = set().union(*(s.values for s in _in_layer(ext, lay)))
        described.add(background_value(ext, lay))
        for v in np.unique(voxels).tolist():
            if v not in described:
                shown = int(v) if float(v).is_integer() else v
                out.append(
                    _err("rule-16", About.value(lay, shown),
                         "present in the data and described by nothing")
                )
    return out


def undescribed_values(
    ext: SegmentationExtension, data: Any, *, list_axis: int | None = None,
    layer: int | None = None,
) -> set[tuple[int, Any]]:
    """The ``(layer, value)`` pairs a reader excludes from comparison and
    measurement because nothing describes them (§2)."""
    found = validate_seg_data(ext, data, list_axis=list_axis, layer=layer)
    return {d.about.key for d in found}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def normalized_for_writing(
    ext: SegmentationExtension,
) -> tuple[SegmentationExtension, list[Diagnostic]]:
    """A copy spelled as a writer spells it: ``label_values`` distinct and
    ascending, ``layer: 0`` and ``implicit_background: true`` omitted, empty
    collections omitted (§4.4), colors in their canonical spelling. An
    unreadable color is dropped. Order and ids are the caller's business."""
    out = ext.model_copy(deep=True)
    out.version = SEG_VERSION
    diagnostics: list[Diagnostic] = []
    if out.implicit_background is True:
        out.implicit_background = None
    if isinstance(out.labeling_scheme, list):
        keys = out.labeling_schemes
        out.labeling_scheme = keys[0] if len(keys) == 1 else (keys or None)
    if not out.terminologies:
        out.terminologies = None
    for seg in out.segments:
        seg.label_values = sorted(set(seg.label_values))
        if seg.layer == 0:
            seg.layer = None
        if not seg.designations:
            seg.designations = None
        if seg.color is not None:
            color = parse_color(seg.color)
            if color is None:
                diagnostics.append(_warn("color-unreadable", About.segment(seg.id)))
                seg.color = None
            else:
                seg.color, clamped = format_color(color)
                if clamped:
                    diagnostics.append(_warn("color-clamped", About.segment(seg.id)))
    return out, diagnostics
