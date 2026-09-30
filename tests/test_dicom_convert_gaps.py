"""Gaps a mutation run of 0.5.3 found in dicom_convert's tests (2026-09-26): stored_values
stated, binary tags opt-in, lossiness judged over every slice, an export minting its own frame
identities. Each test killed a mutant the suite let live."""
import numpy as np, pydicom, zarr
from test_dicom_convert import _make_dataset, _make_file_dataset
from duckn.dicom_convert import dicom_to_zarr, zarr_to_dicom
from duckn.models import DucknMetadata


def _series(tmp_path, edit=lambda i, ds: None):
    d = tmp_path / "dicom"; d.mkdir()
    for i in range(3):
        ds = _make_dataset(rows=8, cols=8, position=(0.0, 0.0, float(i * 5)),
                           pixel_spacing=(0.5, 0.5),
                           pixel_data=np.full((8, 8), i * 100, dtype=np.uint16))
        ds.SliceThickness = 5.0
        edit(i, ds)
        fds = _make_file_dataset(ds, str(d / f"s{i}.dcm"))
        fds.save_as(str(d / f"s{i}.dcm"))
    z = tmp_path / "o.zarr"
    dicom_to_zarr(d, z)
    arr = zarr.open_array(zarr.storage.LocalStore(str(z)), mode="r")
    return z, DucknMetadata(**arr.attrs["duckn"])


def test_the_converter_says_it_holds_stored_values(tmp_path):
    _, m = _series(tmp_path)
    assert m.extensions["dicom"]["stored_values"] is True


def test_binary_tags_stay_out_unless_asked(tmp_path):
    _, m = _series(tmp_path, lambda i, ds: ds.add_new(0x00282000, "OB", b"\x01\x02"))
    assert "ICCProfile" not in m.extensions["dicom"]["tags"]


def test_a_lossy_later_slice_marks_the_store(tmp_path):
    def edit(i, ds):
        if i == 2:
            ds.LossyImageCompression = "01"
    _, m = _series(tmp_path, edit)
    assert m.extensions["dicom"].get("lossy_compressed") is True


def test_an_export_mints_its_own_frame_identities(tmp_path):
    def edit(i, ds):
        ds.SOPInstanceUID, ds.InstanceNumber = f"1.2.826.0.1.3680043.99.{i}", i + 1
    z, m = _series(tmp_path, edit)
    out = tmp_path / "x.dcm"
    zarr_to_dicom(z, out)
    ds = pydicom.dcmread(out)
    src = {s.metadata["dicom"].get("SOPInstanceUID") for s in m.axes[0].samples}
    for fg in getattr(ds, "PerFrameFunctionalGroupsSequence", []):
        assert "SOPInstanceUID" not in fg and "InstanceNumber" not in fg
    assert ds.SOPInstanceUID not in src


def test_an_export_carries_the_lossy_fact(tmp_path):
    """§3.1: lossy_compressed is carried into Lossy Image Compression on export."""
    z, meta = _series(tmp_path)
    arr = zarr.open_array(zarr.storage.LocalStore(str(z)), mode="r+")
    attrs = dict(arr.attrs["duckn"])
    attrs["extensions"]["dicom"]["lossy_compressed"] = True
    attrs["extensions"]["dicom"]["tags"].pop("LossyImageCompression", None)
    arr.attrs["duckn"] = attrs
    out = tmp_path / "out"
    zarr_to_dicom(z, out)
    files = sorted(out.rglob("*.dcm")) if out.is_dir() else [out]
    assert pydicom.dcmread(files[0]).LossyImageCompression == "01"


def test_the_bids_sidecar_carries_no_empty_strings():
    from duckn.bids import duckn_to_bids_sidecar
    from duckn.models import AxisMetadata, DucknMetadata
    meta = DucknMetadata(version="1.0", space="LPS", space_origin=[0, 0, 0],
                         axes=[AxisMetadata(kind="space", space_direction=[1, 0, 0])] * 3,
                         extensions={"dicom": {"version": "1.0",
                                               "tags": {"InstitutionName": "", "Manufacturer": "X"}}})
    sidecar = duckn_to_bids_sidecar(meta)
    assert "InstitutionName" not in sidecar and sidecar.get("Manufacturer") == "X"


