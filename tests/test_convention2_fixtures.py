"""The convention 2.0 fixtures, held to each other before any 2.0 reader exists.

`tests/data/convention-2.0/` keeps what the draft's adversarial review rounds used (2026-09-27
to 2026-09-30): the scenarios' source facts, the questionnaire, the reference headers written
against revision 14, and `truth.json`, computed by `truth.py` from the scenarios' facts alone.
Here each reference header's positions and extents are computed from the header and compared
with `truth.json` - two independent sources, so a header or a truth that drifts fails. The 2.0
reader (step 4 of the plan) will be tested against the same files; the faults and legacy files
record the verdicts it must reach (README.md).
"""

import itertools
import json
import unittest
from pathlib import Path

import numpy as np

DATA = Path(__file__).parent / "data" / "convention-2.0"
TRUTH = json.loads((DATA / "truth.json").read_text())


def _duckn(path):
    return json.loads(path.read_text())["duckn"]


def _position(d, index):
    """origin + sum of index * step; a dimension whose samples carry `origin` replaces it."""
    point = np.array(d["origin"], float)
    for k, (dim, i) in enumerate(zip(d["dimensions"], index)):
        samples = dim.get("samples") or []
        if samples and "origin" in samples[i]:
            point = point - np.array(d["origin"], float) + np.array(samples[i]["origin"], float)
        elif "step" in dim:
            point = point + i * np.array(dim["step"], float)
    return point


def _box(d, shape):
    lo, hi = [], []
    for dim, n in zip(d["dimensions"], shape):
        cell = dim.get("centering") == "cell"
        lo.append(-0.5 if cell else 0.0)
        hi.append(n - 0.5 if cell else n - 1)
    origin = np.array(d["origin"], float)
    steps = [np.array(dim["step"], float) for dim in d["dimensions"]]
    pts = [origin + sum(c * s for c, s in zip(corner, steps))
           for corner in itertools.product(*zip(lo, hi))]
    p = np.array(pts)
    return p.min(0), p.max(0)


class TestReferenceHeadersAgainstTruth(unittest.TestCase):
    """S17-S26: each reference header places its samples where the scenario's facts do."""

    def test_every_scenario_from_s17_has_a_reference_header(self):
        names = sorted(p.stem for p in (DATA / "headers").glob("S*.json"))
        self.assertEqual(names, [f"S{n}" for n in range(17, 27)])

    def test_each_header_is_a_2_0_file_with_one_component_per_world_axis(self):
        for path in sorted((DATA / "headers").glob("*.json")):
            with self.subTest(path.stem):
                d = _duckn(path)
                self.assertEqual(d["version"], "2.0")
                n = len(d["world"]["axes"])
                self.assertEqual(len(d["origin"]), n)
                for dim in d["dimensions"]:
                    if "step" in dim:
                        self.assertEqual(len(dim["step"]), n)
                    for s in dim.get("samples", []):
                        if "origin" in s:
                            self.assertEqual(len(s["origin"]), n)
                self.assertIn("provenance", d["extensions"], "§9: every writer records itself")

    def test_positions(self):
        for path in sorted((DATA / "headers").glob("*.json")):
            name = path.stem
            truth = TRUTH[TRUTH[name].get("same_as", name)]
            d = _duckn(path)
            shape = truth["shape"]
            zero = [0] * len(shape)
            with self.subTest(name, point="first"):
                np.testing.assert_allclose(_position(d, zero), truth["first"], atol=1e-4)
            for k in range(len(shape)):
                one = list(zero)
                one[k] = 1
                with self.subTest(name, point=f"plus1 along {k}"):
                    np.testing.assert_allclose(_position(d, one), truth["plus1"][k], atol=1e-4)
            if name == "S21":
                continue  # slice times are no uniform step (truth.py's note)
            with self.subTest(name, point="last"):
                np.testing.assert_allclose(_position(d, [n - 1 for n in shape]), truth["last"],
                                           atol=1e-4)

    def test_extents(self):
        for path in sorted((DATA / "headers").glob("*.json")):
            name = path.stem
            if name == "S21":
                continue
            truth = TRUTH[TRUTH[name].get("same_as", name)]
            lo, hi = _box(_duckn(path), truth["shape"])
            with self.subTest(name):
                np.testing.assert_allclose(lo, truth["box"][0], atol=1e-4)
                np.testing.assert_allclose(hi, truth["box"][1], atol=1e-4)

    def test_s21_slice_times(self):
        d = _duckn(DATA / "headers" / "S21.json")
        times = [s["origin"][3] for s in d["dimensions"][1]["samples"]]
        self.assertEqual(times, TRUTH["S21"]["slice_times"])


class TestTruthIsComputedFromTheScenarios(unittest.TestCase):
    def test_truth_json_is_what_truth_py_writes(self):
        import runpy

        computed = runpy.run_path(str(DATA / "truth.py"), run_name="not_main")["T"]
        self.assertEqual(json.loads(json.dumps(computed)), TRUTH,
                         "truth.json is stale: run tests/data/convention-2.0/truth.py")


class TestFaultsAndLegacyFilesKeepTheirPlantedFaults(unittest.TestCase):
    """The files a 2.0 reader must reach a stated verdict on (README.md). A fixture 'fixed' by
    accident would make that test pass for the wrong reason."""

    def test_f1_keeps_missing_through_a_linear_resample(self):
        d = _duckn(DATA / "faults" / "F1-missing-through-linear-resample.json")
        self.assertIn("missing", d["values"])
        self.assertIn("linear", d["extensions"]["provenance"]["processing"][-1]["name"])

    def test_f2_axis_linear_is_one_entry_short(self):
        d = _duckn(DATA / "faults" / "F2-axis-linear-one-short.json")
        (t,) = d["values"]["transforms"]
        dim = d["dimensions"][t["parameters"]["dimension"]]
        self.assertEqual(len(t["parameters"]["slope"]) + 1, len(dim["samples"]))

    def test_f3_states_stored_padding_beside_hounsfield_units(self):
        d = _duckn(DATA / "faults" / "F3-stored-padding-beside-hounsfield.json")
        dicom = d["extensions"]["dicom"]
        self.assertFalse(dicom["stored_values"])
        self.assertEqual(dicom["tags"]["PixelPaddingValue"], -2000)
        self.assertEqual(d["values"]["transforms"], [])

    def test_legacy_files_declare_what_readme_says(self):
        versions = {p.stem: _duckn(p).get("version") for p in (DATA / "legacy").glob("*.json")}
        self.assertEqual(versions, {"L1-version-1.2": "1.2", "L2-version-1.1": "1.1",
                                    "L3-dwi-1.0-malformed": "1.0",
                                    "L4-no-version-with-1.1-field": None})
