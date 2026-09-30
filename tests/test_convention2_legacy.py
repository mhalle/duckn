"""The §14 mapping (duckn.convention2_legacy): 1.x files read as 2.0.

The reference is duckn's own 1.x reader: for every NRRD in tests/data, the 1.x metadata duckn's
converter builds is read both by :class:`duckn.spatial.VolumeGeometry` and through the mapping,
and every sample the two can place must land in the same place. The rest are §14's rows, one
case each, and the legacy fixtures' verdicts (tests/data/convention-2.0/README.md).
"""

import json
import unittest
import warnings
from pathlib import Path

import nrrd
import numpy as np

from duckn.convert import _header_to_metadata
from duckn.convention2 import InvalidMetadata, QuantityUnknown
from duckn.convention2_legacy import from_1x, read_any
from duckn.models import DucknMetadata
from duckn.spatial import VolumeGeometry

DATA = Path(__file__).parent / "data"
LEGACY = DATA / "convention-2.0" / "legacy"


def _messages(h):
    return " | ".join(f.message for f in h.findings)


class TestAgreesWithThe1xReader(unittest.TestCase):
    def test_every_nrrd_places_its_samples_where_volume_geometry_does(self):
        checked = 0
        for path in sorted(DATA.glob("*.nrrd")):
            header = nrrd.read_header(str(path))
            meta, _ = _header_to_metadata(header, header["dimension"])
            d = meta.model_dump(by_alias=True, exclude_none=True, mode="json")
            shape = tuple(reversed(header["sizes"]))
            h = read_any(d, shape)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    geo = VolumeGeometry.from_metadata(DucknMetadata(**d), shape)
            except ValueError:
                continue  # no spatial axes: nothing for the 1.x reader to place
            spatial = [k for k, a in enumerate(meta.axes) if a.space_direction is not None]
            n = geo.ndim
            for probe in ([0] * len(shape), [s - 1 for s in shape],
                          *[[1 if k == j else 0 for k in range(len(shape))] for j in spatial]):
                probe = [min(p, s - 1) for p, s in zip(probe, shape)]
                expected = geo.affine @ np.r_[[probe[k] for k in spatial], 1.0]
                with self.subTest(path.name, index=probe):
                    np.testing.assert_allclose(h.position(probe)[:n], expected, atol=1e-9)
            checked += 1
        self.assertGreaterEqual(checked, 8, "most fixtures have a space to place")


class TestLegacyFixtures(unittest.TestCase):
    def _read(self, name, shape=None):
        return read_any(json.loads((LEGACY / f"{name}.json").read_text())["duckn"], shape)

    def test_l1_a_1_2_file_without_value_transforms_states_no_quantity(self):
        h = self._read("L1-version-1.2")
        self.assertNotIn("transforms", h.values)
        with self.assertRaises(QuantityUnknown):
            h.quantity(1000)

    def test_l2_the_same_header_in_1_1_is_the_identity(self):
        h = self._read("L2-version-1.1")
        self.assertEqual(h.values["transforms"], [])
        self.assertEqual(float(h.quantity(1000)), 1000.0)
        self.assertEqual(h.values["unit"]["code"], "[hnsf'U]")

    def test_l3_a_non_symmetric_1_0_frame_is_not_carried(self):
        h = self._read("L3-dwi-1.0-malformed")
        self.assertIn("ambiguous", _messages(h))
        self.assertEqual(h.dims[0], {"components": "list"})
        self.assertEqual(h.gradient_frame, np.eye(3).tolist())  # gradient_frame world

    def test_l4_a_string_from_is_reported_and_not_carried(self):
        h = self._read("L4-no-version-with-1.1-field", shape=(10, 10, 10))
        self.assertIn("not an object", _messages(h))
        self.assertNotIn("transforms", h.duckn["world"])
        self.assertEqual(float(h.quantity(1000)), 1000.0)  # 1.0: identity
        np.testing.assert_allclose(h.position([0, 0, 0]), [-90, -126, -72])


LPS = {"space": "left-posterior-superior"}
AX3 = [{"kind": "space", "space_direction": [0, 0, 2.0], "unit": "mm", "centering": "cell"},
       {"kind": "space", "space_direction": [0, 1.0, 0], "unit": "mm", "centering": "cell"},
       {"kind": "space", "space_direction": [1.0, 0, 0], "unit": "mm", "centering": "cell"}]


def _file(version="1.2", **kw):
    d = {"version": version, **LPS, "space_origin": [0.0, 0.0, 0.0],
         "axes": json.loads(json.dumps(AX3)), "value_transforms": []}
    d.update(kw)
    return d


