"""DICOM tags as SimpleITK reports them, in the ``dicom`` extension's encoding (dicom-spec.md).

The pydicom converter in :mod:`duckn.dicom_convert` reads DICOM files itself. This module is for
the other common source: a series already read by SimpleITK, whose ``ImageSeriesReader`` hands
over one dictionary per slice when asked (``MetaDataDictionaryArrayUpdateOn()``), keyed
``gggg|eeee`` with string values. Converting those dictionaries means the tags recorded are
exactly the ones the image was read with, with no second read of the files.

pydicom is used as a DATA DICTIONARY only - the keyword, VR and VM of a tag - and no file is
opened through it; it is the ``dicom`` extra.

The rules are the spec's:

- keys are PS3.6 keywords; a private or unknown tag keeps its uppercase hex code (§4.1);
- DS, IS and the binary integer and float VRs become JSON numbers, other strings lose the
  padding DICOM adds (§4.2); an empty numeric value is left out, never written as ``null``,
  which the spec reserves for a value that was deliberately removed (§4.3);
- an attribute whose VM can exceed 1 is always an array, one whose VM is exactly 1 a bare value
  (§4.6);
- binary VRs are skipped - SimpleITK's string form of them is no encoding the spec knows (§4.5
  asks for base64 of the bytes, which the strings no longer are); that also takes Overlay Data
  (OB/OW, §9) in every repeating group. Group lengths are dropped too (§9);
- the attributes §2 lists as captured by convention fields are left out, since the fields are
  authoritative and a writer may change the encoding the tags would describe (§9). Two of them
  carry facts a caller must still put in the fields: Slice Thickness goes to the slice axis'
  ``thickness`` and Rescale Type to ``sample_units``; :data:`SLICE_THICKNESS` and
  :data:`RESCALE_TYPE` name their keys so a caller can read them before converting;
- a tag with the same value on every slice is series-level; one that differs, or that some
  slices lack, is per slice (§6.1), returned as one dict per slice for ``samples[i].metadata``
  under ``"dicom"`` (§6.3).

Unlike the pydicom converter's per-slice split, nothing that varies is dropped: §6.3 names
``InstanceNumber`` and ``SOPInstanceUID`` among the tags that belong per slice.

:func:`to_sitk_strings` is the inverse for series-level tags, for a reader that restores them
onto a SimpleITK image as a single-file read would show them. It is not a byte-exact round trip:
``"120.000 "`` comes back ``"120"``, which §4.2 accepts.

:func:`tags_from_datasets` (and :func:`tags_from_files`, which reads the headers itself) is the
same conversion from pydicom datasets, for a caller that wants what SimpleITK's dictionaries
cannot hold: sequences, binary values (base64, §4.5) and private tags as the files carry them.
Same exclusions, same split, same encoding of every value SimpleITK can also report; it also
returns the extension's own fields (§3.1) the files state.

Written in haversack (2026-09-25) for its input copy, a decoded duckn form of each cached input,
and moved here so that the spec's rules live beside the spec.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "EXCLUDED",
    "RESCALE_TYPE",
    "SLICE_THICKNESS",
    "encode",
    "keyword_of",
    "BULK",
    "tags_from_datasets",
    "tags_from_files",
    "tags_from_sitk",
    "to_sitk_strings",
]

#: The attributes dicom-spec §2 and §9 exclude from ``tags``, by tag. Their values would be a
#: second statement of the array's geometry, shape, type or value mapping - and a stale one
#: wherever the array's encoding differs from the source's.
EXCLUDED = frozenset({
    0x7FE00010,                              # Pixel Data
    0x00200032, 0x00200037,                  # Image Position / Orientation (Patient)
    0x00280010, 0x00280011, 0x00280008,      # Rows, Columns, Number of Frames
    0x00280100, 0x00280101, 0x00280102, 0x00280103,   # Bits Allocated/Stored, High Bit, Pixel Representation
    0x00281052, 0x00281053, 0x00281054,      # Rescale Intercept, Slope, Type
    0x00283000, 0x00283002, 0x00283006,      # Modality LUT Sequence, LUT Descriptor, LUT Data
    0x00283004,                              # Modality LUT Type (-> sample_units)
    0x00280030, 0x00180088,                  # Pixel Spacing, Spacing Between Slices
    0x00180050,                              # Slice Thickness (-> axes[i].thickness)
})
#: SimpleITK's keys for the two excluded attributes whose facts a caller moves into convention
#: fields (dicom-spec §2): Slice Thickness -> the slice axis' ``thickness`` (or per sample),
#: Rescale Type -> the array's ``sample_units``.
SLICE_THICKNESS = "0018|0050"
RESCALE_TYPE = "0028|1054"

_NUMERIC_INT = frozenset({"IS", "US", "SS", "UL", "SL", "UV", "SV"})
_NUMERIC_FLOAT = frozenset({"DS", "FL", "FD"})
_BINARY_VRS = frozenset({"OB", "OW", "OF", "OD", "OL", "OV", "UN"})
#: Bulk data the array itself (or no image at all) represents, left out of ``tags`` even where
#: binary values are kept (§4.5): the pixel data in all three of its forms and its offset tables;
#: overlay and curve data in every repeating group (60xx,3000 / 50xx,3000); waveform data.
BULK = frozenset({0x7FE00010, 0x7FE00008, 0x7FE00009, 0x7FE00001, 0x7FE00002, 0x54001010})


def _bulk(tag: int) -> bool:
    group, element = tag >> 16, tag & 0xFFFF
    return (tag in BULK or (0x6000 <= group <= 0x60FF and element == 0x3000)
            or (0x5000 <= group <= 0x50FF and element == 0x3000))


def _tag(key: str) -> int | None:
    """``"0018|0060"`` -> 0x00180060; None for a key that is not a DICOM tag (``ITK_...``)."""
    if "|" not in key:
        return None
    group, elem = key.split("|", 1)
    try:
        return (int(group, 16) << 16) | int(elem, 16)
    except ValueError:
        return None


def _dictionary():
    try:
        import pydicom.datadict as dd
    except ImportError as e:            # the same one-line fix dicom_convert names
        raise ImportError(
            "converting DICOM tags needs pydicom: pip install 'duckn[dicom]'") from e
    return dd


def keyword_of(tag: int) -> str:
    """The PS3.6 keyword, or the uppercase hex code for a private or unknown tag (§4.1)."""
    if (tag >> 16) % 2 == 1:
        return f"{tag:08X}"
    return _dictionary().keyword_for_tag(tag) or f"{tag:08X}"


def _vr_vm(tag: int) -> tuple[str | None, str | None]:
    dd = _dictionary()
    try:
        return dd.dictionary_VR(tag), dd.dictionary_VM(tag)
    except KeyError:                          # private or unknown: a plain string
        return None, None


def _number(text: str, integer: bool):
    v = float(text)
    return int(v) if integer and v == int(v) else v


def encode(tag: int, text: str) -> Any:
    """One SimpleITK value (a string) in the spec's JSON-native form (§4.2, §4.6); None for an
    empty numeric value, which a caller leaves out."""
    vr, vm = _vr_vm(tag)
    if vr is not None and " or " in vr:       # "US or SS": both integers
        vr = vr.split(" or ")[0]
    parts = [p.strip() for p in str(text).split("\\")]
    multi = vm is not None and vm != "1"
    if vr in _NUMERIC_INT or vr in _NUMERIC_FLOAT:
        try:
            values = [_number(p, vr in _NUMERIC_INT) for p in parts if p != ""]
        except ValueError:                    # a malformed number: keep the text, never guess
            return parts if multi or len(parts) != 1 else parts[0]
        if multi:
            return values
        return values[0] if values else None
    if multi:
        return [p.rstrip("\x00 ").lstrip() for p in parts]
    return str(text).rstrip("\x00 ").lstrip()


def tags_from_sitk(per_slice: list[dict[str, str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(series_tags, per_slice_tags)`` from SimpleITK's per-slice dictionaries, in the order
    given (the image's slice order). Keys that are not DICOM tags (SimpleITK's own ``ITK_...``),
    excluded tags, binary VRs and group lengths are dropped."""
    if not per_slice:
        return {}, []
    keys: set[str] = set()
    for d in per_slice:
        keys.update(d)
    series: dict[str, Any] = {}
    varying: list[tuple[int, str]] = []
    for key in sorted(keys):
        tag = _tag(key)
        if tag is None or tag in EXCLUDED or (tag & 0xFFFF) == 0:
            continue
        vr, _ = _vr_vm(tag)
        if vr is not None and vr.split(" or ")[0] in _BINARY_VRS:
            continue
        values = [d.get(key) for d in per_slice]
        if values[0] is not None and all(v == values[0] for v in values):
            value = encode(tag, values[0])
            if value is not None:
                series[keyword_of(tag)] = value
        else:
            varying.append((tag, key))
    slices = []
    for d in per_slice:
        one = {}
        for tag, key in varying:
            if key in d and (value := encode(tag, d[key])) is not None:
                one[keyword_of(tag)] = value
        slices.append(one)
    return series, slices


