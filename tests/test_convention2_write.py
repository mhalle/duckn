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


def _dicom(tmp, instances, **kw):
    """Write ``instances`` (pydicom datasets) as a series and convert it with the 2.0 flag."""
    from duckn.dicom_convert import dicom_to_zarr
    from test_dicom_convert import _make_file_dataset

    src = Path(tmp) / "dcm"
    src.mkdir()
    for n, ds in enumerate(instances):
        ds.InstanceNumber = n + 1
        ds.SOPInstanceUID = f"1.2.3.77.{n + 1}"
        _make_file_dataset(ds, str(src / f"i{n:03d}.dcm")).save_as(str(src / f"i{n:03d}.dcm"))
    out = Path(tmp) / "o.zarr"
    dicom_to_zarr(src, out, convention="2.0", **kw)
    arr = zarr.open_array(str(out), mode="r")
    return dict(arr.attrs)["duckn"], arr


def instances_uid(instances):
    return str(instances[0].SeriesInstanceUID)


def _core(d):
    """The core metadata, per-sample records cut to the keys the reference keeps."""
    return {k: d.get(k) for k in ("version", "world", "origin", "dimensions", "values")}


def _cut_records(d, ref):
    """Reference headers abbreviate the DICOM record to the tags their scenario names (the
    review's rule); cut the converter's fuller record to those keys before comparing."""
    for dim, rdim in zip(d["dimensions"], ref["dimensions"]):
        for s, rs in zip(dim.get("samples", []), rdim.get("samples", [])):
            keep = (rs.get("metadata") or {}).get("dicom", {})
            if "metadata" in s:
                s["metadata"]["dicom"] = {k: v for k, v in s["metadata"]["dicom"].items() if k in keep}
    tags, rtags = d["extensions"]["dicom"]["tags"], ref["extensions"]["dicom"]["tags"]
    d["extensions"]["dicom"]["tags"] = {k: v for k, v in tags.items() if k in rtags}
    return d


class TestDicomAgainstTheReferenceHeaders(unittest.TestCase):
    maxDiff = None

    def _ct(self):
        from test_dicom_convert import _make_dataset
        out = []
        for i in range(3):
            ds = _make_dataset(rows=4, cols=4, position=(-10, -20, 30 + 2 * i),
                               pixel_spacing=(0.5, 0.5), pixel_representation=1,
                               pixel_data=np.full((4, 4), i, np.int16), series_uid="1.2.3.77")
            ds.SliceThickness = 2.0
            ds.RescaleSlope, ds.RescaleIntercept, ds.RescaleType = 1, -1024, "HU"
            ds.PixelPaddingValue = -2000
            ds.FrameOfReferenceUID = "1.2.3.4"
            ds.SynchronizationFrameOfReferenceUID = "1.2.3.9"
            ds.AcquisitionTime = ["101500.000", "101500.500", "101501.000"][i]
            ds.KVP, ds.Manufacturer = 120, "Acme"
            out.append(ds)
        return out

    def _cine(self, first_ms):
        from test_dicom_convert import _make_dataset
        out = []
        for p in range(10):
            for k in range(3):
                ds = _make_dataset(rows=4, cols=4, position=(-3, -3, 8 * k),
                                   pixel_spacing=(1.5, 1.5), modality="MR", series_uid="1.2.3.88")
                ds.SliceThickness = 8.0
                ds.TriggerTime = first_ms + 80.0 * p
                ds.FrameOfReferenceUID = "1.2.3.5"
                ds.SynchronizationFrameOfReferenceUID = "1.2.3.99"
                out.append(ds)
        return out

    def _check(self, name, instances):
        import tempfile
        self._last = instances
        ref = _reference(name)
        with tempfile.TemporaryDirectory() as tmp:
            d, arr = _dicom(tmp, instances)
        d = _cut_records(d, ref)
        self.assertEqual(_core(d), _core(ref))
        self.assertEqual(d["extensions"]["dicom"]["version"], "2.0")
        self.assertEqual(d["extensions"]["dicom"]["stored_values"], ref["extensions"]["dicom"]["stored_values"])
        self.assertEqual(d["extensions"]["dicom"]["tags"], ref["extensions"]["dicom"]["tags"])
        uid = d["extensions"]["dicom"].get("tags", {}).get("SeriesInstanceUID") or \
            instances_uid(self._last)
        self.assertEqual(d["extensions"]["provenance"]["sources"],
                         [{"format": "DICOM", "identifier": uid}])  # provenance 1.1 §1.1
        return d

    def test_s17_a_ct_with_its_stored_values(self):
        self._check("S17", self._ct())

    def test_s19_a_cardiac_cine(self):
        self._check("S19", self._cine(0.0))

    def test_s26_trigger_times_that_start_at_20_ms(self):
        self._check("S26", self._cine(20.0))


