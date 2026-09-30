"""Writing convention 2.0 (docs/proposals/duckn-2.0.md, revision 15): EXPERIMENTAL.

duckn's converters build 1.x metadata, tested against their sources. A 2.0 writer does not
repeat them: :func:`upgrade` takes a converter's 1.x result through the §14 mapping
(:mod:`duckn.convention2_legacy`) and finishes it with what 2.0 asks of a converter and 1.x had no
place for - the one-encoding rule for a series (§5.4), the source format's own finishing (§15,
§17), and the writer's provenance step (§9) - then reads the result back through
:func:`duckn.convention2.read`, so nothing invalid is ever handed out.

Behind a flag (a converter's ``convention="2.0"``) because the extension revisions §17 requires
(``nrrd`` 0.2, ``dicom`` 2.0, ``nifti`` 2.0) exist only as that table's rows: a file written
here is for testing the draft, not for keeping. duckn's default stays 1.x.
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np

from duckn.convention2 import read
from duckn.convention2_legacy import _UNIT_SPELLINGS, from_1x

__all__ = ["upgrade"]


def upgrade(duckn1: dict, shape: tuple[int, ...], data_type: str | None = None, *,
            what: str, source_format: str | None = None,
            software: tuple[str, str] | None = None,
            domain_axes: str | None = None) -> dict:
    """A converter's 1.x ``duckn`` object as the 2.0 object a 2.0 converter writes.

    ``what`` names the conversion for the provenance step (§9); ``source_format`` selects the
    format's finishing and is recorded as the source. ``domain_axes="space"`` is the caller's
    assertion that a no-space NRRD's ``domain`` axes are spatial (§19 item 15), recorded in the
    step. The result has been read back and is valid.
    """
    d = from_1x(duckn1, shape, data_type).duckn
    parameters: dict[str, Any] = {}
    if source_format == "NRRD":
        _finish_nrrd(d, duckn1, shape, domain_axes, parameters)
    _one_encoding(d)
    _record(d, what, source_format, software, parameters)
    read(d, shape, data_type)  # refuses what §10 refuses: never write it
    return d


# ---- §5.4: a step or positions -----------------------------------------------------------------


def _one_encoding(d: dict) -> None:
    """Samples evenly spaced along their dimension take a step, never positions (§5.4)."""
    origin = d.get("origin")
    for dim in d.get("dimensions", []):
        samples = dim.get("samples")
        if not samples or "step" not in dim or not all("position" in s for s in samples):
            continue
        pos = np.array([s["position"] for s in samples], float)
        if len(pos) < 2:
            continue
        diffs = np.diff(pos)
        if not np.allclose(diffs, diffs[0], rtol=1e-12, atol=0) or diffs[0] == 0:
            continue
        step = np.array(dim["step"], float)
        if pos[0] != 0:
            if origin is None:
                continue  # no origin to move: the positions are all the file has
            origin[:] = (np.array(origin, float) + pos[0] * step).tolist()
        dim["step"] = (step * diffs[0]).tolist()
        rest = [{k: v for k, v in s.items() if k != "position"} for s in samples]
        if any(rest):
            dim["samples"] = rest
        else:
            del dim["samples"]


# ---- §9: a writer records itself ---------------------------------------------------------------


def _record(d, what, source_format, software, parameters) -> None:
    if software is None:
        from importlib.metadata import PackageNotFoundError, version
        try:
            software = ("duckn", version("duckn"))
        except PackageNotFoundError:  # a source tree on the path, not installed
            software = ("duckn", "unknown")
    ext = d.setdefault("extensions", {})
    prov = ext.get("provenance") or {}
    prov["version"] = "1.1"
    if source_format is not None:
        prov.setdefault("sources", []).append({"format": source_format})
    step: dict[str, Any] = {"name": what, "software": {"name": software[0], "version": software[1]}}
    if parameters:
        step["parameters"] = parameters
    prov.setdefault("processing", []).append(step)
    # sources before processing, as the provenance extension lists them
    ext["provenance"] = {k: prov[k] for k in ("version", "sources", "processing") if k in prov} | {
        k: v for k, v in prov.items() if k not in ("version", "sources", "processing")}


# ---- NRRD (§15.1, §17's nrrd row) --------------------------------------------------------------


def _finish_nrrd(d, duckn1, shape, domain_axes, parameters) -> None:
    axes1 = duckn1.get("axes") or []
    ext = d.setdefault("extensions", {})
    nrrd = ext.get("nrrd")
    no_space = duckn1.get("space") is None and duckn1.get("space_dimension") is None
    placed = [k for k, a in enumerate(axes1)
              if isinstance((a.get("extensions") or {}).get("nrrd"), dict)
              and ("spacing" in a["extensions"]["nrrd"] or (
                  "axis_min" in a["extensions"]["nrrd"] and "axis_max" in a["extensions"]["nrrd"]))]
    if no_space and placed:
        _nrrd_world(d, axes1, placed, shape, domain_axes, parameters)
    if no_space and (placed or nrrd is not None):
        nrrd = ext.setdefault("nrrd", {})
        nrrd["no_space"] = True
    if nrrd is not None:
        nrrd["version"] = "0.2"
        ext["nrrd"] = {"version": "0.2", **{k: v for k, v in nrrd.items() if k != "version"}}
    if not ext:
        del d["extensions"]


def _nrrd_world(d, axes1, placed, shape, domain_axes, parameters) -> None:
    """A no-space NRRD's spacings, units and axis mins as world axes (§15.1)."""
    # §3's order: spatial and untyped axes first, then time, each in NRRD's axis order (fastest
    # first: the reverse of the array's dimensions).
    def group(k):
        return 1 if axes1[k].get("kind") == "time" else 0
    ordered = sorted(reversed(placed), key=group)
    axes, of = [], {}
    n_first = sum(1 for k in ordered if group(k) == 0)
    for j, k in enumerate(ordered):
        a, kind = axes1[k], axes1[k].get("kind")
        if group(k) == 1:
            t = j - n_first
            axis = {"id": "t" if t == 0 else f"t{t}", "type": "time"}
        else:
            axis = {"id": ["x", "y", "z"][j] if j < 3 else f"a{j}"}
            if kind == "space" or (kind == "domain" and domain_axes == "space"):
                axis["type"] = "space"
        if a.get("unit") is not None:
            u = a["unit"]
            axis["unit"] = _UNIT_SPELLINGS.get(u, u) if isinstance(u, str) else copy.deepcopy(u)
        axes.append(axis)
        of[k] = j
    if domain_axes == "space" and any(axes1[k].get("kind") == "domain" for k in placed):
        parameters["domain_axes"] = "space"  # the caller's assertion (§19 item 15)
    n = len(axes)
    origin: list[float] | None = [0.0] * n
    for k in placed:
        a, dim = axes1[k], d["dimensions"][k]
        info = a["extensions"]["nrrd"]
        centering = a.get("centering") or "cell"  # NRRD's default, written (§15.1)
        size = shape[k]
        spacing = info.get("spacing")
        if spacing is None:
            span = info["axis_max"] - info["axis_min"]
            spacing = span / size if centering == "cell" else span / (size - 1)
        step = [0.0] * n
        step[of[k]] = float(spacing)
        dim["step"] = step
        if size > 1:
            dim["centering"] = centering
        if "axis_min" in info and origin is not None:
            origin[of[k]] = info["axis_min"] + (spacing / 2 if centering == "cell" else 0.0)
        else:
            origin = None  # §15.1: an origin only when every such axis states a min
        rest = {kk: v for kk, v in info.items() if kk not in ("spacing", "axis_min", "axis_max")}
        dim_ext = dim.get("extensions") or {}
        if rest:
            dim_ext["nrrd"] = rest
        else:
            dim_ext.pop("nrrd", None)
        if dim_ext:
            dim["extensions"] = dim_ext
        else:
            dim.pop("extensions", None)
    d["world"] = {"axes": axes}
    if origin is not None:
        d["origin"] = origin
    d["dimensions"] = d.pop("dimensions")  # keep world, origin, dimensions in the draft's order
    if "values" in d:
        d["values"] = d.pop("values")
    if "extensions" in d:
        d["extensions"] = d.pop("extensions")
