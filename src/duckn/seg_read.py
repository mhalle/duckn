"""Reading a seg extension: migration of older files (spec §6.3), then the
model, then the consistency rules.

Migration works on the raw dict and returns diagnostics beside it, which is
why it does not live inside a model validator: a validator has nowhere to put
them.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Sequence

from pydantic import ValidationError

from .diagnostics import About, Diagnostic, DiagnosticsError
from .models import AxisMetadata, _migrate_extension_pre_0_6
from .seg_color import format_color, from_slicer_floats
from .seg_model import (
    SEG_VERSION,
    SegmentationExtension,
    derive_token_ids,
    refusals,
    validate_seg_extension,
    version_tuple,
)

# Older versions were read leniently ("v0.7", "0.7.1"); 0.8 is strict (rule 1).
_OLD_VERSION_RE = re.compile(r"\s*v?(\d+)\.(\d+)")


def _warn(code: str, about: About, message: str = "") -> Diagnostic:
    return Diagnostic(code, "warning", about, message)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _duckn_metadata(obj: dict[str, Any]) -> dict[str, Any]:
    metadata = obj["metadata"] = dict(obj.get("metadata") or {})
    duckn = metadata["duckn"] = dict(metadata.get("duckn") or {})
    return duckn


def _layer(seg: dict[str, Any]) -> int:
    layer = seg.get("layer")
    return layer if _is_int(layer) else 0


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_seg_extension(raw: dict[str, Any]) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Migrate a 0.5, 0.6, or 0.7 seg extension dict to 0.8 (spec §6.3).

    Returns the migrated dict and what the migration changed. A dict that
    declares 0.8 or later, or whose version cannot be read, is returned as it
    is: the rules decide what to make of it. The input is not modified.
    """
    version = raw.get("version") if isinstance(raw, dict) else None
    m = _OLD_VERSION_RE.match(version) if isinstance(version, str) else None
    if m is None or (int(m.group(1)), int(m.group(2))) >= version_tuple(SEG_VERSION):
        return raw, []
    if not isinstance(raw.get("segments"), list) or not all(
        isinstance(s, dict) for s in raw["segments"]
    ):
        return raw, []  # malformed; the model refuses it

    out: list[Diagnostic] = []
    originals = deepcopy(raw["segments"])

    # 1. Pre-0.6 shapes.
    ext = deepcopy(raw)
    if (int(m.group(1)), int(m.group(2))) < (0, 6):
        ext = _migrate_extension_pre_0_6(ext)
    ext["version"] = SEG_VERSION
    segs: list[dict[str, Any]] = ext["segments"]

    old_colors = _colors_as_0_7_resolved_them(segs)

    # 2 and 3. Simple fields; groups flattened against the original ids.
    graph = _Graph(segs)
    kept: list[dict[str, Any]] = []
    for index, seg in enumerate(segs):
        sid = str(seg.get("id"))
        is_group = "members" in seg or (
            isinstance(seg.get("label_value"), list)
            and any(isinstance(e, str) for e in seg["label_value"])
        )
        if is_group:
            flat = graph.flatten(index)
            if flat is None:
                _duckn_metadata(ext).setdefault("omitted", []).append(originals[index])
                out.append(_warn("migrated-group-omitted", About.omitted(sid)))
                continue
            layer, values, subtracted = flat
            for member_id in subtracted:
                out.append(
                    _warn("migrated-background-subtracted", About.segment(sid),
                          f"values of role-bearing member {member_id!r} were removed")
                )
            seg["label_values"] = values
            seg["layer"] = layer
            seg.pop("extent", None)
        else:
            lv = seg.get("label_value")
            if _is_int(lv):
                seg["label_values"] = [lv]
            elif isinstance(lv, list) and lv and all(_is_int(e) for e in lv):
                seg["label_values"] = sorted(set(lv))
            elif "label_value" in seg:
                seg["label_values"] = lv  # not a shape any version had; the model refuses it
        seg.pop("label_value", None)
        seg.pop("members", None)
        for claim in ("disjoint", "exhaustive"):
            if seg.pop(claim, None):
                out.append(_warn("migrated-claim-dropped", About.segment(sid), claim))
        if seg.pop("background", None):
            seg["role"] = "background"
            out.append(_warn("migrated-background", About.segment(sid)))
        if seg.get("layer") == 0:
            del seg["layer"]
        _migrate_color(seg)
        display = seg.pop("display", None)
        if display:
            _duckn_metadata(seg)["display"] = display
        _migrate_algorithm(seg)
        kept.append(seg)
    ext["segments"] = segs = kept

    # 4. Designation collisions.
    out.extend(_set_aside_colliding_designations(segs))

    # 5. Ids are made tokens. Reported against the new id, the one the file has.
    # A segment with no id, or one that is not a string, is the model's to refuse.
    named = [s for s in segs if isinstance(s.get("id"), str)]
    new_ids = derive_token_ids([s["id"] for s in named])
    for seg, new_id in zip(named, new_ids):
        if new_id != seg["id"]:
            _duckn_metadata(seg)["id"] = seg.get("id")
            out.append(_warn("id-changed", About.segment(new_id), f"was {seg.get('id')!r}"))
            out = [_renamed(d, str(seg.get("id")), new_id) for d in out]
            seg["id"] = new_id

    # 6. Order, layer by layer.
    ext["segments"] = segs = _order_by_containment(segs)

    new_colors: dict[tuple[int, int], str] = {}
    for seg in segs:
        if isinstance(seg.get("color"), str) and isinstance(seg.get("label_values"), list):
            for v in seg["label_values"]:
                new_colors[(_layer(seg), v)] = seg["color"]
    for key in sorted(set(old_colors) | set(new_colors)):
        if old_colors.get(key) != new_colors.get(key):
            out.append(_warn("migrated-color-differs", About.value(*key)))

    return ext, out


