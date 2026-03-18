# Microscopy Extension for duckn

**Extension name:** `microscopy`
**Version:** 1.0
**Status:** Draft

---

## 1. Purpose

This document defines the `microscopy` extension for the duckn convention. It captures optical microscopy acquisition metadata — imaging modality, objective parameters, channel-level fluorophore and spectral information, detector settings, and specimen context — as structured JSON within a Zarr store.

The extension addresses two complementary needs:

- **Channel semantics.** Multi-channel microscopy images are not merely "list" axes with opaque positions. Each channel has a physical meaning: an excitation wavelength, an emission filter, a fluorophore or contrast method. This information determines how channels can be combined, compared, and quantified. The extension places per-channel metadata on the channel axis, following the same pattern that `dwmri` uses for gradient vectors on the diffusion axis.

- **Acquisition context.** Microscope settings — objective NA, detector gain, laser power, pinhole size, exposure time — are essential for quantitative fluorescence analysis, deconvolution, resolution estimation, and reproducibility. These have no counterpart in the duckn convention or in Zarr itself.

### What this extension is not

This is not a microscopy file format. It does not attempt to represent vendor-specific proprietary metadata, plate/well layouts for high-content screening, or multi-resolution pyramids (which are a Zarr group-level concern, not array-level metadata). It preserves the metadata a microscopist or analysis pipeline needs to understand what was imaged, how it was acquired, and what each channel represents — without requiring a vendor-specific reader to access it.

### Relationship to OME-NGFF

The OME-NGFF (OME-Zarr) specification defines its own multiscale metadata and channel rendering hints for bioimaging. The two specifications address different concerns and can coexist in the same Zarr store without conflict, as they use different top-level attribute keys (`"duckn"` vs `"ome"`).

To understand when one or both might be useful, it helps to compare what each carries:

| Concern | OME-NGFF | This extension (duckn + microscopy) |
|---|---|---|
| Multi-resolution pyramids | Yes (`multiscales` at group level) | No (out of scope; array-level only) |
| Spatial calibration | `coordinateTransformations` (scale + translation) | `space_direction` vectors (encode scale, shear, rotation) |
| Channel display color | `omero.channels[].color` (hex string) | `channels[].color` ([R,G,B] in [0,1]) |
| Channel display name | `omero.channels[].label` | `channels[].name` |
| Window/level (contrast) | `omero.channels[].window` (start/end/min/max) | Not included (display hint; see §11) |
| Axis types | `"space"`, `"time"`, `"channel"` | Full NRRD kind vocabulary (30+ kinds) |
| Sample centering | Not specified | Explicit `"cell"` / `"node"` per axis |
| Fluorophore identity | Not specified | `channels[].fluorophore` |
| Excitation/emission wavelengths | Not specified | `channels[].excitation_nm`, `emission_nm`, `emission_range_nm` |
| Contrast method | Not specified | `channels[].contrast_method` |
| Detector settings (gain, exposure) | Not specified | Per-channel `detector_gain`, `exposure_ms`, `laser_power_*` |
| Objective parameters | Not specified | `objective` (NA, magnification, immersion, model) |
| Modality | Not specified | `modality` (confocal, light-sheet, STED, etc.) |
| Modality-specific settings | Not specified | Pinhole size, sheet thickness, pulse parameters, etc. |
| Specimen context | Not specified | `specimen` (organism, tissue, preparation) |
| Measurement frame | Not specified | Convention-level `measurement_frame` |
| Plate/well layout | Yes (`plate`, `well` specs) | Not included (separate HCS extension) |
| Physical units with formal binding | Bare string (`"micrometer"`) | String or structured UCUM/QUDT object |

The practical consequence is:

- **OME-NGFF alone** is sufficient when the goal is cloud-optimized visualization of multi-resolution bioimages and the viewer needs channel colors, contrast limits, and spatial scale. The `omero` block was designed for this — rendering-oriented metadata.

- **This extension alone** (without OME-NGFF) is sufficient when the goal is to describe a single-resolution array with full acquisition context: what modality was used, what each channel physically represents, what objective and detector settings were applied, and how the spatial embedding works (including centering, oblique orientations, and measurement frames). This is the metadata an analysis pipeline, deconvolution algorithm, or methods section needs.

- **Both together** makes sense when the same Zarr store must serve both visualization tools (which read `ome.multiscales` and `ome.omero`) and analysis tools (which read `duckn` convention fields and this extension). The two metadata blocks occupy non-overlapping attribute keys and can be maintained independently.

A few specific advantages of the duckn approach for microscopy analysis:

- **Centering is explicit.** Microscopy pixels are `cell`-centered — each pixel integrates light over a detector element area. OME-NGFF does not express this distinction, which matters for half-voxel-accurate registration, resampling, and bounding box computation.

- **The kind vocabulary prevents resampling errors.** The `"list"` kind on the channel axis tells any general-purpose resampling tool "do not interpolate across this axis." OME-NGFF's `"type": "channel"` serves a similar purpose for OME-aware tools, but the NRRD kind system provides this for any duckn-aware reader, including those from non-bioimaging domains.

- **Oblique and rotated acquisitions are first-class.** The `space_direction` vector per axis encodes arbitrary orientation — a rotated scan field, an oblique light-sheet, a tilted stage. OME-NGFF's `coordinateTransformations` support scale and translation, and in principle affine transforms, but in practice most writers emit only isotropic scale factors. The convention's per-axis direction vectors make oblique geometry the default representation rather than an edge case.

- **Channel metadata is per-axis, not a separate block.** The `channels` array lives on the axis it describes, exactly as `dwmri` gradients live on the diffusion axis. This keeps axis objects self-contained — a reader can learn everything about the channel axis by inspecting a single object, without cross-referencing a separate metadata block at a different level of the Zarr hierarchy.

---

## 2. Relationship to duckn Convention Fields

The following microscopy concepts are already captured by Zarr or the duckn convention and should not be duplicated in extension fields:

| Microscopy concept | Captured by | Notes |
|---|---|---|
| Image dimensions (width, height, depth) | Zarr `shape` | Pixel counts per axis |
| Pixel bit depth | Zarr `data_type` | e.g., `uint8`, `uint16`, `float32` |
| Voxel size (XY and Z spacing) | `axes[i].space_direction` | Direction vectors encode both spacing and orientation |
| Physical units | `axes[i].unit` | e.g., `"µm"` or structured UCUM object `{"symbol": "µm", "scheme": "UCUM", "code": "um"}` |
| Number of channels | Zarr `shape` on the channel axis | Dimension size |
| Number of time points | Zarr `shape` on the time axis | Dimension size |
| Time interval | `axes[t].space_direction` or `axes[t].unit` | Regular temporal spacing |
| Spatial coordinate system | `space` or `space_dimension` | World space identity |
| Stage position | `space_origin` | World-space position of the first voxel |
| Sample centering | `axes[i].centering` | `"cell"` for pixelated detectors (the common case) |
| Axis semantics | `axes[i].kind` | `"space"`, `"time"`, `"list"`, color kinds |
| Value rescaling | `value_transforms` `linear` | e.g., gain/offset correction already applied |

These fields *may* be mentioned in a `source_metadata` object (§5) for provenance when the original file carried its own representation of these values. The convention fields are authoritative for processing.

---

## 3. Extension Structure

The `microscopy` extension is declared at the top level of the `"duckn"` object's `"extensions"`.

```json
"extensions": {
  "microscopy": {
    "version": "1.0",
    "modality": "confocal",
    "objective": { ... },
    "instrument": { ... },
    "specimen": { ... }
  }
}
```

Per-channel and per-timepoint metadata are carried in per-axis extension objects within the `axes` array (§6).

### 3.1 Top-Level Extension Fields

These describe the imaging system and acquisition context that apply to the array as a whole.

#### `version`

Required. The version of this extension specification.

```json
"version": "1.0"
```

#### `schema`

A URL pointing to a schema or specification document for the extension. Optional. Readers may use this for validation or surface it to users as documentation.

```json
"schema": "https://example.org/microscopy-zarr/v1.0/schema.json"
```

#### `modality`

The imaging modality. This is the primary discriminator for what other fields are meaningful.

| Value | Description |
|-------|-------------|
| `"widefield"` | Standard widefield fluorescence or transmitted-light microscope |
| `"confocal"` | Laser scanning confocal (single-point) |
| `"spinning-disk"` | Spinning-disk (Nipkow disk) confocal |
| `"two-photon"` | Two-photon (multiphoton) excitation fluorescence |
| `"light-sheet"` | Light-sheet fluorescence microscopy (LSFM / SPIM) |
| `"structured-illumination"` | Structured illumination microscopy (SIM) |
| `"sted"` | Stimulated emission depletion |
| `"palm-storm"` | Photoactivated localization / stochastic optical reconstruction |
| `"brightfield"` | Transmitted-light brightfield |
| `"phase-contrast"` | Zernike phase contrast |
| `"dic"` | Differential interference contrast |
| `"darkfield"` | Dark-field illumination |
| `"spectral"` | Lambda-stack / hyperspectral acquisition (spectral detector) |
| `"flim"` | Fluorescence lifetime imaging |

This vocabulary is extensible. A reader that encounters an unrecognized modality should treat it as an opaque string — the data remains valid, but modality-specific fields (§4) may not be interpretable.

```json
"modality": "confocal"
```

Omit if the modality is unknown.

#### `objective`

An object describing the objective lens. See §3.2.

#### `instrument`

An object describing the microscope and detector hardware. See §3.3.

#### `specimen`

An object describing the biological or material sample. See §3.4.

#### `environment`

An object describing environmental conditions during live-cell imaging. See §3.5.

---

### 3.2 Objective

The objective lens defines the imaging system's resolution limit and light-gathering capacity. These parameters are essential for deconvolution, PSF estimation, and resolution claims.

```json
"objective": {
  "magnification": 63,
  "numerical_aperture": 1.4,
  "immersion": "oil",
  "refractive_index": 1.515,
  "model": "Plan-Apochromat 63x/1.40 Oil DIC M27",
  "working_distance_mm": 0.19,
  "correction_collar_mm": 0.17
}
```