class TestDicomTimes(unittest.TestCase):
    def test_a_series_timed_by_acquisition_time_is_in_seconds_with_no_centering(self):
        import tempfile
        from test_dicom_convert import _make_dataset
        instances = []
        for t in ("101500.000", "101502.500", "101505.000"):
            for z in (0.0, 2.0):
                ds = _make_dataset(position=(0, 0, z), modality="MR", series_uid="1.2.3.99")
                ds.AcquisitionTime = t
                instances.append(ds)
        with tempfile.TemporaryDirectory() as tmp:
            d, arr = _dicom(tmp, instances)
        self.assertEqual(d["world"]["axes"][3], {"id": "t", "type": "time", "unit": "s"})
        self.assertEqual(d["dimensions"][0], {"step": [0.0, 0.0, 0.0, 2.5]})  # no centering (§5.1)
        self.assertNotIn("reference", d["world"])  # no Frame of Reference UID in the source

    def test_a_rescale_that_varies_by_slice_is_an_axis_linear_with_no_missing(self):
        import tempfile
        import warnings
        ct = TestDicomAgainstTheReferenceHeaders()._ct()
        ct[2].RescaleSlope = 2
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            d, arr = _dicom(tmp, ct)
        (t,) = d["values"]["transforms"]
        self.assertEqual(t, {"name": "axis_linear", "parameters": {
            "dimension": 0, "slope": [1.0, 1.0, 2.0], "intercept": -1024.0}})
        # the padding's quantity differs by slice: one list would mark measured voxels (§6)
        self.assertNotIn("missing", d["values"])
        self.assertEqual(d["extensions"]["dicom"]["tags"]["PixelPaddingValue"], -2000)
        h = read(d, arr.shape, str(arr.dtype))
        self.assertEqual(float(h.quantity(100, index={0: 2})), -824.0)
        self.assertEqual(float(h.quantity(100, index={0: 0})), -924.0)

    def test_padding_is_not_restated_with_a_range_limit(self):
        import tempfile
        ct = TestDicomAgainstTheReferenceHeaders()._ct()
        for ds in ct:
            ds.PixelPaddingRangeLimit = -1990
        with tempfile.TemporaryDirectory() as tmp:
            d, _ = _dicom(tmp, ct)
        self.assertNotIn("missing", d["values"])  # a range has no form yet (§6): absent, not []


def _nifti(tmp, shape=(8, 8, 6, 4), slice_code=3, sform_code=1, duration=0.1, edit=None):
    import nibabel as nib
    from duckn.nifti_convert import nifti_to_zarr

    aff = np.array([[3, 0, 0, -12], [0, 3, 0, -12], [0, 0, 3, -9], [0, 0, 0, 1.0]])
    img = nib.Nifti1Image(np.zeros(shape, np.float32), aff)
    h = img.header
    h.set_sform(aff, code=sform_code)
    h.set_qform(None, code=0)
    h.set_xyzt_units("mm", "sec")
    h["pixdim"][4] = 2.0
    if slice_code:
        h.set_dim_info(slice=2)
        h["slice_code"], h["slice_duration"] = slice_code, duration
        h["slice_start"], h["slice_end"] = 0, shape[2] - 1
    if edit:
        edit(h)
    nib.save(img, str(Path(tmp) / "f.nii"))
    out = Path(tmp) / "o.zarr"
    nifti_to_zarr(Path(tmp) / "f.nii", out, convention="2.0", overwrite=True)
    arr = zarr.open_array(str(out), mode="r")
    return dict(arr.attrs)["duckn"], arr


