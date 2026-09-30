"""Reading 1.x files as 2.0: the §14 mapping of docs/proposals/duckn-2.0.md (revision 15).

:func:`from_1x` takes the ``duckn`` object of a 1.0, 1.1 or 1.2 file (or one with no version and
none of 2.0's fields, a 1.0 file) and returns the 2.0 object it means, with the findings §14 asks
a reader to report. It works on the raw object, not through :class:`duckn.models.DucknMetadata`:
§14 reads a part a 1.x rule refuses as refused and the rest as it is, where the 1.x model refuses
the whole file. The result is read by :func:`duckn.convention2.read` like any 2.0 header;
:func:`read_any` does both.

What 1.x said wrongly is carried as it said it (§14's closing paragraph): the mapping never
repairs a converter's defect it cannot detect.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from duckn.convention2 import TOKEN, Finding, Header, InvalidMetadata, NotConvention2, read

__all__ = ["Mapped", "from_1x", "read_any"]

_ABBREVS = {"RAS": "right-anterior-superior", "LAS": "left-anterior-superior",
            "LPS": "left-posterior-superior", "RAST": "right-anterior-superior-time",
            "LAST": "left-anterior-superior-time", "LPST": "left-posterior-superior-time"}
_ANATOMICAL = {"right-anterior-superior", "left-anterior-superior", "left-posterior-superior"}
_GENERAL_3D = {"scanner-xyz", "right-up-back", "right-up-forward", "right-forward-up",
               "right-down-forward", "forward-right-up", "east-north-up", "3D-right-handed",
               "3D-left-handed"}

# §3.4's normalization table (1.x spellings -> UCUM codes).
_UNIT_SPELLINGS = {
    "µm": "um", "μm": "um", "micron": "um", "microns": "um", "micrometer": "um",
    "micrometers": "um", "millimeter": "mm", "millimeters": "mm", "nanometer": "nm",
    "nanometers": "nm", "sec": "s", "second": "s", "seconds": "s", "msec": "ms",
    "millisecond": "ms", "milliseconds": "ms", "ppm": "[ppm]", "degree": "deg",
    "degrees": "deg", "radian": "rad", "radians": "rad", "hertz": "Hz",
}
_UDUNITS_TIME = {"seconds": "s", "second": "s", "s": "s", "minutes": "min", "minute": "min",
                 "min": "min", "hours": "h", "hour": "h", "h": "h", "days": "d", "day": "d",
                 "d": "d", "milliseconds": "ms", "ms": "ms"}

_RANGE_KINDS = {
    "list", "point", "vector", "covariant-vector", "normal", "stub", "scalar", "complex",
    "2-vector", "3-color", "RGB-color", "HSV-color", "XYZ-color", "4-color", "RGBA-color",
    "3-vector", "3-gradient", "3-normal", "4-vector", "quaternion", "2D-symmetric-matrix",
    "2D-masked-symmetric-matrix", "2D-matrix", "2D-masked-matrix", "3D-symmetric-matrix",
    "3D-masked-symmetric-matrix", "3D-matrix", "3D-masked-matrix",
}
_FRAME_KINDS_ANY = {"point", "vector", "covariant-vector", "normal"}  # when size = spatial count
_FRAME_KINDS_2 = {"2D-symmetric-matrix", "2D-masked-symmetric-matrix", "2D-matrix", "2D-masked-matrix"}
_FRAME_KINDS_3 = {"3-gradient", "3-normal", "3D-symmetric-matrix", "3D-masked-symmetric-matrix",
                  "3D-matrix", "3D-masked-matrix"}


@dataclass
class Mapped:
    """A 1.x file read as 2.0: the object, what the mapping reports, and the one fact 2.0 has no
    core place for yet - the frame of a ``dwmri`` 1.0 block's gradients (a matrix; ``None``
    when the file has no such block or the frame is unknown)."""

    duckn: dict
    findings: list[Finding] = field(default_factory=list)
    gradient_frame: list[list[float]] | None = None


def read_any(duckn: dict, shape: tuple[int, ...] | None = None,
             data_type: str | None = None) -> Header:
    """Read a 2.0 file, or a 1.x one through the §14 mapping, as a :class:`Header`."""
    try:
        return read(duckn, shape, data_type)
    except NotConvention2:
        version = duckn.get("version") if isinstance(duckn, dict) else None
        if version is not None and not str(version).startswith("1."):
            raise
    mapped = from_1x(duckn, shape, data_type)
    header = read(mapped.duckn, shape, data_type)
    header.findings[:0] = mapped.findings
    header.gradient_frame = mapped.gradient_frame
    return header


def _unit(u: Any, notes: list[Finding], where: str) -> Any:
    """§3.4's normalization, and a UDUNITS reference date dropped (reported)."""
    if isinstance(u, str):
        m = re.fullmatch(r"\s*(\w+)\s+since\s+(.+)", u)
        if m and m.group(1).lower() in _UDUNITS_TIME:
            notes.append(Finding("reported", "§14", f"{where}: the reference date of {u!r} is not carried"))
            return _UDUNITS_TIME[m.group(1).lower()]
        return _UNIT_SPELLINGS.get(u, u)
    return copy.deepcopy(u)


