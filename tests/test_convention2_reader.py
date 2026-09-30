"""The 2.0 reader (duckn.convention2) against the review's fixtures and §10's rules, one by one.

The fixtures' positions and extents are checked against ``truth.json``, which ``truth.py``
computes from the scenarios' facts alone - never from a header, so a reader that agrees with a
header it misreads still fails. Each §10 rule has a test that breaks exactly that rule in an
otherwise valid header and expects a refusal naming it.
"""

import copy
import json
import unittest
from pathlib import Path

import numpy as np

from duckn.convention2 import InvalidMetadata, NotConvention2, QuantityUnknown, read

DATA = Path(__file__).parent / "data" / "convention-2.0"
TRUTH = json.loads((DATA / "truth.json").read_text())


def _load(path):
    return json.loads(path.read_text())["duckn"]


def _header(name):
    truth = TRUTH[TRUTH[name].get("same_as", name)]
    return read(_load(DATA / "headers" / f"{name}.json"), shape=tuple(truth["shape"])), truth


class TestReferenceHeaders(unittest.TestCase):
    def test_every_reference_header_is_valid(self):
        for path in sorted((DATA / "headers").glob("*.json")):
            with self.subTest(path.stem):
                read(_load(path), shape=tuple(TRUTH[TRUTH[path.stem].get("same_as", path.stem)]["shape"]))

    def test_positions_match_the_scenarios(self):
        for path in sorted((DATA / "headers").glob("*.json")):
            h, truth = _header(path.stem)
            zero = [0] * len(truth["shape"])
            np.testing.assert_allclose(h.position(zero), truth["first"], atol=1e-4, err_msg=path.stem)
            for k in range(len(zero)):
                one = list(zero)
                one[k] = 1
                np.testing.assert_allclose(h.position(one), truth["plus1"][k], atol=1e-4,
                                           err_msg=f"{path.stem} along {k}")
            if path.stem != "S21":
                np.testing.assert_allclose(h.position([n - 1 for n in truth["shape"]]),
                                           truth["last"], atol=1e-4, err_msg=path.stem)

    def test_boxes_match_the_scenarios(self):
        for path in sorted((DATA / "headers").glob("*.json")):
            if path.stem == "S21":
                continue
            h, truth = _header(path.stem)
            lo, hi = h.box()
            np.testing.assert_allclose(lo, truth["box"][0], atol=1e-4, err_msg=path.stem)
            np.testing.assert_allclose(hi, truth["box"][1], atol=1e-4, err_msg=path.stem)

    def test_orientation(self):
        h, _ = _header("S17")
        self.assertEqual(h.orientation(), ([0, 1, 2], [1, 1, 1], 1))  # LPS, right-handed
        h, _ = _header("S21")
        self.assertEqual(h.orientation(), ([0, 1, 2], [-1, -1, 1], 1))  # RAS, right-handed
        h, _ = _header("S20")
        self.assertIsNone(h.orientation())  # no positive: no orientation

    def test_ct_quantity_and_padding(self):
        h, _ = _header("S17")
        self.assertEqual(float(h.quantity(1000)), -24.0)
        stored = np.array([[-2000, 1000], [0, -2000]], np.int16)
        np.testing.assert_array_equal(h.missing_mask(stored), [[True, False], [False, True]])
        m, _ = _header("S18")
        self.assertEqual(float(m.quantity(1000)), 1000.0)
        np.testing.assert_array_equal(m.missing_mask(np.array([-3024, 7])), [True, False])

    def test_unstated_quantity_is_refused_not_guessed(self):
        h, _ = _header("S20")  # transforms [] and no unit: the stored values are the quantity
        self.assertEqual(float(h.quantity(5)), 5.0)
        d = _load(DATA / "headers" / "S20.json")
        del d["values"]["transforms"]
        with self.assertRaises(QuantityUnknown):
            read(d, shape=(100, 200)).quantity(5)