class TestNiftiAgainstTheReferenceHeader(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_s21_interleaved_slice_timing(self):
        d, arr = _nifti(self.tmp.name)
        ref = _core(_reference("S21"))
        # duckn's NIfTI import keeps NIfTI's dimension order (i, j, k, t); the scenario states
        # the array the other way round. The same dimensions, reversed.
        ref["dimensions"] = ref["dimensions"][::-1]
        self.assertEqual(_core(d), ref)
        self.assertEqual(d["extensions"]["nifti"]["version"], "2.0")
        self.assertNotIn("slice_timing", d["extensions"]["nifti"]["tags"])  # geometry now
        self.assertNotIn("legacy", d["extensions"]["nifti"])  # the core states the sform
        h = read(d, arr.shape)
        self.assertEqual(h.position([0, 0, 2, 1])[3], 2.1)  # volume 1, slice 2

    def test_sequential_slices_are_a_step_through_space_and_time(self):
        d, _ = _nifti(self.tmp.name, slice_code=1)
        self.assertEqual(d["dimensions"][2], {"step": [0.0, 0.0, 3.0, 0.1], "centering": "cell"})
        d, _ = _nifti(self.tmp.name, slice_code=2)
        self.assertEqual(d["dimensions"][2]["step"], [0.0, 0.0, 3.0, -0.1])
        self.assertEqual(d["origin"][3], 0.5)  # slice 0 is acquired last of six

    def test_an_order_that_starts_on_slice_1_keeps_samples_0_at_the_origin(self):
        d, arr = _nifti(self.tmp.name, slice_code=5)  # 1, 3, 5, 0, 2, 4
        times = [s["origin"][3] for s in d["dimensions"][2]["samples"]]
        self.assertEqual(times, [0.3, 0.0, 0.4, 0.1, 0.5, 0.2])
        self.assertEqual(d["origin"], d["dimensions"][2]["samples"][0]["origin"])

    def test_no_code_names_a_shared_frame(self):
        # code 4 says "an MNI 152 template", not which: the code stays in the record (nifti 2.0 §1)
        for code in (1, 2, 3, 4, 5):
            d, _ = _nifti(self.tmp.name, sform_code=code, slice_code=0)
            self.assertNotIn("reference", d["world"])
            self.assertEqual(d["extensions"]["nifti"]["tags"]["sform_code"], code)

    def test_a_partial_slice_range_stays_in_the_record(self):
        d, _ = _nifti(self.tmp.name, edit=lambda h: h.__setitem__("slice_end", 4))
        self.assertIn("slice_timing", d["extensions"]["nifti"]["tags"])
        self.assertNotIn("samples", d["dimensions"][2])

    def test_toffset_is_the_origins_time(self):
        d, _ = _nifti(self.tmp.name, slice_code=0, edit=lambda h: h.__setitem__("toffset", 7.5))
        self.assertEqual(d["origin"][3], 7.5)
        self.assertNotIn("toffset", d["extensions"]["nifti"].get("tags", {}))

    def test_volumes_state_no_centering(self):
        # nifti1.h gives a volume a time and no extent, and does not say which instant of its
        # acquisition that time is (nifti 2.0 §2)
        d, _ = _nifti(self.tmp.name, slice_code=0)
        self.assertEqual(d["dimensions"][3], {"step": [0.0, 0.0, 0.0, 2.0]})


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

    def test_uneven_positions_stay_positions_on_a_step_one_unit_long(self):
        d = upgrade(self._file([0.0, 2.0, 5.0]), (3, 2, 2), what="convert")
        self.assertEqual(d["dimensions"][0]["step"], [0.0, 0.0, 1.0])  # one mm (§5.4)
        self.assertEqual([s["position"] for s in d["dimensions"][0]["samples"]], [0.0, 2.0, 5.0])
        np.testing.assert_allclose(read(d, (3, 2, 2)).position([2, 0, 0]), [0, 0, 15.0])

    def test_a_uniform_gantry_tilt_is_a_sheared_step(self):
        d1 = self._file([0.0, 2.0, 4.0])
        del d1["axes"][0]["samples"]
        d1["axes"][0]["samples"] = [{"origin": [0.0, 0.44 * i, 10.0 + 1.25 * i]} for i in range(3)]
        d = upgrade(d1, (3, 2, 2), what="convert")
        np.testing.assert_allclose(d["dimensions"][0]["step"], [0.0, 0.44, 1.25])
        self.assertNotIn("samples", d["dimensions"][0])

    def test_an_uneven_tilt_keeps_its_origins(self):
        d1 = self._file([0.0, 2.0, 4.0])
        d1["axes"][0]["samples"] = [{"origin": [0.0, y, 10.0 + 1.25 * i]}
                                    for i, y in enumerate((0.0, 0.44, 1.2))]
        d = upgrade(d1, (3, 2, 2), what="convert")
        self.assertEqual([s["origin"][1] for s in d["dimensions"][0]["samples"]], [0.0, 0.44, 1.2])


class TestCallersMissing(unittest.TestCase):
    def test_a_materializing_writer_states_what_its_padding_became(self):
        d = TestOneEncoding()._file([0.0, 2.0, 4.0])
        out = upgrade(d, (3, 2, 2), what="input copy", missing=[-3024.0])
        self.assertEqual(out["values"], {"transforms": [], "missing": [-3024]})
        np.testing.assert_array_equal(read(out, (3, 2, 2)).missing_mask(np.array([-3024, 5])),
                                      [True, False])

    def test_missing_is_refused_where_6_forbids_it(self):
        d = TestOneEncoding()._file([0.0, 2.0, 4.0])
        d["value_transforms"] = [{"name": "lut", "parameters": {"values": [0, 1]}}]
        with self.assertRaisesRegex(ValueError, "§6"):
            upgrade(d, (3, 2, 2), "uint8", what="input copy", missing=[0])


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
