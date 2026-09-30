"""Convention 2.0: reading a header (docs/proposals/duckn-2.0.md, revision 15).

One entry point, :func:`read`, turns the ``duckn`` object of a 2.0 file into a :class:`Header`,
refusing it (:class:`InvalidMetadata`) when it breaks a rule of §10 and keeping everything else
the draft says is *not* validity as findings: a value transform that cannot be applied makes only
the quantity unknown (§6), a misplaced ``missing`` is ignored, a unit that does not fit its axis's
type is reported (§3.1). The derivations are §11's: positions and the index-to-world affine, the
grid's cells, the orientation, the quantity and the ``missing`` mask.

1.x files are not read here: :func:`read` refuses them with :class:`NotConvention2`, and the §14
mapping that turns them into a 2.0 header is its own module (not yet written). Nothing here writes.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = [
    "Finding",
    "Header",
    "InvalidMetadata",
    "NotConvention2",
    "QuantityUnknown",
    "read",
]

TOKEN = re.compile(r"[A-Za-z0-9_-]+")

# §5.2: kind -> (fixed size or None, when it is spatial). "count": when its size is the world's
# spatial count; 2 or 3: in a world with that many spatial axes; None: never.
KINDS: dict[str, tuple[int | None, Any]] = {
    "list": (None, None), "point": (None, "count"), "vector": (None, "count"),
    "covariant-vector": (None, "count"), "normal": (None, "count"),
    "stub": (1, None), "scalar": (1, None), "complex": (2, None), "2-vector": (2, None),
    "3-color": (3, None), "RGB-color": (3, None), "HSV-color": (3, None), "XYZ-color": (3, None),
    "4-color": (4, None), "RGBA-color": (4, None), "3-vector": (3, None),
    "3-gradient": (3, 3), "3-normal": (3, 3), "4-vector": (4, None), "quaternion": (4, None),
    "2D-symmetric-matrix": (3, 2), "2D-masked-symmetric-matrix": (4, 2),
    "2D-matrix": (4, 2), "2D-masked-matrix": (5, 2),
    "3D-symmetric-matrix": (6, 3), "3D-masked-symmetric-matrix": (7, 3),
    "3D-matrix": (9, 3), "3D-masked-matrix": (10, 3),
}

# §5.3
COLOR_SPACES = {
    "RGB-color": {"srgb", "srgb-linear", "display-p3", "display-p3-linear", "a98-rgb",
                  "prophoto-rgb", "rec2020"},
    "XYZ-color": {"xyz-d50", "xyz-d65"},
}
COLOR_SPACES["RGBA-color"] = COLOR_SPACES["RGB-color"]
UNORM_KINDS = ("RGB-color", "RGBA-color")  # §5.3: unsigned integers under [] are fractions

# §3.1: each term's LPS unit vector; the three pairs.
_LPS = {"left": (0, 1), "right": (0, -1), "posterior": (1, 1), "anterior": (1, -1),
        "superior": (2, 1), "inferior": (2, -1)}

# §3.1: units that fit each defined type, for the fit check a reader reports (never validity).
# A unit outside this table is "not resolved", and its fit is not checked.
_UNIT_DIMENSION = {
    **{u: "length" for u in ("m", "cm", "mm", "um", "nm", "pm", "km", "[in_i]", "Ao")},
    **{u: "time" for u in ("s", "ms", "us", "ns", "min", "h", "d")},
    **{u: "frequency" for u in ("Hz", "kHz", "MHz", "GHz", "rad/s")},
    **{u: "angle" for u in ("rad", "deg", "mrad", "'", "''")},
    "[ppm]": "ratio",
}
_TYPE_FITS = {"space": {"length"}, "wavelength": {"length"}, "time": {"time"},
              "frequency": {"frequency"}, "chemical-shift": {"ratio"}, "angle": {"angle"}}


class InvalidMetadata(ValueError):
    """The header breaks a rule of §10: its duckn metadata is refused, never guessed at."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        super().__init__("; ".join(f"{f.rule}: {f.message}" for f in findings))


