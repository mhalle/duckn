"""Writing 2.0 (duckn.convention2_write): a converter's output against the review's references.

The strongest check a writer has: the scenario's source, converted, must equal the reference
header two independent writers agreed on in the draft's review rounds
(tests/data/convention-2.0/headers). The rest hold the writer to the reader and to duckn's 1.x
geometry on every NRRD in tests/data.
"""

import json
import unittest
import warnings
from pathlib import Path

import nrrd
import numpy as np
import zarr

from duckn.convention2 import read
from duckn.convention2_write import upgrade
from duckn.convert import _header_to_metadata, nrrd_to_zarr
from duckn.models import DucknMetadata
from duckn.spatial import VolumeGeometry

DATA = Path(__file__).parent / "data"
REF = DATA / "convention-2.0" / "headers"

# scenarios.md's NRRD headers, as pynrrd hands them over (axes fastest first)
S20 = {"type": "uint8", "dimension": 2, "sizes": np.array([200, 100]),
       "spacings": np.array([0.25, 0.5]), "units": ["mm", "mm"], "axis mins": np.array([-5.0, 10.0]),
       "centerings": ["cell", "cell"], "kinds": ["domain", "domain"]}
S24 = {"type": "float", "dimension": 2, "sizes": np.array([40, 30]), "space dimension": 2,
       "space units": ["mm", "um"], "space directions": np.array([[0, 250.0], [0.5, 0]]),
       "space origin": np.array([1.0, 2.0]), "centerings": ["cell", "cell"],
       "kinds": ["space", "space"]}


def _convert(header, **kw):
    meta, _ = _header_to_metadata(header, header["dimension"])
    shape = tuple(int(n) for n in reversed(header["sizes"]))
    return upgrade(meta.model_dump(by_alias=True, exclude_none=True, mode="json"), shape,
                   what="convert NRRD", source_format="NRRD", software=("duckn", "0.7.0"), **kw)


def _reference(name):
    return json.loads((REF / f"{name}.json").read_text())["duckn"]


class TestAgainstTheReferenceHeaders(unittest.TestCase):
    maxDiff = None

    def test_s20_a_nrrd_with_no_space(self):
        self.assertEqual(_convert(S20), _reference("S20"))

    def test_s24_a_nrrd_in_two_spatial_units(self):
        self.assertEqual(_convert(S24), _reference("S24"))

    def test_the_callers_assertion_types_domain_axes_and_is_recorded(self):
        d = _convert(S20, domain_axes="space")
        self.assertEqual({a.get("type") for a in d["world"]["axes"]}, {"space"})
        self.assertEqual(d["extensions"]["provenance"]["processing"][-1]["parameters"],
                         {"domain_axes": "space"})


class TestOneEncoding(unittest.TestCase):
    def _file(self, positions):
        return {"version": "1.2", "space": "left-posterior-superior", "space_origin": [0.0, 0.0, 10.0],
                "value_transforms": [],
                "axes": [{"kind": "space", "space_direction": [0, 0, 2.0], "unit": "mm",
                          "samples": [{"position": p} for p in positions]},
                         {"kind": "space", "space_direction": [0, 1.0, 0], "unit": "mm"},
                         {"kind": "space", "space_direction": [1.0, 0, 0], "unit": "mm"}]}

    def test_evenly_spaced_positions_become_a_step(self):
        d = upgrade(self._file([3.0, 8.0, 13.0]), (3, 2, 2), what="convert")
        self.assertEqual(d["dimensions"][0], {"step": [0.0, 0.0, 5.0]})
        self.assertEqual(d["origin"], [0.0, 0.0, 13.0])
        np.testing.assert_allclose(read(d, (3, 2, 2)).position([2, 0, 0]), [0, 0, 23.0])

    def test_uneven_positions_stay_positions(self):
        d = upgrade(self._file([0.0, 2.0, 5.0]), (3, 2, 2), what="convert")
        self.assertEqual([s["position"] for s in d["dimensions"][0]["samples"]], [0.0, 1.0, 2.5])


class TestNothingInvalidIsWritten(unittest.TestCase):
    def test_a_1x_result_that_breaks_a_2_0_rule_is_refused_not_written(self):
        from duckn.convention2 import InvalidMetadata
        d = TestOneEncoding()._file([0.0, 2.0, 4.0])
        d["axes"][1]["space_direction"] = [0, 0, 1.0]  # two dimensions along one line (§10 rule 3)
        with self.assertRaisesRegex(InvalidMetadata, "rule 3"):
            upgrade(d, (3, 2, 2), what="convert")


class TestEveryNrrd(unittest.TestCase):
    def test_the_flag_writes_a_valid_2_0_store_placed_as_1x_places_it(self):
        import tempfile
        for path in sorted(DATA.glob("*.nrrd")):
            with self.subTest(path.name), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "a.zarr"
                nrrd_to_zarr(path, out, convention="2.0")
                arr = zarr.open_array(str(out), mode="r")
                d = dict(arr.attrs)["duckn"]
                self.assertEqual(d["version"], "2.0")
                h = read(d, arr.shape, str(arr.dtype))
                steps = d["extensions"]["provenance"]["processing"]
                self.assertEqual(steps[-1]["software"]["name"], "duckn")
                header = nrrd.read_header(str(path))
                meta, _ = _header_to_metadata(header, header["dimension"])
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        geo = VolumeGeometry.from_metadata(
                            DucknMetadata(**meta.model_dump(exclude_none=True)), arr.shape)
                except ValueError:
                    continue
                spatial = [k for k, a in enumerate(meta.axes) if a.space_direction is not None]
                last = [n - 1 for n in arr.shape]
                np.testing.assert_allclose(h.position(last)[:geo.ndim],
                                           geo.affine @ np.r_[[last[k] for k in spatial], 1.0],
                                           atol=1e-9)

    def test_an_unknown_convention_is_refused(self):
        with self.assertRaises(ValueError):
            nrrd_to_zarr(DATA / "minimal.nrrd", "/nonexistent/x.zarr", convention="3.0")


if __name__ == "__main__":
    unittest.main()
