# Scenarios: source facts a converter author has in hand

Each scenario is a dataset about to be written as a duckn 2.0 Zarr array, its facts stated the
way the source gives them. Array order is C order: the first dimension listed is the slowest.
Write only what the facts support. S1-S16 were written for review rounds 1-3 (2026-09-27);
S17-S26 for rounds 4-10 (2026-09-29/30). `truth.py` computes, from these facts alone, what a
reader must recover; `headers/` holds reference 2.0 headers for S17-S26.

## S1 - Oblique axial CT (DICOM)
- 120 slices of 512 x 512 pixels, stored uint16. Array order: slice, row, column.
- DICOM patient coordinates (+x toward the patient's left, +y toward posterior, +z toward head).
- Image Orientation (Patient): row direction cosines (1, 0, 0); column direction cosines
  (0, 0.9962, -0.0872).
- Pixel Spacing 0.7 mm between rows and 0.7 mm between columns.
- First slice's Image Position (Patient): (-180.0, -200.0, -350.0). Successive slices advance by
  2.5 mm along the slice normal (0, 0.0872, 0.9962). Slice Thickness 3.0 mm.
- Rescale Slope 1, Rescale Intercept -1024, Rescale Type HU.
- Frame of Reference UID 1.2.826.0.1.3680043.2.1125.1.1.

## S2 - fMRI (NIfTI)
- 200 volumes of 64 x 64 x 36 voxels, float32. Array order: time, k, j, i.
- NIfTI sform (RAS+: +x right, +y anterior, +z superior), code 1:
  x = 3.0*i - 96.0, y = 3.0*j - 126.0, z = 3.0*k - 72.0.
- Repetition time 2.0 s; the first volume starts at t = 0.
- Slices along k were acquired in ascending order, slice k at 0.055*k s after each volume's
  start.
- Values are raw BOLD signal in arbitrary units, stored as they are.

## S3 - Whole-slide image (brightfield)
- 30000 rows x 40000 columns x 3 samples, uint8. Array order: row, column, sample.
- Samples are red, green, blue; the scanner declares the color space sRGB.
- 0.25 micrometers between pixels in both directions. No anatomical orientation and no stage
  origin are known.

## S4 - Diffusion tensor field
- 60 x 96 x 96 voxels x 6 tensor components, float32. Array order: k, j, i, component.
- Patient coordinates are DICOM's (LPS). Voxel spacing 2 mm in each direction, axis-aligned:
  i steps +x, j steps +y, k steps +z. First voxel at (-95.0, -110.0, -60.0).
- Components Dxx, Dxy, Dxz, Dyy, Dyz, Dzz in mm^2/s, written in the SCANNER's gradient frame,
  which relates to patient coordinates by the rotation (rows) [[0.9848, 0, 0.1736], [0, 1, 0],
  [-0.1736, 0, 0.9848]] (patient = R @ scanner).

## S5 - Diffusion-weighted series
- 65 volumes x 70 x 128 x 128, int16. Array order: volume, k, j, i.
- LPS, 2 mm voxels, axis-aligned (i +x, j +y, k +z), first voxel at (-127.0, -127.0, -69.0).
- Volume 0 is b = 0; volumes 1-64 are b = 1000 s/mm^2 with 64 gradient directions given in
  patient coordinates. (The gradient table itself can be left as "given elsewhere".)
- Values are raw signal, stored as they are.

## S6 - Label map
- 256 x 256 x 256 voxels, uint8. Array order: k, j, i.
- RAS+, 1 mm, axis-aligned (i +x, j +y, k +z), first voxel at (-128.0, -128.0, -128.0).
- Values 0..5: 0 background, 1 liver, 2 spleen, 3 left kidney, 4 right kidney, 5 aorta.

## S7 - One 2D slice in 3D (DICOM)
- A single slice of 256 rows x 256 columns, int16. Array order: row, column.
- LPS. Sagittal: row direction cosines (0, 1, 0), column direction cosines (0, 0, -1).
- Pixel Spacing 1.0 mm both ways; Image Position (Patient) (10.0, -120.0, 90.0);
  Slice Thickness 5.0 mm.
- Rescale Slope 1, Intercept 0, MR signal in arbitrary units.

## S8 - MR spectroscopic imaging
- 8 x 16 x 16 voxels x 1024 spectral points x 2 (real, imaginary), float32.
  Array order: k, j, i, point, part.
- LPS, 10 mm voxels, axis-aligned (i +x, j +y, k +z), first voxel at (-75.0, -75.0, -35.0).
- The spectral axis is chemical shift: point 0 is at 4.70 ppm, each point 0.0098 ppm lower.

## S9 - Registered T1 with an MNI transform
- 182 x 218 x 182 voxels, float32. Array order: k, j, i.
- RAS+ scanner coordinates, 1 mm, axis-aligned (i +x, j +y, k +z), first voxel at
  (-90.0, -126.0, -72.0).
- An affine registration to MNI152: mni = A @ [x, y, z, 1] with
  A = [[1.02, 0, 0, -2.5], [0, 0.98, 0, 4.0], [0, 0, 1.01, 1.2]].

## S10 - Fluorescence time-lapse
- 50 time points x 3 channels x 30 planes x 1024 x 1024, uint16. Array order: time, channel,
  z, y, x.
- Channels are DAPI, GFP, mCherry (three separate fluorescence channels, not a color).
- x and y 0.1 micrometers per pixel, z planes 0.5 micrometers apart; stage orientation
  unknown; origin at the stage's (0, 0, 0).
- Time points at irregular times (s), all 50: 0, 30, 65, 95, 130, 165, 195, 230, 260, 295, 325, 360, 390, 425, 455, 490, 520, 555, 585, 620, 650, 685, 715, 750, 780, 815, 845, 880, 910, 945, 975, 1010, 1040, 1075, 1105, 1140, 1170, 1205, 1235, 1270, 1300, 1335, 1365, 1400, 1430, 1465, 1495, 1530, 1560, 1595. Each is an instantaneous snapshot.

## S11 - Dynamic PET
- 24 frames x 90 x 128 x 128, int16. Array order: frame, k, j, i.
- LPS, 2 mm voxels, axis-aligned (i +x, j +y, k +z), first voxel at (-127.0, -127.0, -89.0).
- Frame start times (s), all 24: 0, 10, 20, 30, 40, 50, 60, 90, 120, 150, 180, 210, 240, 300, 360, 420, 480, 540, 600, 720, 840, 960, 1080, 1200.
- Frame durations (s): 10, 10, 10, 10, 10, 10, 30, 30, 30, 30, 30, 30, 60, 60, 60, 60, 60, 60, 120, 120, 120, 120, 120, 120. Each frame integrates activity over its whole duration.
- Each frame's activity is stored scaled: Bq/mL = stored * slope_f, intercept 0, with slopes: 1.1, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17, 1.18, 1.19, 1.2, 1.21, 1.22, 1.23, 1.24, 1.25, 1.26, 1.27, 1.28, 1.29, 1.3, 1.31, 1.32, 1.33.

## S12 - Signed distance field (node-centered)
- 101 x 101 x 101 samples, float32 millimeters (negative inside). Array order: k, j, i.
- RAS+. Samples sit exactly on a grid of nodes spanning -50 to +50 mm on each axis, 1 mm
  apart (i +x, j +y, k +z).

## S13 - Gantry-tilted CT
- 80 slices of 512 x 512 pixels, int16 already in HU. Array order: slice, row, column.
- LPS. Row cosines (1, 0, 0), column cosines (0, 1, 0). Pixel Spacing 0.8 mm.
- Slice n's Image Position (Patient) is (-200.0, -200.0 + 0.44*n, -300.0 + 1.25*n): the table
  moved 1.25 mm per slice while the gantry was tilted, so each slice is also shifted 0.44 mm in y.
- Slice Thickness 1.25 mm.

## S14 - Displacement field
- 128 x 128 x 128 voxels x 3, float32 millimeters. Array order: k, j, i, component.
- RAS+, 1.5 mm, axis-aligned (i +x, j +y, k +z), first voxel at (-96.0, -96.0, -96.0).
- The three components are the displacement's x, y, z in the same RAS+ axes.

## S15 - CT with irregular slice spacing (DICOM)
- 4 slices of 512 x 512, int16 already in HU. Array order: slice, row, column.
- LPS. Row cosines (1, 0, 0), column cosines (0, 1, 0). Pixel Spacing 0.5 mm.
- Image Position (Patient) of the four slices: (-128, -128, -100.0), (-128, -128, -97.5),
  (-128, -128, -95.0), (-128, -128, -90.0). Slice Thickness 2.5 mm.

## S16 - Image gradient field in a non-orthogonal basis
- 64 x 64 x 64 voxels x 3, float32. Array order: k, j, i, component.
- LPS, 1 mm, axis-aligned (i +x, j +y, k +z), first voxel at (0.0, 0.0, 0.0).
- Each voxel holds the spatial GRADIENT of an intensity image (a covariant vector), in units of
  1/mm, written with respect to a skewed basis whose three basis vectors, in patient coordinates,
  are the columns of B = [[1.0, 0.2, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]].

## S17 - CT converted from DICOM, stored values preserved
- 3 slices of 4 rows x 4 columns, int16, the source's STORED values unchanged. Array order:
  slice, row, column.
- DICOM patient coordinates (LPS). Row direction cosines (1, 0, 0), column direction cosines
  (0, 1, 0). Pixel Spacing 0.5 mm both ways. Slice Thickness 2.0 mm.
- Image Position (Patient) of the three slices: (-10, -20, 30), (-10, -20, 32), (-10, -20, 34).
- Rescale Slope 1, Rescale Intercept -1024, Rescale Type HU.
- Pixel Padding Value -2000 (in stored values).
- Frame of Reference UID 1.2.3.4. Synchronization Frame of Reference UID 1.2.3.9.
- Per-slice Acquisition Time: 101500.000, 101500.500, 101501.000 (DICOM TM). Series tags to
  carry: Modality CT, KVP 120, Manufacturer "Acme".
- Written by the software "duckn" version "0.7.0", converting DICOM.

## S18 - The same CT, materialized to Hounsfield units
- The same three slices, but the writer applied the rescale: the array holds Hounsfield units
  (int16). Otherwise S17's facts. Written by "haversack" version "0.16.0" (an input copy).

## S19 - Cardiac cine MR from DICOM
- 10 cardiac phases x 3 slices of 4 x 4, uint16, stored values (no rescale in the source).
  Array order: phase, slice, row, column.
- LPS. Row cosines (1, 0, 0), column cosines (0, 1, 0), Pixel Spacing 1.5 mm, slices 8 mm apart
  along +z, first slice's Image Position (Patient) (-3, -3, 0). Slice Thickness 8 mm.
- Trigger Time of the phases (ms after the ECG R wave): 0, 80, 160, ..., 720. Each image is an
  instant for this purpose.
- Frame of Reference UID 1.2.3.5. Synchronization Frame of Reference UID 1.2.3.99.
- Written by "duckn" "0.7.0".

## S20 - A NRRD with no space
- 2D, uint8. NRRD header (NRRD lists axes fastest first): `sizes: 200 100`,
  `spacings: 0.25 0.5`, `units: "mm" "mm"`, `axis mins: -5 10`, `centers: cell cell`,
  `kinds: domain domain`. No `space`, no `space directions`, no `space origin`.
- Array order in Zarr: 100 x 200 (C order: the NRRD's last axis first).
- Written by "duckn" "0.7.0".

## S21 - fMRI with interleaved slice timing (NIfTI)
- 4 volumes x 6 slices of 8 x 8, float32, raw signal as stored. Array order: t, k, j, i.
- sform code 1 (RAS): x = 3i - 12, y = 3j - 12, z = 3k - 9.
- TR 2.0 s (pixdim[4]); `xyzt_units` millimeters and seconds. The first volume starts at t = 0.
- `slice_code` interleaved increasing (odd first: NIfTI's `NIFTI_SLICE_ALT_INC`, slices acquired
  in the order k = 0, 2, 4, 1, 3, 5), `slice_duration` 0.1 s, `slice_start` 0, `slice_end` 5.
- Written by "duckn" "0.7.0".

## S22 - A resampled derivative of S17
- The tool "resample-tool" version "1.2" resampled S17's array to 1 mm x 1 mm in-plane (2 x 2
  pixels), keeping the three slices, by linear interpolation of Hounsfield units, and wrote
  float32 Hounsfield units. The world and the slice geometry are unchanged; the first pixel's
  center moved to (-9.75, -19.75, 30).

## S23 - Spectral (hyperspectral) microscopy
- 32 spectral bins x 64 x 64, uint16, raw counts as stored. Array order: bin, y, x.
- Stage axes with unknown orientation; 0.2 micrometers per pixel in x and y; stage origin
  (0, 0) at the first pixel.
- Each bin states its center wavelength: 500, 505, 510, ..., 655 nm; each bin is 5 nm wide.
- Converted from an OME-TIFF by "duckn" "0.7.0".

## S24 - A NRRD in two spatial units, permuted
- 2D, float32. NRRD header (axes fastest first): `sizes: 40 30`, `space dimension: 2`,
  `space units: "mm" "um"`, `space directions: (0,250) (0.5,0)`, `space origin: (1,2)`,
  `centers: cell cell`, `kinds: space space`.
- Array order in Zarr: 30 x 40 (the NRRD's last axis first).
- Written by "duckn" "0.7.0".

## S25 - S18, the materialized CT, with its padding kept
- Exactly S18 (the rescale applied, Hounsfield units, int16, written by haversack 0.16.0), where
  the writer wants a reader to know which voxels are padding: the source's Pixel Padding Value
  is -2000 in stored values (Rescale Intercept -1024, slope 1).

## S26 - Cardiac cine whose trigger times do not start at zero
- As S19, but the Trigger Times are 20, 100, 180, ..., 740 ms (10 phases, 80 ms apart), each
  measured after the ECG R wave, and each image an instant.