def _values_unit(u: Any, notes: list[Finding]) -> Any:
    if u == "HU":
        return {"symbol": "HU", "scheme": "UCUM", "code": "[hnsf'U]"}
    return _unit(u, notes, "sample_units")


def _token(name: str, report) -> str:
    """A 1.x target name as a 2.0 reference: prefixed as it is, bare sanitized (§14)."""
    if ":" in name:
        prefix, rest = name.split(":", 1)
        if TOKEN.fullmatch(prefix) and rest:
            return name
    token = re.sub(r"[^A-Za-z0-9_-]", "_", name) or "_"
    if token != name:
        report(f"target name {name!r} read as {token!r}")
    return token


def _affine_h(m: np.ndarray) -> np.ndarray:
    """An n x (n+1) affine as its (n+1) x (n+1) homogeneous form."""
    n = m.shape[0]
    h = np.eye(n + 1)
    h[:n, :] = m
    return h


def from_1x(duckn: dict, shape: tuple[int, ...] | None = None,
            data_type: str | None = None) -> Mapped:
    d = duckn
    notes: list[Finding] = []
    report = lambda msg: notes.append(Finding("reported", "§14", msg))  # noqa: E731
    version = d.get("version") or "1.0"
    if not str(version).startswith("1."):
        raise NotConvention2(f"version {version} is not a 1.x version")
    v12 = tuple(int(x) for x in str(version).split(".")[:2]) >= (1, 2)
    axes1 = [a if isinstance(a, dict) else {} for a in (d.get("axes") or [])]
    extensions = d.get("extensions") or {}
    fits = "fits" in extensions

    # ---- the 1.x space ------------------------------------------------------------------
    space = d.get("space")
    space = _ABBREVS.get(space, space)
    world_axes: list[dict] = []
    has_time_space = False
    if space is not None:
        base = space[:-5] if space.endswith("-time") else space
        has_time_space = space.endswith("-time")
        if base in _ANATOMICAL:
            world_axes = [{"id": i, "type": "space", "positive": t}
                          for i, t in zip("xyz", base.split("-"))]
        elif base in _GENERAL_3D:
            world_axes = [{"id": i, "type": "space"} for i in "xyz"]
            report(f"space {space!r}: its axis directions and handedness are not carried")
        else:
            raise InvalidMetadata([Finding("invalid", "§14", f"space {space!r} is not a 1.x space name")])
        if has_time_space:
            world_axes.append({"id": "t", "type": "time"})
    elif d.get("space_dimension") is not None:
        n = d["space_dimension"]
        ids = ["x", "y", "z"][:n] + [f"a{k}" for k in range(3, n)]
        world_axes = [{"id": i} if fits else {"id": i, "type": "space"} for i in ids]
        if fits:
            report("a fits file's axes take no type: its sky axes are angles in a linearized projection")
    n1 = len(world_axes)  # the 1.x space's dimension

    origin = d.get("space_origin")
    origin = [float(x) for x in origin] if origin is not None else None
    directions = [a.get("space_direction") for a in axes1]

    # A -time space's time axis is added only when something states a time (§14).
    keep_time = has_time_space and (
        origin is not None or any(v is not None and v[3] != 0 for v in directions))
    if has_time_space and not keep_time:
        world_axes = world_axes[:3]
        n1 = 3
        directions = [v[:3] if v is not None else None for v in directions]

    # Units: each world axis takes the unit of the dimensions stepping along it.
    for j, ax in enumerate(world_axes):
        units = {_json(_unit(a.get("unit"), notes, f"axis {k}")) for k, a in enumerate(axes1)
                 if directions[k] is not None and directions[k][j] != 0 and a.get("unit") is not None}
        if len(units) > 1:
            raise InvalidMetadata([Finding("invalid", "§14", f"world axis {ax['id']} would take two units")])
        if units:
            ax["unit"] = _unjson(units.pop())
    spatial_units = {_json(ax["unit"]) for ax in world_axes[:3 if has_time_space else n1]
                     if "unit" in ax}
    for ax in world_axes[:3 if has_time_space else n1]:
        if "unit" not in ax and len(spatial_units) == 1:
            ax["unit"] = _unjson(next(iter(spatial_units)))
    if keep_time and "unit" not in world_axes[3]:
        time_units = [a.get("unit") for a in axes1 if a.get("kind") == "time" and a.get("unit")]
        if time_units:
            world_axes[3]["unit"] = _unit(time_units[0], notes, "time axis")

    out: dict[str, Any] = {"version": "2.0"}
    world_origin = list(origin[:n1]) if origin is not None else None

    # ---- dimensions ---------------------------------------------------------------------
    dims: list[dict] = []
    added: list[tuple[int, dict, list[float]]] = []  # (dimension, new world axis, positions)
    for k, a in enumerate(axes1):
        kind = a.get("kind")
        dim: dict[str, Any] = {}
        v = directions[k]
        samples1 = a.get("samples")
        if v is not None:
            dim["step"] = [float(x) for x in v]
        elif kind in ("time", "domain") and samples1 and all(
                isinstance(s, dict) and s.get("position") is not None for s in samples1):
            unit = a.get("unit")
            if kind == "time" and keep_time:
                t0 = world_origin[3] if world_origin is not None else 0.0
                step = [0.0] * n1
                step[3] = 1.0
                dim["step"] = step
                dim["samples"] = [{"position": s["position"] - t0} for s in samples1]
            elif kind == "time" or unit is not None:
                new = _added_axis(kind, _unit(unit, notes, f"axis {k}") if unit is not None else None,
                                  world_axes, [x[1] for x in added])
                added.append((k, new, [s["position"] for s in samples1]))
            else:
                report(f"axis {k}: a domain with positions and no unit states nothing in 2.0")
        elif kind in _RANGE_KINDS:
            dim["components"] = kind
        if kind in _RANGE_KINDS and "step" in dim:
            report(f"axis {k}: a range kind with a direction keeps its step, not its kind")
        for key in ("centering", "thickness", "color_space"):
            if key in a:
                dim[key] = a[key]
        if "extensions" in a:
            dim["extensions"] = copy.deepcopy(a["extensions"])
        dims.append(dim)

    # Added world axes (1.x time or spectral axes with positions and no direction).
    for k, new, positions in added:
        world_axes.append(new)
        if world_origin is not None:
            world_origin.append(0.0)
    n2 = len(world_axes)
    for d2 in dims:
        if "step" in d2 and len(d2["step"]) < n2:
            d2["step"] = d2["step"] + [0.0] * (n2 - len(d2["step"]))
    for idx, (k, new, positions) in enumerate(added):
        step = [0.0] * n2
        step[n1 + idx] = 1.0
        dims[k]["step"] = step
        dims[k]["samples"] = [{"position": float(p)} for p in positions]
        if world_origin is None:
            report(f"axis {k}: times or positions with no space_origin are relative to the first sample")

    _map_samples(axes1, dims, directions, world_origin, n1, n2, has_time_space and keep_time, report)

    if shape is not None:
        for k, d2 in enumerate(dims):
            if k < len(shape) and shape[k] == 1 and "centering" in d2:
                del d2["centering"]  # §5.1: a length-1 dimension has no cell

    gradient_frame = _map_frames(d, dims, world_axes, version, report, has_time_space and keep_time)

    # ---- world ----------------------------------------------------------------------------
    if world_axes:
        out["world"] = {"axes": world_axes}
        _map_transforms(d, out, dims, directions, world_origin, world_axes, n1, shape, report,
                        fits, has_time_space, keep_time)
        # A dicom block's Frame of Reference is the frame the array's patient coordinates are
        # in (its geometry is the source's Image Position and Orientation): §14.
        uid = ((extensions.get("dicom") or {}).get("tags") or {}).get("FrameOfReferenceUID") \
            if isinstance(extensions.get("dicom"), dict) else None
        if isinstance(uid, str) and uid and "reference" not in out["world"] and any(
                a.get("type") == "space" for a in world_axes):
            out["world"]["reference"] = f"dicom:{uid}"
    if world_origin is not None and world_axes:
        out["origin"] = world_origin
    out["dimensions"] = dims

    # ---- values ---------------------------------------------------------------------------
    values: dict[str, Any] = {}
    if d.get("sample_units") is not None:
        values["unit"] = _values_unit(d["sample_units"], notes)
    elif d.get("intent") in ("displacement-field", "velocity-field"):
        spatial = {_json(ax.get("unit")) for ax in world_axes if ax.get("type") == "space"}
        if len(spatial) == 1 and next(iter(spatial)) != _json(None):
            u = _unjson(next(iter(spatial)))
            values["unit"] = u if d["intent"] == "displacement-field" else f"{u}/s"
            report(f"a 1.x {d['intent']} with no sample_units: assumed in {values['unit']} (§19 item 18)")
    transforms = d.get("value_transforms")
    if transforms is None:
        if not v12:
            values["transforms"] = []  # 1.0 and 1.1: absence meant identity
    else:
        values["transforms"] = []
        for t in transforms:
            t = copy.deepcopy(t)
            if t.get("name") == "axis_linear" and "axis" in (t.get("parameters") or {}):
                t["parameters"]["dimension"] = t["parameters"].pop("axis")
            values["transforms"].append(t)
    if values.get("transforms") == [] and data_type is not None and np.issubdtype(
            np.dtype(data_type), np.unsignedinteger) and any(
            x.get("components") == "XYZ-color" for x in dims):
        values["transforms"] = [{"name": "linear", "parameters": {
            "slope": 1.0 / float(np.iinfo(np.dtype(data_type)).max), "intercept": 0.0}}]
        report("an unsigned integer XYZ-color array under identity: 1.2's fraction reading "
               "carried as a linear")
    if values:
        out["values"] = values

    for key in ("intent", "unit_systems"):
        if key in d:
            out[key] = copy.deepcopy(d[key])
    if extensions:
        out["extensions"] = copy.deepcopy(extensions)
        for ext in out["extensions"].values():
            if isinstance(ext, dict):
                ext.pop("space_transforms", None)  # carried into world.transforms
    known = {"version", "space", "space_dimension", "space_origin", "measurement_frame",
             "sample_units", "value_transforms", "intent", "axes", "space_transforms",
             "extensions", "unit_systems"}
    for key in sorted(set(d) - known):
        report(f"{key!r} is not a 1.x field: not carried")
    seen, unique = set(), []
    for f in notes:
        if f.message not in seen:
            seen.add(f.message)
            unique.append(f)
    return Mapped(out, unique, gradient_frame)