class NotConvention2(ValueError):
    """A 1.x file (read through the §14 mapping), or a major version this reader does not know."""


class QuantityUnknown(ValueError):
    """The stored values' mapping to the quantity is not stated or cannot be applied (§6)."""


@dataclass(frozen=True)
class Finding:
    """What a reader says of a file that is not a refusal.

    ``kind`` is ``"invalid"`` (a §10 rule; collected into :class:`InvalidMetadata`),
    ``"quantity"`` (the quantity is unknown, the rest stands), ``"ignored"`` (a field set aside,
    such as a misplaced ``missing``) or ``"reported"`` (true of the file and worth saying).
    """

    kind: str
    rule: str
    message: str


# ---------------------------------------------------------------------------------------------


def _unit_key(unit: Any) -> tuple:
    """§3.1: two units are the same as written - never as a reader resolves them."""
    if unit is None:
        return ("absent",)
    if isinstance(unit, str):
        return ("ucum", unit)
    if isinstance(unit, dict) and unit.get("scheme") == "UCUM" and "code" in unit:
        return ("ucum", unit["code"])
    return ("json", json.dumps(unit, sort_keys=True))


def _ucum(unit: Any) -> str | None:
    key = _unit_key(unit)
    return key[1] if key[0] == "ucum" else None


def _is_token(value: Any) -> bool:
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


def _is_reference(value: Any) -> bool:
    """§10 rule 11: a bare token, or ``<prefix>:<value>`` with a token prefix and a value."""
    if not isinstance(value, str):
        return False
    if ":" not in value:
        return _is_token(value)
    prefix, rest = value.split(":", 1)
    return _is_token(prefix) and rest != ""


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _vector(v: Any, n: int) -> bool:
    return isinstance(v, list) and len(v) == n and all(_finite(x) for x in v)


class _Check:
    def __init__(self):
        self.findings: list[Finding] = []

    def invalid(self, rule: str, message: str) -> None:
        self.findings.append(Finding("invalid", rule, message))

    def add(self, kind: str, rule: str, message: str) -> None:
        self.findings.append(Finding(kind, rule, message))


# ---------------------------------------------------------------------------------------------


def read(duckn: dict, shape: tuple[int, ...] | None = None, data_type: str | None = None) -> Header:
    """Read a 2.0 ``duckn`` object. ``shape`` and ``data_type`` are Zarr's, where known: without
    ``shape`` a dimension's length is known only where its ``samples`` fix it."""
    if not isinstance(duckn, dict):
        raise InvalidMetadata([Finding("invalid", "§2", "the duckn metadata is not an object")])
    version = duckn.get("version")
    own_fields = [k for k in ("world", "origin", "dimensions", "values") if k in duckn]
    if version is None:
        if own_fields:
            raise InvalidMetadata([Finding(
                "invalid", "§10 rule 1",
                f"{', '.join(own_fields)} without a version: neither a 1.x file nor a 2.0 one")])
        raise NotConvention2("no version and none of 2.0's fields: a 1.0 file (§14)")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+", version):
        raise InvalidMetadata([Finding("invalid", "§2.1", f"version {version!r} is not major.minor")])
    major = int(version.split(".")[0])
    if major == 1:
        raise NotConvention2(f"a {version} file: read it through the §14 mapping")
    if major != 2:
        raise NotConvention2(f"convention {version} is a major version this reader does not know")

    check = _Check()
    world = duckn.get("world")
    axes = world.get("axes") if isinstance(world, dict) else None
    if world is not None and not (isinstance(axes, list) and axes and all(isinstance(a, dict) for a in axes)):
        check.invalid("§10 rule 1", "a world has axes")
        axes = []
    axes = axes or []
    n = len(axes)
    dims = duckn.get("dimensions") or []
    if not isinstance(dims, list) or not all(isinstance(d, dict) for d in dims):
        check.invalid("§5", "dimensions is a list of objects")
        dims = []
    origin = duckn.get("origin")

    _check_axes(check, axes)
    spatial = [i for i, a in enumerate(axes) if a.get("type") == "space"]
    lengths = _check_dimensions(check, dims, shape, n, world is not None, axes, spatial)
    if origin is not None:
        if world is None:
            check.invalid("§10 rule 1", "an origin requires a world")
        elif not _vector(origin, n):
            check.invalid("§10 rule 1", f"origin has {len(origin) if isinstance(origin, list) else '?'} "
                          f"components for {n} world axes")
    if isinstance(world, dict):
        if "reference" in world and not _is_reference(world["reference"]):
            check.invalid("§10 rule 11", f"reference {world['reference']!r} is neither a token nor prefix:value")
        _check_transforms(check, world.get("transforms") or [], axes)
    values = duckn.get("values")
    if values is not None and not isinstance(values, dict):
        check.invalid("§6", "values is an object")
        values = None
    _check_values(check, values or {}, dims, lengths, data_type)

    invalid = [f for f in check.findings if f.kind == "invalid"]
    if invalid:
        raise InvalidMetadata(invalid)
    return Header(duckn=duckn, version=version, axes=axes, dims=dims, lengths=lengths,
                  origin=np.array(origin, float) if origin is not None else None,
                  values=values, data_type=data_type, findings=check.findings)