def test_the_streaming_converter_stores_values_not_container_bits(tmp_path):
    """Signed 12-bit in 16: the byte copy stored -1 as 4095 (0.5.3), beside stored_values."""
    import warnings
    from duckn.dicom_convert import dicom_to_zarr_streaming

    def signed12(i, ds):
        ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 12, 11, 1
        ds.PixelData = np.array([0x0FFF, 0x0800, 5, 0x07FF] * 16, np.uint16).tobytes()
    warnings.simplefilter("ignore")
    z, _ = _series(tmp_path, signed12)
    s = tmp_path / "s.zarr"
    dicom_to_zarr_streaming(tmp_path / "dicom", s)
    mem = np.asarray(zarr.open_array(zarr.storage.LocalStore(str(z)), mode="r")[0]).ravel()[:4]
    got = np.asarray(zarr.open_array(zarr.storage.LocalStore(str(s)), mode="r")[0]).ravel()[:4]
    assert list(mem) == list(got) == [-1, -2048, 5, 2047]


def test_a_rescale_that_varies_by_slice_is_an_axis_linear_in_a_1_2_file(tmp_path):
    """0.5.3 dropped a varying rescale; 0.5.4 to 0.6.2 kept it per slice but declared 1.0,
    where an absent value_transforms means identity: the file claimed its stored values were
    the quantity. 1.2's axis_linear states the mapping, and the file declares 1.2."""
    import warnings

    def vary(i, ds):
        ds.RescaleSlope, ds.RescaleIntercept, ds.RescaleType = [1, 2, 1][i], -1024, "HU"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, meta = _series(tmp_path, vary)
    assert meta.version == "1.2"
    (t,) = meta.value_transforms
    assert t.name == "axis_linear"
    assert t.parameters == {"axis": 0, "slope": [1.0, 2.0, 1.0], "intercept": -1024.0}
    assert meta.sample_units == "HU"  # RescaleType is the same on every slice
    # the core states the mapping: the record does not repeat it per slice
    per = [(s.metadata or {}).get("dicom", {}) for s in meta.axes[0].samples or []]
    assert not any("RescaleSlope" in d or "RescaleIntercept" in d for d in per)
    assert meta.values_stated()


def test_a_time_series_whose_rescale_varies_states_no_mapping_and_declares_1_2():
    """Along two axes there is no axis_linear: the mapping stays per slice, and the file must
    not be a 1.0 file, where an absent value_transforms means identity."""
    import warnings
    from duckn.dicom_convert import DicomImageInfo, build_duckn_metadata
    from duckn.models import SpaceName
    from test_dicom_convert import _make_dataset

    datasets = []
    for t in range(2):
        for k, z in enumerate((0.0, 2.0)):
            ds = _make_dataset(position=(0, 0, z), modality="CT")
            ds.RescaleSlope, ds.RescaleIntercept = 1 + k, -1024
            ds.TriggerTime = 100.0 * t
            datasets.append(ds)
    geom = DicomImageInfo(
        shape=(2, 2, 4, 4), dtype=np.dtype("uint16"), space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
        space_origin=[0.0, 0.0, 0.0], space_directions=[[0, 0, 2.0], [0, 1.0, 0], [1.0, 0, 0]],
        slice_thickness=2.0, rescale_slope=None, rescale_intercept=None, rescale_type=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        meta = build_duckn_metadata(geom, datasets, anonymized=None, include_tags=True)
    assert meta.version == "1.2" and meta.value_transforms is None
    assert not meta.values_stated()
    per = [(s.metadata or {}).get("dicom", {}) for s in meta.axes[1].samples]
    assert [d["RescaleSlope"] for d in per] == [1.0, 2.0]