def _json(x):
    import json
    return json.dumps(x, sort_keys=True)


def _unjson(s):
    import json
    return json.loads(s)


def _added_axis(kind, unit, world_axes, already) -> dict:
    taken = {a["id"] for a in world_axes} | {a["id"] for a in already}
    if kind == "time":
        base, new = "t", {"type": "time"}
    elif unit in ("Hz", "rad/s"):
        base, new = "frequency", {"type": "frequency"}
    else:  # [ppm] offsets and any other unit: no type (§14)
        base, new = "a", {}
    ident = base if base not in taken and base != "a" else None
    k = 1 if base != "a" else len(world_axes) + len(already)
    while ident is None or ident in taken:
        ident = f"{base}{k}"
        k += 1
    axis = {"id": ident, **new}
    if unit is not None:
        axis["unit"] = unit
    return axis


def _spacing(step: list[float], n_spatial: int) -> float:
    return float(np.linalg.norm(step[:n_spatial]))


def _map_samples(axes1, dims, directions, origin, n1, n2, time_space, report) -> None:
    n_spatial = 3 if time_space else n1
    spatial_dims = [k for k, v in enumerate(directions) if v is not None]
    for k, a in enumerate(axes1):
        samples1 = a.get("samples")
        d2 = dims[k]
        if not samples1 or "samples" in d2 and all("position" in s for s in d2["samples"]):
            continue
        out = []
        is_time_dir = a.get("kind") == "time" and directions[k] is not None and time_space
        for i, s in enumerate(samples1):
            s = s if isinstance(s, dict) else {}
            s2: dict[str, Any] = {}
            if s.get("origin") is not None:
                o = [float(x) for x in s["origin"]][:n1]
                s2["origin"] = o + (origin[n1:] if origin is not None else [0.0] * (n2 - n1))
            elif s.get("position") is not None and "step" in d2:
                if is_time_dir:
                    t0 = origin[3] if origin is not None else 0.0
                    s2["position"] = (s["position"] - t0) / d2["step"][3]
                else:
                    sp = _spacing(d2["step"], n_spatial)
                    s2["position"] = s["position"] / sp if sp else s["position"]
            if s.get("directions") is not None:
                if "origin" not in s2:
                    base = np.array(origin if origin is not None else [0.0] * n2, float)
                    at = s2.get("position", i)
                    s2["origin"] = list(base + at * np.array(d2["step"], float))
                    s2.pop("position", None)
                steps, it = [], iter(s["directions"])
                for kk, other in enumerate(dims):
                    if kk in spatial_dims:
                        v = [float(x) for x in next(it)][:n1]
                        steps.append(v + [0.0] * (n2 - len(v)))
                    elif "step" in other:
                        steps.append(other["step"])
                    else:
                        steps.append(None)
                s2["steps"] = steps
            if s.get("thickness") is not None and "step" in d2:
                s2["thickness"] = s["thickness"]
            if s.get("metadata") is not None:
                s2["metadata"] = copy.deepcopy(s["metadata"])
            out.append(s2)
        d2["samples"] = out


