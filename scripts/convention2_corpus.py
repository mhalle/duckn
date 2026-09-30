"""Hold the convention 2.0 reader and writers to real files.

    python scripts/convention2_corpus.py PATH [PATH ...]

Each PATH is a DICOM series directory, a NIfTI file or a NRRD file. It is converted twice, as
duckn writes today (1.x) and with ``convention="2.0"``, and both stores are read through the 2.0
reader (the 1.x one through the §14 mapping). The checks compare them with each other and with a
reader that shares none of duckn's code: pydicom's own Image Position, orientation, spacing,
rescale and padding for DICOM; nibabel's affine and scaling for NIfTI; pynrrd's header for NRRD.
A disagreement is a defect in a converter, in the reader, or in the draft - which is the point.

Exit status 1 when any check fails. Nothing is written outside a temporary directory.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import zarr

from duckn.convention2 import QuantityUnknown, read
from duckn.convention2_legacy import read_any


class Report:
    def __init__(self, name):
        self.name, self.lines, self.failed = name, [], 0

    def check(self, what, ok, detail=""):
        self.failed += not ok
        self.lines.append(f"  {'ok  ' if ok else 'FAIL'} {what}{(': ' + detail) if detail else ''}")

    def note(self, text):
        self.lines.append(f"  note {text}")

    def close(self, a, b, tol=1e-6):
        a, b = np.asarray(a, float), np.asarray(b, float)
        return a.shape == b.shape and bool(np.allclose(a, b, atol=tol, rtol=0))


def _open(path):
    arr = zarr.open_array(str(path), mode="r")
    return arr, dict(arr.attrs)["duckn"]


def _probes(shape, rng, n=6):
    corners = [[0] * len(shape), [s - 1 for s in shape]]
    corners += [[(s - 1) if k == j else 0 for k, s in enumerate(shape)] for j in range(len(shape))]
    corners += [[int(rng.integers(0, s)) for s in shape] for _ in range(n)]
    return corners


def _both(convert, src, tmp, rep, **kw):
    out = {}
    for conv in ("1.x", "2.0"):
        dst = Path(tmp) / f"{conv}.zarr"
        t = time.time()
        convert(src, dst, convention=conv, **kw)
        arr, d = _open(dst)
        out[conv] = (arr, d, time.time() - t)
    a1, d1, t1 = out["1.x"]
    a2, d2, t2 = out["2.0"]
    rep.note(f"shape {a2.shape} {a2.dtype}; 1.x declares {d1.get('version')}; "
             f"converted in {t1:.1f} s / {t2:.1f} s")
    h1 = read_any(d1, a1.shape, str(a1.dtype))
    h2 = read(d2, a2.shape, str(a2.dtype))
    for f in h1.findings + h2.findings:
        rep.note(f"finding [{f.kind}] {f.message}")
    rng = np.random.default_rng(0)
    probes = _probes(a2.shape, rng)
    rep.check("the 1.x store through §14 and the 2.0 store place samples alike",
              all(rep.close(h1.position(p), h2.position(p)) for p in probes))
    return a2, d2, h1, h2, probes


def _quantity(h, stored, index=None):
    try:
        return h.quantity(stored, index=index)
    except QuantityUnknown as e:
        return e


# ---- DICOM -------------------------------------------------------------------------------------


def check_dicom(src: Path, tmp) -> Report:
    import pydicom
    from duckn.dicom_convert import dicom_to_zarr

    rep = Report(f"DICOM {src}")
    files = [Path(r) / n for r, _, ns in os.walk(src) for n in ns
             if not n.startswith(".") and not n.endswith((".json", ".txt", ".md"))]
    heads = [pydicom.dcmread(str(f), stop_before_pixels=True, force=True) for f in files]
    keep = [(f, h) for f, h in zip(files, heads) if "ImagePositionPatient" in h]
    iop = np.array(keep[0][1].ImageOrientationPatient, float)
    row_dir, col_dir = iop[:3], iop[3:]
    normal = np.cross(row_dir, col_dir)
    keep.sort(key=lambda fh: float(np.dot(normal, np.array(fh[1].ImagePositionPatient, float))))
    ds0 = keep[0][1]
    ps = [float(x) for x in ds0.PixelSpacing]
    rep.note(f"{len(keep)} instances, {ds0.get('Modality')} {ds0.get('Manufacturer')}, "
             f"padding {ds0.get('PixelPaddingValue')}, rescale {ds0.get('RescaleSlope')} "
             f"{ds0.get('RescaleIntercept')}")

    a2, d2, h1, h2, probes = _both(dicom_to_zarr, src, tmp, rep)
    if a2.ndim != 3 or a2.shape[0] != len(keep):
        rep.note(f"array shape {a2.shape} is not one slice per instance: the pydicom checks "
                 f"are skipped")
        return rep

    def truth(i, r, c):
        ipp = np.array(keep[i][1].ImagePositionPatient, float)
        return ipp + r * ps[0] * col_dir + c * ps[1] * row_dir

    rep.check("positions equal pydicom's Image Position, orientation and spacing",
              all(rep.close(h2.position(p)[:3], truth(*p), 1e-4) for p in probes))
    uid = ds0.get("FrameOfReferenceUID")
    rep.check("world.reference is the Frame of Reference UID",
              d2["world"].get("reference") == (f"dicom:{uid}" if uid else None))
    o = h2.orientation()
    rep.check("the world is LPS, right-handed", o is not None and o[1] == [1, 1, 1] and o[2] == 1)

    rng = np.random.default_rng(1)
    slices = sorted({0, len(keep) // 2, len(keep) - 1, int(rng.integers(0, len(keep)))})
    same_pixels = same_quantity = same_missing = True
    n_missing = 0
    for i in slices:
        ds = pydicom.dcmread(str(keep[i][0]), force=True)
        px = ds.pixel_array
        stored = a2[i]
        same_pixels &= bool(np.array_equal(px, stored))
        slope = float(ds.get("RescaleSlope", 1) or 1)
        inter = float(ds.get("RescaleIntercept", 0) or 0)
        expect = px.astype(np.float64) * slope + inter
        q = _quantity(h2, stored, index={0: i})
        same_quantity &= not isinstance(q, Exception) and bool(np.array_equal(q, expect))
        pad = ds.get("PixelPaddingValue")
        if pad is not None:
            full = np.zeros(a2.shape[1:], bool)
            mask = h2.missing_mask(stored)
            if mask is None:
                same_missing = False
            else:
                same_missing &= bool(np.array_equal(mask, px == pad))
                n_missing += int(mask.sum())
            del full
    rep.check("stored values equal pydicom's pixel_array", same_pixels)
    rep.check("the quantity equals pydicom's rescale, voxel for voxel", same_quantity)
    rescales = {(float(h.get("RescaleSlope", 1) or 1), float(h.get("RescaleIntercept", 0) or 0))
                for _, h in keep}
    if ds0.get("PixelPaddingValue") is not None and "PixelPaddingRangeLimit" not in ds0:
        if len(rescales) == 1:
            rep.check("values.missing marks exactly the padded voxels", same_missing,
                      f"{n_missing} padded voxels in {len(slices)} slices")
        else:
            rep.check("no values.missing under a rescale that varies by slice (§6)",
                      "missing" not in (d2.get("values") or {}),
                      f"{len(rescales)} rescales in the series")
    q1 = _quantity(h1, a2[slices[0]], index={0: slices[0]})
    q2 = _quantity(h2, a2[slices[0]], index={0: slices[0]})
    rep.check("the 1.x store through §14 gives the same quantity",
              not isinstance(q1, Exception) and np.array_equal(q1, q2))
    return rep


def check_multiframe(src: Path, tmp) -> Report:
    """One multi-frame (Enhanced) DICOM file, against its functional groups as pydicom reads
    them."""
    import pydicom
    from duckn.dicom_convert import dicom_to_zarr

    rep = Report(f"DICOM multi-frame {src}")
    ds = pydicom.dcmread(str(src), force=True)
    n = int(ds.NumberOfFrames)
    shared = ds.SharedFunctionalGroupsSequence[0] if "SharedFunctionalGroupsSequence" in ds else None
    per = list(ds.get("PerFrameFunctionalGroupsSequence", []))
    rep.note(f"{n} frames, {ds.get('Modality')} {ds.SOPClassUID.name}, "
             f"{'with' if per else 'without'} per-frame functional groups")
    a2, d2, h1, h2, probes = _both(dicom_to_zarr, src, tmp, rep)

    def group(i, name):
        for g in ([per[i]] if per else []) + ([shared] if shared is not None else []):
            if name in g:
                return g[name][0]
        return None

    if not per or group(0, "PlanePositionSequence") is None:
        rep.note("no per-frame positions: only the 1.x and 2.0 stores are compared")
        return rep
    iop = np.array(group(0, "PlaneOrientationSequence").ImageOrientationPatient, float)
    row_dir, col_dir = iop[:3], iop[3:]
    normal = np.cross(row_dir, col_dir)
    ipps = [np.array(group(i, "PlanePositionSequence").ImagePositionPatient, float) for i in range(n)]
    ps = [float(x) for x in group(0, "PixelMeasuresSequence").PixelSpacing]
    if a2.ndim != 3 or a2.shape[0] != n:
        rep.note(f"array shape {a2.shape} is not one slice per frame (a 4D or grouped layout): "
                 f"frame-by-frame checks are skipped")
        return rep
    order = sorted(range(n), key=lambda i: float(np.dot(normal, ipps[i])))
    ok = all(rep.close(h2.position([k, r, c])[:3],
                       ipps[i] + r * ps[0] * col_dir + c * ps[1] * row_dir, 1e-4)
             for k, i in enumerate(order) for r, c in ((0, 0), (a2.shape[1] - 1, a2.shape[2] - 1)))
    rep.check("every frame's position equals its Plane Position, orientation and spacing", ok)
    px = ds.pixel_array
    rep.check("stored values equal pydicom's pixel_array, frame for frame",
              all(np.array_equal(px[i], a2[k]) for k, i in enumerate(order)))
    same = True
    for k, i in enumerate(order):
        t = group(i, "PixelValueTransformationSequence")
        slope = float(t.RescaleSlope) if t is not None else 1.0
        inter = float(t.RescaleIntercept) if t is not None else 0.0
        q = _quantity(h2, a2[k], index={0: k})
        same &= not isinstance(q, Exception) and bool(
            np.array_equal(q, px[i].astype(np.float64) * slope + inter))
    rep.check("the quantity equals each frame's own rescale", same)
    return rep


# ---- NIfTI -------------------------------------------------------------------------------------


def check_nifti(src: Path, tmp) -> Report:
    import nibabel as nib
    from duckn.nifti_convert import nifti_to_zarr

    rep = Report(f"NIfTI {src}")
    img = nib.load(str(src))
    hdr = img.header
    rep.note(f"sform {int(hdr['sform_code'])} qform {int(hdr['qform_code'])}, "
             f"scl {float(hdr['scl_slope'])} {float(hdr['scl_inter'])}, "
             f"slice_code {int(hdr['slice_code'])}, units {hdr.get_xyzt_units()}")
    a2, d2, h1, h2, probes = _both(nifti_to_zarr, src, tmp, rep)
    aff = img.affine
    ok = True
    for p in probes:
        if len(p) < 3:
            continue
        expect = aff @ np.r_[p[:3], 1.0]
        ok &= rep.close(h2.position(p)[:3], expect[:3], 1e-3)
    rep.check("positions equal nibabel's affine (RAS)", ok)
    o = h2.orientation()
    rep.check("the world is RAS, right-handed", o is not None and o[1] == [-1, -1, 1] and o[2] == 1)
    zooms = hdr.get_zooms()
    if a2.ndim == 4 and hdr.get_xyzt_units()[1] != "unknown":
        t = next((j for j, a in enumerate(h2.axes) if a.get("type") == "time"), None)
        scale = {"sec": 1.0, "msec": 1.0, "usec": 1.0}[hdr.get_xyzt_units()[1]]
        rep.check("volumes are one TR apart in time",
                  t is not None and abs((h2.position([0, 0, 0, 1])[t] - h2.position([0, 0, 0, 0])[t])
                                        - float(zooms[3]) * scale) < 1e-6,
                  f"TR {float(zooms[3])} {hdr.get_xyzt_units()[1]}")
    sl = tuple(slice(None) if k < 2 else min(3, s - 1) for k, s in enumerate(a2.shape))
    stored = a2[sl]
    unscaled = np.asarray(img.dataobj.get_unscaled()[sl])
    rep.check("stored values equal nibabel's unscaled array", bool(np.array_equal(stored, unscaled)))
    expect = np.asarray(img.dataobj[sl], np.float64)
    q = _quantity(h2, stored)
    rep.check("the quantity equals nibabel's scaled values",
              not isinstance(q, Exception) and bool(np.allclose(q, expect, rtol=1e-6, atol=0)),
              "" if not isinstance(q, Exception) else str(q))
    q1 = _quantity(h1, stored)
    rep.check("the 1.x store through §14 gives the same quantity",
              type(q1) is type(q) and (isinstance(q, Exception) or np.array_equal(q1, q)))
    return rep


# ---- NRRD --------------------------------------------------------------------------------------


def check_nrrd(src: Path, tmp) -> Report:
    import nrrd
    from duckn.convert import nrrd_to_zarr

    rep = Report(f"NRRD {src}")
    header = nrrd.read_header(str(src))
    rep.note(f"space {header.get('space')}, kinds {header.get('kinds')}, "
             f"sizes {list(header['sizes'])}")
    a2, d2, h1, h2, probes = _both(nrrd_to_zarr, src, tmp, rep)
    if "space directions" in header and "space origin" in header:
        dirs = header["space directions"]
        origin = np.array(header["space origin"], float)
        n = len(origin)
        ok = True
        for p in probes:
            fastest_first = p[::-1]
            expect = origin.copy()
            for k, v in enumerate(dirs):
                if v is not None and not np.any(np.isnan(np.asarray(v, float))):
                    expect = expect + fastest_first[k] * np.asarray(v, float)
            ok &= rep.close(h2.position(p)[:n], expect, 1e-6)
        rep.check("positions equal pynrrd's space origin and directions", ok)
    return rep


def main(paths):
    failed = 0
    for p in map(Path, paths):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                if p.is_dir():
                    rep = check_dicom(p, tmp)
                elif p.name.endswith(".dcm"):
                    rep = check_multiframe(p, tmp)
                elif p.name.endswith((".nii", ".nii.gz")):
                    rep = check_nifti(p, tmp)
                elif p.name.endswith((".nrrd", ".nhdr")):
                    rep = check_nrrd(p, tmp)
                else:
                    print(f"{p}: not a DICOM directory, a NIfTI or a NRRD file")
                    failed += 1
                    continue
            except Exception as e:  # a converter or reader that raises is a finding, reported
                rep = Report(str(p))
                rep.check("converts and reads", False, f"{type(e).__name__}: {e}")
        print(rep.name)
        print("\n".join(rep.lines))
        failed += rep.failed
    print(f"\n{'FAILED: ' + str(failed) + ' check(s)' if failed else 'all checks passed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
