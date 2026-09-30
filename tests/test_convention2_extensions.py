"""The extension revisions for 2.0 (docs/proposals/*-2.0.md, nrrd-extension-0.2.md,
provenance-extension-1.1.md), held to the 2.0 reader and to what the writers write.

Every complete 2.0 example in the revision documents must read as a valid header; each rule a
revision states that a writer implements has a case here, written from the document's words.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

import nrrd
import numpy as np
import zarr

from duckn.convention2 import read
from duckn.convention2_write import upgrade
from duckn.convert import nrrd_to_zarr

PROPOSALS = Path(__file__).parent.parent / "docs" / "proposals"
REVISIONS = sorted(p for p in PROPOSALS.glob("*.md") if p.name != "duckn-2.0.md")


def _examples(path):
    for block in re.findall(r"```json\n(.*?)```", path.read_text(), re.S):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("version") == "2.0":
            yield obj


def _nrrd(tmp, data, header):
    src = Path(tmp) / "in.nrrd"
    nrrd.write(str(src), data, header, index_order="C")
    out = Path(tmp) / "o.zarr"
    nrrd_to_zarr(src, out, convention="2.0")
    arr = zarr.open_array(str(out), mode="r")
    return dict(arr.attrs)["duckn"], arr


class TestTheDocumentsExamples(unittest.TestCase):
    def test_the_five_revisions_exist(self):
        self.assertEqual({p.name for p in REVISIONS}, {
            "nrrd-extension-0.2.md", "provenance-extension-1.1.md", "dicom-spec-2.0.md",
            "nifti-spec-2.0.md", "dwi-extension-2.0.md"})

    def test_every_complete_2_0_example_is_a_valid_header(self):
        seen = 0
        for path in REVISIONS:
            for obj in _examples(path):
                with self.subTest(path.name):
                    read(obj)
                    seen += 1
        self.assertGreaterEqual(seen, 4)


class TestDwmri(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _dwi(self, frame=((0, 1, 0), (-1, 0, 0), (0, 0, 1))):
        header = {"space": "left-posterior-superior",
                  "space directions": [[np.nan] * 3, [2.0, 0, 0], [0, 2.0, 0], [0, 0, 2.0]],
                  "space origin": [0.0, 0.0, 0.0], "kinds": ["list", "domain", "domain", "domain"],
                  "modality": "DWMRI", "DWMRI_b-value": "1000",
                  "DWMRI_gradient_0000": "0 0 0", "DWMRI_gradient_0001": "1 0 0",
                  "DWMRI_gradient_0002": "0 1 0"}
        if frame is not None:
            header["measurement frame"] = [list(v) for v in frame]
        return _nrrd(self.tmp.name, np.zeros((3, 4, 5, 6), np.int16), header)

    def test_a_nrrd_dwi_writes_dwmri_2_0_with_its_frame_as_rows(self):
        d, arr = self._dwi()
        dw = d["extensions"]["dwmri"]
        self.assertEqual(dw["version"], "2.0")
        # NRRD writes the frame's vectors as columns (0,1,0) (-1,0,0) (0,0,1): as rows
        self.assertEqual(dw["frame"], [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        self.assertNotIn("gradient_frame", dw)
        self.assertNotIn("legacy", dw)  # the DWMRI_* strings restated the gradients
        self.assertEqual(d["intent"], "diffusion-weighted")
        self.assertEqual(d["dimensions"][3]["components"], "list")
        self.assertEqual(d["dimensions"][3]["extensions"]["dwmri"]["gradients"][1], [1.0, 0.0, 0.0])
        self.assertNotIn("frame", d["dimensions"][3])  # a list of volumes is no vector
        read(d, arr.shape, str(arr.dtype))

    def test_no_measurement_frame_is_dwi_1_0s_identity_stated(self):
        d, _ = self._dwi(frame=None)
        self.assertEqual(d["extensions"]["dwmri"]["frame"], np.eye(3).tolist())

    def _upgrade(self, gradient_frame):
        d1 = {"version": "1.1", "space": "LPS", "space_origin": [0.0, 0.0, 0.0],
              "measurement_frame": [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
              "axes": [{"kind": "list", "extensions": {"dwmri": {"gradients": [[0, 0, 0], [1, 0, 0]]}}},
                       {"kind": "space", "space_direction": [0, 0, 2.0]},
                       {"kind": "space", "space_direction": [0, 2.0, 0]},
                       {"kind": "space", "space_direction": [2.0, 0, 0]}],
              "extensions": {"dwmri": {"version": "1.0", "b_value": 1000, "gradient_frame": gradient_frame,
                                       "acquisition": {"phase_encoding_direction": "j-",
                                                       "total_readout_time": 0.05}}}}
        return upgrade(d1, (2, 2, 2, 2), what="convert")["extensions"]["dwmri"]

    def test_gradient_frame_world_is_the_identity_and_image_is_unknown(self):
        self.assertEqual(self._upgrade("world")["frame"], np.eye(3).tolist())
        self.assertNotIn("frame", self._upgrade("image"))  # FSL's flip unrecorded: unknown

    def test_an_image_axis_letter_is_not_carried(self):
        acq = self._upgrade("world")["acquisition"]
        self.assertNotIn("phase_encoding_direction", acq)
        self.assertEqual(acq["total_readout_time"], 0.05)


class TestNrrd02(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_range_axis_unit_stays_in_the_block(self):
        header = {"space": "left-posterior-superior",
                  "space directions": [[np.nan] * 3, [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
                  "space origin": [0.0, 0.0, 0.0], "kinds": ["vector", "domain", "domain", "domain"],
                  "units": ["mm/s", "", "", ""]}
        d, _ = _nrrd(self.tmp.name, np.zeros((2, 2, 2, 3), np.float32), header)
        self.assertEqual(d["dimensions"][3]["extensions"]["nrrd"], {"unit": "mm/s"})
        self.assertEqual(d["extensions"]["nrrd"]["version"], "0.2")

    def test_a_scalar_files_measurement_frame_stays_in_the_block(self):
        header = {"space": "left-posterior-superior",
                  "space directions": [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
                  "space origin": [0.0, 0.0, 0.0], "kinds": ["domain"] * 3,
                  "measurement frame": [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]}
        d, _ = _nrrd(self.tmp.name, np.zeros((2, 2, 2), np.float32), header)
        self.assertEqual(d["extensions"]["nrrd"]["measurement_frame"],
                         [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])

    def test_a_domain_axis_with_no_geometry_keeps_its_kind(self):
        header = {"space": "left-posterior-superior",
                  "space directions": [[np.nan] * 3, [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
                  "space origin": [0.0, 0.0, 0.0], "kinds": ["domain"] * 4}
        d, _ = _nrrd(self.tmp.name, np.zeros((2, 2, 2, 4), np.float32), header)
        self.assertEqual(d["dimensions"][3], {"extensions": {"nrrd": {"kind": "domain"}}})


class TestNifti20Spectra(unittest.TestCase):
    def _spectrum(self, toffset):
        import nibabel as nib
        from duckn.nifti_convert import nifti_to_zarr
        tmp = tempfile.mkdtemp()
        img = nib.Nifti1Image(np.zeros((2, 2, 2, 8), np.float32), np.diag([10.0, 10, 10, 1]))
        img.header.set_xyzt_units("mm", "ppm")
        img.header["pixdim"][4] = 0.5
        img.header.set_sform(np.diag([10.0, 10, 10, 1]), code=1)
        if toffset:
            img.header["toffset"] = toffset
        nib.save(img, str(Path(tmp) / "s.nii"))
        nifti_to_zarr(Path(tmp) / "s.nii", Path(tmp) / "o.zarr", convention="2.0")
        return dict(zarr.open_array(str(Path(tmp) / "o.zarr"), mode="r").attrs)["duckn"]

    def test_ppm_with_toffset_is_a_chemical_shift_placed_at_it(self):
        d = self._spectrum(4.7)
        axis = d["world"]["axes"][3]
        self.assertEqual((axis["id"], axis["type"], axis["unit"]),
                         ("chemical-shift", "chemical-shift", "[ppm]"))
        self.assertAlmostEqual(d["origin"][3], 4.7, places=5)

    def test_ppm_without_toffset_stays_untyped(self):
        d = self._spectrum(0.0)
        self.assertNotIn("type", d["world"]["axes"][3])


if __name__ == "__main__":
    unittest.main()


class TestReviewFixes(unittest.TestCase):
    """The review of the revisions (2026-09-30): each case is one finding, fixed."""

    def _nifti(self, img):
        from duckn.nifti_convert import nifti_to_zarr
        tmp = tempfile.mkdtemp()
        import nibabel as nib
        nib.save(img, str(Path(tmp) / "a.nii"))
        nifti_to_zarr(Path(tmp) / "a.nii", Path(tmp) / "o.zarr", convention="2.0")
        return dict(zarr.open_array(str(Path(tmp) / "o.zarr"), mode="r").attrs)["duckn"]

    def test_the_sform_places_and_a_differing_qform_is_a_transform(self):
        """The sform by default (duckn 0.6.4; nibabel's rule), used as written, even where its
        column lengths disagree with pixdim; the scanner qform is a transform to the bare
        `qform`. No code names a shared frame (nifti 2.0 §1): an MNI code stays in the record."""
        import nibabel as nib
        img = nib.Nifti1Image(np.zeros((2, 2, 2), np.int16), np.diag([2.0, 2, 2, 1]))
        img.set_qform(np.diag([2.0, 2, 2, 1]), code=1)
        img.set_sform(np.diag([2.5, 2.5, 2.5, 1]), code=4)  # disagrees with pixdim 2
        d = self._nifti(img)
        self.assertNotIn("reference", d["world"])
        self.assertEqual(d["extensions"]["nifti"]["tags"]["sform_code"], 4)
        self.assertEqual(d["dimensions"][0]["step"][0], 2.5)
        (t,) = d["world"]["transforms"]
        self.assertEqual(t["to"]["reference"], "qform")
        np.testing.assert_allclose(np.array(t["forward"]["affine"])[:, :3], np.eye(3) * 0.8)
        self.assertNotIn("legacy", d["extensions"]["nifti"])

    def test_a_qform_that_differs_is_a_transform_to_qform(self):
        import nibabel as nib
        s = np.diag([2.0, 2, 2, 1])
        q = s.copy()
        q[:3, 3] = [1.0, 0, 0]
        img = nib.Nifti1Image(np.zeros((2, 2, 2), np.int16), s)
        img.set_sform(s, code=1)
        img.set_qform(q, code=1)
        d = self._nifti(img)
        (t,) = d["world"]["transforms"]
        self.assertEqual(t["to"]["reference"], "qform")
        np.testing.assert_allclose(np.array(t["forward"]["affine"])[:, 3], [1.0, 0, 0])

    def test_a_4d_file_with_no_temporal_unit_states_no_time(self):
        import nibabel as nib
        img = nib.Nifti1Image(np.zeros((2, 2, 2, 3), np.int16), np.eye(4))
        img.header.set_xyzt_units("mm", "unknown")
        img.header["pixdim"][4] = 2.0
        d = self._nifti(img)
        self.assertEqual(d["world"]["axes"][3], {"id": "a3"})
        self.assertNotIn("centering", d["dimensions"][3])

    def test_toffset_places_a_frequency_axis(self):
        import nibabel as nib
        img = nib.Nifti1Image(np.zeros((2, 2, 2, 4), np.float32), np.eye(4))
        img.header.set_xyzt_units("mm", "hz")
        img.header["pixdim"][4] = 10.0
        img.header["toffset"] = -20.0
        d = self._nifti(img)
        self.assertEqual(d["world"]["axes"][3]["type"], "frequency")
        self.assertEqual(d["origin"][3], -20.0)

    def test_a_spaced_axis_beside_a_space_is_a_world_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            header = {"space": "left-posterior-superior",
                      "space directions": [[np.nan] * 3, [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
                      "space origin": [0.0, 0.0, 0.0], "kinds": ["time", "domain", "domain", "domain"],
                      "spacings": [2.5, np.nan, np.nan, np.nan], "axis mins": [10.0, np.nan, np.nan, np.nan],
                      "units": ["s", "", "", ""], "centerings": ["node", "cell", "cell", "cell"]}
            d, arr = _nrrd(tmp, np.zeros((2, 2, 2, 4), np.float32), header)
        self.assertEqual(d["world"]["axes"][3], {"id": "t", "type": "time", "unit": "s"})
        self.assertEqual(d["dimensions"][3]["step"], [0.0, 0.0, 0.0, 2.5])
        self.assertEqual(d["origin"][3], 10.0)                       # node: the min itself
        self.assertNotIn("extensions", d["dimensions"][3])           # no 0.1 geometry in 0.2

    def test_a_no_kind_axis_keeps_its_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            header = {"space": "left-posterior-superior",
                      "space directions": [[np.nan] * 3, [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
                      "space origin": [0.0, 0.0, 0.0], "units": ["kPa", "", "", ""]}
            d, _ = _nrrd(tmp, np.zeros((2, 2, 2, 3), np.float32), header)
        self.assertEqual(d["dimensions"][3], {"extensions": {"nrrd": {"unit": "kPa"}}})

    def _dicom_units(self, **tags):
        d1 = {"version": "1.0", "space": "LPS", "space_origin": [0.0, 0.0, 0.0],
              "axes": [{"kind": "space", "space_direction": [0, 0, 1.0], "unit": "mm"},
                       {"kind": "space", "space_direction": [0, 1.0, 0], "unit": "mm"},
                       {"kind": "space", "space_direction": [1.0, 0, 0], "unit": "mm"}],
              "value_transforms": [{"name": "linear", "parameters": {"slope": 1, "intercept": -1024}}],
              "extensions": {"dicom": {"version": "1.0", "stored_values": True, "tags": tags}}}
        if "sample_units" in tags:
            d1["sample_units"] = tags.pop("sample_units")
        return upgrade(d1, (2, 2, 2), what="convert", source_format="DICOM")["values"]

    def test_dicom_units(self):
        self.assertEqual(self._dicom_units(Modality="CT")["unit"]["code"], "[hnsf'U]")  # C.8.2.1
        self.assertNotIn("unit", self._dicom_units(Modality="MR", sample_units="US"))
        self.assertEqual(self._dicom_units(Modality="PT", Units="BQML")["unit"], "Bq/mL")

    def test_trigger_times_that_vary_within_a_phase_are_not_a_phase_record(self):
        from duckn.dicom_convert import _time_source

        class DS:
            def __init__(self, t):
                self.TriggerTime = t
        same = [DS(0), DS(0), DS(80), DS(80)]
        varied = [DS(0), DS(5), DS(80), DS(85)]
        self.assertEqual(_time_source(same, (2, 2, 4, 4)), ("TriggerTime", True))
        self.assertEqual(_time_source(varied, (2, 2, 4, 4)), ("TriggerTime", False))
        self.assertEqual(_time_source([DS("x")] * 4, (2, 2, 4, 4))[0], None)

    def test_an_earlier_first_step_names_its_sources_before_one_is_appended(self):
        d1 = {"version": "1.2", "space": "LPS", "space_origin": [0.0, 0.0, 0.0], "value_transforms": [],
              "axes": [{"kind": "space", "space_direction": [0, 0, 1.0], "unit": "mm"}],
              "extensions": {"provenance": {"version": "1.0", "sources": [{"format": "DICOM"}],
                                            "processing": [{"name": "convert"}]}}}
        prov = upgrade(d1, (2,), what="resample", source_format="NRRD")["extensions"]["provenance"]
        self.assertEqual(prov["processing"][0]["inputs"], [0])
        self.assertEqual(len(prov["sources"]), 2)