def _check_axes(check: _Check, axes: list[dict]) -> None:
    ids = [a.get("id") for a in axes if "id" in a]
    for i, a in enumerate(axes):
        if "id" in a and not _is_token(a["id"]):
            check.invalid("§10 rule 11", f"world axis {i}'s id {a['id']!r} is not a token")
        if "type" in a and not _is_token(a["type"]):
            check.invalid("§10 rule 9", f"world axis {i}'s type {a['type']!r} is not a token")
        if "positive" in a:
            if a.get("type") != "space":
                check.invalid("§10 rule 5", f"world axis {i} has positive but is not of type space")
            elif a["positive"] not in _LPS:
                check.invalid("§3.1", f"world axis {i}'s positive {a['positive']!r} is not an anatomical term")
        fits = _TYPE_FITS.get(a.get("type"))
        dim = _UNIT_DIMENSION.get(_ucum(a.get("unit")) or "")
        if fits and dim and dim not in fits:
            check.add("reported", "§3.1", f"world axis {i}'s unit {a['unit']!r} does not fit type "
                      f"{a['type']!r}: taken as unknown")
    if len(set(ids)) != len(ids):
        check.invalid("§10 rule 5", "world axis ids are not unique")
    pairs = [_LPS[a["positive"]][0] for a in axes if a.get("positive") in _LPS]
    if len(set(pairs)) != len(pairs):
        check.invalid("§10 rule 5", "two world axes use terms of one anatomical pair")


