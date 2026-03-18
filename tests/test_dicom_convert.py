"""Tests for DICOM to duckn Zarr conversion.

Uses synthetic pydicom Dataset objects — no real .dcm files needed for unit tests.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest

pydicom = pytest.importorskip("pydicom")

from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence as DicomSequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from duckn.dicom_convert import (
    DicomGeometry,
    _extract_seg_extension,
    _is_dicom_seg,
    build_duckn_metadata,
    _compute_geometry,
    _convert_value,
    _dataset_to_tags,
    _detect_anonymized,
    _load_single_frame_series,
    _should_be_array,
    _sort_datasets,
    dicom_to_zarr,
)
from duckn.models import DucknMetadata, SegmentationExtension, SpaceName


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dataset(
    *,
    rows: int = 4,
    cols: int = 4,
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    orientation: tuple[float, ...] = (1, 0, 0, 0, 1, 0),
    pixel_spacing: tuple[float, float] = (1.0, 1.0),
    bits_allocated: int = 16,
    pixel_representation: int = 0,
    pixel_data: np.ndarray | None = None,
    series_uid: str = "1.2.3.4.5",
    modality: str = "CT",
) -> Dataset:
    """Create a minimal synthetic DICOM dataset."""
    ds = Dataset()
    ds.Rows = rows
    ds.Columns = cols
    ds.ImagePositionPatient = list(position)
    ds.ImageOrientationPatient = list(orientation)
    ds.PixelSpacing = list(pixel_spacing)
    ds.BitsAllocated = bits_allocated
    ds.BitsStored = bits_allocated
    ds.HighBit = bits_allocated - 1
    ds.PixelRepresentation = pixel_representation
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.SeriesInstanceUID = series_uid
    ds.Modality = modality

    if pixel_data is None:
        dtype = np.uint16 if pixel_representation == 0 else np.int16
        pixel_data = np.zeros((rows, cols), dtype=dtype)
    ds.PixelData = pixel_data.tobytes()

    # Set file_meta so pixel_array works in pydicom 3.x
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    return ds


def _make_file_dataset(
    ds: Dataset,
    filename: str = "test.dcm",
) -> FileDataset:
    """Wrap a Dataset as a FileDataset with file meta (for save_as)."""
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    fds = FileDataset(
        filename,
        ds,
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )
    return fds


# ---------------------------------------------------------------------------
# Tag extraction tests
# ---------------------------------------------------------------------------


class TestTagExtraction:
    def test_string_tags(self):
        ds = Dataset()
        ds.Modality = "CT"
        ds.Manufacturer = "SIEMENS"
        ds.StudyDescription = "CHEST CT"
        tags = _dataset_to_tags(ds)
        assert tags["Modality"] == "CT"
        assert tags["Manufacturer"] == "SIEMENS"
        assert tags["StudyDescription"] == "CHEST CT"

    def test_ds_to_float(self):
        """DS (Decimal String) → float. KVP has VM=1 → bare value."""
        ds = Dataset()
        ds.add_new(0x00180060, "DS", "120.0")  # KVP
        tags = _dataset_to_tags(ds)
        assert tags["KVP"] == 120.0
        assert isinstance(tags["KVP"], float)

    def test_is_to_int(self):
        """IS (Integer String) → int. SeriesNumber has VM=1 → bare value."""
        ds = Dataset()
        ds.add_new(0x00200011, "IS", "3")  # SeriesNumber
        tags = _dataset_to_tags(ds)
        assert tags["SeriesNumber"] == 3
        assert isinstance(tags["SeriesNumber"], int)

    def test_pixel_spacing_always_array(self):
        """PixelSpacing has VM=2 → always JSON array."""
        ds = Dataset()
        ds.PixelSpacing = [0.703, 0.703]
        tags = _dataset_to_tags(ds, _skip_geometry=False)
        assert tags["PixelSpacing"] == [0.703, 0.703]
        assert isinstance(tags["PixelSpacing"], list)

    def test_window_center_always_array(self):
        """WindowCenter has VM=1-n → always array even with single value."""
        ds = Dataset()
        ds.add_new(0x00281050, "DS", "40")  # WindowCenter, VM=1-n
        tags = _dataset_to_tags(ds)
        assert tags["WindowCenter"] == [40.0]
        assert isinstance(tags["WindowCenter"], list)

    def test_person_name_to_string(self):
        ds = Dataset()
        ds.PatientName = "Doe^John"
        tags = _dataset_to_tags(ds)
        assert tags["PatientName"] == "Doe^John"

    def test_person_name_empty_is_none(self):
        ds = Dataset()
        ds.add_new(0x00100010, "PN", "")
        tags = _dataset_to_tags(ds)
        assert tags["PatientName"] is None

    def test_sequence_to_list_of_dicts(self):
        item = Dataset()
        item.CodeValue = "T-11000"
        item.CodingSchemeDesignator = "SRT"
        item.CodeMeaning = "Lung"

        ds = Dataset()
        ds.AnatomicRegionSequence = DicomSequence([item])
        tags = _dataset_to_tags(ds)

        assert isinstance(tags["AnatomicRegionSequence"], list)
        assert len(tags["AnatomicRegionSequence"]) == 1
        assert tags["AnatomicRegionSequence"][0]["CodeValue"] == "T-11000"

    def test_nested_sequences(self):
        """Deeply nested DICOM sequences round-trip."""
        inner = Dataset()
        inner.CodeValue = "C-111A1"
        inner.CodingSchemeDesignator = "SRT"

        outer = Dataset()
        outer.Radiopharmaceutical = "FDG"
        outer.RadionuclideCodeSequence = DicomSequence([inner])

        ds = Dataset()
        ds.RadiopharmaceuticalInformationSequence = DicomSequence([outer])
        tags = _dataset_to_tags(ds)

        seq = tags["RadiopharmaceuticalInformationSequence"]
        assert len(seq) == 1
        assert seq[0]["Radiopharmaceutical"] == "FDG"
        inner_seq = seq[0]["RadionuclideCodeSequence"]
        assert len(inner_seq) == 1
        assert inner_seq[0]["CodeValue"] == "C-111A1"

    def test_binary_vr_excluded(self):
        ds = Dataset()
        ds.Modality = "CT"
        ds.add_new(0x7FE00010, "OW", b"\x00\x01\x02\x03")  # PixelData
        tags = _dataset_to_tags(ds)
        assert "PixelData" not in tags
        assert "Modality" in tags

    def test_group_length_excluded(self):
        ds = Dataset()
        ds.Modality = "CT"
        ds.add_new(0x00080000, "UL", 100)  # group length
        tags = _dataset_to_tags(ds)
        # Group length tag should not appear
        assert "00080000" not in tags
        assert "GenericGroupLength" not in tags

    def test_geometry_tags_skipped_by_default(self):
        ds = Dataset()
        ds.Modality = "CT"
        ds.Rows = 512
        ds.Columns = 512
        ds.BitsAllocated = 16
        tags = _dataset_to_tags(ds)
        assert "Rows" not in tags
        assert "Columns" not in tags
        assert "BitsAllocated" not in tags
        assert "Modality" in tags

    def test_geometry_tags_included_when_not_skipped(self):
        ds = Dataset()
        ds.Rows = 512
        tags = _dataset_to_tags(ds, _skip_geometry=False)
        assert "Rows" in tags

    def test_native_int_types(self):
        """US, SS, UL, SL → int."""
        ds = Dataset()
        ds.add_new(0x00280010, "US", 512)  # Rows — but test the conversion
        tags = _dataset_to_tags(ds, _skip_geometry=False)
        assert isinstance(tags["Rows"], int)

    def test_native_float_types(self):
        """FL, FD → float."""
        ds = Dataset()
        ds.add_new(0x00189306, "FD", 25.5)  # SingleCollimationWidth
        tags = _dataset_to_tags(ds)
        assert isinstance(tags["SingleCollimationWidth"], float)


# ---------------------------------------------------------------------------
# VM handling tests
# ---------------------------------------------------------------------------


class TestVMHandling:
    def test_vm1_bare_value(self):
        """Modality (VM=1) should be a bare string."""
        ds = Dataset()
        ds.Modality = "CT"
        elem = ds["Modality"]
        assert not _should_be_array(elem)

    def test_vm2_always_array(self):
        """PixelSpacing (VM=2) should always be array."""
        ds = Dataset()
        ds.PixelSpacing = [0.5, 0.5]
        elem = ds["PixelSpacing"]
        assert _should_be_array(elem)

    def test_vm1n_always_array(self):
        """WindowCenter (VM=1-n) should always be array."""
        ds = Dataset()
        ds.add_new(0x00281050, "DS", "40")  # WindowCenter
        elem = ds[0x00281050]
        assert _should_be_array(elem)


# ---------------------------------------------------------------------------
# Anonymization detection
# ---------------------------------------------------------------------------


class TestAnonymizationDetection:
    def test_both_absent(self):
        ds = Dataset()
        ds.Modality = "CT"
        assert _detect_anonymized(ds) is True

    def test_both_empty(self):
        ds = Dataset()
        ds.PatientName = ""
        ds.PatientID = ""
        assert _detect_anonymized(ds) is True

    def test_present(self):
        ds = Dataset()
        ds.PatientName = "Doe^John"
        ds.PatientID = "12345"
        assert _detect_anonymized(ds) is None

    def test_partial(self):
        """Only one present → not detected as anonymized."""
        ds = Dataset()
        ds.PatientName = "Doe^John"
        assert _detect_anonymized(ds) is None


# ---------------------------------------------------------------------------
# Geometry tests
# ---------------------------------------------------------------------------


class TestGeometry:
    def _make_axial_series(self, n_slices: int = 3, spacing: float = 5.0):
        """Create n axial slices at z=0, spacing, 2*spacing, ..."""
        datasets = []
        for i in range(n_slices):
            pixel_data = np.full((4, 4), i, dtype=np.uint16)
            ds = _make_dataset(
                position=(0.0, 0.0, float(i * spacing)),
                pixel_spacing=(0.5, 0.5),
                pixel_data=pixel_data,
            )
            ds.SliceThickness = spacing
            datasets.append(ds)
        return datasets

    def test_axial_geometry(self):
        datasets = self._make_axial_series()
        sorted_ds = _sort_datasets(datasets)
        volume, geom = _compute_geometry(sorted_ds)

        assert geom.space == SpaceName.LEFT_POSTERIOR_SUPERIOR
        assert volume.shape == (3, 4, 4)
        np.testing.assert_allclose(geom.space_origin, [0.0, 0.0, 0.0])
        # Slice direction: z-axis, spacing 5.0
        np.testing.assert_allclose(geom.space_directions[0], [0, 0, 5.0])
        # Row axis (col_cosines * row_spacing): [0,1,0] * 0.5
        np.testing.assert_allclose(geom.space_directions[1], [0, 0.5, 0])
        # Col axis (row_cosines * col_spacing): [1,0,0] * 0.5
        np.testing.assert_allclose(geom.space_directions[2], [0.5, 0, 0])
        assert geom.slice_thickness == 5.0

    def test_slice_sorting(self):
        """Out-of-order slices get sorted by position."""
        datasets = self._make_axial_series()
        # Shuffle
        shuffled = [datasets[2], datasets[0], datasets[1]]
        sorted_ds = _sort_datasets(shuffled)

        positions = [ds.ImagePositionPatient[2] for ds in sorted_ds]
        assert positions == [0.0, 5.0, 10.0]

    def test_pixel_data_preserved(self):
        datasets = self._make_axial_series()
        sorted_ds = _sort_datasets(datasets)
        volume, _geom = _compute_geometry(sorted_ds)

        # Each slice was filled with its index value
        assert volume[0, 0, 0] == 0
        assert volume[1, 0, 0] == 1
        assert volume[2, 0, 0] == 2

    def test_single_slice_fallback(self):
        """Single slice uses SliceThickness for slice direction."""
        ds = _make_dataset(position=(0.0, 0.0, 0.0))
        ds.SliceThickness = 3.0
        volume, geom = _compute_geometry([ds])

        assert volume.shape == (1, 4, 4)
        np.testing.assert_allclose(geom.space_directions[0], [0, 0, 3.0])

    def test_single_slice_no_thickness_warns(self):
        """Single slice without thickness defaults to 1.0mm with warning."""
        ds = _make_dataset(position=(0.0, 0.0, 0.0))
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            volume, geom = _compute_geometry([ds])
            assert any("1.0 mm" in str(warning.message) for warning in w)
        np.testing.assert_allclose(geom.space_directions[0], [0, 0, 1.0])

    def test_non_uniform_spacing_warns(self):
        """Non-uniform slice spacing produces a warning."""
        datasets = []
        for i, z in enumerate([0.0, 5.0, 11.0]):  # non-uniform
            ds = _make_dataset(
                position=(0.0, 0.0, z),
                pixel_data=np.zeros((4, 4), dtype=np.uint16),
            )
            datasets.append(ds)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sorted_ds = _sort_datasets(datasets)
            _volume, _geom = _compute_geometry(sorted_ds)
            assert any("Non-uniform" in str(warning.message) for warning in w)

    def test_rescale_fields(self):
        ds = _make_dataset()
        ds.SliceThickness = 1.0
        ds.RescaleSlope = 1.0
        ds.RescaleIntercept = -1024.0
        ds.RescaleType = "HU"
        _volume, geom = _compute_geometry([ds])

        assert geom.rescale_slope == 1.0
        assert geom.rescale_intercept == -1024.0
        assert geom.rescale_type == "HU"


# ---------------------------------------------------------------------------
# Metadata building tests
# ---------------------------------------------------------------------------


class TestMetadataBuilding:
    def test_value_transforms(self):
        geom = DicomGeometry(
            shape=(3, 4, 4),
            dtype=np.dtype(np.uint16),
            space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
            space_origin=[0, 0, 0],
            space_directions=[[0, 0, 5], [0, 1, 0], [1, 0, 0]],
            slice_thickness=5.0,
            rescale_slope=1.0,
            rescale_intercept=-1024.0,
            rescale_type="HU",
        )
        ds = _make_dataset()
        ds.Modality = "CT"

        meta = build_duckn_metadata(geom, [ds], anonymized=None, include_tags=True)

        assert meta.value_transforms is not None
        assert len(meta.value_transforms) == 1
        assert meta.value_transforms[0].name == "linear"
        assert meta.value_transforms[0].parameters["slope"] == 1.0
        assert meta.value_transforms[0].parameters["intercept"] == -1024.0
        assert meta.sample_units == "HU"

    def test_dicom_extension_structure(self):
        geom = DicomGeometry(
            shape=(3, 4, 4),
            dtype=np.dtype(np.uint16),
            space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
            space_origin=[0, 0, 0],
            space_directions=[[0, 0, 5], [0, 1, 0], [1, 0, 0]],
            slice_thickness=5.0,
            rescale_slope=None,
            rescale_intercept=None,
            rescale_type=None,
        )
        ds = _make_dataset(modality="MR")

        meta = build_duckn_metadata(geom, [ds], anonymized=None, include_tags=True)

        assert meta.extensions is not None
        dicom_ext = meta.extensions["dicom"]
        assert dicom_ext["version"] == "1.0"
        assert dicom_ext["tags"]["Modality"] == "MR"

    def test_no_tags_flag(self):
        geom = DicomGeometry(
            shape=(1, 4, 4),
            dtype=np.dtype(np.uint16),
            space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
            space_origin=[0, 0, 0],
            space_directions=[[0, 0, 1], [0, 1, 0], [1, 0, 0]],
            slice_thickness=None,
            rescale_slope=None,
            rescale_intercept=None,
            rescale_type=None,
        )
        ds = _make_dataset()
        meta = build_duckn_metadata(geom, [ds], anonymized=None, include_tags=False)
        assert meta.extensions is None

    def test_axes_structure(self):
        geom = DicomGeometry(
            shape=(3, 4, 4),
            dtype=np.dtype(np.uint16),
            space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
            space_origin=[0, 0, 0],
            space_directions=[[0, 0, 5], [0, 0.5, 0], [0.5, 0, 0]],
            slice_thickness=5.0,
            rescale_slope=None,
            rescale_intercept=None,
            rescale_type=None,
        )
        ds = _make_dataset()
        meta = build_duckn_metadata(geom, [ds], anonymized=None, include_tags=False)

        assert len(meta.axes) == 3
        assert all(ax.kind == "space" for ax in meta.axes)
        assert all(ax.centering == "cell" for ax in meta.axes)
        assert all(ax.unit == "mm" for ax in meta.axes)
        assert meta.axes[0].thickness == 5.0
        assert meta.axes[1].thickness is None


# ---------------------------------------------------------------------------
# End-to-end tests
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_dicom_to_zarr_directory(self, tmp_path):
        """Write synthetic .dcm files to a directory, convert, verify."""
        dcm_dir = tmp_path / "dicom"
        dcm_dir.mkdir()

        for i in range(3):
            pixel_data = np.full((8, 8), i * 100, dtype=np.uint16)
            ds = _make_dataset(
                rows=8,
                cols=8,
                position=(0.0, 0.0, float(i * 5)),
                pixel_spacing=(0.5, 0.5),
                pixel_data=pixel_data,
            )
            ds.SliceThickness = 5.0
            ds.RescaleSlope = 1.0
            ds.RescaleIntercept = -1024.0
            ds.RescaleType = "HU"
            fds = _make_file_dataset(ds, str(dcm_dir / f"slice_{i:03d}.dcm"))
            fds.save_as(str(dcm_dir / f"slice_{i:03d}.dcm"))

        zarr_path = tmp_path / "output.zarr"
        dicom_to_zarr(dcm_dir, zarr_path)

        # Verify
        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")

        assert arr.shape == (3, 8, 8)
        assert arr.dtype == np.uint16

        duckn_meta = DucknMetadata(**arr.attrs["duckn"])
        assert duckn_meta.space == "left-posterior-superior"
        assert duckn_meta.space_origin == [0.0, 0.0, 0.0]
        assert len(duckn_meta.axes) == 3

        # Slice axis direction
        np.testing.assert_allclose(duckn_meta.axes[0].space_direction, [0, 0, 5.0])
        # Row axis
        np.testing.assert_allclose(duckn_meta.axes[1].space_direction, [0, 0.5, 0])
        # Col axis
        np.testing.assert_allclose(duckn_meta.axes[2].space_direction, [0.5, 0, 0])

        # Value transforms
        assert duckn_meta.value_transforms is not None
        assert duckn_meta.value_transforms[0].parameters["intercept"] == -1024.0

        # DICOM extension
        assert duckn_meta.extensions is not None
        dicom_ext = duckn_meta.extensions["dicom"]
        assert dicom_ext["version"] == "1.0"
        assert dicom_ext["tags"]["Modality"] == "CT"

        # Pixel data
        data = arr[:]
        assert data[0, 0, 0] == 0
        assert data[1, 0, 0] == 100
        assert data[2, 0, 0] == 200

    def test_dicom_to_zarr_overwrite(self, tmp_path):
        dcm_dir = tmp_path / "dicom"
        dcm_dir.mkdir()

        ds = _make_dataset(position=(0.0, 0.0, 0.0))
        ds.SliceThickness = 1.0
        fds = _make_file_dataset(ds, str(dcm_dir / "slice.dcm"))
        fds.save_as(str(dcm_dir / "slice.dcm"))

        zarr_path = tmp_path / "output.zarr"
        dicom_to_zarr(dcm_dir, zarr_path)
        # Second time should fail without overwrite
        with pytest.raises(Exception):
            dicom_to_zarr(dcm_dir, zarr_path)
        # With overwrite should succeed
        dicom_to_zarr(dcm_dir, zarr_path, overwrite=True)

    def test_no_tags_mode(self, tmp_path):
        dcm_dir = tmp_path / "dicom"
        dcm_dir.mkdir()

        ds = _make_dataset(position=(0.0, 0.0, 0.0))
        ds.SliceThickness = 1.0
        fds = _make_file_dataset(ds, str(dcm_dir / "slice.dcm"))
        fds.save_as(str(dcm_dir / "slice.dcm"))

        zarr_path = tmp_path / "output.zarr"
        dicom_to_zarr(dcm_dir, zarr_path, tags=False)

        import zarr

        store = zarr.storage.LocalStore(str(zarr_path))
        arr = zarr.open_array(store, mode="r")
        duckn_meta = DucknMetadata(**arr.attrs["duckn"])
        assert duckn_meta.extensions is None

    def test_multiple_series_rejected(self, tmp_path):
        dcm_dir = tmp_path / "dicom"
        dcm_dir.mkdir()

        for i, uid in enumerate(["1.2.3.4.5", "1.2.3.4.6"]):
            ds = _make_dataset(
                position=(0.0, 0.0, float(i)),
                series_uid=uid,
            )
            fds = _make_file_dataset(ds, str(dcm_dir / f"slice_{i}.dcm"))
            fds.save_as(str(dcm_dir / f"slice_{i}.dcm"))

        zarr_path = tmp_path / "output.zarr"
        with pytest.raises(ValueError, match="series"):
            dicom_to_zarr(dcm_dir, zarr_path)


# ---------------------------------------------------------------------------
# DICOM SEG → slicerseg extension
# ---------------------------------------------------------------------------


def _make_seg_dataset() -> Dataset:
    """Create a minimal DICOM SEG dataset with SegmentSequence."""
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.66.4"
    ds.SegmentationType = "BINARY"
    ds.Rows = 4
    ds.Columns = 4
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.ImagePositionPatient = [0.0, 0.0, 0.0]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.PixelSpacing = [1.0, 1.0]
    ds.Modality = "SEG"

    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.66.4"

    # Build segment 1
    seg1 = Dataset()
    seg1.SegmentNumber = 1
    seg1.SegmentLabel = "Liver"
    seg1.RecommendedDisplayCIELabValue = [39330, 30580, 41942]

    cat1 = Dataset()
    cat1.CodingSchemeDesignator = "SCT"
    cat1.CodeValue = "123037004"
    cat1.CodeMeaning = "Body structure"
    seg1.SegmentedPropertyCategoryCodeSequence = DicomSequence([cat1])

    type1 = Dataset()
    type1.CodingSchemeDesignator = "SCT"
    type1.CodeValue = "10200004"
    type1.CodeMeaning = "Liver"
    seg1.SegmentedPropertyTypeCodeSequence = DicomSequence([type1])

    anat1 = Dataset()
    anat1.CodingSchemeDesignator = "SCT"
    anat1.CodeValue = "10200004"
    anat1.CodeMeaning = "Liver"
    seg1.AnatomicRegionSequence = DicomSequence([anat1])

    # Build segment 2 (minimal)
    seg2 = Dataset()
    seg2.SegmentNumber = 2
    seg2.SegmentLabel = "Tumor"

    ds.SegmentSequence = DicomSequence([seg1, seg2])
    return ds


class TestDicomSegExtraction:
    def test_is_dicom_seg_by_sop_class(self):
        ds = _make_seg_dataset()
        assert _is_dicom_seg(ds)

    def test_is_dicom_seg_by_segment_sequence(self):
        ds = Dataset()
        ds.SegmentSequence = DicomSequence([Dataset()])
        assert _is_dicom_seg(ds)

    def test_not_dicom_seg(self):
        ds = Dataset()
        ds.Modality = "CT"
        assert not _is_dicom_seg(ds)

    def test_extract_segments(self):
        ds = _make_seg_dataset()
        ext = _extract_seg_extension(ds)
        assert ext is not None
        assert ext.version == "1.0"
        assert ext.source_representation == "binary-labelmap"
        assert len(ext.segments) == 2

        seg1 = ext.segments[0]
        assert seg1.id == "Liver"
        assert seg1.name == "Liver"
        assert seg1.label_value == 1
        assert seg1.color is not None
        assert len(seg1.color) == 3

        # DICOM classification
        assert seg1.dicom is not None
        assert seg1.dicom.category.scheme == "SCT"
        assert seg1.dicom.category.code == "123037004"
        assert seg1.dicom.type.code == "10200004"
        assert seg1.dicom.anatomic_region.code == "10200004"

        seg2 = ext.segments[1]
        assert seg2.id == "Tumor"
        assert seg2.label_value == 2
        assert seg2.dicom is None  # no coded entries

    def test_fractional_seg(self):
        ds = _make_seg_dataset()
        ds.SegmentationType = "FRACTIONAL"
        ext = _extract_seg_extension(ds)
        assert ext.source_representation == "fractional-labelmap"

    def test_seg_in_build_duckn_metadata(self):
        ds = _make_seg_dataset()
        ds.PixelData = np.zeros((4, 4), dtype=np.uint8).tobytes()
        geom = DicomGeometry(
            shape=(1, 4, 4),
            dtype=np.dtype("uint8"),
            space=SpaceName.LEFT_POSTERIOR_SUPERIOR,
            space_origin=[0.0, 0.0, 0.0],
            space_directions=[[0, 0, 1.0], [0, 1.0, 0], [1.0, 0, 0]],
            slice_thickness=None,
            rescale_slope=None,
            rescale_intercept=None,
            rescale_type=None,
        )
        meta = build_duckn_metadata(geom, [ds], anonymized=None, include_tags=False)
        assert meta.extensions is not None
        assert "slicerseg" in meta.extensions
        seg_ext = SegmentationExtension(**meta.extensions["slicerseg"])
        assert len(seg_ext.segments) == 2