def _renamed(d: Diagnostic, old: str, new: str) -> Diagnostic:
    if d.about == About.segment(old):
        return Diagnostic(d.code, d.severity, About.segment(new), d.message)
    return d


def _css_from_floats(color: Any) -> str | None:
    if (
        isinstance(color, (list, tuple))
        and len(color) == 3
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in color)
    ):
        return format_color(from_slicer_floats(color))[0]
    return None


def _migrate_color(seg: dict[str, Any]) -> None:
    css = _css_from_floats(seg.get("color"))
    if css is not None:
        seg["color"] = css


def _migrate_algorithm(seg: dict[str, Any]) -> None:
    kept = (seg.get("metadata") or {}).get("dicom")
    if not isinstance(kept, dict):
        return
    moves = {"SegmentAlgorithmType": "algorithm_type", "SegmentAlgorithmName": "algorithm_name"}
    if not any(k in kept for k in moves):
        return
    kept = dict(kept)
    dicom = dict(seg.get("dicom") or {})
    for keyword, field in moves.items():
        if keyword in kept:
            dicom.setdefault(field, kept.pop(keyword))
    seg["dicom"] = dicom
    seg["metadata"] = dict(seg["metadata"])
    if kept:
        seg["metadata"]["dicom"] = kept
    else:
        del seg["metadata"]["dicom"]
        if not seg["metadata"]:
            del seg["metadata"]


class _Graph:
    """The membership graph of an older file, over its original ids."""

    def __init__(self, segs: list[dict[str, Any]]):
        self.segs = segs
        self.by_id: dict[Any, int] = {}
        for i, seg in enumerate(segs):
            self.by_id.setdefault(seg.get("id"), i)
        # Snapshot what resolution reads, since the segments are rewritten in place.
        self.own: list[set[tuple[int, int]]] = []
        self.members: list[list[str]] = []
        self.has_role: list[bool] = []
        for seg in segs:
            lv = seg.get("label_value")
            entries = lv if isinstance(lv, list) else [lv]
            self.own.append({(_layer(seg), e) for e in entries if _is_int(e)})
            refs = [e for e in entries if isinstance(e, str)]
            refs += [r for r in seg.get("members") or [] if isinstance(r, str)]
            self.members.append(refs)
            self.has_role.append(bool(seg.get("background")))

    def _closure(self, index: int) -> list[int] | None:
        """Indices of the transitive membership, or None on a cycle or a
        member that does not resolve."""
        order: list[int] = []
        state: dict[int, int] = {}  # 1 = on the path, 2 = done

        def visit(i: int) -> bool:
            if state.get(i) == 2:
                return True
            if state.get(i) == 1:
                return False
            state[i] = 1
            for ref in self.members[i]:
                j = self.by_id.get(ref)
                if j is None or not visit(j):
                    return False
            state[i] = 2
            order.append(i)
            return True

        return order if visit(index) else None

    def flatten(self, index: int) -> tuple[int, list[int], list[str]] | None:
        """``(layer, label_values, ids of role-bearing members subtracted)``,
        or None when the group has no 0.8 form."""
        closure = self._closure(index)
        if closure is None:
            return None
        pairs = set().union(*(self.own[i] for i in closure))
        if len({layer for layer, _ in pairs}) != 1:
            return None
        subtracted = []
        for i in sorted(closure):
            if i != index and self.has_role[i] and self.own[i] & pairs:
                pairs -= self.own[i]
                subtracted.append(str(self.segs[i].get("id")))
        if not pairs:
            return None
        return next(iter(pairs))[0], sorted(v for _, v in pairs), subtracted


def _colors_as_0_7_resolved_them(segs: list[dict[str, Any]]) -> dict[tuple[int, int], str]:
    """``{(layer, value): color}`` under 0.7's rule: a leaf's own color, else
    that of the first group in document order that contains the value."""
    graph = _Graph(segs)
    colors: dict[tuple[int, int], str] = {}
    leaf_seen: set[tuple[int, int]] = set()
    for i, seg in enumerate(segs):  # leaves: one integer
        if graph.members[i] or len(graph.own[i]) != 1:
            continue
        (pair,) = graph.own[i]
        if pair in leaf_seen:
            continue
        leaf_seen.add(pair)
        css = _css_from_floats(seg.get("color"))
        if css is not None:
            colors[pair] = css
    uncolored_leaves = leaf_seen - set(colors)
    for i, seg in enumerate(segs):  # groups, first in document order wins
        if not graph.members[i] and len(graph.own[i]) == 1:
            continue
        css = _css_from_floats(seg.get("color"))
        closure = graph._closure(i)
        if css is None or closure is None:
            continue
        for pair in set().union(*(graph.own[j] for j in closure)):
            if pair in uncolored_leaves or pair not in leaf_seen:
                colors.setdefault(pair, css)
    return colors