def _check_dimensions(check, dims, shape, n, has_world, axes, spatial) -> list[int | None]:
    if shape is not None and len(dims) != len(shape):
        check.invalid("§10 rule 1", f"{len(dims)} dimensions for a shape of {len(shape)}")
    lengths: list[int | None] = []
    carriers = []
    stepped = []
    for d, dim in enumerate(dims):
        length = shape[d] if shape is not None and d < len(shape) else None
        samples = dim.get("samples")
        if samples is not None:
            if not isinstance(samples, list) or not all(isinstance(s, dict) for s in samples):
                check.invalid("§5.4", f"dimension {d}'s samples is a list of objects")
                samples = []
            elif length is not None and len(samples) != length:
                check.invalid("§10 rule 10", f"dimension {d} has {len(samples)} samples for a length of {length}")
            elif length is None:
                length = len(samples)
        lengths.append(length)
        has_step, has_comp = "step" in dim, "components" in dim
        if has_step and has_comp:
            check.invalid("§10 rule 2", f"dimension {d} has both step and components")
        if has_step:
            if not has_world:
                check.invalid("§10 rule 1", f"dimension {d} has a step and the file has no world")
            elif not _vector(dim["step"], n):
                check.invalid("§10 rule 1", f"dimension {d}'s step has no one finite component per world axis")
            else:
                stepped.append(np.array(dim["step"], float))
                _check_units_across(check, d, dim["step"], axes, spatial)
        step_only = [k for k in ("centering", "thickness") if k in dim]
        step_only += [f"samples[].{k}" for k in ("position", "origin", "steps", "thickness")
                      if any(k in s for s in samples or [])]
        if step_only and not has_step:
            check.invalid("§10 rule 2", f"dimension {d} has {', '.join(step_only)} and no step")
        if "centering" in dim and dim["centering"] not in ("cell", "node"):
            check.invalid("§5.1", f"dimension {d}'s centering {dim['centering']!r} is neither cell nor node")
        comp_only = [k for k in ("color_space", "frame") if k in dim]
        if comp_only and not has_comp:
            check.invalid("§10 rule 2", f"dimension {d} has {', '.join(comp_only)} and no components")
        if has_comp:
            _check_components(check, d, dim, length, len(spatial), axes, spatial)
        ids = [s["id"] for s in samples or [] if "id" in s]
        if len(set(ids)) != len(ids):
            check.invalid("§10 rule 5", f"dimension {d}'s sample ids are not unique")
        for s in samples or []:
            if "id" in s and not _is_token(s["id"]):
                check.invalid("§10 rule 11", f"dimension {d}'s sample id {s['id']!r} is not a token")
            if "position" in s and "origin" in s:
                check.invalid("§10 rule 10", f"a sample of dimension {d} has both position and origin")
            if "steps" in s and "origin" not in s:
                check.invalid("§10 rule 10", f"a sample of dimension {d} has steps and no origin")
            if "origin" in s and not _vector(s["origin"], n):
                check.invalid("§10 rule 1", f"a sample origin of dimension {d} has no one component per world axis")
            if "steps" in s:
                st = s["steps"]
                ok = isinstance(st, list) and len(st) == len(dims) and all(
                    (v is None) == ("step" not in dims[k]) and (v is None or _vector(v, n))
                    for k, v in enumerate(st))
                if not ok:
                    check.invalid("§10 rule 10", f"a sample's steps on dimension {d} do not match the dimensions")
        if any("origin" in s or "steps" in s for s in samples or []):
            carriers.append(d)
    if len(carriers) > 1:
        check.invalid("§10 rule 10", f"dimensions {carriers} all carry sample origins or steps")
    if stepped and np.linalg.matrix_rank(np.array(stepped)) < len(stepped):
        check.invalid("§10 rule 3", "the steps are not linearly independent")
    return lengths


def _check_units_across(check, d, step, axes, spatial) -> None:
    units = {_unit_key(axes[i].get("unit")) for i in spatial if step[i] != 0}
    if len(units) > 1:
        check.invalid("§10 rule 5", f"dimension {d}'s step spans spatial axes of different units")


def _check_components(check, d, dim, length, n_spatial, axes, spatial) -> None:
    kind = dim["components"]
    if kind not in KINDS:
        check.add("reported", "§5.2", f"dimension {d}'s components {kind!r} is not a known kind")
        return
    size, spatial_rule = KINDS[kind]
    if size is not None and length is not None and size != length:
        check.invalid("§10 rule 4", f"{kind} has {size} components; dimension {d} has length {length}")
    if "color_space" in dim:
        if dim["color_space"] not in COLOR_SPACES.get(kind, ()):
            check.invalid("§10 rule 7", f"color_space {dim['color_space']!r} does not fit {kind}")
    if "frame" in dim:
        is_spatial = (spatial_rule == "count" and length == n_spatial) or (
            isinstance(spatial_rule, int) and spatial_rule == n_spatial)
        f = dim["frame"]
        square = (isinstance(f, list) and len(f) == n_spatial
                  and all(_vector(r, n_spatial) for r in f))
        if not is_spatial:
            check.invalid("§10 rule 6", f"frame on {kind}, which is not spatial in this world")
        elif not square or abs(np.linalg.det(np.array(f, float))) < 1e-12:
            check.invalid("§10 rule 6", f"dimension {d}'s frame is not a square invertible matrix "
                          f"with one row per spatial axis")
        elif len({_unit_key(axes[i].get("unit")) for i in spatial}) > 1:
            check.invalid("§10 rule 5", "a world whose spatial axes differ in unit has no frame")