| Field | Type | Description |
|-------|------|-------------|
| `magnification` | number | Nominal magnification (unitless). Not used for calibration — voxel size comes from `space_direction`. |
| `numerical_aperture` | number | The objective's NA. Determines lateral and axial resolution limits. |
| `immersion` | string | Immersion medium. See table below. |
| `refractive_index` | number | Refractive index of the immersion medium at the design wavelength. |
| `model` | string | Manufacturer's full designation. Provenance only. |
| `working_distance_mm` | number | Free working distance in millimeters. |
| `correction_collar_mm` | number | Correction collar setting as cover glass thickness in millimeters (e.g., 0.17 for standard #1.5 cover glass). For objectives with non-thickness correction collars, document the actual parameter in `model`. |

All fields are optional. Include what is known.

**Immersion values:**

| Value | Description |
|-------|-------------|
| `"air"` | Dry objective (no immersion) |
| `"water"` | Water immersion |
| `"oil"` | Immersion oil (typically n ≈ 1.515) |
| `"glycerol"` | Glycerol immersion (typically n ≈ 1.47) |
| `"silicone"` | Silicone oil immersion (typically n ≈ 1.40) |
| `"multi"` | Multi-immersion objective (actual medium should be stated in `refractive_index`) |

Other values are permitted as free strings.

---

### 3.3 Instrument

Hardware identification and global detector settings.

```json
"instrument": {
  "manufacturer": "Zeiss",
  "model": "LSM 880",
  "detector_type": "GaAsP-PMT",
  "software": "ZEN 3.5",
  "bit_depth_acquired": 12
}
```

| Field | Type | Description |
|-------|------|-------------|
| `manufacturer` | string | Microscope manufacturer. |
| `model` | string | Microscope model name. |
| `detector_type` | string | Primary detector technology. See table below. |
| `software` | string | Acquisition software and version. |
| `camera_model` | string | Camera model, for camera-based systems (widefield, spinning-disk). Omit for point-scanning systems without a camera. |
| `bit_depth_acquired` | integer | Actual digitization depth (e.g., 12 bits stored in a 16-bit container). |

All fields are optional.

**Detector type values:**

| Value | Description |
|-------|-------------|
| `"PMT"` | Photomultiplier tube |
| `"GaAsP-PMT"` | GaAsP photomultiplier (high QE) |
| `"HyD"` | Hybrid detector (Leica) |
| `"APD"` | Avalanche photodiode |
| `"CCD"` | Charge-coupled device camera |
| `"sCMOS"` | Scientific CMOS camera |
| `"EMCCD"` | Electron-multiplying CCD |
| `"spectral"` | Spectral detector (array of wavelength bins) |
| `"SPAD-array"` | Single-photon avalanche diode array (used in FLIM, photon counting) |

Other values are permitted as free strings.

---

### 3.4 Specimen

Biological or material sample context. These fields support reproducibility and data discovery.

```json
"specimen": {
  "organism": "Mus musculus",
  "tissue": "brain",
  "cell_type": "cortical neuron",
  "preparation": "cryosection",
  "mounting_medium": "ProLong Gold",
  "cover_glass_thickness_mm": 0.17,
  "staining_protocol": "Immunofluorescence, paraformaldehyde-fixed"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `organism` | string | Species name (binomial nomenclature recommended). Omit for non-biological samples. |
| `tissue` | string | Tissue or organ. |
| `cell_type` | string | Specific cell type, if applicable. |
| `preparation` | string | Sample preparation method (e.g., `"cryosection"`, `"whole-mount"`, `"cell-culture"`, `"smear"`, `"paraffin-section"`, `"clearing"`, `"thin-film"`, `"polished-section"`). |
| `mounting_medium` | string | Mounting or embedding medium. Important for refractive-index matching. |
| `cover_glass_thickness_mm` | number | Cover glass thickness in mm (common values: 0.17 for #1.5, 0.13 for #1). |
| `staining_protocol` | string | Free-text description of the staining or labeling protocol. |
| `material` | string | For non-biological samples: material description (e.g., `"silicon wafer"`, `"GaAs thin film"`, `"polymer blend"`). |

All fields are optional. Include what is known. For cultured cells, `tissue` may be omitted and `cell_type` used alone. For materials science or industrial inspection, `organism` and `tissue` are omitted and `material` is used instead.

---

### 3.5 Environment

Environmental conditions during acquisition. Relevant for live-cell imaging and time-lapse experiments.

```json
"environment": {
  "temperature_celsius": 37.0,
  "co2_percent": 5.0,
  "humidity_percent": 95.0,
  "medium": "DMEM + 10% FBS"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `temperature_celsius` | number | Incubator or stage temperature in °C. |
| `co2_percent` | number | CO₂ concentration (percent). |
| `humidity_percent` | number | Relative humidity (percent). |
| `medium` | string | Culture medium or immersion liquid. |

All fields are optional. Omit the entire `environment` object for fixed samples or when conditions are unknown.

---

## 4. Modality-Specific Fields

Some acquisition parameters are meaningful only for specific modalities. These fields appear at the top level of the extension object, alongside the universal fields.

### 4.1 Confocal (`"confocal"`, `"spinning-disk"`)

| Field | Type | Description |
|-------|------|-------------|
| `pinhole_diameter_au` | number | Pinhole aperture in Airy units. 1 AU = 1.22 λ/NA. |
| `pinhole_diameter_um` | number | Pinhole aperture in micrometers (physical size). |
| `pinhole_spacing_um` | number | Center-to-center distance between pinholes (spinning-disk only). |
| `scan_speed_hz` | number | Line scan rate in Hz (laser-scanning only). |
| `scan_direction` | string | `"unidirectional"` or `"bidirectional"` (laser-scanning only). |
| `averaging` | integer | Number of line or frame averages. |
| `zoom` | number | Scan zoom factor (> 1 means smaller field of view). |

For spinning-disk systems, `pinhole_diameter_um` refers to a single pinhole in the disk.

```json
"modality": "confocal",
"pinhole_diameter_au": 1.0,
"scan_direction": "bidirectional",
"averaging": 2,
"zoom": 1.5
```

### 4.2 Light-Sheet (`"light-sheet"`)

| Field | Type | Description |
|-------|------|-------------|
| `sheet_thickness_um` | number | Nominal light-sheet thickness (FWHM) at the waist, in micrometers. |
| `sheet_na` | number | Effective NA of the illumination path. |
| `illumination_objective` | string | Model or description of the illumination objective. |
| `detection_objective` | string | Model or description of the detection objective (if different from §3.2 `objective`). |
| `sheet_angle_degrees` | number | Angle between the sheet and the detection axis, in degrees (90 for orthogonal). |
| `dual_sided` | boolean | `true` if illumination comes from both sides. |
| `pivot_scan` | boolean | `true` if the sheet is pivot-scanned to reduce striping. |

```json
"modality": "light-sheet",
"sheet_thickness_um": 4.0,
"sheet_na": 0.03,
"dual_sided": true
```

### 4.3 Two-Photon (`"two-photon"`)

| Field | Type | Description |
|-------|------|-------------|
| `laser_wavelength_nm` | number | Fundamental (excitation) wavelength of the pulsed laser. |
| `pulse_rate_mhz` | number | Laser repetition rate. |
| `pulse_width_fs` | number | Pulse duration (FWHM) in femtoseconds. |
| `gdd_compensation` | boolean | `true` if group delay dispersion was pre-compensated. |

```json
"modality": "two-photon",
"laser_wavelength_nm": 920,
"pulse_rate_mhz": 80,
"pulse_width_fs": 140
```

### 4.4 Super-Resolution (`"sted"`, `"structured-illumination"`, `"palm-storm"`)

| Field | Type | Description |
|-------|------|-------------|
| `depletion_wavelength_nm` | number | STED depletion laser wavelength. |
| `depletion_power_mw` | number | STED depletion laser power in milliwatts. |
| `pattern_orientations` | integer | Number of SIM pattern orientations (typically 3 or 5). |
| `pattern_phases` | integer | Number of SIM phase steps per orientation (typically 3 or 5). |
| `localization_precision_nm` | number | Estimated localization precision for PALM/STORM. |
| `reconstruction_method` | string | Algorithm used for image reconstruction (e.g., `"fairSIM"`, `"ThunderSTORM"`, `"Huygens"`). |

### 4.5 Spectral / Lambda-Stack (`"spectral"`)

| Field | Type | Description |
|-------|------|-------------|
| `spectral_detector_channels` | integer | Number of detector channels in the spectral array. |
| `spectral_range_nm` | array of 2 numbers | Total wavelength range of the spectral detector as `[low, high]` in nanometers. |
| `spectral_bin_width_nm` | number | Width of each spectral bin in nanometers (when uniform). |
| `unmixing_method` | string | Linear unmixing algorithm applied (e.g., `"linear-least-squares"`, `"non-negative-least-squares"`, `"VCA"`). Omit if data is raw (not unmixed). |
| `reference_spectra` | object | Reference emission fingerprints used for unmixing. Keys are fluorophore names; values are arrays of intensity values (one per spectral bin). |

The spectral/wavelength axis uses the convention's existing `"list"` kind with per-axis extension metadata to describe each spectral bin (see §6.4).

```json
"modality": "spectral",
"spectral_detector_channels": 32,
"spectral_range_nm": [415, 735],
"spectral_bin_width_nm": 10
```

### 4.6 Fluorescence Lifetime (`"flim"`)

| Field | Type | Description |
|-------|------|-------------|
| `excitation_wavelength_nm` | number | Pulsed excitation laser wavelength. |
| `time_resolution_ps` | number | Temporal resolution of the TCSPC histogram in picoseconds. |
| `time_range_ns` | number | Total time range of the decay histogram in nanoseconds. |
| `repetition_rate_mhz` | number | Laser repetition rate. |
| `irf_fwhm_ps` | number | Instrument response function FWHM in picoseconds. |
| `fitting_method` | string | Lifetime fitting approach (e.g., `"single-exponential"`, `"bi-exponential"`, `"phasor"`). Omit if data is raw photon counts. |

The time-bin axis for raw TCSPC data uses `"list"` kind with per-axis metadata describing the bin edges (see §6.5).

```json
"modality": "flim",
"excitation_wavelength_nm": 488,
"time_resolution_ps": 12.5,
"time_range_ns": 12.5,
"repetition_rate_mhz": 80,
"irf_fwhm_ps": 120
```

### 4.7 Modality-Specific Field Rules

- Modality-specific fields must not appear when their modality is not declared in `modality`. A reader encountering `pinhole_diameter_au` without `"modality": "confocal"` (or `"spinning-disk"`) should treat it as an error.
- A reader that understands the `modality` value should interpret the modality-specific fields. A reader that does not recognize the modality should ignore all fields it does not understand, per the convention's extension rules.
- All modality-specific fields are optional. Include what is known.

---

## 5. Source Metadata Provenance

When converting from a vendor file format (Zeiss CZI, Nikon ND2, Leica LIF, OME-TIFF, etc.), the original format may carry acquisition values that overlap with convention fields. To preserve the source-native representation for round-tripping or debugging, the extension may include a `source_metadata` object.

```json
"source_metadata": {
  "format": "CZI",
  "software_version": "ZEN 3.5 (blue edition)",
  "pixel_size_x_um": 0.0908,
  "pixel_size_y_um": 0.0908,
  "pixel_size_z_um": 0.50
}
```

| Field | Type | Description |
|-------|------|-------------|
| `format` | string | Source file format name (e.g., `"CZI"`, `"ND2"`, `"LIF"`, `"OME-TIFF"`, `"ICS"`, `"LSM"`). |
| `software_version` | string | Acquisition software version string from the source file. |

Additional keys are free-form and carry the source format's native values. The convention fields (`space_direction`, `space_origin`, `unit`, etc.) are authoritative for processing. Source metadata values are provenance.

This parallels the DICOM extension's approach: `PixelSpacing` may appear in `tags` for provenance, but `axes[i].space_direction` governs computation.

---

## 6. Per-Axis Extension Fields

Per-axis metadata appears in the `extensions.microscopy` object on individual axes within the `axes` array.

### 6.1 Channel Axis

The channel axis carries the most important per-axis metadata in this extension. Each position along the channel axis has distinct physical meaning: a fluorophore, a wavelength range, a contrast method. The `channels` array encodes this, with one element per position along the axis.

```json
{
  "kind": "list",
  "extensions": {
    "microscopy": {
      "channels": [
        { ... },
        { ... }
      ]
    }
  }
}
```

The length of `channels` must equal the corresponding dimension in `shape`.

#### Channel Object Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Display label (e.g., `"DAPI"`, `"GFP"`, `"Brightfield"`). |
| `contrast_method` | string | no | How the signal is generated. See table below. |
| `fluorophore` | string | no | Name of the fluorescent dye or protein (e.g., `"DAPI"`, `"EGFP"`, `"Alexa Fluor 647"`, `"tdTomato"`). Omit for non-fluorescence channels. |
| `antibody_target` | string | no | Target protein or antigen for immunofluorescence (e.g., `"Ki-67"`, `"CD3"`, `"PanCK"`). Distinct from `fluorophore`, which names the label, not the target. |
| `excitation_nm` | number | no | Peak excitation wavelength in nanometers. |
| `emission_nm` | number | no | Peak emission wavelength (or center of bandpass filter) in nanometers. |
| `emission_range_nm` | array of 2 numbers | no | Emission bandpass filter window as `[low, high]` in nanometers. |
| `excitation_range_nm` | array of 2 numbers | no | Excitation bandpass as `[low, high]` in nanometers (relevant for widefield with filter cubes). |
| `color` | array of 3 numbers | no | Suggested display pseudocolor as `[R, G, B]`, each in `[0, 1]`. |
| `exposure_ms` | number | no | Exposure time or pixel dwell time in milliseconds. |
| `laser_power_percent` | number | no | Laser power as a percentage of maximum. |
| `laser_power_mw` | number | no | Laser power in milliwatts (absolute). |
| `detector_gain` | number | no | Detector gain setting (arbitrary units, instrument-dependent). |
| `detector_offset` | number | no | Detector offset/black level setting. |
| `illumination_intensity_percent` | number | no | Lamp or LED intensity as a percentage (for widefield). |
| `light_source` | string | no | Description of the light source (e.g., `"Ar 488nm"`, `"Mercury arc"`, `"LED 405nm"`, `"White light laser"`). |
| `cycle` | integer | no | Imaging cycle or round number (for cyclic multiplexed protocols such as CyCIF, CODEX, or mIHC). |

**Contrast method values:**

| Value | Description |
|-------|-------------|
| `"fluorescence"` | Standard single-photon fluorescence excitation |
| `"two-photon-fluorescence"` | Two-photon excitation fluorescence |
| `"transmitted"` | Transmitted-light brightfield |
| `"phase-contrast"` | Zernike phase contrast |
| `"dic"` | Differential interference contrast |
| `"darkfield"` | Dark-field illumination |
| `"reflected"` | Reflected-light / epi-illumination (non-fluorescence) |
| `"polarization"` | Polarized light |
| `"second-harmonic"` | Second-harmonic generation (SHG) |
| `"third-harmonic"` | Third-harmonic generation (THG) |
| `"coherent-anti-stokes-raman"` | CARS imaging |
| `"stimulated-raman"` | SRS imaging |
| `"metal-isotope"` | Metal-tagged antibody detection (imaging mass cytometry, MIBI) |

Other values are permitted as free strings. A reader that encounters an unrecognized value should treat it as opaque.

**Why `name` is required:** Every channel needs a display label. The name is independent of any ontology — it might be a fluorophore name, a filter set designation, or a user label. It serves the same role as `name` on segments in the segmentation extension.

**Why `color` is here:** The same rationale as the segmentation extension: pseudocolor assignment is so tightly bound to channel identity in microscopy workflows that omitting it would force every viewer to reinvent LUT assignment. Green for GFP, blue for DAPI, red for mCherry are near-universal conventions. The field is a recommended display color, not a mandate.

**Why `antibody_target` is separate from `fluorophore`:** In immunofluorescence, the fluorophore (Alexa Fluor 488) and the target (Ki-67) are independent. A Ki-67 antibody could be conjugated to any fluorophore. In multiplexed tissue imaging, the target protein is often more important for analysis than the detection chemistry. Keeping them separate avoids overloading `fluorophore` with two different kinds of information.

---

### 6.2 Time Axis

For time-lapse acquisitions with regular intervals, the convention's `space_direction` (or the axis `unit` alone) is sufficient. The extension adds per-timepoint metadata when the acquisition was irregular or when per-timepoint context is needed.

```json
{
  "kind": "time",
  "unit": "s",
  "extensions": {
    "microscopy": {
      "timestamps": [0.0, 30.1, 60.0, 90.2, 120.1],
      "events": {
        "15": "Drug addition: 10µM nocodazole",
        "35": "Washout"
      }
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `timestamps` | array of numbers | Actual acquisition time of each frame, in the units declared by the axis `unit`. Length must equal the time axis dimension in `shape`. |
| `events` | object | Optional. Keys are string-encoded frame indices (e.g., `"15"`); values are free-text event descriptions. String keys are required because JSON object keys must be strings. Useful for recording experimental interventions during time-lapse. |

When `timestamps` is present, it is authoritative for the time coordinate of each frame. The axis `space_direction` (if present) gives the nominal inter-frame interval; `timestamps` gives the actual values.

---

### 6.3 Z Axis

Spatial Z axes use the convention's standard `space_direction` and `unit` fields. The extension adds Z-specific metadata only when needed:

```json
{
  "kind": "space",
  "centering": "cell",
  "space_direction": [0, 0, 0.5],
  "unit": "µm",
  "extensions": {
    "microscopy": {
      "z_positions": [0.0, 0.52, 1.01, 1.53, 2.02],
      "z_drive": "piezo"
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `z_positions` | array of numbers | Actual Z stage or piezo positions for each slice, in the units declared by the axis `unit`. Length must equal the Z axis dimension in `shape`. |
| `z_drive` | string | Z positioning mechanism: `"piezo"`, `"stepper"`, `"galvo"` (for remote focusing), or a free string. |

`z_positions` serves the same purpose as `timestamps` on the time axis: the actual measured positions when the nominal spacing is not perfectly uniform. The axis `space_direction` gives the nominal Z step; `z_positions` gives the reality.

---

### 6.4 Spectral (Lambda) Axis

For spectral imaging (lambda stacks), the wavelength axis carries per-bin metadata describing the center wavelength of each spectral channel:

```json
{
  "kind": "list",
  "extensions": {
    "microscopy": {
      "spectral_bins_nm": [420, 430, 440, 450, 460, 470, 480, 490, 500, 510, 520, 530],
      "spectral_bin_width_nm": 10
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `spectral_bins_nm` | array of numbers | Center wavelength of each spectral bin, in nanometers. Length must equal the spectral axis dimension in `shape`. |
| `spectral_bin_width_nm` | number | Width of each spectral bin (when uniform). When bin widths vary, omit this and compute from adjacent `spectral_bins_nm` values. |

A reader that ignores the extension sees a `"list"` axis and knows not to interpolate across it. A reader that understands the extension additionally knows the wavelength associated with each position.

---

### 6.5 FLIM Time-Bin Axis

For raw TCSPC (time-correlated single photon counting) data, the time-bin axis carries per-bin timing metadata:

```json
{
  "kind": "list",
  "extensions": {
    "microscopy": {
      "time_bins_ps": [0, 12.5, 25.0, 37.5, 50.0],
      "time_bin_width_ps": 12.5
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `time_bins_ps` | array of numbers | Start time of each time bin in picoseconds relative to the excitation pulse. Length must equal the time-bin axis dimension in `shape`. |
| `time_bin_width_ps` | number | Width of each time bin in picoseconds (when uniform). |

The time-bin axis uses `"list"` kind, not `"time"`, because these are bins in a decay histogram — not sequential acquisition timepoints. They should not be resampled. The convention-level `"time"` axis (if present) describes the time-lapse dimension; the FLIM time-bin axis describes photon arrival times within each pixel.

---

## 7. Tiling and Mosaic Provenance

For stitched or tiled acquisitions (common in whole-slide imaging, large-area confocal, and tile-scan experiments), individual tiles may be stored as separate Zarr arrays within a group. Each tile array carries its own `space_origin` (the convention field), which places it in world coordinates. The extension adds provenance about the tile's position in the acquisition grid.

```json
"tile": {
  "grid_position": [2, 3],
  "grid_size": [5, 8],
  "overlap_percent": 10,
  "stitching_method": "phase-correlation",
  "stitching_quality": 0.95
}
```

| Field | Type | Description |
|-------|------|-------------|
| `grid_position` | array of integers | This tile's `[row, column]` position in the acquisition grid (0-indexed). |
| `grid_size` | array of integers | Total grid dimensions as `[rows, columns]`. |
| `overlap_percent` | number | Nominal overlap between adjacent tiles (percent of tile width/height). |
| `stitching_method` | string | Algorithm used for registration (e.g., `"phase-correlation"`, `"feature-matching"`, `"stage-coordinates"`). |
| `stitching_quality` | number | Quality metric from the stitching algorithm (range and meaning are algorithm-dependent). |

The `tile` object is optional. Omit entirely for non-tiled acquisitions. When present, the convention's `space_origin` gives the tile's final registered position in world coordinates; `grid_position` records where it sat in the acquisition grid before stitching.

---

## 8. Consistency Rules

- If `modality` is present, modality-specific fields (§4) should be consistent with the declared modality. A reader should warn if `pinhole_diameter_au` appears without a confocal modality.
- The length of `channels` must equal the corresponding dimension in `shape` for the channel axis.
- The length of `timestamps` must equal the corresponding dimension in `shape` for the time axis.
- The length of `z_positions` must equal the corresponding dimension in `shape` for the Z spatial axis.
- The length of `spectral_bins_nm` must equal the corresponding dimension in `shape` for the spectral axis.
- The length of `time_bins_ps` must equal the corresponding dimension in `shape` for the FLIM time-bin axis.
- Channel `color` values must be in the range `[0, 1]`.
- `emission_range_nm` must be `[low, high]` where `low < high`.
- Spatial calibration is carried by the convention's `axes[i].space_direction` — not by the extension. `source_metadata` pixel sizes are provenance only.
- The convention's `space_origin` is authoritative for tile position, not `tile.grid_position`.
- If the extension is used anywhere in the `"duckn"` object — including only on per-axis `extensions` — it must have an entry in the top-level `extensions` with at least a `"version"`, per the convention's extension rules.

---

## 9. Examples

### 9.1 3D Confocal Time-Lapse, Two Fluorescence Channels

A live-cell confocal Z-stack time series with DAPI and mCherry:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [50, 2, 40, 1024, 1024],
  "data_type": "uint16",
  "dimension_names": ["t", "channel", "z", "y", "x"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [1, 1, 40, 512, 512] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "3D-right-handed",
      "space_origin": [1200.0, 3400.0, 0.0],
      "sample_units": "counts",
      "axes": [
        {
          "kind": "time",
          "centering": "node",
          "unit": "s",
          "extensions": {
            "microscopy": {
              "timestamps": [0, 30, 60, 90, 120]
            }
          }
        },
        {
          "kind": "list",
          "extensions": {
            "microscopy": {
              "channels": [
                {
                  "name": "DAPI",
                  "fluorophore": "DAPI",
                  "contrast_method": "fluorescence",
                  "excitation_nm": 405,
                  "emission_nm": 461,
                  "emission_range_nm": [410, 510],
                  "color": [0.0, 0.0, 1.0],
                  "laser_power_percent": 5.0,
                  "detector_gain": 650,
                  "light_source": "Diode 405nm"
                },
                {
                  "name": "mCherry",
                  "fluorophore": "mCherry",
                  "contrast_method": "fluorescence",
                  "excitation_nm": 561,
                  "emission_nm": 610,
                  "emission_range_nm": [570, 650],
                  "color": [1.0, 0.0, 0.0],
                  "laser_power_percent": 8.0,
                  "detector_gain": 700,
                  "light_source": "DPSS 561nm"
                }
              ]
            }
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 0.5],
          "unit": {
            "symbol": "µm",
            "scheme": "UCUM",
            "code": "um"
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0.09, 0],
          "unit": {
            "symbol": "µm",
            "scheme": "UCUM",
            "code": "um"
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.09, 0, 0],
          "unit": {
            "symbol": "µm",
            "scheme": "UCUM",
            "code": "um"
          }
        }
      ],
      "extensions": {
        "microscopy": {
          "version": "1.0",
          "schema": "https://example.org/microscopy-zarr/v1.0/schema.json",
          "modality": "confocal",
          "pinhole_diameter_au": 1.0,
          "scan_direction": "bidirectional",
          "averaging": 2,
          "objective": {
            "magnification": 63,
            "numerical_aperture": 1.4,
            "immersion": "oil",
            "refractive_index": 1.515,
            "model": "Plan-Apochromat 63x/1.40 Oil DIC M27"
          },
          "instrument": {
            "manufacturer": "Zeiss",
            "model": "LSM 880",
            "detector_type": "GaAsP-PMT",
            "software": "ZEN 3.5",
            "bit_depth_acquired": 12
          },
          "environment": {
            "temperature_celsius": 37.0,
            "co2_percent": 5.0
          },
          "specimen": {
            "organism": "Homo sapiens",
            "cell_type": "HeLa",
            "preparation": "cell-culture",
            "mounting_medium": "live imaging medium"
          }
        }
      }
    }
  }
}
```

A reader that does not understand the `microscopy` extension still sees: five axes (time in seconds, a 2-element list, and three spatial axes in micrometers), stage position from `space_origin`, and voxel sizes from `space_direction`. It can display and resample the spatial data correctly. The extension adds the knowledge that channel 0 is DAPI at 405/461nm and channel 1 is mCherry at 561/610nm, acquired on a Zeiss LSM 880 with a 63×/1.4 oil objective, 1 AU pinhole, at 37°C.

### 9.2 Widefield RGB Histology

A brightfield histology image from a whole-slide scanner, stored as RGB:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [50000, 40000, 3],
  "data_type": "uint8",
  "dimension_names": ["y", "x", "channel"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [512, 512, 3] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 255,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space_dimension": 2,
      "space_origin": [0.0, 0.0],
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0.25],
          "unit": "µm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.25, 0],
          "unit": "µm"
        },
        {
          "kind": "RGB-color"
        }
      ],
      "extensions": {
        "microscopy": {
          "version": "1.0",
          "modality": "brightfield",
          "objective": {
            "magnification": 40,
            "numerical_aperture": 0.95,
            "immersion": "air"
          },
          "instrument": {
            "manufacturer": "Hamamatsu",
            "model": "NanoZoomer S360",
            "detector_type": "CCD"
          },
          "specimen": {
            "organism": "Homo sapiens",
            "tissue": "liver",
            "preparation": "paraffin-section",
            "staining_protocol": "H&E"
          }
        }
      }
    }
  }
}
```

Here the color axis uses the convention's `RGB-color` kind directly — no per-channel extension metadata is needed because the three components are red, green, and blue by definition. The extension carries the modality, objective, and specimen context.

### 9.3 Light-Sheet with Cleared Tissue

A cleared-tissue light-sheet volume:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [3, 2000, 2000, 2000],
  "data_type": "uint16",
  "dimension_names": ["channel", "z", "y", "x"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [1, 128, 128, 128] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "3D-right-handed",
      "space_origin": [0.0, 0.0, 0.0],
      "sample_units": "counts",
      "axes": [
        {
          "kind": "list",
          "extensions": {
            "microscopy": {
              "channels": [
                {
                  "name": "Autofluorescence",
                  "contrast_method": "fluorescence",
                  "excitation_nm": 488,
                  "emission_nm": 525,
                  "emission_range_nm": [500, 550],
                  "color": [0.0, 1.0, 0.0]
                },
                {
                  "name": "tdTomato",
                  "fluorophore": "tdTomato",
                  "contrast_method": "fluorescence",
                  "excitation_nm": 561,
                  "emission_nm": 581,
                  "emission_range_nm": [570, 620],
                  "color": [1.0, 0.5, 0.0]
                },
                {
                  "name": "iDISCO nuclear",
                  "fluorophore": "TO-PRO-3",
                  "contrast_method": "fluorescence",
                  "excitation_nm": 647,
                  "emission_nm": 661,
                  "emission_range_nm": [655, 720],
                  "color": [1.0, 0.0, 1.0]
                }
              ]
            }
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 2.0],
          "unit": "µm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0.65, 0],
          "unit": "µm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.65, 0, 0],
          "unit": "µm"
        }
      ],
      "extensions": {
        "microscopy": {
          "version": "1.0",
          "modality": "light-sheet",
          "sheet_thickness_um": 4.0,
          "sheet_na": 0.03,
          "dual_sided": true,
          "pivot_scan": true,
          "objective": {
            "magnification": 10,
            "numerical_aperture": 0.5,
            "immersion": "oil",
            "refractive_index": 1.56,
            "model": "LVMI-Fluor 10x/0.50"
          },
          "instrument": {
            "manufacturer": "LaVision BioTec",
            "model": "UltraMicroscope II",
            "detector_type": "sCMOS",
            "camera_model": "Andor Zyla 4.2"
          },
          "specimen": {
            "organism": "Mus musculus",
            "tissue": "brain",
            "preparation": "clearing",
            "staining_protocol": "iDISCO+ whole-mount immunolabeling"
          }
        }
      }
    }
  }
}
```

### 9.4 Multiplexed Tissue Imaging (CyCIF)

A subset of channels from a 30-channel CyCIF (cyclic immunofluorescence) dataset on FFPE tissue. This example shows the top-level extension and the per-axis channel metadata separately for readability. In a real array, both appear in the same `"duckn"` object.

Top-level extension:

```json
"extensions": {
  "microscopy": {
    "version": "1.0",
    "modality": "widefield",
    "objective": {
      "magnification": 20,
      "numerical_aperture": 0.75,
      "immersion": "air"
    },
    "instrument": {
      "manufacturer": "RareCyte",
      "model": "CyteFinder",
      "detector_type": "sCMOS"
    },
    "specimen": {
      "organism": "Homo sapiens",
      "tissue": "lung adenocarcinoma",
      "preparation": "FFPE section",
      "staining_protocol": "CyCIF, 10 cycles"
    }
  }
}
```

Per-axis channel metadata (showing 4 of 30 channels):

```json
{
  "kind": "list",
  "extensions": {
    "microscopy": {
      "channels": [
        {
          "name": "DNA-1",
          "fluorophore": "Hoechst 33342",
          "antibody_target": "DNA",
          "contrast_method": "fluorescence",
          "excitation_nm": 395,
          "emission_nm": 440,
          "color": [0.0, 0.0, 1.0],
          "cycle": 1
        },
        {
          "name": "Ki-67",
          "fluorophore": "Alexa Fluor 488",
          "antibody_target": "Ki-67",
          "contrast_method": "fluorescence",
          "excitation_nm": 488,
          "emission_nm": 525,
          "color": [0.0, 1.0, 0.0],
          "cycle": 2
        },
        {
          "name": "PanCK",
          "fluorophore": "Alexa Fluor 555",
          "antibody_target": "Pan-Cytokeratin",
          "contrast_method": "fluorescence",
          "excitation_nm": 555,
          "emission_nm": 580,
          "color": [1.0, 0.5, 0.0],
          "cycle": 3
        },
        {
          "name": "CD3",
          "fluorophore": "Alexa Fluor 647",
          "antibody_target": "CD3",
          "contrast_method": "fluorescence",
          "excitation_nm": 647,
          "emission_nm": 665,
          "color": [1.0, 0.0, 0.0],
          "cycle": 4
        }
      ]
    }
  }
}
```

The `cycle` field records which imaging round each channel was acquired in. This is critical for interpreting bleaching artifacts, registration accuracy between cycles, and understanding the experimental workflow. The `antibody_target` field names the biological marker, distinct from the `fluorophore` that provides the signal.

### 9.5 Tile from a Mosaic Acquisition

One tile in a tiled confocal scan:

```json
"extensions": {
  "microscopy": {
    "version": "1.0",
    "modality": "confocal",
    "pinhole_diameter_au": 1.0,
    "objective": {
      "magnification": 20,
      "numerical_aperture": 0.8,
      "immersion": "air"
    },
    "tile": {
      "grid_position": [2, 3],
      "grid_size": [5, 8],
      "overlap_percent": 10,
      "stitching_method": "phase-correlation",
      "stitching_quality": 0.97
    }
  }
}
```

The tile's world position comes from `space_origin`. The `tile` object records the acquisition grid context.

### 9.6 Minimal

A microscopy image with only the modality declared:

```json
"extensions": {
  "microscopy": {
    "version": "1.0",
    "modality": "widefield"
  }
}
```

This says "this data was acquired on a widefield microscope" and nothing more.

---

## 10. NRRD Key/Value Encoding

When microscopy metadata is stored in a NRRD file (rather than a Zarr store), the same fields are encoded as key/value pairs with a `microscopy_` prefix:

```
microscopy_modality:=confocal
microscopy_objective_magnification:=63
microscopy_objective_numerical_aperture:=1.4
microscopy_objective_immersion:=oil
microscopy_instrument_manufacturer:=Zeiss
microscopy_instrument_model:=LSM 880
microscopy_pinhole_diameter_au:=1.0
microscopy_channel0_name:=DAPI
microscopy_channel0_fluorophore:=DAPI
microscopy_channel0_excitation_nm:=405
microscopy_channel0_emission_nm:=461
microscopy_channel0_contrast_method:=fluorescence
microscopy_channel0_color:=0.0 0.0 1.0
microscopy_channel1_name:=mCherry
microscopy_channel1_fluorophore:=mCherry
microscopy_channel1_excitation_nm:=561
microscopy_channel1_emission_nm:=610
microscopy_channel1_contrast_method:=fluorescence
microscopy_channel1_color:=1.0 0.0 0.0
microscopy_specimen_organism:=Mus musculus
microscopy_specimen_tissue:=brain
```

Nested fields use underscore-separated paths. Per-channel fields use a 0-based channel index. Array values (like `color` and `emission_range_nm`) are space-separated. The mapping between NRRD key/value pairs and JSON extension fields follows the same mechanical pattern as the FITS and NIfTI extensions' NRRD encodings.

---

## 11. Design Notes

**Why channel metadata is per-axis, not per-array.** The duckn convention bundles per-axis metadata on the axis it describes. Channel semantics describe what each position along the channel axis represents — this is structurally identical to diffusion gradients describing what each position along the diffusion axis represents. Putting channel metadata on the channel axis follows the established pattern and keeps the axis object self-contained. A reader that understands the extension can associate channel 0 with DAPI and channel 1 with GFP by inspecting a single axis object, without cross-referencing a separate metadata block.

**Why `modality` is a flat string, not a hierarchy.** Microscopy modalities don't form a clean tree. A spinning-disk confocal with TIRF illumination, or a two-photon system with SHG detection, doesn't fit neatly into a single branch. A flat modality string identifies the primary imaging mode. Secondary modes and hybrid configurations are captured by the combination of modality-specific fields present and per-channel `contrast_method` values. For example, a two-photon system that simultaneously collects SHG forward-scatter would use `"modality": "two-photon"` with one channel having `"contrast_method": "fluorescence"` and another having `"contrast_method": "second-harmonic"`.

**Why `color` is `[R, G, B]` in `[0, 1]`.** This matches the segmentation extension's `color` convention. Using floating-point `[0, 1]` rather than integer `[0, 255]` avoids bit-depth assumptions and is consistent with how colors are typically specified in scientific visualization. OME-NGFF uses hex strings (`"00FF00"`); the convention uses numeric arrays for consistency with its other color-related fields.

**Why `name` is required on channels.** Every channel needs a display label. Without one, viewers must generate labels from indices or wavelengths, which produces inconsistent and often confusing results. Requiring `name` costs almost nothing (the converter always has a label available) and eliminates a class of display problems.

**Why `exposure_ms` rather than a unit-agnostic exposure.** Milliseconds are the universal unit for exposure times in microscopy software. Using a bare number with an implicit unit is simpler than a structured unit object for a field that is never anything other than milliseconds. For exotic cases, the `source_metadata` object can carry the original representation.

**Why objective parameters are separate from channel parameters.** The objective is shared across all channels; per-channel settings like gain, laser power, and exposure vary per channel. Separating them avoids redundancy and matches the physical reality: you swap objectives, but you don't use different objectives for different channels in the same acquisition.

**Why no window/level or contrast limits.** The duckn convention's design philosophy excludes display hints. The `color` field per channel and the segmentation extension's `color` per segment are narrow exceptions — pseudocolor is so tightly bound to identity that it practically is metadata. Window/level settings, on the other hand, are user preference, not data description. OME-NGFF's `omero.channels[].window` handles this for viewers that read OME metadata; this extension deliberately leaves it to the viewer. When both metadata blocks are present in the same store, viewers can read the OME-NGFF window/level while analysis tools read the duckn acquisition context.

**Why specimen context is not a coded ontology.** Specimen metadata in microscopy is wildly varied — from "HeLa cells in a dish" to "iDISCO-cleared whole mouse brain" to "live zebrafish embryo" to "polished silicon wafer." No single ontology covers this range well, and requiring coded terms would create a barrier to adoption. Free-text fields with conventional values (like NCBI taxonomy binomials for `organism`) provide a pragmatic balance of structure and flexibility. A future version could add optional coded designations following the segmentation extension's pattern.

**Why spectral and FLIM bin axes use `"list"` kind.** Lambda-stack spectral bins and TCSPC time bins are not continuous domains that can be meaningfully interpolated. They are discrete measurements — the 420nm bin is not a "position" between the 410nm and 430nm bins the way a Z slice is a position between two other Z slices. The `"list"` kind correctly marks these axes as non-resamplable. The extension's per-axis metadata adds the physical meaning (wavelengths or time delays) that transforms an opaque list into interpretable spectral or temporal data.

**Multiplexed tissue imaging and the `cycle` field.** Highly multiplexed tissue imaging platforms (CyCIF, CODEX/PhenoCycler, MIBI, imaging mass cytometry) produce images with tens to hundreds of channels acquired across multiple imaging cycles or using non-optical detection (metal isotopes for IMC/MIBI). The per-channel `cycle` field records the acquisition round, which is essential metadata for understanding bleaching, registration quality, and staining batch effects. The `antibody_target` field captures the biological marker independent of detection chemistry, which is the primary axis of analysis in spatial proteomics workflows. For non-optical detection methods like MIBI or IMC, the `contrast_method` value `"metal-isotope"` identifies the detection modality; the `fluorophore` field can hold the metal-tagged antibody label (e.g., `"Ir-191"`, `"Nd-143"`).

**Applicability beyond biological microscopy.** Although the examples focus on biological fluorescence, this extension is equally applicable to materials science optical microscopy (metallography, semiconductor inspection, thin-film characterization), industrial quality control (surface defect detection, optical profilometry), and environmental microscopy (pollen analysis, microplastics). The `specimen.material` field supports non-biological samples, and the contrast methods include reflected light and polarization. The modality vocabulary covers transmitted-light techniques used across all these domains. The extension deliberately avoids assuming a biological context in its required fields.

**Relationship to the DICOM extension.** When microscopy data passes through a DICOM pathway (e.g., digital pathology stored as DICOM Whole Slide Image), both extensions may coexist. The DICOM extension carries DICOM-native metadata; the microscopy extension carries acquisition context that DICOM does not model. They occupy different keys in `"extensions"` and do not conflict.

**What about multi-resolution pyramids?** Pyramids are a storage concern (a Zarr group containing arrays at multiple resolutions), not an array-level metadata concern. This extension describes a single array. Each resolution level in a pyramid would carry the same extension metadata, with `space_direction` scaled appropriately for its resolution. The group-level organization of the pyramid is outside this extension's scope — it belongs to OME-NGFF's `multiscales`, or to a future duckn group-level convention.

**What about high-content screening?** Plate/well layouts, field-of-view indices, and screening metadata are a separate domain with its own complexity. They would be a separate extension (e.g., `hcs`) that could coexist with this one, just as `segmentation` and `dicom` coexist.

**Correlative light and electron microscopy (CLEM).** This extension covers the light microscopy component of a CLEM workflow. The electron microscopy data would use its own extension (or the convention's spatial fields alone for simple EM volumes). The convention's shared `space` and `space_origin` fields provide the common coordinate framework for registering the two modalities. Each array carries its own extension metadata appropriate to its acquisition method.