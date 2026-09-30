"""What a reader must recover from each scenario, computed from `scenarios.md`'s facts alone.

Never from a header: this file is the independent side of every check against the reference
headers (a header checked against itself always passes). Run it to regenerate `truth.json`.

For each scenario with a placed grid: `first` (the sample at index 0 of every dimension),
`plus1` (one index further along each dimension, one at a time, in array order), `last`, and
`box` (the extent, a cell reaching half a step beyond its sample, a node reaching nothing).
Coordinates are in world-axis order as `order` names it, each in the unit the scenario states.
Other facts are prose, for the questionnaire.
"""

import itertools
import json
from pathlib import Path

import numpy as np

T = {}


def _box(o, steps, shape, cell):
    lo = [-0.5 if c else 0.0 for c in cell]
    hi = [n - 0.5 if c else n - 1 for n, c in zip(shape, cell)]
    pts = [o + sum(ci * si for ci, si in zip(corner, steps))
           for corner in itertools.product(*zip(lo, hi))]
    p = np.array(pts)
    return p.min(0).round(4).tolist(), p.max(0).round(4).tolist()


def rec(name, order, origin, steps, shape, cell=True, **facts):
    """A regular grid: steps in array order, `cell` one flag or one per dimension."""
    o = np.array(origin, float)
    s = [np.array(v, float) for v in steps]
    cell = [cell] * len(s) if isinstance(cell, bool) else list(cell)
    last = o + sum((n - 1) * v for n, v in zip(shape, s))
    T[name] = dict(order=order, shape=shape, first=o.round(4).tolist(),
                   plus1=[(o + v).round(4).tolist() for v in s],
                   last=last.round(4).tolist(), box=_box(o, s, shape, cell), **facts)


LPS, RAS = ["x (left)", "y (posterior)", "z (superior)"], ["x (right)", "y (anterior)", "z (superior)"]

# ---- rounds 1-3 -------------------------------------------------------------------------
rec("S1", LPS, [-180, -200, -350],
    [[0, 2.5 * 0.0872, 2.5 * 0.9962], [0, 0.7 * 0.9962, -0.7 * 0.0872], [0.7, 0, 0]],
    [120, 512, 512], value_1000="-24 HU")
rec("S2", RAS + ["t (s)"], [-96, -126, -72, 0],
    [[0, 0, 0, 2.0], [0, 0, 3, 0.055], [0, 3, 0, 0], [3, 0, 0, 0]], [200, 36, 64, 64],
    cell=[False, True, True, True],
    time_index2="4.0 s (volume 2, slice 0); slice k is 0.055*k s later",
    value_1000="1000, unit not stated by the source")
T["S3"] = dict(spacing="0.25 um both", origin="not stated",
               value_1000="n/a (uint8 sRGB): stored 255 is the component 1.0")
rec("S4", LPS, [-95, -110, -60], [[0, 0, 2], [0, 2, 0], [2, 0, 0]], [60, 96, 96],
    tensor="D_world = R D R^T with the stated R (patient = R @ scanner)")
rec("S5", LPS, [-127, -127, -69], [[0, 0, 2], [0, 2, 0], [2, 0, 0]], [70, 128, 128])
rec("S6", RAS, [-128, -128, -128], [[0, 0, 1], [0, 1, 0], [1, 0, 0]], [256, 256, 256])
rec("S7", LPS, [10, -120, 90], [[0, 0, -1], [0, 1, 0]], [256, 256],
    thickness="5 mm along the normal (1, 0, 0)")
rec("S8", LPS + ["chemical shift ([ppm])"], [-75, -75, -35, 4.70],
    [[0, 0, 10, 0], [0, 10, 0, 0], [10, 0, 0, 0], [0, 0, 0, -0.0098]], [8, 16, 16, 1024])
rec("S9", RAS, [-90, -126, -72], [[0, 0, 1], [0, 1, 0], [1, 0, 0]], [182, 218, 182],
    mni_first=(np.array([[1.02, 0, 0, -2.5], [0, 0.98, 0, 4.0], [0, 0, 1.01, 1.2]])
               @ np.array([-90, -126, -72, 1])).round(4).tolist())