def _map_frames(d, dims, world_axes, version, report, time_space):
    """measurement_frame onto spatial components dimensions, and a dwmri block's gradients."""
    mf = d.get("measurement_frame")
    dwmri = (d.get("extensions") or {}).get("dwmri")
    dwi_dim = None
    if isinstance(dwmri, dict):
        for k, dim in enumerate(dims):
            if dim.get("components") in ("vector", "list", "3-vector") or "dwmri" in (dim.get("extensions") or {}):
                dwi_dim = k
                break
        if dwi_dim is not None and "components" in dims[dwi_dim]:
            dims[dwi_dim]["components"] = "list"
    n_spatial = sum(1 for a in world_axes if a.get("type") == "space")
    gradient_frame = None
    frame = None
    if mf is not None:
        f = np.array(mf, float)
        if f.shape == (4, 4) and time_space:
            if not (np.allclose(f[3, :3], 0) and np.allclose(f[:3, 3], 0) and math.isclose(f[3, 3], 1)):
                report("a 4 x 4 measurement_frame couples time and space: the coupling is not carried")
            f = f[:3, :3]
        if tuple(int(x) for x in str(version).split(".")[:2]) < (1, 1):
            f = f.T  # 1.0 wrote the frame by columns, as NRRD does; 1.1 turned it to rows
        frame = f.tolist()
    used = False
    for k, dim in enumerate(dims):
        kind = dim.get("components")
        if kind == "3-vector" and mf is not None and n_spatial == 3 and k != dwi_dim:
            dim["components"] = kind = "vector"
        spatial = (kind in _FRAME_KINDS_ANY) or (kind in _FRAME_KINDS_3 and n_spatial == 3) or (
            kind in _FRAME_KINDS_2 and n_spatial == 2)
        if spatial and k != dwi_dim and frame is not None:
            dim["frame"] = copy.deepcopy(frame)
            used = True
    if isinstance(dwmri, dict):
        gf = dwmri.get("gradient_frame", "measurement")
        if gf == "world":
            gradient_frame = np.eye(n_spatial).tolist()
        elif gf == "image":
            report("dwmri gradient_frame image is not carried: FSL's first-component flip is "
                   "unrecorded, so the gradients' frame is unknown")
        elif mf is None:
            gradient_frame = np.eye(n_spatial).tolist()  # dwi 1.0 §6: absent is the identity
        elif frame is None:
            report("the dwmri gradients' frame is unknown (the measurement_frame was not carried)")
        else:
            gradient_frame = frame
            used = True
    if mf is not None and frame is not None and not used:
        report("measurement_frame governs nothing in this file: not carried")
    return gradient_frame