class TestFaults(unittest.TestCase):
    def test_f2_axis_linear_one_short_is_valid_with_the_quantity_unknown(self):
        h = read(_load(DATA / "faults" / "F2-axis-linear-one-short.json"))
        self.assertEqual(h.lengths[0], 3)
        self.assertTrue(any(f.kind == "quantity" for f in h.findings))
        with self.assertRaises(QuantityUnknown):
            h.quantity(1000, index={0: 0})
        np.testing.assert_allclose(h.position([2, 0, 0, 0]), [0, 0, 0, 120])  # geometry stands

    def test_f1_and_f3_are_valid(self):
        # Their faults are conformance (§8, §9): not visible to the core reader as validity.
        read(_load(DATA / "faults" / "F1-missing-through-linear-resample.json"), shape=(3, 2, 2))
        read(_load(DATA / "faults" / "F3-stored-padding-beside-hounsfield.json"), shape=(3, 4, 4))


class TestVersions(unittest.TestCase):
    def test_1x_and_other_majors_are_not_read_here(self):
        for path in (DATA / "legacy").glob("*.json"):
            with self.subTest(path.stem), self.assertRaises(NotConvention2):
                read(_load(path))
        with self.assertRaises(NotConvention2):
            read({"version": "3.0"})

    def test_2_0_fields_without_a_version_are_invalid(self):
        d = _load(DATA / "headers" / "S17.json")
        del d["version"]
        with self.assertRaisesRegex(InvalidMetadata, "rule 1"):
            read(d)

    def test_the_minimum_is_valid(self):
        read({"version": "2.0", "dimensions": [{"components": "list"}]})