def _designation_key(d: Any) -> tuple | None:
    if not isinstance(d, dict):
        return None
    modifier = d.get("modifier")
    return (d.get("scheme"), d.get("code"), _designation_key(modifier) if modifier else None)


def _set_aside_colliding_designations(segs: list[dict[str, Any]]) -> list[Diagnostic]:
    """For each designation carried by several segments of a layer, the one
    with the most values keeps it, the first among equals (§6.3 step 4).
    Older files have no ``uri``, so a scheme is its key."""
    out: list[Diagnostic] = []
    carriers: dict[tuple, list[dict[str, Any]]] = {}
    for seg in segs:
        keys = {_designation_key(d) for d in seg.get("designations") or []} - {None}
        for key in keys:
            carriers.setdefault((_layer(seg), key), []).append(seg)
    for (_, key), group in carriers.items():
        if len(group) < 2:
            continue
        keeper = max(group, key=lambda s: len(s.get("label_values") or []))  # first max
        for seg in group:
            if seg is keeper:
                continue
            moved = [d for d in seg["designations"] if _designation_key(d) == key]
            seg["designations"] = [d for d in seg["designations"] if _designation_key(d) != key]
            if not seg["designations"]:
                del seg["designations"]
            _duckn_metadata(seg).setdefault("designations", []).extend(moved)
            out.append(_warn("designation-set-aside", About.segment(str(seg.get("id")))))
    return out


def _order_by_containment(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Establish rule 12 (§6.3 step 6): each layer's segments are re-dealt into
    the positions the layer occupies, repeatedly taking the first not yet
    placed whose values no other unplaced segment strictly contains."""
    result = list(segs)
    positions: dict[int, list[int]] = {}
    for i, seg in enumerate(segs):
        positions.setdefault(_layer(seg), []).append(i)
    for slots in positions.values():
        pending = [segs[i] for i in slots]
        values = {id(s): set(s["label_values"]) if isinstance(s.get("label_values"), list)
                  and all(_is_int(v) for v in s["label_values"]) else set() for s in pending}
        for slot in slots:
            chosen = next(
                s for s in pending
                if not any(values[id(o)] > values[id(s)] for o in pending if o is not s)
            )
            pending.remove(chosen)
            result[slot] = chosen
    return result


# ---------------------------------------------------------------------------
# The reader's entry point
# ---------------------------------------------------------------------------


def read_seg_extension(
    raw: dict[str, Any],
    *,
    axes: Sequence[AxisMetadata] | None = None,
    shape: Sequence[int] | None = None,
    dtype: Any = None,
    fill_value: Any = None,
    strict: bool = False,
) -> tuple[SegmentationExtension, list[Diagnostic]]:
    """Read a seg extension dict of any supported version.

    Migrates an older file, builds the model, and checks the rules of spec §5
    that the given context allows. Returns the extension and everything there
    is to report. Raises :class:`DiagnosticsError` when a rule's violation
    makes a reader refuse the file — or, with ``strict``, on any error — and
    ``ValueError`` when the dict is not the shape of a seg extension at all.
    """
    data, diagnostics = migrate_seg_extension(raw)

    # A color that is not a string is treated as absent, and reported (§3.2).
    segments = data.get("segments") if isinstance(data, dict) else None
    if isinstance(segments, list) and any(
        isinstance(s, dict) and s.get("color") is not None and not isinstance(s["color"], str)
        for s in segments
    ):
        data = {**data, "segments": [dict(s) if isinstance(s, dict) else s for s in segments]}
        for seg in data["segments"]:
            if isinstance(seg, dict) and seg.get("color") is not None and not isinstance(
                seg["color"], str
            ):
                del seg["color"]
                diagnostics.append(
                    _warn("color-unreadable", About.segment(str(seg.get("id"))))
                )

    version = data.get("version") if isinstance(data, dict) else None
    if isinstance(version, str) and version_tuple(version) != version_tuple(SEG_VERSION):
        # Rule 1, before looking at the fields: a later version's are not ours to judge.
        raise DiagnosticsError(
            [Diagnostic("rule-1", "error", About.extension(),
                        f"version {version!r} is not one this reader supports")]
        )
    try:
        ext = SegmentationExtension.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"not a readable seg extension: {exc}") from exc

    diagnostics = diagnostics + validate_seg_extension(
        ext, axes=axes, shape=shape, dtype=dtype, fill_value=fill_value
    )
    refused = [d for d in diagnostics if d.severity == "error"] if strict else refusals(diagnostics)
    if refused:
        raise DiagnosticsError(refused)
    return ext, diagnostics