def _check_transforms(check, transforms, axes) -> None:
    ids = [a.get("id") for a in axes]
    seen = set()
    for k, t in enumerate(transforms):
        if not isinstance(t, dict):
            check.invalid("§7", f"transform {k} is not an object")
            continue
        to = t.get("to")
        if not isinstance(to, dict) or "reference" not in to:
            check.invalid("§10 rule 11", f"transform {k}'s to has no reference")
            continue
        if not _is_reference(to["reference"]):
            check.invalid("§10 rule 11", f"transform {k}'s reference {to['reference']!r} is malformed")
        on = t.get("on", ids)
        if not isinstance(on, list) or len(set(on)) != len(on) or any(a not in ids or a is None for a in on):
            check.invalid("§10 rule 8", f"transform {k}'s on does not name this world's axes, each once")
            continue
        key = (to["reference"], frozenset(on))
        if key in seen:
            check.invalid("§10 rule 8", f"a second transform to {to['reference']!r} on the same axes")
        seen.add(key)
        if "forward" not in t and "inverse" not in t:
            check.invalid("§7", f"transform {k} has neither forward nor inverse")
        target = to.get("axes")
        for direction in ("forward", "inverse"):
            obj = t.get(direction)
            if isinstance(obj, dict) and "affine" in obj:
                rows, cols = (len(target) if target else None, len(on) + 1)
                if direction == "inverse":
                    rows, cols = len(on), (len(target) + 1 if target else None)
                m = obj["affine"]
                ok = isinstance(m, list) and m and all(isinstance(r, list) and all(_finite(x) for x in r) for r in m)
                if not ok or (cols is not None and any(len(r) != cols for r in m)) or (
                        rows is not None and len(m) != rows):
                    check.invalid("§10 rule 8", f"transform {k}'s {direction} affine does not match "
                                  f"its axes ({len(on)} source, {len(target) if target else '?'} target)")


def _check_values(check, values, dims, lengths, data_type) -> None:
    transforms = values.get("transforms")
    if transforms is not None:
        if not isinstance(transforms, list):
            check.add("quantity", "§6", "transforms is not a list")
        else:
            for k, t in enumerate(transforms):
                problem = _transform_problem(k, t, dims, lengths, data_type)
                if problem:
                    check.add("quantity", "§6", problem)
    if "missing" in values:
        allowed = transforms == [] or (
            isinstance(transforms, list) and len(transforms) == 1
            and isinstance(transforms[0], dict) and transforms[0].get("name") == "linear"
            and (transforms[0].get("parameters") or {}).get("slope", 0) != 0)
        m = values["missing"]
        if not allowed:
            check.add("ignored", "§6", "missing is ignored: only under [] or one linear of non-zero slope")
        elif not (isinstance(m, list) and all(_finite(x) for x in m)):
            check.add("ignored", "§6", "missing is ignored: not a list of finite numbers")


def _transform_problem(k, t, dims, lengths, data_type) -> str | None:
    if not isinstance(t, dict):
        return f"value transform {k} is not an object"
    name, p = t.get("name"), t.get("parameters") or {}
    if name == "linear":
        if not (_finite(p.get("slope")) and _finite(p.get("intercept"))):
            return f"value transform {k} (linear) needs finite slope and intercept"
    elif name == "lut":
        vals = p.get("values")
        if k != 0:
            return f"value transform {k} (lut) is not first"
        if not (isinstance(vals, list) and vals and all(_finite(v) for v in vals)):
            return "the lut's values are empty or not finite"
        if not isinstance(p.get("first_value", 0), int):
            return "the lut's first_value is not an integer"
        if data_type is not None and not np.issubdtype(np.dtype(data_type), np.integer):
            return "a lut on non-integer stored values"
    elif name == "axis_linear":
        dim = p.get("dimension")
        if not (isinstance(dim, int) and 0 <= dim < len(dims)):
            return f"value transform {k} (axis_linear) names no dimension"
        for par in ("slope", "intercept"):
            v = p.get(par)
            if _finite(v):
                continue
            if not (isinstance(v, list) and all(_finite(x) for x in v)):
                return f"axis_linear's {par} is neither a finite number nor a list of them"
            if lengths[dim] is not None and len(v) != lengths[dim]:
                return (f"axis_linear's {par} has {len(v)} entries for dimension {dim} "
                        f"of length {lengths[dim]}")
    else:
        return f"value transform {k} has a type this reader does not know ({name!r})"
    return None