T["S10"] = dict(first=[0, 0, 0, 0], time_index2="65 s (an instant)",
                spacing="x, y 0.1 um; z 0.5 um")
T["S11"] = dict(first=[-127, -127, -89, 5], time_index2="25 s (frame 2's middle; it covers 20-30 s)",
                value_1000_frame0="1100 Bq/mL")
rec("S12", RAS, [-50, -50, -50], [[0, 0, 1], [0, 1, 0], [1, 0, 0]], [101, 101, 101], cell=False)
rec("S13", LPS, [-200, -200, -300], [[0, 0.44, 1.25], [0, 0.8, 0], [0.8, 0, 0]], [80, 512, 512],
    thickness="1.25 mm, along the slice normal (DICOM)")
rec("S14", RAS, [-96, -96, -96], [[0, 0, 1.5], [0, 1.5, 0], [1.5, 0, 0]], [128, 128, 128],
    vectors="already in the RAS world axes, in mm")

# ---- rounds 4-10 ------------------------------------------------------------------------
CT = dict(order=LPS, origin=[-10, -20, 30], steps=[[0, 0, 2], [0, 0.5, 0], [0.5, 0, 0]],
          shape=[3, 4, 4])
rec("S17", CT["order"], CT["origin"], CT["steps"], CT["shape"],
    value_1000="-24 HU", missing="-3024 HU (stored -2000)", frame="dicom:1.2.3.4")
rec("S18", CT["order"], CT["origin"], CT["steps"], CT["shape"],
    value_1000="1000 HU", missing="-3024 HU", frame="dicom:1.2.3.4")
CINE = dict(order=LPS + ["t (ms, from the R wave)"],
            steps=[[0, 0, 0, 80], [0, 0, 8, 0], [0, 1.5, 0, 0], [1.5, 0, 0, 0]],
            shape=[10, 3, 4, 4], cell=[False, True, True, True])
rec("S19", CINE["order"], [-3, -3, 0, 0], CINE["steps"], CINE["shape"], cell=CINE["cell"],
    time_index2="160 ms after the R wave", frame="dicom:1.2.3.5 (spatial axes only)")
rec("S20", ["x (NRRD axis 0, mm)", "y (NRRD axis 1, mm)"], [-4.875, 10.25],
    [[0, 0.5], [0.25, 0]], [100, 200], frame="none (local zeros)")
# S21: slice k's time is its acquisition rank (order 0, 2, 4, 1, 3, 5) times 0.1 s.
rec("S21", RAS + ["t (s)"], [-12, -12, -9, 0],
    [[0, 0, 0, 2.0], [0, 0, 3, 0.3], [0, 3, 0, 0], [3, 0, 0, 0]], [4, 6, 8, 8],
    cell=[False, True, True, True],
    slice_times=[0.0, 0.3, 0.1, 0.4, 0.2, 0.5],
    note="plus1 along k is slice 1, acquired at 0.3 s; slice times are not a uniform step, "
         "so `last` and `box` along t are not meaningful here: use slice_times",
    time_index2="4.0 s along t (slice 0); 0.1 s along k (slice 2, volume 0)")
rec("S22", CT["order"], [-9.75, -19.75, 30], [[0, 0, 2], [0, 1, 0], [1, 0, 0]], [3, 2, 2],
    value_1000="1000 HU", missing="none may be stated (a linear resample mixes padding)",
    frame="dicom:1.2.3.4")
rec("S23", ["x (um)", "y (um)", "wavelength (nm)"], [0, 0, 500],
    [[0, 0, 5], [0, 0.2, 0], [0.2, 0, 0]], [32, 64, 64],
    compares="wavelength across files; x and y only within the array")
rec("S24", ["x (mm)", "y (um)"], [1, 2], [[0.5, 0], [0, 250]], [30, 40],
    note="each axis in its own unit")
T["S25"] = dict(same_as="S18")
rec("S26", CINE["order"], [-3, -3, 0, 20], CINE["steps"], CINE["shape"], cell=CINE["cell"],
    time_index2="180 ms after the R wave")

if __name__ == "__main__":
    out = Path(__file__).with_name("truth.json")
    out.write_text(json.dumps(T, indent=1) + "\n")
    print(f"wrote {out} ({len(T)} scenarios)")
