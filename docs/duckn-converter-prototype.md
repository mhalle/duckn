duckn Round-Trip Converter: Specification and Action Plan
Status: Draft
Date: 2026-03-02
Scope: Lossless round-trip conversion between .nrrd files and .duckn Zarr V3 stores

1. Goals
Convert a complete (non-detached) .nrrd file into a .duckn directory containing a single Zarr V3 array, and convert it back, such that the round-tripped .nrrd file is value-identical to the original. The data should pass through without decompression or recompression when possible.
In scope

Single .nrrd files (header + data in one file, not .nhdr + detached data).
Binary encodings: raw, gzip/gz.
Little-endian data only.
All standard NRRD header fields supported by pynrrd.
Key/value pairs (key:=value syntax).
pynrrd-compatible read() / read_header() functions for .duckn stores.

Out of scope (first pass)

Detached headers (.nhdr + separate data file).
ascii, hex, text encodings.
Big-endian source data (reject with a clear error).
bzip2 encoding (defer — requires non-standard Zarr codec via numcodecs).
duckn extensions (DICOM, slicerseg, DWMRI, etc.).
Multi-array Zarr groups / multi-resolution pyramids.
Writing .duckn from scratch (only conversion from existing .nrrd).


2. File Layout
2.1 The .duckn directory
A .duckn store is a standard Zarr V3 directory store containing a single array at the root level.
example.duckn/
  zarr.json          # Zarr V3 array metadata + duckn convention attributes
  c/0/0/0            # Single chunk file (for a 3D array; path depends on dimensionality)
The directory name uses the .duckn extension by convention to distinguish it from generic Zarr stores. Any Zarr V3 reader can open it.
2.2 Chunk layout
The array uses a single chunk spanning the full array shape. The chunk grid configuration is:
json{
  "name": "regular",
  "configuration": {
    "chunk_shape": [128, 128, 60]
  }
}
where chunk_shape equals shape.
2.3 Chunk key encoding
Default Zarr V3 chunk key encoding with / separator:
json{
  "name": "default",
  "configuration": {
    "separator": "/"
  }
}
For a 3D array, the single chunk key is c/0/0/0.

3. Codec Pipeline and Zero-Copy Rationale
The Zarr codec pipeline is chosen to match the NRRD encoding, enabling zero-copy data transfer. Both pynrrd and the Zarr V3 gzip codec use RFC 1952 gzip streams — verified by examining pynrrd's source (zlib.decompressobj(zlib.MAX_WBITS | 16) expects RFC 1952) and the Zarr gzip codec spec (mandates RFC 1952). This format-level identity is what makes the zero-copy path possible.
3.1 NRRD raw encoding
json"codecs": [
  {
    "name": "bytes",
    "configuration": { "endian": "little" }
  }
]
The chunk file contains raw little-endian bytes, identical to the data portion of the NRRD file.
3.2 NRRD gzip / gz encoding
json"codecs": [
  {
    "name": "bytes",
    "configuration": { "endian": "little" }
  },
  {
    "name": "gzip",
    "configuration": { "level": 5 }
  }
]
Zero-copy path: When converting NRRD to duckn, the gzip-compressed data blob is copied byte-for-byte from the NRRD file into the Zarr chunk file — no decompression or recompression occurs. The reverse direction works the same way. The level field in the codec configuration records the default gzip level but does not affect the stored data.
3.3 Rejected encodings
EncodingDispositionascii, text, txtReject with error: "ASCII encoding not supported"hexReject with error: "Hex encoding not supported"bzip2, bz2Reject with error: "bzip2 not yet supported" (future pass)
3.4 Endianness
Only little-endian data is supported. If the NRRD header specifies endian: big, the converter rejects the file with an error. Single-byte types (int8, uint8) have no endianness and are always accepted.

