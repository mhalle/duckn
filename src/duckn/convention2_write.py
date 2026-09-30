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
            domain_axes: str | None = None, time_tag: str | None = None,
            missing: list[float] | None = None) -> dict:
    """A converter's 1.x ``duckn`` object as the 2.0 object a 2.0 converter writes.

    ``what`` names the conversion for the provenance step (§9); ``source_format`` selects the
    format's finishing and is recorded as the source. ``domain_axes="space"`` is the caller's
    assertion that a no-space NRRD's ``domain`` axes are spatial (§19 item 15), recorded in the
    step. ``time_tag`` is the DICOM attribute a series' time positions came from
    (``"TriggerTime"`` or ``"AcquisitionTime"``), which the 1.x metadata does not record.
    ``missing`` is the caller's ``values.missing``, in the quantity's units: a writer that
    materialized a source knows what its padding became (a CT's Pixel Padding Value through the
    rescale it applied), and by then the 1.x record has dropped it; refused where §6 forbids one.
    The result has been read back and is valid.
    """
    mapped = from_1x(duckn1, shape, data_type)
    d = mapped.duckn
    parameters: dict[str, Any] = {}
    if source_format == "NRRD":
        _finish_nrrd(d, duckn1, shape, domain_axes, parameters)
    elif source_format == "DICOM":
        _finish_dicom(d, time_tag)
    elif source_format == "NIfTI":
        _finish_nifti(d, shape)
    _finish_dwmri(d, mapped.gradient_frame)
    _one_encoding(d)
    if missing is not None:
        values = d.setdefault("values", {})
        t = values.get("transforms")
        if not (t == [] or (isinstance(t, list) and len(t) == 1 and t[0].get("name") == "linear"
                            and (t[0].get("parameters") or {}).get("slope", 0) != 0)):
            raise ValueError("values.missing is stated only under transforms [] or one linear "
                             "of non-zero slope (§6)")
        values["missing"] = [int(m) if float(m).is_integer() else float(m) for m in missing]
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
    # What the core has no field for stays here (nrrd 0.2 §2): a range axis's units, the kind of
    # a domain axis that states nothing, and a frame no components dimension carries.
    for k, a in enumerate(axes1):
        dim = d["dimensions"][k] if k < len(d["dimensions"]) else {}
        keep = {}
        if "components" in dim and a.get("unit") is not None:
            keep["unit"] = a["unit"]
        if a.get("kind") in ("domain", "space", "time") and "step" not in dim:
            keep["kind"] = a["kind"]
        if keep:
            dim.setdefault("extensions", {}).setdefault("nrrd", {}).update(keep)
    frame = duckn1.get("measurement_frame")
    if frame is not None and "dwmri" not in ext and not any(
            "frame" in dim for dim in d["dimensions"]):
        rows = frame if tuple(int(x) for x in str(duckn1.get("version") or "1.0").split(".")[:2]) \
            >= (1, 1) else [list(c) for c in zip(*frame)]
        nrrd = ext.setdefault("nrrd", {}) if nrrd is None else nrrd
        nrrd["measurement_frame"] = [[float(v) for v in r] for r in rows]
        ext["nrrd"] = nrrd
    nrrd = ext.get("nrrd")
    if nrrd is not None or any("nrrd" in (dim.get("extensions") or {}) for dim in d["dimensions"]):
        # a dimension's block is read under the top-level one, which it requires (duckn 2.0 §2.3)
        ext["nrrd"] = {"version": "0.2", **{k: v for k, v in (nrrd or {}).items() if k != "version"}}
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


# ---- DICOM (§3.2, §17's dicom row) -------------------------------------------------------------


def _finish_dicom(d: dict, time_tag: str | None) -> None:
    dicom = (d.get("extensions") or {}).get("dicom")
    if not isinstance(dicom, dict):
        return
    dicom["version"] = "2.0"
    tags = dicom.get("tags") or {}
    world = d.get("world")
    # The Frame of Reference names the spatial frame; the UID stays in the record (§2.3). The
    # Synchronization Frame of Reference stays in the record only: no time here is measured
    # from its zero (§3.2).
    uid = tags.get("FrameOfReferenceUID")
    if world is not None and isinstance(uid, str) and uid:
        world["reference"] = f"dicom:{uid}"
        world["axes"] = world.pop("axes")  # reference first, as the draft writes it
    _dicom_times(d, time_tag)
    _dicom_padding(d, dicom, tags)


