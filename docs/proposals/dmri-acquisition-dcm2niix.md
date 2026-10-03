# Diffusion MRI acquisition facts against dcm2niix: what duckn 2.0 can record, where, and how a converter gets them

**Status:** proposal, 2026-10-03. Nothing here is adopted.
**For:** duckn convention 2.0 (`duckn-2.0.md`, revision 17), dwmri 2.0 (`dwi-extension-2.0.md`), and
a BIDS extension that does not exist yet (§2).
**Asked for by:** tractline (the owner's diffusion MRI pipeline), at the owner's direction. tractline's
own proposal (`tractline/docs/duckn-proposal.md`, 2026-10-03: B0 field pairing, phase encoding from
1.x, shim, per-volume polarity, value sources) and its map of dcm2niix
(`tractline/docs/dcm2niix-dmri-map.md`) are the starting point; §6 takes up each of its six decisions.

**Source of the vendor rules.** dcm2niix, github.com/rordenlab/dcm2niix at commit `8282dbf`
(2026-07-24), "Copyright (c) 2014-2025 Chris Rorden", BSD 2-Clause license. dcm2niix is the most
complete public record of what scanner vendors put in diffusion DICOM and of the rules that turn it into
b-values, directions, phase encoding, readout and slice timing. This document describes its behavior and
cites its lines; it copies no code. Every rule below "documented from dcm2niix" is credited to it, and a
duckn converter that implements one should credit it the same way. Line numbers are at that commit:
**D** = `nii_dicom.cpp`, **B** = `nii_dicom_batch.cpp`, **M** = `main_console.cpp`. They were read from
the source, and several of the earlier map's lines and claims are corrected here (Appendix A).

Validation data: neurolabusc's `dcm_qa_ge`, `dcm_qa_polar` and `dcm_qa_trt` (GE only), already on this
machine; their references were made by dcm2niix v1.0.20260620 (§5).

---

## 1. What was found

dwmri 2.0 can hold the diffusion encoding a converter needs: per-volume b-values, gradients in a stated
`frame`, the volumes with no direction (§4 of revision 17). It cannot say **how** most acquisition values
were obtained, and dcm2niix's own sidecar cannot either: across vendors the same key holds a value read
from a header, one computed by a vendor formula, one synthesized from protocol rules, one inferred from
other data, or one estimated with an empirical constant - and only Philips's readout estimate is marked
(`Estimated…`). The difference matters exactly where tractline found it matters: pairing series for
distortion correction, and trusting slice timing.

The gaps, in brief (§3 has each with its source):

1. **How each value was obtained** - stated / derived / synthesized / inferred / estimated / assumed - for
   readout, echo spacing, slice timing, multiband factor, partial Fourier, phase-encoding polarity.
2. **Per-volume facts**: GE `epi_pepolar` alternates phase-encoding polarity within one series; GE
   recovers each volume's b-value from its vector's length.
3. **Facts with no field**: shim setting; partial Fourier and its direction; in-plane and out-of-plane
   acceleration beyond one number (technique, reference lines, compressed sensing); slice-timing
   provenance; diffusion gradient scheme (bipolar / monopolar); B0 field pairing.
4. **Derived maps that are not diffusion-weighted images** (ADC, FA, exponential ADC): dwmri 2.0's
   `directionality` covers trace volumes; ADC and FA are other quantities and need their own intent.
5. **Traps in dcm2niix's sidecar** for anyone converting from it: the phase-encoding sign depends on
   dcm2niix's own row flip; GE `epi_pepolar` reversed volumes are row-mirrored in the data without a
   change to the affine; Philips `ISOTROPIC` volumes get b = 2000 as a placeholder; a Siemens `ShimSetting`
   is omitted when its X offset is 0.

---

## 2. Where the facts live: a `bids` extension (decision 1)

### Why not `dwmri`

Phase encoding, readout, shim, slice timing, acceleration, partial Fourier and B0 field pairing describe
an **MR acquisition**, not a diffusion encoding. Field maps and functional series need every one of them.
tractline's proposal says as much (its §1, "Open: a home outside `dwmri`") and asks for the decision before
2.0 is released, since moving fields later is a major version. dwmri 1.0's `acquisition` object is already
a hand-copied subset of BIDS's sidecar in snake_case.

### Proposed: `bids` 0.1

A BIDS sidecar's keys, held under BIDS's own names and units. BIDS defines their meaning (the BIDS
specification's MRI sidecar fields), so duckn does not re-specify each one and does not drift from BIDS.
dcm2niix's sidecar is already this vocabulary.

| Field | Where | Meaning |
|---|---|---|
| `version` | top-level `extensions.bids` | `"0.1"` (a 0.x version reads only as itself, duckn 2.0 §2.3) |
| `bids_version` | top-level | the BIDS specification version the keys follow (e.g. `"1.10.0"`) |
| `keys` | top-level | the sidecar's key/value pairs, as BIDS writes them (seconds, Hz, and so on) |
| `axes` | top-level | which array dimension each BIDS voxel-axis letter names: `{ "i": 2, "j": 1, "k": 0 }`. Every letter-valued key (`PhaseEncodingDirection`, `PhaseEncodingAxis`, `SliceEncodingDirection`) resolves through it; `+` is along increasing index. Required when such a key is present. |
| `sources` | top-level | key -> how its value was obtained (§2.1); absent for a key: not stated |
| `samples[i].metadata.bids` | a dimension's samples | per-volume values of a key (§2.2) |

Rules:

- **Stated once** (duckn 2.0 §19 item 29). A key the core or another extension states is not repeated
  here: b-values and gradients are `dwmri`'s (BIDS's own `.bval`/`.bvec` are files, not keys), geometry is
  the core's, `SliceThickness` is `thickness`. dwmri 2.0's `acquisition` object moves here whole; dwmri
  keeps `b_value`, `b_values`, `gradients` / `b_matrices`, `frame`, `directionality`, `nex`. The draft's
  §7 `phase_encoding: {dimension, polarity}` becomes `keys.PhaseEncodingDirection` plus `axes`.
- **Keys BIDS does not define** are allowed (BIDS itself tolerates them in sidecars) and listed in
  `nonstandard` with their origin, e.g. `{ "PhaseEncodingPolarityGE": "dcm2niix" }`. A reader treats a
  nonstandard key as information, never as a BIDS-defined fact.
- **A description of the acquisition grid.** Like the `dicom` block (duckn 2.0 §2.3) it describes the array
  only while the array is the acquisition's grid; a derivative that resamples drops it. Phase encoding in a
  resampled array is not a fact about its rows.
- **B0 field pairing is BIDS's**: `B0FieldIdentifier` and `B0FieldSource` are BIDS keys (BIDS 1.7) and
  live in `keys`, with tractline's rules (its §1) as the extension's reader rules. dcm2niix never writes
  them (B 4843-4844: a post-pass does); they come from curation.

### 2.1 `sources`: how a value was obtained

| Value | Meaning | Example (dcm2niix) |
|---|---|---|
| `stated` | read from a header element, at most converted in unit | Siemens `MosaicRefAcqTimes` (D 1654-1666) |
| `derived` | computed exactly from stated values by a fixed formula | Siemens `EffectiveEchoSpacing` = 1 / (BWPPPE x ReconMatrixPE) (B 3213-3222) |
| `synthesized` | constructed from protocol rules, not measured times | GE slice timing from the protocol block (B 8587-8645) |
| `inferred` | guessed from a pattern in other data | multiband factor from repeated slice times (B 8503-8524) |
| `estimated` | computed with an empirical constant | Philips readout from the water-fat shift (B 3224-3244) |
| `assumed` | put in by a converter or a user, not from the source | a nominal readout time |

A non-`stated` value names its rule in the converter's provenance step (provenance 1.1 `parameters`), e.g.
`{ "rules": { "SliceTiming": "ge-protocol-block", "TotalReadoutTime": "ge-asset-rounding" },
"rule_source": "dcm2niix 8282dbf" }`. A reader that pairs series reports a pairing that rests on an
`assumed` or `inferred` value, as tractline's §3.3 asks.

### 2.2 Per-volume values

A key whose value changes along a dimension is stated per sample: `samples[i].metadata.bids` holds that
volume's value, and `keys` does not hold it. The one case dcm2niix shows is GE `epi_pepolar`
(`PhaseEncodingDirection` alternating, D 9588-9608). A converter keeps such a series whole (tractline §3.2):
its volumes share one shim, one readout and one frame by construction.

### Example

The diffusion series of tractline's example, written this way:

```json
"extensions": {
  "dwmri": { "version": "2.0", "b_value": 2800, "frame": [[1,0,0],[0,1,0],[0,0,1]] },
  "bids": { "version": "0.1", "bids_version": "1.10.0", "axes": { "i": 3, "j": 2, "k": 1 },
    "keys": { "PhaseEncodingDirection": "j-", "TotalReadoutTime": 0.0475,
              "EffectiveEchoSpacing": 0.000499, "MultibandAccelerationFactor": 3,
              "ShimSetting": [17, 43, -297], "B0FieldIdentifier": "pepolar1",
              "B0FieldSource": "pepolar1" },
    "nonstandard": { "ShimSetting": "dcm2niix" },
    "sources": { "TotalReadoutTime": "derived", "EffectiveEchoSpacing": "derived",
                 "ShimSetting": "stated", "PhaseEncodingDirection": "stated" } }
}
```

(Which keys BIDS's dictionary defines - `ShimSetting`, `DiffusionScheme`, `ParallelReductionFactorOutOfPlane`
and the others marked nonstandard or BIDS-defined in this document - was not checked here: there is no copy
of the BIDS schema on this machine, and the marks are provisional until checked against the BIDS version
named in `bids_version`.)

---

## 3. What duckn 2.0 cannot record today

Each row: the fact, where it comes from, whether 2.0 with this proposal has a place for it.

### 3.1 How a value was obtained

| Fact | Vendor and source | Obtained by | dcm2niix | Place |
|---|---|---|---|---|
| Effective echo spacing, total readout time | Siemens: 1 / (BWPPPE x ReconMatrixPE); BWPPPE from CSA, 0019,1028, XA 0021,1153 | derived | B 3188-3222, 3276-3289 | `keys` + `sources` |
| | GE: TRT = (ceil(lines / (R x k)) x k - 1) x ESP, k = 2, or 4 under partial Fourier; ESP 0043,102C (us), R from 0043,1083; then EES = TRT / (lines - 1) and the written TRT = EES x (ReconMatrixPE - 1). The variables are named `NotPhysical…`. Written only when R > 0. | derived (vendor rule) | B 3245-3256, 3276-3289 | `keys` + `sources` + rule |
| | Philips: echo spacing = WFS / (ImagingFrequency x 3.4 x (EPI factor + 1)), from 2001,1022, 0018,0084, 2001,1013 | estimated | B 3224-3244 | `keys.EstimatedTotalReadoutTime`… + `sources` |
| | UIH: TRT = AcquisitionDuration (0018,9073) / 1000 | stated (other element) | B 3472-3473 | `keys` + `sources` |
| | Canon, Hitachi, Bruker: none unless 0019,1028 is present | - | - | (absent) |
| Phase-encoding polarity | Siemens CSA `PhaseEncodingDirectionPositive`; XA 0021,111C (1 = positive); GE 0018,9034 (RX27+), else the 0043,102A binary block (offsets move at version 25.002); UIH 0065,1058 (1 = flipped) | stated (GE binary: decoded) | D 1669-1670, 7275-7283, 6705-6714, 8419-8509, 7377-7385 | `keys` + `sources` |
| | Philips, Canon, Hitachi, Bruker: axis only (`PhaseEncodingAxis`) | - | B 3514-3522 | `keys.PhaseEncodingAxis` |
| Slice timing | Siemens mosaic CSA `MosaicRefAcqTimes`; de-identified 0019,1029; XA 0021,1104 (from the second volume) | stated | D 1654-1666, 6854-6873, 7220-7232; B 8559-8575 | `keys.SliceTiming` + `sources` |
| | Siemens 2D, UIH: differences of AcquisitionTime (0008,0032) across the first volume's files | derived | B 9708-9732, 9791-9803 | + `sources` |
| | GE: from the protocol block (0025,101B: `SLICEORDER`, `MBACCEL`, `NOSLC`, …) and the software version (rules switch at 27.0 R03; none for multiband below 26.0; diffusion assumed interleaved) | synthesized | B 9551-9685, 8587-8645 | + `sources` + rule |
| | Siemens "desperate" fallback from ASCCONV `sSliceArray.ucMode`, `lDelayTimeInTR` | synthesized | B 9737-9785 | + `sources` |
| | First volume replaced by the second's when their ranges differ (CMRR bug) | substituted (stated values of another volume) | B 8495-8502, 9906-9908 | `sources: "derived"` + rule |
| | Philips, Canon, Hitachi, Bruker | none | B 8339-8340 | (absent) |
| Multiband factor | Siemens 0021,1009 / 0051,1011 text; GE 0043,10B6, protocol `MBACCEL` | stated | D 7193-7208, 7668-7682, 8602-8609 | `keys` + `sources` |
| | From repeated slice times (Siemens; overrides the header in one path) | inferred | D 1530-1555; B 8503-8524, 9724-9730 | + `sources` |
| Partial Fourier fraction | Siemens ASCCONV `sKSpace.ucPhasePartialFourier` (1/2/4/8 -> 0.5/0.625/0.75/0.875) | stated | B 2974-2986 | `keys.PartialFourier` + `sources` |
| | Siemens XA without CSA: ETL / (lines / R), "not sure if we round up or down" - **same key** | inferred | B 3080-3091 | + `sources` (the case that most needs it) |
| In-plane acceleration | 0018,9069; Siemens 0051,1011 / 0021,1009 text and ASCCONV `sPat.lAccelFactPE`; GE 1 / 0043,1083[0]; UIH 0065,100D text; Philips CSENSE moves R to `CompressedSensingFactor` | stated / reinterpreted | D 6733-6738, 7668-7682, 8544-8554, 7361-7369; B 3059-3070, 3182-3187 | `keys` + `sources` |

### 3.2 Per-volume

| Fact | Source | dcm2niix | Place |
|---|---|---|---|
| Phase-encoding polarity alternating within a series (GE `epi_pepolar`, 0019,109C; mode in 0019,10B3: 1 all reversed, 2 rev-fwd, 3 fwd-rev; parity of 0020,0100) | stated (mode) + rule | D 6964-6976, 9588-9608 | `samples[i].metadata.bids.PhaseEncodingDirection` (§2.2) |
| b-value recovered from the vector's length: when 0.03 < \|g\| < 0.97, b' = b x \|g\|^2 rounded to a multiple of 5 (minimum 5), vector rescaled (GE, and Canon classic, which dcm2niix routes through the GE path) | derived per volume | B 375-397 | `dwmri.b_values` (exists) + `bids.sources` cannot name a dwmri field: a provenance rule names it |

### 3.3 Facts with no field

| Fact | Source | dcm2niix | Place |
|---|---|---|---|
| Shim setting: Siemens 8 values (ASCCONV `sGRADSPEC.asGPAData[0].lOffsetX/Y/Z`, else `sGRADSPEC.lOffsetX/Y/Z`, then `sGRADSPEC.alShimCurrent[0..4]`); GE 3 linear (0043,1002-1004) | stated | B 1005-1031, 2993-3001; D 8394-8408 | `keys.ShimSetting` (nonstandard: dcm2niix) |
| Partial Fourier direction (0018,9036) | stated | D 6716-6728 | `keys.PartialFourierDirection` |
| Partial Fourier present, no fraction (0018,9081; `PFF` in 0018,0022 on GE; Philips 2001,1019) | stated | D 6805-6810, 7817-7821, 8089-8096 | `keys.ScanOptions` holds `PFF`; a boolean has no BIDS key - `nonstandard` |
| Parallel technique, out-of-plane R, reference lines, compressed sensing | 0018,9078; 0018,9155 / ASCCONV `sPat.lAccelFact3D`; `sPat.lRefLinesPE`; GE 0043,10B7, Siemens `sPat.dTotalAccelFact` | D 6796-6798, 7992-7996, 8611-8618; B 3046-3075 | `keys` (whether BIDS defines each: to be checked, see §2's example note) |
| Diffusion gradient scheme, bipolar / monopolar (affects eddy currents) | Siemens ASCCONV `sDiffusion.dsScheme`, else `ucReadOutMode` | B 853-860, 3002-3007 | `keys.DiffusionScheme` (nonstandard) - or `dwmri`, being diffusion's (decision 6) |
| Free-waveform diffusion parameters | Siemens `ep2d_diff_fwf` ASCCONV `sWipMemBlock.*`, `sDiffusion.sFreeDiffusionData.*` | B 1033-1052, 2948-2971 | `keys` (nonstandard), or out of scope |
| GE gradient cycling mode, number of directions and T2 volumes | 0019,10B3 / 10B6 / model name; 0019,10E0, 0019,10DF | D 9547-9580 | `keys` (nonstandard); cycling decides whether slice timing exists |
| Gradient pulse duration and separation (delta, Delta) | **not read by dcm2niix** | - | `dwmri` 1.0 has the fields; a converter needs another source |
| B0 field pairing | not in DICOM; curation | B 4843-4844 | `keys.B0FieldIdentifier` / `B0FieldSource` |

### 3.4 Derived maps

dcm2niix marks a series derived from ImageType `ADC`, `TRACEW`, `TRACE`, `FA` (bracketed tokens) or
`DERIVED` (D 6210-6235, 6283-6284). Within a diffusion series it removes b > 0 zero-vector volumes for
Philips (always) and GE (when not DTI), moves them to the end, drops them from bval/bvec, and saves a full
`_ADC` copy (B 3946-4054, 12216-12229); with `-i y` it skips derived series entirely (M 405-414). Philips
`ISOTROPIC` frames get **b = 2000.0** as a placeholder (D 5767-5773).

- A trace volume inside a diffusion series is dwmri 2.0 §4's `null` + `directionality: "isotropic"`, with
  the volume's real b-value - never dcm2niix's 2000 placeholder. A duckn converter keeps it.
- An **ADC or FA map** is not a diffusion-weighted image: its values are a diffusivity (mm²/s) or a
  fraction. Proposed: such an array is not a `dwmri` array; it states its quantity in `values.unit` and an
  `intent` (`"apparent-diffusion-coefficient"`, `"fractional-anisotropy"`, `"exponential-adc"`), with the
  series it came from in provenance (decision 5). Exponential ADC is not detected by dcm2niix by name.

### 3.5 Traps in dcm2niix's sidecar

A converter that reads a dcm2niix sidecar instead of DICOM (tractline's case) must know:

- **The `j` sign depends on dcm2niix's row flip** (`-y`, default on, M 545-556): COL positive is `j-` with
  the flip and `j` without (B 3534-3543). The sidecar tests the option, not what happened; a 3D series
  reoriented by `nii_setOrtho` is not tracked (B 3560, "ignores if 3D image re-oriented"). Resolve the
  letter against the NIfTI dcm2niix wrote, not against the DICOM.
- **GE `epi_pepolar` reversed volumes are row-mirrored in the data** (`nii_flipImgY`, B 12160-12161) with
  the affine unchanged, and their sidecar polarity is inverted to match. The alternating modes split the
  reversed volumes into series number + 1000 (D 9601-9602). A duckn converter from DICOM does neither: it
  keeps the rows as acquired and states the per-volume polarity (§2.2).
- **Philips `ISOTROPIC` b = 2000** is a placeholder (above).
- **A Siemens `ShimSetting` with X offset 0 is omitted entirely** (B 2993-3001); a missing key is not "no
  shim". GE values are integers, with -33333 meaning not present (D 892-894). In `dcm_qa_ge` and
  `dcm_qa_trt` every GE series reports `[0, 0, 0]`; in `dcm_qa_polar` they are nonzero and differ between
  series (`[17, 43, -297]`, `[22, 49, -299]`, `[23, 52, -310]`) - so zeros may mean "not recorded" on some
  software, and a pairing rule should not treat equal zeros as evidence of one shim.
- **`ParallelReductionFactorInPlane`** differs between dcm2niix versions: v1.0.20260620 writes `1` where
  v1.0.20260416 writes nothing (observed on all three `dcm_qa*` sets).
- **`TotalReadoutTime` is BIDS's (topup's) effective readout, not the physical echo train** (dcm_qa_trt's
  README; B 3276-3289, "Really should be called EffectiveReadOutTime").

---

## 4. Converter guidance per vendor (dwmri 2.0 §5 made complete)

What a duckn DICOM -> dwmri converter reads, by vendor. "Frame" is the frame of the vector as the source
states it; the converter writes gradients in the world (`frame` identity in an LPS world) after the stated
rule, and records the rule and its version (here, dcm2niix 8282dbf's) in provenance.

### 4.1 b-values and directions

| Vendor | b-value | Direction | Frame | Rule (documented from dcm2niix) |
|---|---|---|---|---|
| Standard / Enhanced | 0018,9087 (per frame; GE 27+ omits it at b = 0) | 0018,9089; 0018,9075 decides: anything but `DIRECTIONAL` / `BMATRIX` has no direction | patient (LPS) | values from the frame at the first slice position (D 4743-4744); a component > 1.5 drops the volume's record silently (D 4751-4759) - a converter reports it instead |
| Siemens | CSA `B_value` (negative: clamped to 0, D 1627-1630); fallback 0019,100C | CSA `DiffusionGradientDirection`; else 0019,100E | patient | all three components < -1.0 means b = 0 (Syngo 2004A, D 9112-9114); CSA `B_matrix` and 0019,1027 are not read |
| GE | 0043,1039 first value mod 1,000,000,000 (D 4663-4673) | 0019,10BB / 10BC / 10BD | **image** (the tag names say phase, frequency, slice; B 312-315) | `geCorrectBvecs` (B 309-424): per-plane flip mask, negate the second component, ROW swaps and negates, all negated; b from \|g\| (§3.2). Not corrected: non-HFS (B 331), non-axial (B 341), ROW "untested" (B 365). If 0018,9089 is present the GE rule is skipped (B 322) |
| Philips | 2001,1003 | 2005,10B0 / 10B1 / 10B2 (RL / AP / FH) | patient (dcm2niix treats the components as LPS-signed, B 431, 456-474) | classic DTI: one series per b-value number by a series-number kludge (D 9436-9443) |
| Canon / Toshiba | 0018,9087 | classic: parsed from ImageComments `b=<b>(x,y,z)` (D 9506-9521), swapped to (y, x, z); enhanced: 0018,9089 | classic **image**, enhanced patient | classic goes through the GE rule, including its b scaling (B 320) |
| UIH | 0065,1009 | 0065,1037 (0018,9089 ignored) | image, by treatment: y negated, no rotation (B 435-446) | - |
| Bruker | 0018,9087 | 0018,9089 | patient | b-matrix: only XX read (D 8026-8031); "experimental" (B 491) |
| Hitachi, Mediso | standard elements | standard | patient | - |

**From dcm2niix's `.bvec` to the world.** dcm2niix writes vectors in its NIfTI voxel axes after its flips:
i = row direction r, j = -(column direction p) under the default row flip, k = slice normal n, a negative
determinant, FSL's convention (B 456-476, 4079-4091, 12093-12157). So a world (LPS) gradient is
g = b1 r - b2 p + b3 n. For GE axial COL the net rule gives (-x, y, z) in those axes, so in the patient
frame g = -x r - y p + z n (from 0019,10BB/BC/BD = x, y, z); axial ROW gives (-y, -x, z). These
compositions are this document's derivation from dcm2niix's code, not dcm2niix's statement. **Checked for
axial COL** (2026-10-03) on all four `dcm_qa_ge` DWI series: (-x, y, z) read from the DICOM equals
`Ref/*.bvec` exactly, unnormalized (GE vectors are not normalized: lengths 0.9995-1.0004), every b-value
equals `Ref/*.bval`, and the NIfTI dcm2niix writes has voxel axes i = r, j = -p, k = n (determinant -1),
which makes the patient-frame form above exact. ROW, sagittal, coronal and non-HFS are not checked (no
data here; dcm2niix marks them untested).

### 4.2 Phase encoding, readout, acceleration, shim

| Fact | Siemens | GE | Philips | Canon | UIH |
|---|---|---|---|---|---|
| PE axis | 0018,1312 | 0018,1312 | 0018,1312 | 0018,1312 | 0018,1312 |
| PE polarity | CSA `PhaseEncodingDirectionPositive`; XA 0021,111C (wins) | 0018,9034 (`L…` flipped, `R…` unflipped; RX27+), else 0043,102A | - | - | 0065,1058 (1 = flipped) |
| Readout | derived (BWPPPE) | derived (ESP, ASSET R, partial-Fourier rounding) | estimated (WFS) | - | stated (0018,9073) |
| In-plane R | ASCCONV `sPat.lAccelFactPE` > 0051,1011 / 0021,1009 text | 1 / 0043,1083[0] | 0018,9069; CSENSE moved | 0018,9069 | 0065,100D |
| Partial Fourier | ASCCONV fraction (stated); XA inferred | `PFF` flag only | 2001,1019 flag | 0018,9081 | - |
| Shim | ASCCONV, 8 values | 0043,1002-1004, 3 values | - | - | - |

### 4.3 Slice timing and series assembly

Slice timing as in §3.1, with its `sources` value and rule. Zero is the earliest slice for every stated and
derived source; GE synthesis takes slice index 0 in dcm2niix's sort order as excited first, and the GE
path itself notes "unclear what happens if slice order is flipped" (B 9595). Times follow the data when
the slice order is flipped (B 9929-9937, 9687-9706); a duckn converter states times in its own slice
order (`slice_dimension`, index order).

To reproduce dcm2niix's volume order and count a converter: groups by Series Instance UID; sorts by
(series, instance, DimensionIndexValues, AcquisitionTime); drops exact duplicates; derives the volume count
from files at the first slice position; re-sorts enhanced Philips / Canon frames (with dcm2niix's
"Guessing temporal order" and Canon kludges, D 9298-9333); and applies the vendor series edits (GE
pepolar + 1000, Philips classic b-value x 1000). A duckn converter should **not** reproduce the edits that
exist only because NIfTI cannot hold them: it keeps an `epi_pepolar` series whole, and a Philips classic
DTI series as one array (B 13910-13966, 14026-14203, 11535-11610).

---

## 5. Test plan: a duckn converter against dcm2niix on the `dcm_qa*` sets

**Data (on this machine, GE only):** `dcm_qa_ge` (4 DWI series with reference `.bval`/`.bvec`, 24 fMRI
series for slice timing, one T1), `dcm_qa_polar` (4 `epi_pepolar` series in modes 0-3, written by dcm2niix
as 6 because two are split), `dcm_qa_trt` (13 series for `TotalReadoutTime` across acceleration, multiband
and interpolation). Each repository's `Ref/` holds
dcm2niix's output.

**Reference version.** The `Ref/` files were made by dcm2niix v1.0.20260620; the installed binary is
v1.0.20260416; the source read here is 8282dbf. Running the installed binary as the repositories' own
`batch.sh` does (`-b y -z n`) reproduced every `.bval` and `.bvec`, and every key in the table below but
one, on all three sets: v1.0.20260620 writes `ParallelReductionFactorInPlane: 1` where v1.0.20260416 writes
nothing. Otherwise the versions differ only in keys outside this table (`PulseSequenceType`,
`PulseSequenceName` renamed `SequenceName`, `ParallelReductionFactorOutOfPlane`,
`InstitutionalDepartmentName`, `BidsGuess`).
The test compares against `Ref/` and names the version it was made by; a key that differs between dcm2niix
versions is compared only when the reference version writes it.

**Comparisons, per series:**

| duckn field | dcm2niix | Tolerance |
|---|---|---|
| `dwmri.b_values` | `.bval` | exact |
| `dwmri.gradients` (world) projected on dcm2niix's voxel axes (i = r, j = -p, k = n) | `.bvec` | 1e-5 per component |
| `bids.keys.PhaseEncodingDirection` resolved through `bids.axes` to a dimension and sign, then expressed in dcm2niix's voxel axes | sidecar `PhaseEncodingDirection` | exact |
| `bids.keys.TotalReadoutTime`, `EffectiveEchoSpacing` | sidecar | 1e-6 s; `sources` = `derived` for GE |
| `bids.keys.SliceTiming` | sidecar | 1e-6 s; `sources` = `synthesized` for GE |
| `MultibandAccelerationFactor`, `ParallelReductionFactorInPlane` | sidecar | exact |
| `ShimSetting` | sidecar | exact |

**`dcm_qa_polar`:** duckn keeps series 6 (mode 2, rev-fwd) as one array; its odd volumes state the
polarity dcm2niix's series 1006 states and its even volumes series 6's; its odd volumes' data equal series
1006's rows mirrored (dcm2niix mirrors them, duckn does not); the shim is one value for the whole array,
equal to both sidecars'. Series 7 (mode 1) states the reversed polarity for every volume.

**Expected failures, kept as findings:** the GE ROW, non-axial and non-HFS branches have no data here
(dcm2niix marks them untested); no Siemens, Philips, Canon or UIH DWI is on this machine. neurolabusc
publishes further `dcm_qa*` repositories for other vendors; which ones, and their sizes, are to be
checked and put to the owner before anything is downloaded.

---

## 6. Decisions asked

1. **A `bids` extension (0.1)** holds the MR acquisition facts under BIDS's names, with `axes`, `sources`,
   per-volume values and `nonstandard` (§2); dwmri 2.0's `acquisition` object moves into it, and dwmri keeps
   the diffusion encoding only. (Alternative: extend dwmri's `acquisition` with tractline's fields.)
2. **`sources`** with the six values of §2.1, and the rule named in provenance for every non-`stated` value.
3. **tractline's proposals, placed:** `b0_field_identifier` / `b0_field_source` -> `keys.B0FieldIdentifier`
   / `B0FieldSource` with tractline's §1 rules (its decision 1); the "home outside dwmri" -> this extension
   (its decision 2); a 1.x `phase_encoding_direction` carried when the 1.x array's `dimension_names` name
   `i`, `j`, `k` -> it becomes `keys.PhaseEncodingDirection` with `axes` read from those names (its decision
   3); `shim_setting` -> `keys.ShimSetting`, with the pairing rule, and the §3.5 caveats about zeros and
   omission (its decision 4); per-volume polarity -> §2.2 (its decision 5); value sources -> §2.1 (its
   decision 6).
4. **Converter guidance (§4)** goes into dwmri 2.0 §5 and the `bids` extension's DICOM section, credited
   to dcm2niix 8282dbf.
5. **ADC, FA and exponential ADC maps** are arrays of their own intent, not `dwmri` arrays (§3.4).
6. **`DiffusionScheme`** (bipolar / monopolar) lives in `dwmri` (a diffusion fact) or in `bids` as a
   nonstandard key.
7. **The test plan (§5)** is adopted for duckn's DICOM -> dwmri converter, which does not exist yet.

---

## Appendix A. Corrections to the earlier map (`tractline/docs/dcm2niix-dmri-map.md`)

- Siemens b = 0: all three components < -1.0 (D 9112-9114); the string "-1.0001" does not occur.
- GE b from vector length is per volume (0.03 < \|g\| < 0.97), not against the series' maximum b.
- `geCorrectBvecs` is B 309-424; its header comment (B 315-319) describes a COL/ROW rule the code does not
  follow (the code is the "issue 970" recipe, B 344-348).
- `.mvec` and the b-matrix polarity warning are unreachable: `isVectorFromBMatrix` is never set true (D 963,
  9687). No vector is ever derived from a b-matrix (D 4772-4818 is commented out).
- Bruker directions come from 0018,9089; only the XX b-matrix element is read.
- Canon classic vectors are swapped (y, x, z) in the default build and then go through the GE rule.
- The b > 0 zero-vector sort at D 9655 applies to every vendor; removal is GE and Philips only (B 3956-3970).
- UIH vectors are image-frame by treatment (B 435-446); UIH's 0018,9089 is ignored.
- The GE protocol block is key/value text after inflation, not XML (B 1097, 1150-1170; XML only warned
  about, B 1148-1149).
- GE diffusion gets slice timing when gradient cycling is off (B 9650-9651); only All-TR, 2TR, 3TR and
  unknown cycling get none.
- The multiband override of the header happens only in `checkSliceTiming`'s second-volume path
  (B 8503-8524); elsewhere inference fills a missing value or takes the maximum.
- The Siemens VB12/13 `_ep_b` cross-series merge is unreachable in the default build (`myBubbleSort`
  commented out, B 14572).
- Philips enhanced slice timing is dead code (D 5651-5655 with D 6300-6328 commented out); FrameAcquisition-
  DateTime (0018,9074) is not read (D 6745-6754).
- The `epi_pepolar` + 1000 split applies to the alternating modes only; mode 1 stays one series, all
  reversed. Reversed volumes are also row-mirrored in the data.
- A Siemens `ShimSetting` is suppressed when its X offset is 0; Siemens multiband from ASCCONV
  `sSliceAcceleration.lMultiBandFactor` is deliberately not read ("not reliable", B 868-871).