def _tag_of_keyword(keyword: str) -> int | None:
    if len(keyword) == 8:
        try:
            return int(keyword, 16)
        except ValueError:
            pass
    return _dictionary().tag_for_keyword(keyword)


def _text(value: Any) -> str:
    def one(v):
        if isinstance(v, float) and v == int(v):
            return str(int(v)) if abs(v) < 1e15 else repr(v)
        return str(v)
    if isinstance(value, list):
        return "\\".join(one(v) for v in value)
    return one(value)


def to_sitk_strings(tags: dict[str, Any]) -> dict[str, str]:
    """Series-level tags as SimpleITK shows them: ``gggg|eeee`` -> string. A ``null`` (a
    redacted value, §4.3) and a sequence (no SimpleITK string form) are left out."""
    out = {}
    for keyword, value in tags.items():
        tag = _tag_of_keyword(keyword)
        if tag is None or value is None:
            continue
        if isinstance(value, list) and any(isinstance(v, dict) for v in value):
            continue
        out[f"{tag >> 16:04x}|{tag & 0xFFFF:04x}"] = _text(value)
    return out


def _pydicom_value(elem: Any) -> Any:
    """One pydicom element in the spec's encoding: :mod:`duckn.dicom_convert`'s own conversion
    (sequences recursive, binary as base64). Empty values as :func:`encode` has them: an empty
    number is left out (at every depth - ``null`` is reserved for a value deliberately removed,
    §4.3), an empty string is ``""`` (``[""]`` where the VM makes it an array) - an attribute
    present and empty says something an absent one does not."""
    from .dicom_convert import _convert_value, _should_be_array

    def strip(v):
        if isinstance(v, dict):
            return {k: sv for k, x in v.items() if (sv := strip(x)) is not None}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v
    value = strip(_convert_value(elem))
    vr = elem.VR.split(" or ")[0] if elem.VR else None
    if value is None and vr not in _NUMERIC_INT | _NUMERIC_FLOAT | _BINARY_VRS | {"SQ", "AT"}:
        return [""] if _should_be_array(elem) else ""
    return value