def _dicom_times(d: dict, time_tag: str | None) -> None:
    """A time axis from Trigger Time is instants after the R wave, in ms; one from a time of
    day (Acquisition Time, measured from the first frame) is in s, with no centering: when in
    its acquisition a frame was stamped is not stated without a duration (§5.1, §17)."""
    axes = (d.get("world") or {}).get("axes") or []
    for dim in d.get("dimensions", []):
        step = dim.get("step")
        if step is None:
            continue
        along = [j for j, v in enumerate(step) if v != 0]
        if len(along) != 1 or axes[along[0]].get("type") != "time":
            continue
        axis = axes[along[0]]
        samples = dim.get("samples") or []
        if time_tag == "TriggerTime":
            axis["name"] = "time after R wave"
            dim["centering"] = "node"
            for s in samples:  # the record keeps each phase's Trigger Time (§2.3)
                if "position" in s:
                    t = s["position"]
                    s.setdefault("metadata", {}).setdefault("dicom", {})["TriggerTime"] = (
                        int(t) if float(t).is_integer() else t)
        elif axis.get("unit") == "ms":
            axis["unit"] = "s"
            for s in samples:
                if "position" in s:
                    s["position"] = s["position"] / 1000.0
            dim.pop("centering", None)


def _dicom_padding(d: dict, dicom: dict, tags: dict) -> None:
    """A single Pixel Padding Value restated as values.missing, where §6 allows it."""
    values = d.get("values") or {}
    padding = tags.get("PixelPaddingValue")
    if padding is None or not dicom.get("stored_values") or "PixelPaddingRangeLimit" in tags:
        return
    transforms = values.get("transforms")
    if transforms == []:
        missing = padding
    elif (isinstance(transforms, list) and len(transforms) == 1
          and transforms[0].get("name") == "linear"
          and transforms[0]["parameters"].get("slope", 0) != 0):
        p = transforms[0]["parameters"]
        missing = float(padding) * p["slope"] + p["intercept"]  # §6: product, then sum
    else:
        return
    if float(missing).is_integer():
        missing = int(missing)
    values["missing"] = [missing]


# ---- NIfTI (§5.1, §17's nifti row) -------------------------------------------------------------

_XFORM_REFERENCE = {3: "nifti:talairach", 4: "nifti:mni152"}  # codes 1, 2, 5 name no shared frame


def _slice_ranks(code: str, n: int) -> list[int] | None:
    """Each slice's place in the acquisition order, for a NIfTI ``slice_code``."""
    up, down = list(range(n)), list(range(n - 1, -1, -1))
    order = {
        "sequential-increasing": up,
        "sequential-decreasing": down,
        "alternating-increasing": up[0::2] + up[1::2],
        "alternating-decreasing": down[0::2] + down[1::2],
        "alternating-increasing-2": up[1::2] + up[0::2],
        "alternating-decreasing-2": down[1::2] + down[0::2],
    }.get(code)
    if order is None:
        return None
    ranks = [0] * n
    for rank, k in enumerate(order):
        ranks[k] = rank
    return ranks