def _map_transforms(d, out, dims, directions, origin, world_axes, n1, shape, report, fits,
                    has_time_space, keep_time) -> None:
    entries = [(None, t) for t in d.get("space_transforms") or []]
    for name, ext in (d.get("extensions") or {}).items():
        if isinstance(ext, dict):
            entries += [(name, t) for t in ext.get("space_transforms") or []]
    if not entries:
        return
    if has_time_space and not keep_time:
        report("space_transforms on a -time space with no time axis: not carried")
        return
    on = [a["id"] for a in world_axes[:n1]]
    overridden = any(k in s for a in (d.get("axes") or []) for s in (a.get("samples") or [])
                     if isinstance(s, dict) for k in ("position", "origin", "directions"))
    transforms = []
    for ext_name, t in entries:
        if not isinstance(t, dict):
            report("a space_transforms entry that is not an object: not carried")
            continue
        to, frm = t.get("to") or {}, t.get("from", {"space": "world"})
        frm_space = frm.get("space") if isinstance(frm, dict) else None
        if not isinstance(frm, dict):
            report(f"a space_transforms entry's from {frm!r} is not an object (transform "
                   f"specification §4): not carried")
            continue
        name = to.get("name")
        if name is None:
            report("a space_transforms entry with no target name: not carried")
            continue
        if ext_name is not None and ":" not in name:
            name = f"{ext_name}:{name}"
        ref = _token(name, report)
        if ":" not in ref:
            report(f"target {ref!r} is local to this array (1.x let a container share it)")
        forward, inverse = t.get("forward"), t.get("inverse")
        if frm_space not in (None, "world"):
            composed = _compose(frm_space, forward, inverse, dims, directions, origin, n1, shape,
                                report)
            if composed is None:
                continue
            forward, inverse = composed
        elif overridden:
            report("a 1.x world transform on per-sample positions: 1.x applied it to nominal ones")
        entry: dict[str, Any] = {"to": {"reference": ref}, "on": on}
        if to.get("axes"):
            entry["to"]["axes"] = [_target_axis(a, i, fits) for i, a in enumerate(to["axes"])]
        if forward is not None:
            entry["forward"] = copy.deepcopy(forward)
        if inverse is not None:
            entry["inverse"] = copy.deepcopy(inverse)
        if t.get("metadata") is not None:
            entry["metadata"] = copy.deepcopy(t["metadata"])
        transforms.append(entry)
    spatial = [a["id"] for a in world_axes if a.get("type") == "space"]
    identities = [e for e in transforms if (e.get("forward") or {}).get("identity")
                  and set(e["on"]) == set(spatial) and spatial]
    if len(identities) == 1:
        out["world"]["reference"] = identities[0]["to"]["reference"]
        transforms.remove(identities[0])
    if transforms:
        out["world"]["transforms"] = transforms