def _one_dataset(ds: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for elem in ds:                          # the data set only: group 0002 describes the file
        tag = int(elem.tag)
        if tag in EXCLUDED or (tag & 0xFFFF) == 0 or _bulk(tag):
            continue
        value = _pydicom_value(elem)
        if value is not None:
            out[keyword_of(tag)] = value
    return out


def tags_from_datasets(datasets) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """``(series_tags, per_slice_tags, extension_fields)`` from pydicom datasets in the image's
    slice order - an iterable, each converted as it comes, so a caller can read one header at a
    time. The split and the exclusions are :func:`tags_from_sitk`'s; unlike it, sequences,
    binary values (base64, bulk data excepted) and private tags are kept. ``extension_fields``
    holds what §3.1 asks of the files: ``source_transfer_syntax`` when every file states the
    same one, and ``lossy_compressed`` when any file says yes, or every file says no."""
    from .dicom_convert import _get_transfer_syntax, _is_lossy_compressed
    per: list[dict[str, Any]] = []
    syntaxes: set = set()
    lossy: list = []
    for ds in datasets:
        per.append(_one_dataset(ds))
        syntaxes.add(_get_transfer_syntax(ds))
        lossy.append(_is_lossy_compressed(ds))
    if not per:
        return {}, [], {}
    keys = sorted({k for d in per for k in d})
    series = {}
    varying = []
    for k in keys:
        first = per[0].get(k)
        if first is not None and all(d.get(k) == first for d in per):
            series[k] = first
        else:
            varying.append(k)
    slices = [{k: d[k] for k in varying if k in d} for d in per]
    ext: dict[str, Any] = {}
    if len(syntaxes) == 1 and None not in syntaxes:
        ext["source_transfer_syntax"] = next(iter(syntaxes))
    if any(v is True for v in lossy):
        ext["lossy_compressed"] = True
    elif lossy and all(v is False for v in lossy):
        ext["lossy_compressed"] = False
    return series, slices, ext


def tags_from_files(paths) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """:func:`tags_from_datasets` of DICOM files, in the order given, reading each header
    (never the pixel data) and letting it go before the next; ``force`` reads a file without
    the Part 10 preamble, which real archives hold."""
    _dictionary()
    import pydicom

    def headers():
        for p in paths:
            yield pydicom.dcmread(str(p), stop_before_pixels=True, force=True)
    return tags_from_datasets(headers())