class TestEachRuleRefuses(unittest.TestCase):
    """§10: each case breaks one rule of an otherwise valid header (S17 or S19)."""

    def _broken(self, name, edit, shape, rule):
        d = copy.deepcopy(_load(DATA / "headers" / f"{name}.json"))
        edit(d)
        with self.assertRaises(InvalidMetadata) as cm:
            read(d, shape=shape)
        rules = {f.rule for f in cm.exception.findings}
        self.assertEqual(rules, {f"§10 {rule}"}, "exactly the rule this case breaks")

    def _s17(self, edit, rule):
        self._broken("S17", edit, (3, 4, 4), rule)

    def test_rule_1_origin_components(self):
        self._s17(lambda d: d["origin"].append(0), "rule 1")

    def test_rule_1_step_components(self):
        self._s17(lambda d: d["dimensions"][1].update(step=[0, 0.5]), "rule 1")

    def test_rule_1_dimensions_against_shape(self):
        self._broken("S17", lambda d: None, (3, 4), "rule 1")

    def test_rule_1_step_without_world(self):
        self._s17(lambda d: d.pop("world"), "rule 1")

    def test_rule_2_step_and_components(self):
        self._s17(lambda d: d["dimensions"][2].update(components="list"), "rule 2")

    def test_rule_2_centering_without_step(self):
        self._s17(lambda d: d["dimensions"][2].pop("step"), "rule 2")

    def test_rule_3_dependent_steps(self):
        self._s17(lambda d: d["dimensions"][1].update(step=[0.5, 0, 0]), "rule 3")

    def test_rule_4_component_size(self):
        self._broken("S17", lambda d: d["dimensions"][2].update(
            step=None, centering=None) or d["dimensions"][2].clear() or d["dimensions"][2].update(
            components="RGB-color"), (3, 4, 4), "rule 4")

    def test_rule_5_duplicate_axis_ids(self):
        self._s17(lambda d: d["world"]["axes"][1].update(id="x"), "rule 5")

    def test_rule_5_positive_off_a_space_axis(self):
        self._s17(lambda d: d["world"]["axes"][0].pop("type"), "rule 5")

    def test_rule_5_one_term_of_a_pair_twice(self):
        self._s17(lambda d: d["world"]["axes"][1].update(positive="right"), "rule 5")

    def test_rule_5_step_across_units(self):
        def edit(d):
            d["world"]["axes"][1]["unit"] = "um"
            d["dimensions"][0]["step"] = [0, 1, 2]
        self._s17(edit, "rule 5")

    def test_rule_5_units_are_compared_as_written(self):
        def edit(d):
            d["world"]["axes"][1]["unit"] = "10*-3.m"  # a millimeter, spelled otherwise
            d["dimensions"][0]["step"] = [0, 1, 2]
        self._s17(edit, "rule 5")

    def test_rule_5_a_frame_in_a_world_of_mixed_spatial_units(self):
        def edit(d):
            d["world"]["axes"][1]["unit"] = "um"
            d["dimensions"][0]["step"] = [0, 0, 2]
            d["dimensions"][1]["step"] = [0, 500, 0]
            d["dimensions"][2].clear()
            d["dimensions"][2].update(components="vector", frame=[[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        self._broken("S17", edit, (3, 4, 3), "rule 5")

    def test_rule_6_frame_on_a_non_spatial_kind(self):
        self._broken("S17", lambda d: d["dimensions"][2].clear() or d["dimensions"][2].update(
            components="list", frame=[[1, 0, 0], [0, 1, 0], [0, 0, 1]]), (3, 4, 4), "rule 6")

    def test_rule_6_singular_frame(self):
        self._broken("S17", lambda d: d["dimensions"][2].clear() or d["dimensions"][2].update(
            components="vector", frame=[[1, 0, 0], [1, 0, 0], [0, 0, 1]]), (3, 4, 3), "rule 6")

    def test_rule_7_color_space(self):
        self._broken("S17", lambda d: d["dimensions"][2].clear() or d["dimensions"][2].update(
            components="RGB-color", color_space="xyz-d65"), (3, 4, 3), "rule 7")

    def test_rule_8_affine_columns(self):
        self._s17(lambda d: d["world"].update(transforms=[{
            "to": {"reference": "nifti:mni152"}, "on": ["x", "y"],
            "forward": {"affine": [[1, 0, 0, 0]]}}]), "rule 8")

    def test_rule_8_on_names_an_unknown_axis(self):
        self._s17(lambda d: d["world"].update(transforms=[{
            "to": {"reference": "plan"}, "on": ["q"], "forward": {"identity": True}}]), "rule 8")

    def test_rule_8_two_transforms_to_one_reference_on_one_axis_set(self):
        t = {"to": {"reference": "plan"}, "on": ["x", "y"], "forward": {"identity": True}}
        u = dict(t, on=["y", "x"])
        self._s17(lambda d: d["world"].update(transforms=[t, u]), "rule 8")

    def test_rule_9_type_token(self):
        self._broken("S20", lambda d: d["world"]["axes"][0].update(type="a space"), (100, 200),
                     "rule 9")

    def test_rule_10_samples_length(self):
        self._s17(lambda d: d["dimensions"][0]["samples"].pop(), "rule 10")

    def test_rule_10_steps_without_origin(self):
        def edit(d):
            d["dimensions"][0]["samples"][0]["steps"] = [[0, 0, 2], [0, 0.5, 0], [0.5, 0, 0]]
        self._s17(edit, "rule 10")

    def test_rule_10_position_and_origin(self):
        self._s17(lambda d: d["dimensions"][0]["samples"][0].update(position=0, origin=[-10, -20, 30]),
                  "rule 10")

    def test_rule_11_reference(self):
        self._s17(lambda d: d["world"].update(reference="dicom:"), "rule 11")


class TestMoreDerivations(unittest.TestCase):
    PET = {"version": "2.0",
           "world": {"axes": [{"id": "x", "type": "space", "unit": "mm", "positive": "left"},
                              {"id": "y", "type": "space", "unit": "mm", "positive": "anterior"},
                              {"id": "z", "type": "space", "unit": "mm", "positive": "superior"},
                              {"id": "t", "type": "time", "unit": "s"}]},
           "origin": [-150.0, -150.0, -100.0, 0.0],
           "dimensions": [{"step": [0, 0, 0, 1.0], "centering": "cell",
                           "samples": [{"position": 5, "thickness": 10}, {"position": 15, "thickness": 10},
                                       {"position": 40, "thickness": 40}]},
                          {"step": [0, 0, 2.0, 0], "centering": "cell"},
                          {"step": [0, -2.0, 0, 0], "centering": "cell"},
                          {"step": [2.0, 0, 0, 0], "centering": "cell"}],
           "values": {"unit": "Bq/mL", "transforms": [{"name": "axis_linear", "parameters": {
               "dimension": 0, "slope": [1.10, 1.12, 1.15], "intercept": 0}}]}}

    def test_positions_along_a_dimension_placed_by_position(self):
        h = read(self.PET)
        np.testing.assert_allclose(h.position([2, 0, 0, 0]), [-150, -150, -100, 40])
        np.testing.assert_allclose(h.position([1, 1, 1, 1]), [-148, -152, -98, 15])
        self.assertEqual(h.cells(0), (0.0, 52.5))
        self.assertAlmostEqual(float(h.quantity(1000, index={0: 2})), 1150.0)

    def test_left_handed_world(self):
        # left, anterior, superior: LAS, whose determinant in LPS is -1.
        self.assertEqual(read(self.PET).orientation(), ([0, 1, 2], [1, -1, 1], -1))

    def test_a_ucum_object_and_its_code_are_one_unit(self):
        d = _load(DATA / "headers" / "S17.json")
        d["world"]["axes"][1]["unit"] = {"symbol": "mm", "scheme": "UCUM", "code": "mm"}
        d["dimensions"][0]["step"] = [0, 1, 2]
        read(d, shape=(3, 4, 4))


class TestNotValidity(unittest.TestCase):
    """§10's closing list: these never refuse a file."""

    def test_unit_misfit_is_reported(self):
        d = _load(DATA / "headers" / "S17.json")
        d["world"]["axes"][0]["unit"] = "s"
        d["world"]["axes"][1]["unit"] = "s"
        d["world"]["axes"][2]["unit"] = "s"
        h = read(d, shape=(3, 4, 4))
        self.assertTrue(any(f.kind == "reported" and "does not fit" in f.message for f in h.findings))

    def test_missing_under_a_lut_is_ignored(self):
        d = _load(DATA / "headers" / "S17.json")
        d["values"]["transforms"] = [{"name": "lut", "parameters": {"values": [0, 1, 2]}}]
        h = read(d, shape=(3, 4, 4), data_type="int16")
        self.assertTrue(any(f.kind == "ignored" for f in h.findings))
        self.assertIsNone(h.missing_mask(np.zeros((3, 4, 4), np.int16)))

    def test_lut_on_floats_makes_the_quantity_unknown(self):
        d = _load(DATA / "headers" / "S17.json")
        d["values"] = {"transforms": [{"name": "lut", "parameters": {"values": [0, 1]}}]}
        h = read(d, shape=(3, 4, 4), data_type="float32")
        with self.assertRaises(QuantityUnknown):
            h.quantity(1.0)

    def test_a_lut_after_another_transform_makes_the_quantity_unknown(self):
        d = _load(DATA / "headers" / "S17.json")
        d["values"]["transforms"].append({"name": "lut", "parameters": {"values": [0, 1]}})
        with self.assertRaises(QuantityUnknown):
            read(d, shape=(3, 4, 4), data_type="int16").quantity(1)

    def test_unknown_transform_type(self):
        d = _load(DATA / "headers" / "S17.json")
        d["values"]["transforms"] = [{"name": "sqrt", "parameters": {}}]
        with self.assertRaises(QuantityUnknown):
            read(d, shape=(3, 4, 4)).quantity(4)


class TestColor(unittest.TestCase):
    def _rgb(self, data_type):
        return read({"version": "2.0",
                     "world": {"axes": [{"id": "x", "type": "space", "unit": "um"},
                                        {"id": "y", "type": "space", "unit": "um"}]},
                     "dimensions": [{"step": [0, 0.25], "centering": "cell"},
                                    {"step": [0.25, 0], "centering": "cell"},
                                    {"components": "RGB-color", "color_space": "srgb"}],
                     "values": {"transforms": []}},
                    shape=(2, 2, 3), data_type=data_type)

    def test_unsigned_integers_under_identity_are_fractions(self):
        self.assertEqual(float(self._rgb("uint8").quantity(255)), 1.0)
        self.assertEqual(float(self._rgb("uint16").quantity(65535)), 1.0)

    def test_floats_are_the_components(self):
        self.assertEqual(float(self._rgb("float32").quantity(0.5)), 0.5)


if __name__ == "__main__":
    unittest.main()