# ---------------------------------------------------------------------------------------------


def _wide(x: np.ndarray) -> np.ndarray:
    """Values widened for a transform: float64, or complex128 for complex values (a scale
    applies to both parts; float64 would drop the imaginary one)."""
    return x.astype(np.complex128 if np.iscomplexobj(x) else np.float64)


@dataclass
class Header:
    """A valid 2.0 header, and what §11 says a reader derives from it."""

    duckn: dict
    version: str
    axes: list[dict]
    dims: list[dict]
    lengths: list[int | None]
    origin: np.ndarray | None
    values: dict | None
    data_type: str | None
    findings: list[Finding] = field(default_factory=list)
    # A 1.x file's dwmri gradients' frame (§14), which 2.0 core has no place for until dwmri 2.0.
    gradient_frame: list[list[float]] | None = None

    # ---- geometry -------------------------------------------------------------------------

    @property
    def spatial_axes(self) -> list[int]:
        return [i for i, a in enumerate(self.axes) if a.get("type") == "space"]

    def _carrier(self) -> int | None:
        for d, dim in enumerate(self.dims):
            if any("origin" in s or "steps" in s for s in dim.get("samples") or []):
                return d
        return None

    def position(self, index) -> np.ndarray:
        """The world point of the sample at ``index`` (§1, §5.4). Without an origin it is
        relative to the first sample (§4)."""
        if len(index) != len(self.dims):
            raise ValueError(f"{len(index)} indices for {len(self.dims)} dimensions")
        n = len(self.axes)
        base = self.origin.copy() if self.origin is not None else np.zeros(n)
        steps = [np.array(dim["step"], float) if "step" in dim else None for dim in self.dims]
        carrier = self._carrier()
        if carrier is not None:
            sample = self.dims[carrier]["samples"][index[carrier]]
            base = np.array(sample["origin"], float)
            if "steps" in sample:
                steps = [np.array(s, float) if s is not None else None for s in sample["steps"]]
        for d, (dim, i) in enumerate(zip(self.dims, index)):
            if d == carrier or steps[d] is None:
                continue
            samples = dim.get("samples") or []
            at = samples[i].get("position", i) if samples else i
            base = base + at * steps[d]
        return base

    def affine(self) -> np.ndarray:
        """The index-to-world affine, columns the steps and the last the origin (§11). Only for
        a regular grid: per-sample positions, origins or steps override it."""
        if any(k in s for dim in self.dims for s in dim.get("samples") or []
               for k in ("position", "origin", "steps")):
            raise ValueError("per-sample placement: use position()")
        n = len(self.axes)
        cols = [np.array(dim["step"], float) if "step" in dim else np.zeros(n) for dim in self.dims]
        last = self.origin if self.origin is not None else np.zeros(n)
        return np.column_stack(cols + [last])

    def cells(self, d: int) -> tuple[float, float] | None:
        """The grid's extent along dimension ``d`` in index units (§5.1): a cell reaches half a
        step beyond its sample, a node none; unknown centering, or a length-1 dimension, gives
        ``None`` (a length-1 dimension's extent is its thickness)."""
        dim, n = self.dims[d], self.lengths[d]
        if "step" not in dim or n is None or n < 2 or "centering" not in dim:
            return None
        if any("position" in s for s in dim.get("samples") or []):
            pos = [s.get("position") for s in dim["samples"]]
            if dim["centering"] == "node":
                return (pos[0], pos[-1])
            return (pos[0] - (pos[1] - pos[0]) / 2, pos[-1] + (pos[-1] - pos[-2]) / 2)
        return (-0.5, n - 0.5) if dim["centering"] == "cell" else (0.0, n - 1.0)

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        """The world bounding box of the grid's cells, for a regular grid whose every stepped
        dimension states its extent."""
        a = self.affine()
        ranges = []
        for d, dim in enumerate(self.dims):
            if "step" not in dim:
                ranges.append((0.0, 0.0))
                continue
            c = self.cells(d)
            if c is None:
                raise ValueError(f"dimension {d} states no extent")
            ranges.append(c)
        import itertools
        pts = [a[:, :-1] @ np.array(corner) + a[:, -1] for corner in itertools.product(*ranges)]
        p = np.array(pts)
        return p.min(0), p.max(0)

    def orientation(self) -> tuple[list[int], list[int], int] | None:
        """§11: for a world with exactly three axes carrying ``positive``, one of each pair: the
        world axis index, sign and LPS axis of each, and the handedness (+1 right-handed)."""
        terms = [(i, a["positive"]) for i, a in enumerate(self.axes) if "positive" in a]
        if len(terms) != 3 or len({_LPS[t][0] for _, t in terms}) != 3:
            return None
        m = np.zeros((3, 3))
        for col, (_, t) in enumerate(terms):
            lps_axis, sign = _LPS[t]
            m[lps_axis, col] = sign
        return ([i for i, _ in terms], [_LPS[t][1] for _, t in terms],
                int(round(np.linalg.det(m))))

    # ---- values ---------------------------------------------------------------------------

    def _color_unorm(self) -> float | None:
        if self.data_type is None or not np.issubdtype(np.dtype(self.data_type), np.unsignedinteger):
            return None
        if any(dim.get("components") in UNORM_KINDS for dim in self.dims):
            return float(np.iinfo(np.dtype(self.data_type)).max)
        return None

    def quantity(self, stored, index: dict[int, int] | None = None) -> np.ndarray:
        """The quantity for ``stored`` values (§6). ``stored`` is the whole array (the header's
        shape), or values at one position, with ``index`` giving it along an ``axis_linear``
        dimension. Raises :class:`QuantityUnknown` when the mapping is not stated or cannot be
        applied, never guessing."""
        values = self.values or {}
        transforms = values.get("transforms")
        if transforms is None:
            raise QuantityUnknown("values.transforms is absent: the mapping is not stated")
        if any(f.kind == "quantity" for f in self.findings):
            raise QuantityUnknown("; ".join(f.message for f in self.findings if f.kind == "quantity"))
        x = np.asarray(stored)
        if transforms == []:
            scale = self._color_unorm()
            return x / scale if scale is not None else x
        for t in transforms:
            p = t.get("parameters") or {}
            if t["name"] == "linear":
                x = (_wide(x) * p["slope"]) + p["intercept"]
            elif t["name"] == "lut":
                table = np.asarray(p["values"])
                i = np.clip(x.astype(np.int64) - p.get("first_value", 0), 0, len(table) - 1)
                x = table[i]
            elif t["name"] == "axis_linear":
                d = p["dimension"]
                slope, intercept = (np.asarray(p[k], float) for k in ("slope", "intercept"))
                if index is not None:
                    i = index[d]
                    s = slope[i] if slope.ndim else slope
                    c = intercept[i] if intercept.ndim else intercept
                    x = _wide(x) * s + c
                else:
                    shape = [1] * x.ndim
                    shape[d] = -1
                    x = (_wide(x) * (slope.reshape(shape) if slope.ndim else slope)
                         + (intercept.reshape(shape) if intercept.ndim else intercept))
        return x

    def missing_mask(self, stored) -> np.ndarray | None:
        """Which elements are ``missing`` (§6), element by element; ``None`` where the file
        states no usable ``missing``. The quantity is computed as §6 fixes it: the product
        rounded, then the sum rounded (NumPy's two ufuncs), never fused."""
        values = self.values or {}
        if "missing" not in values or any(f.kind == "ignored" and "missing" in f.message
                                          for f in self.findings):
            return None
        q = self.quantity(stored)
        return np.isin(q, np.asarray(values["missing"], float))