class TestRows(unittest.TestCase):
    def test_space_names(self):
        h = read_any(_file(space="RAS"), (2, 2, 2))
        self.assertEqual([a["positive"] for a in h.axes], ["right", "anterior", "superior"])
        h = read_any(_file(space="scanner-xyz"), (2, 2, 2))
        self.assertTrue(all("positive" not in a and a["type"] == "space" for a in h.axes))
        self.assertIn("not carried", _messages(h))

    def test_a_time_space_adds_t_only_when_a_time_is_stated(self):
        axes = [dict(a, space_direction=a["space_direction"] + [0]) for a in AX3]
        h = read_any(_file(space="LPST", space_origin=None, axes=axes), (2, 2, 2))
        self.assertEqual([a["id"] for a in h.axes], ["x", "y", "z"])
        h = read_any(_file(space="LPST", space_origin=[0, 0, 0, 5.0], axes=axes), (2, 2, 2))
        self.assertEqual([a["id"] for a in h.axes], ["x", "y", "z", "t"])
        self.assertEqual(h.position([0, 0, 0])[3], 5.0)

    def test_times_in_a_time_space_are_measured_from_the_origins_time(self):
        axes = [{"kind": "time", "unit": "s", "samples": [{"position": 5.0}, {"position": 7.5}]}]
        axes += [dict(a, space_direction=a["space_direction"] + [0]) for a in AX3]
        h = read_any(_file(space="LPST", space_origin=[0, 0, 0, 5.0], axes=axes), (2, 2, 2, 2))
        self.assertEqual([s["position"] for s in h.dims[0]["samples"]], [0.0, 2.5])
        self.assertEqual([h.position([i, 0, 0, 0])[3] for i in range(2)], [5.0, 7.5])

    def test_a_time_dimension_with_times_and_no_direction_adds_a_time_axis(self):
        axes = [{"kind": "time", "unit": "s",
                 "samples": [{"position": 0.0}, {"position": 2.5}, {"position": 7.0}]}] + AX3
        h = read_any(_file(axes=axes), (3, 2, 2, 2))
        self.assertEqual(h.axes[3], {"id": "t", "type": "time", "unit": "s"})
        self.assertEqual([h.position([i, 0, 0, 0])[3] for i in range(3)], [0.0, 2.5, 7.0])

    def test_a_spectrum_in_hz_is_a_frequency_axis_and_in_ppm_untyped(self):
        for unit, expect in (("Hz", {"id": "frequency", "type": "frequency", "unit": "Hz"}),
                             ("ppm", {"id": "a3", "unit": "[ppm]"})):
            axes = AX3 + [{"kind": "domain", "unit": unit,
                           "samples": [{"position": 0.0}, {"position": 1.5}]}]
            h = read_any(_file(axes=axes), (2, 2, 2, 2))
            with self.subTest(unit):
                self.assertEqual(h.axes[3], expect)

    def test_units_normalized_and_udunits_dates_reported(self):
        axes = [dict(a, unit="micrometer") for a in AX3]
        h = read_any(_file(axes=axes), (2, 2, 2))
        self.assertEqual({a["unit"] for a in h.axes}, {"um"})
        axes = [{"kind": "time", "unit": "hours since 2020-01-01",
                 "samples": [{"position": 0.0}, {"position": 1.0}]}] + AX3
        h = read_any(_file(axes=axes), (2, 2, 2, 2))
        self.assertEqual(h.axes[3]["unit"], "h")
        self.assertIn("reference date", _messages(h))

    def test_two_units_on_one_world_axis_are_refused(self):
        axes = json.loads(json.dumps(AX3))
        axes[0]["space_direction"] = [0, 1.0, 2.0]
        axes[0]["unit"] = "um"
        with self.assertRaisesRegex(InvalidMetadata, "two units"):
            read_any(_file(axes=axes), (2, 2, 2))

    def test_value_transforms_by_version(self):
        self.assertEqual(read_any(_file("1.0", value_transforms=None), (2, 2, 2)).values["transforms"], [])
        self.assertNotIn("transforms", read_any(_file("1.2", value_transforms=None), (2, 2, 2)).values or {})
        h = read_any(_file(value_transforms=[{"name": "axis_linear", "parameters": {
            "axis": 0, "slope": [1.0, 2.0], "intercept": 0}}]), (2, 2, 2))
        self.assertEqual(h.values["transforms"][0]["parameters"]["dimension"], 0)
        self.assertEqual(float(h.quantity(3, index={0: 1})), 6.0)

    def test_a_1_2_frame_is_carried_onto_its_vector_dimension(self):
        f = [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]
        axes = AX3 + [{"kind": "3-vector"}]
        h = read_any(_file(axes=axes, measurement_frame=f), (2, 2, 2, 3))
        self.assertEqual(h.dims[3], {"components": "vector", "frame": f})

    def test_a_symmetric_1_0_frame_is_carried(self):
        f = [[1, 0, 0], [0, -1, 0], [0, 0, 1]]
        h = read_any(_file("1.0", axes=AX3 + [{"kind": "vector"}], measurement_frame=f), (2, 2, 2, 3))
        self.assertEqual(h.dims[3]["frame"], f)

    def test_a_distance_position_becomes_a_multiple_of_the_step(self):
        axes = json.loads(json.dumps(AX3))
        axes[0]["samples"] = [{"position": 0.0}, {"position": 2.0}, {"position": 5.0}]
        h = read_any(_file(axes=axes), (3, 2, 2))
        self.assertEqual([s["position"] for s in h.dims[0]["samples"]], [0.0, 1.0, 2.5])
        self.assertEqual(h.position([2, 0, 0])[2], 5.0)

    def test_per_sample_directions_become_steps_with_an_origin(self):
        axes = json.loads(json.dumps(AX3))
        axes[0]["samples"] = [{}, {"directions": [[0, 0, 2.0], [0, 0.9, 0.1], [1.0, 0, 0]]}]
        h = read_any(_file(axes=axes), (2, 2, 2))
        s = h.dims[0]["samples"][1]
        self.assertEqual(s["origin"], [0.0, 0.0, 2.0])
        np.testing.assert_allclose(h.position([1, 1, 0]), [0, 0.9, 2.1])

    def test_centering_dropped_on_a_length_1_dimension(self):
        h = read_any(_file(), (1, 2, 2))
        self.assertNotIn("centering", h.dims[0])

    def test_a_unitless_1x_displacement_field_is_assumed_in_the_spatial_unit(self):
        h = read_any(_file(intent="displacement-field", axes=AX3 + [{"kind": "vector"}]), (2, 2, 2, 3))
        self.assertEqual(h.values["unit"], "mm")
        self.assertIn("assumed", _messages(h))

    def test_an_identity_world_transform_becomes_the_reference(self):
        st = [{"to": {"name": "nifti:mni152"}, "forward": {"identity": True}}]
        h = read_any(_file("1.1", space_transforms=st), (2, 2, 2))
        self.assertEqual(h.duckn["world"]["reference"], "nifti:mni152")
        self.assertNotIn("transforms", h.duckn["world"])

    def test_a_transform_from_index_is_composed_exactly(self):
        a = [[1, 0, 0, 10], [0, 1, 0, 20], [0, 0, 1, 30]]
        st = [{"from": {"space": "index"}, "to": {"name": "plan"}, "forward": {"affine": a}}]
        d = _file("1.1", space_transforms=st, space_origin=[5.0, 6.0, 7.0])
        h = read_any(d, (4, 4, 4))
        (t,) = h.duckn["world"]["transforms"]
        m = np.array(t["forward"]["affine"])
        for idx in ([0, 0, 0], [3, 1, 2]):
            world = h.position(idx)
            spatial_index = [idx[2], idx[1], idx[0]][::-1]  # the dims in axes order
            expected = np.array(a) @ np.r_[spatial_index, 1.0]
            np.testing.assert_allclose(m @ np.r_[world, 1.0], expected)

    def test_a_bare_target_is_local_and_reported(self):
        st = [{"to": {"name": "surgical plan"}, "forward": {"affine": [[1, 0, 0, 1], [0, 1, 0, 0], [0, 0, 1, 0]]}}]
        h = read_any(_file("1.1", space_transforms=st), (2, 2, 2))
        self.assertEqual(h.duckn["world"]["transforms"][0]["to"]["reference"], "surgical_plan")
        self.assertIn("local to this array", _messages(h))

    def test_an_unsigned_xyz_array_under_identity_keeps_1_2s_fractions(self):
        h = read_any(_file(axes=AX3 + [{"kind": "XYZ-color"}]), (2, 2, 2, 3), data_type="uint16")
        self.assertAlmostEqual(float(h.quantity(65535)), 1.0)

    def test_a_1_0_file_is_what_a_no_version_file_is(self):
        d = _file(None, value_transforms=None)
        del d["version"]
        self.assertEqual(from_1x(d).duckn["values"]["transforms"], [])


if __name__ == "__main__":
    unittest.main()