def _target_axis(a, i, fits) -> dict:
    out = {"id": ["x", "y", "z"][i] if i < 3 else f"a{i}"}
    kind = a.get("kind")
    if kind == "space" and not fits:
        out["type"] = "space"
    elif kind == "time":
        out["type"] = "time"
    if a.get("unit") is not None:
        out["unit"] = _UNIT_SPELLINGS.get(a["unit"], a["unit"]) if isinstance(a["unit"], str) else a["unit"]
    return out


def _compose(frm, forward, inverse, dims, directions, origin, n1, shape, report):
    """A 1.x transform from a derived space, composed with the nominal placement into one from
    this world (§7). world = D index + o, the transform specification's §2.1 formula."""
    spatial_dims = [k for k, v in enumerate(directions) if v is not None]
    if origin is None or len(spatial_dims) != n1:
        report(f"a transform from {frm!r} needs a space_origin and one step per world axis: not carried")
        return None
    D = np.column_stack([np.array(directions[k][:n1], float) for k in spatial_dims])
    o = np.array(origin[:n1], float)
    if abs(np.linalg.det(D)) < 1e-12:
        report(f"a transform from {frm!r}: the placement is not invertible; not carried")
        return None
    if frm == "index":
        m = np.column_stack([np.linalg.inv(D), -np.linalg.inv(D) @ o])  # world -> index
    else:
        U, _, Vt = np.linalg.svd(D)
        R = U @ Vt
        m = np.column_stack([R.T, o - R.T @ o])  # world -> axis-aligned
        if frm == "axis-aligned-centered":
            if shape is None:
                report("a transform from axis-aligned-centered needs the array's shape: not carried")
                return None
            S = Vt.T @ np.diag(np.linalg.svd(D)[1]) @ Vt
            n = np.array([shape[k] for k in spatial_dims], float)
            center = o + (n - 1) / 2 * np.diag(S)
            m[:, -1] -= center
        elif frm != "axis-aligned":
            report(f"a transform from {frm!r}: not a 1.x space; not carried")
            return None
    h = _affine_h(m)
    new_forward = new_inverse = None
    if forward is not None:
        if forward.get("identity"):
            new_forward = {"affine": m.tolist()}
        else:
            a = np.array(forward["affine"], float)
            new_forward = {"affine": (a @ h).tolist()}
    if inverse is not None:
        hinv = np.linalg.inv(h)
        if inverse.get("identity"):
            new_inverse = {"affine": hinv[:-1].tolist()}
        else:
            a = _affine_h(np.array(inverse["affine"], float))
            new_inverse = {"affine": (hinv @ a)[:-1].tolist()}
    report(f"a transform from {frm!r} composed into one from this world (D index + o)")
    return new_forward, new_inverse