4. Metadata Mapping
4.1 Zarr-level fields (derived from NRRD header)
Zarr V3 fieldSourceshapeheader['sizes'] — see §4.5 for axis orderingdata_typeMapped from header['type'] via NRRD-to-Zarr type tabledimension_namesheader['labels'] if present, else nullfill_value0 (or type-appropriate default)chunk_gridSingle chunk, chunk_shape = shapecodecsDerived from header['encoding'] per §3
4.2 duckn convention fields (attributes.nrrd)
Mapped from the pynrrd header dict into the structured convention format.
Convention fieldSource in pynrrd headerversionAlways "1.0"spaceheader['space']space_originheader['space origin'] as JSON array of numbersmeasurement_frameheader['measurement frame'] as nested JSON arrayssample_unitsheader['sample units']axes[i].kindheader['kinds'][i]axes[i].centeringheader['centerings'][i]axes[i].space_directionRow i of header['space directions'], omitted if NaN rowaxes[i].unitheader['units'][i] if presentaxes[i].thicknessheader['thicknesses'][i] if present and not NaN
Spacings: The spacings field in NRRD is redundant when space directions is present (spacing = magnitude of the direction vector). It is not stored separately in the convention. When space directions is absent but spacings is present, the converter constructs axis-aligned direction vectors from the spacings.
4.3 The legacy object (attributes.nrrd.legacy)
Stores fields needed for lossless round-trip back to .nrrd that have no semantic home in the convention.
json"legacy": {
  "nrrd_type": "short",
  "encoding": "gzip",
  "space_units": ["mm", "mm", "mm"],
  "content": "MRI volume",
  "old_min": 0.0,
  "old_max": 255.0,
  "keyvalues": {
    "DWMRI_b-value": "1000",
    "patient_id": "anon_001"
  }
}
```

#### Fields in `legacy`

| Field | Type | Description |
|---|---|---|
| `nrrd_type` | string | Original NRRD type string (e.g., `"short"`, `"float"`, `"unsigned char"`). Needed because NRRD has many aliases for the same numeric type. |
| `encoding` | string | Original NRRD encoding string (`"raw"`, `"gzip"`, `"gz"`). Used to select encoding when writing back. |
| `space_units` | array of string | `header['space units']` — per-world-axis units, distinct from per-array-axis units. |
| `content` | string | `header['content']` if present. |
| `old_min` | number | `header['old min']` if present. |
| `old_max` | number | `header['old max']` if present. |
| `min` | number | `header['min']` if present. |
| `max` | number | `header['max']` if present. |
| `keyvalues` | object | All `key:=value` pairs from the NRRD file, as a flat JSON object with string keys and string values. |

Fields are omitted if not present in the source NRRD header (following the "absent means unknown" principle).

### 4.4 NRRD type mapping

Bidirectional mapping between NRRD type strings and Zarr `data_type`:

| NRRD type strings (selected aliases) | Zarr `data_type` |
|---|---|
| `signed char`, `int8`, `int8_t` | `int8` |
| `uchar`, `unsigned char`, `uint8`, `uint8_t` | `uint8` |
| `short`, `int16`, `int16_t` | `int16` |
| `ushort`, `unsigned short`, `uint16`, `uint16_t` | `uint16` |
| `int`, `signed int`, `int32`, `int32_t` | `int32` |
| `uint`, `unsigned int`, `uint32`, `uint32_t` | `uint32` |
| `longlong`, `long long`, `int64`, `int64_t` | `int64` |
| `ulonglong`, `unsigned long long`, `uint64`, `uint64_t` | `uint64` |
| `float` | `float32` |
| `double` | `float64` |
| `block` | **Reject** — not supported |

The original type string is preserved in `legacy.nrrd_type` so it can be written back identically.

### 4.5 Axis ordering

**NRRD convention:** Axes are listed fastest-varying to slowest-varying (Fortran order).

**Zarr convention:** `shape[i]` describes dimension `i`; no memory-layout semantics are assigned to dimension order. Zarr's default `bytes` codec uses C order (last dimension varies fastest).

**Conversion rule:** The `.duckn` store stores axes in the NRRD order (fastest-first). This means `shape` in `zarr.json` matches `header['sizes']` directly, and `axes[i]` describes the same axis as NRRD's axis `i`. The single chunk file contains bytes in NRRD's native memory order, which is what enables zero-copy.

A Zarr reader using C-order interpretation will see the axes in the opposite order from what a Fortran-order reader expects. The convention's `axes` array provides the semantic meaning regardless of ordering convention.

**When reading back** via the pynrrd-compatible `read()` function, the `index_order` parameter controls the numpy array layout, just as in pynrrd.

---

## 5. Conversion Procedures

### 5.1 NRRD → duckn
```
Input:  path/to/file.nrrd
Output: path/to/file.duckn/   (directory)
```

**Steps:**

1. **Parse NRRD header** using pynrrd's `read_header()`.
2. **Validate** encoding, endianness, and that the file is not detached.
3. **Compute data offset** — the byte position where data begins in the `.nrrd` file (after the header + blank line separator). Apply `line skip` and `byte skip` if present.
4. **Create the `.duckn` directory structure** including chunk subdirectories.
5. **Copy the data blob** — read the data portion of the `.nrrd` file and write it directly as the chunk file. No decompression or recompression.
6. **Build and write `zarr.json`** — construct Zarr V3 metadata from the parsed header, map fields per §4, write to store root.

### 5.2 duckn → NRRD
```
Input:  path/to/file.duckn/
Output: path/to/file.nrrd
Steps:

Read zarr.json from the store root.
Extract convention attributes from attributes.nrrd.
Reconstruct NRRD header from convention fields + legacy stash.
Write NRRD header to output file — magic line NRRD0005, standard fields, key/value pairs from legacy.keyvalues, blank line separator.
Append data blob — read the single chunk file, append directly after the header. No decompression or recompression.

5.3 Round-trip fidelity
AspectIdentical?NotesArray valuesYesByte-for-byte in the data blobdtype / type stringYesVia legacy.nrrd_typeShape / sizesYesSpace, space originYesSpace directionsYesKinds, centerings, unitsYesMeasurement frameYesKey/value pairsYesVia legacy.keyvaluesEncodingYesVia legacy.encodingCompressed byte streamYesZero-copy preserves the exact streamHeader field orderingNoWe don't preserve insertion orderline skip / byte skipNoZeroed on outputHeader commentsNoNot preservedNRRD magic versionNoAlways writes NRRD0005

6. pynrrd-Compatible Read API
6.1 Functions
pythondef read(zarr_path: str, index_order: str = 'C') -> Tuple[np.ndarray, OrderedDict]:
    """Read a .duckn store, returning (data, header) like pynrrd.read().
    
    Default is C-order (unlike pynrrd's F-order default), since this is
    new code targeting modern Python conventions.
    """

def read_header(zarr_path: str) -> OrderedDict:
    """Read header from a .duckn store without loading data."""
6.2 Header reconstruction
The pynrrd-compatible header dict is reconstructed from zarr.json:
pynrrd header keySource'type'legacy.nrrd_type, or reverse-mapped from Zarr data_type'dimension'len(shape)'sizes'np.array(shape)'encoding'legacy.encoding, or inferred from codec pipeline'endian''little' (always)'space'nrrd.space'space origin'np.array(nrrd.space_origin)'space directions'Matrix from axes[i].space_direction; NaN rows for non-spatial axes'kinds'[axes[i].kind for ...]'centerings'[axes[i].centering for ...]'units'[axes[i].unit for ...] if any present'thicknesses'[axes[i].thickness for ...] if any present'measurement frame'np.array(nrrd.measurement_frame)'sample units'nrrd.sample_units'space units'legacy.space_units'labels'dimension_names from Zarr metadata'content'legacy.content'old min'legacy.old_min'old max'legacy.old_max
Keys are omitted from the dict if the source field is absent.