def _finish_nifti(d: dict, shape: tuple[int, ...]) -> None:
    from decimal import Decimal

    nifti = (d.get("extensions") or {}).get("nifti")
    if not isinstance(nifti, dict):
        return
    nifti["version"] = "2.0"
    tags = nifti.get("tags") or {}
    world = d.get("world")
    if world is None:
        return
    axes = world["axes"]
    code = tags.get("sform_code") or tags.get("qform_code")
    if code in _XFORM_REFERENCE and "reference" not in world:
        world["reference"] = _XFORM_REFERENCE[code]
    # The core states the affine the world was taken from: the record leaves it out (§2.3).
    legacy_tags = (nifti.get("legacy") or {}).get("tags") or {}
    legacy_tags.pop("sform" if tags.get("sform_code") else "qform", None)
    if "legacy" in nifti and not legacy_tags:
        del nifti["legacy"]

    t = next((j for j, a in enumerate(axes) if a.get("type") == "time"), None)
    origin = d.get("origin")
    for k, dim in enumerate(d["dimensions"]):
        step = dim.get("step")
        if step is not None and t is not None and step[t] != 0 and all(
                v == 0 for j, v in enumerate(step) if j != t) and shape[k] > 1:
            dim["centering"] = "node"  # NIfTI's time points are instants; slice times add to them
    if t is not None and origin is not None and "toffset" in tags:
        origin[t] = tags.pop("toffset")
    # A spectrum in ppm is a chemical shift only where toffset places its first bin (nifti 2.0
    # §2): duckn 1.x placed the first bin at 0 whatever the header said.
    ppm = next((j for j, a in enumerate(axes) if a.get("unit") == "[ppm]" and "type" not in a), None)
    if ppm is not None and origin is not None and "toffset" in tags:
        axes[ppm]["type"] = "chemical-shift"
        axes[ppm]["id"] = "chemical-shift"
        origin[ppm] = tags.pop("toffset")

    timing = tags.get("slice_timing")
    slice_dim = (tags.get("dim_info") or {}).get("slice_dim")
    if not (isinstance(timing, dict) and slice_dim and t is not None and origin is not None):
        return
    k = slice_dim - 1  # duckn's NIfTI import keeps NIfTI's dimension order
    n = shape[k]
    ranks = _slice_ranks(timing.get("code"), n)
    duration = timing.get("duration")
    if ranks is None or not duration or timing.get("start", 0) != 0 or timing.get("end", n - 1) != n - 1:
        return  # a partial or unknown order has no geometry: the record keeps what the source said
    # The header states the duration as a float32: compute in its shortest decimal, so that
    # three slices of 0.1 s are 0.3 s and not 0.30000000447 (§9: as the source states it).
    unit = Decimal(str(np.float32(duration)))
    times = [float(unit * r) for r in ranks]
    dim = d["dimensions"][k]
    step = [float(v) for v in dim["step"]]
    if timing["code"].startswith("sequential"):
        # a step through space and time (§5.1), in the same decimal terms as the times
        step[t] = float(unit * (ranks[1] - ranks[0])) if n > 1 else 0.0
        dim["step"] = step
        origin[t] = origin[t] + times[0]
    else:
        base = np.array(origin, float)
        samples = dim.get("samples") or [{} for _ in range(n)]
        for i, s in enumerate(samples):
            point = base + i * np.array(step)
            point[t] = origin[t] + times[i]
            s["origin"] = point.tolist()
        dim["samples"] = samples
        origin[t] = samples[0]["origin"][t]  # samples[0].origin equals origin (§5.4)
    del tags["slice_timing"]
    if not tags:
        nifti.pop("tags", None)


# ---- dwmri (dwmri 2.0) -------------------------------------------------------------------------


def _finish_dwmri(d: dict, gradient_frame) -> None:
    """A dwmri 1.0 block as 2.0 writes it: the gradients' frame as a matrix of its own (the §14
    mapping's reading of gradient_frame and the 1.x measurement_frame; absent where it is
    unknown), the DWI dimension a list, the intent stated, the key/value record that restated
    the gradients left out (dwmri 2.0 §3, §5)."""
    dw = (d.get("extensions") or {}).get("dwmri")
    if not isinstance(dw, dict):
        return
    dw["version"] = "2.0"
    dw.pop("gradient_frame", None)
    dw.pop("legacy", None)
    if gradient_frame is not None:
        dw["frame"] = [[float(v) for v in r] for r in gradient_frame]
    acq = dw.get("acquisition")
    if isinstance(acq, dict):
        # 1.0 named an image axis ("j-") in a layout the file does not fix: not carried
        acq.pop("phase_encoding_direction", None)
    d.setdefault("intent", "diffusion-weighted")