7. Example zarr.json
A complete example for a 3D MRI volume originally stored as brain.nrrd with gzip encoding:
json{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [256, 256, 128],
  "data_type": "int16",
  "dimension_names": null,
  "chunk_grid": {
    "name": "regular",
    "configuration": {
      "chunk_shape": [256, 256, 128]
    }
  },
  "chunk_key_encoding": {
    "name": "default",
    "configuration": {
      "separator": "/"
    }
  },
  "fill_value": 0,
  "codecs": [
    {
      "name": "bytes",
      "configuration": { "endian": "little" }
    },
    {
      "name": "gzip",
      "configuration": { "level": 5 }
    }
  ],
  "attributes": {
    "nrrd": {
      "version": "1.0",
      "space": "left-posterior-superior",
      "space_origin": [-119.53125, -159.609375, -71.7],
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.9375, 0.0, 0.0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.0, 0.9375, 0.0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.0, 0.0, 1.2],
          "unit": "mm"
        }
      ],
      "legacy": {
        "nrrd_type": "short",
        "encoding": "gzip"
      }
    }
  }
}

8. Implementation Plan
Phase 1: Core converter (this pass)
Deliverables: A Python package with four public functions:

nrrd_to_duckn(nrrd_path, duckn_path=None) -> str — Convert NRRD file to duckn store. Returns output path.
duckn_to_nrrd(duckn_path, nrrd_path=None) -> str — Convert duckn store to NRRD file. Returns output path.
read(duckn_path, index_order='C') -> (np.ndarray, OrderedDict) — pynrrd-compatible read.
read_header(duckn_path) -> OrderedDict — Header-only read.

Dependencies: pynrrd, zarr (>= 3.0), numpy.
Test matrix:
Test caseValidatesRound-trip raw-encoded scalar volumeBasic pipeline, raw codecRound-trip gzip-encoded scalar volumegzip zero-copy pathRound-trip with various type strings (short, float, unsigned char)legacy.nrrd_type preservationRound-trip with key/value pairslegacy.keyvaluesRound-trip with space directions + non-spatial axis (NaN rows)Axis reconstructionRound-trip with measurement frameMatrix field handlingRound-trip with all optional fields populatedCompletenessread() matches pynrrd.read() output on original fileAPI compatibilityReject big-endian inputValidationReject ASCII encodingValidationReject detached headerValidation
Phase 2: bzip2 support
Add bzip2 via numcodecs.zarr3.BZ2. Evaluate interoperability implications — this codec is not in the Zarr V3 spec proper.
Phase 3: Extension round-tripping
Selectively promote legacy.keyvalues entries into structured duckn extensions for known patterns (DWMRI gradients, .seg.nrrd metadata, DICOM key/value pairs).
Phase 4: Write from scratch
Add write(duckn_path, data, header, index_order='C') for creating .duckn stores directly without an intermediate .nrrd file.
Phase 5: Detached header support
Support .nhdr + separate data file as input.

9. Open Questions

fill_value choice. Use 0 universally, or NaN for float types? Does not affect round-tripping (single chunk always populated) but matters for generic Zarr tool expectations.
spacings without space directions. Some NRRD files have spacings but no space directions. Should we synthesize axis-aligned direction vectors, or store spacings separately?
Compression level on round-trip. If someone modifies data in the Zarr store using zarr-python (which recompresses at its default level), then converts back to NRRD, the compressed stream will differ from the original. The values are preserved; only the compression changes. Is this acceptable?
labels vs dimension_names. NRRD labels are per-axis descriptive strings. Zarr V3 dimension_names serves a similar purpose. Direct mapping? What about empty strings (NRRD convention for "no label") — map to null in Zarr?
Header field write order. pynrrd writes fields in a conventional order when creating NRRD files. We should use the same ordering for maximum compatibility with other NRRD readers, but we don't preserve the original header's insertion order. Is this acceptable?