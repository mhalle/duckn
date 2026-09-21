"""Segment colors: the four CSS forms of the seg extension (spec §3.2).

A color is held as the form it was read in and the numbers it was read
with; nothing is converted until a target needs another space. Parsing and
formatting are done here, because a reader accepts less than CSS does and a
writer's spelling is fixed. Conversion between color spaces and gamut
mapping are CSS Color 4's, and are coloraide's. The D65 CIELab steps of
§6.2 are written out with the spec's constants, so that the strings do not
depend on a library's.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, NamedTuple, Sequence

from coloraide import Color as _Color

Form = Literal["hex", "srgb", "lab", "xyz-d65"]


@dataclass(frozen=True)
class SegColor:
    """A parsed color. For `hex`, `coords` are three integers 0-255;
    otherwise three floats, as parsed."""

    form: Form
    coords: tuple[float, float, float]


class SrgbResult(NamedTuple):
    rgb: tuple[float, float, float]  # each in 0-1
    clamped: bool  # a color(srgb) component was outside 0-1
    gamut_mapped: bool  # a lab/xyz-d65 color was outside the sRGB gamut


# --- reading ---------------------------------------------------------------

_HEX = re.compile(r"#([0-9a-fA-F]{6})\Z")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_WS = re.compile(r"[ \t\n\r\f]*")
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
_NUMBER = re.compile(r"[+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?")
_SPACES = {"srgb": "srgb", "xyz-d65": "xyz-d65", "xyz": "xyz-d65"}


def parse_color(text: object) -> SegColor | None:
    """Parse one of the four forms a reader accepts, or return None.

    None means the color is treated as absent; the caller reports
    `color-unreadable`.
    """
    if not isinstance(text, str):
        return None
    s = text.strip(" \t\n\r\f")
    m = _HEX.match(s)
    if m:
        h = m.group(1)
        return SegColor("hex", (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))

    s = _COMMENT.sub(" ", s)
    # A function token is its name and the parenthesis, with nothing between.
    m = re.match(r"([A-Za-z]+)\(", s)
    if not m or not s.endswith(")"):
        return None
    name = m.group(1).lower()
    pos, end = m.end(), len(s) - 1

    def skip_ws() -> None:
        nonlocal pos
        pos = _WS.match(s, pos, end).end()

    if name == "color":
        skip_ws()
        m = _IDENT.match(s, pos, end)
        if not m:
            return None
        form = _SPACES.get(m.group(0).lower())
        if form is None:
            return None
        pos = m.end()
    elif name == "lab":
        form = "lab"
    else:
        return None

    coords: list[float] = []
    for _ in range(3):
        skip_ws()
        m = _NUMBER.match(s, pos, end)
        if not m:
            return None
        pos = m.end()
        # A number followed by a name or a percent sign is a dimension or a
        # percentage, not a number.
        if pos < end and (s[pos] == "%" or s[pos].isalpha() or s[pos] == "_"):
            return None
        x = float(m.group(0))
        if not math.isfinite(x):
            return None
        coords.append(x)
    skip_ws()
    if pos != end:
        return None
    return SegColor(form, (coords[0], coords[1], coords[2]))  # type: ignore[arg-type]


# --- writing ---------------------------------------------------------------


def _g6(x: float) -> str:
    """C's %.6g, written out in full where it would use an exponent."""
    s = "%.6g" % x
    if "e" in s:
        s = format(Decimal(s), "f")
    return "0" if float(s) == 0 else s


def _fixed(x: float, places: int) -> str:
    s = "%.*f" % (places, x)
    return s[1:] if s.startswith("-") and float(s) == 0 else s


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def format_color(color: SegColor) -> tuple[str, bool]:
    """The canonical spelling of a color, in the form it was read in.

    Returns the string and whether a `color(srgb)` component was clamped
    (the caller reports `color-clamped`).
    """
    a, b, c = color.coords
    if color.form == "hex":
        return "#%02x%02x%02x" % (int(a), int(b), int(c)), False
    if color.form == "srgb":
        clamped = tuple(_clamp01(x) for x in color.coords)
        text = "color(srgb %s)" % " ".join(_g6(x) for x in clamped)
        return text, clamped != color.coords
    if color.form == "lab":
        return "lab(%s)" % " ".join(_fixed(x, 4) for x in color.coords), False
    return "color(xyz-d65 %s)" % " ".join(_fixed(x, 7) for x in color.coords), False


def canonical_color(text: object) -> tuple[str | None, bool]:
    """Parse and respell. Returns (None, False) for an unreadable color."""
    color = parse_color(text)
    if color is None:
        return None, False
    return format_color(color)


# --- sRGB targets: .seg.nrrd floats, 8-bit color tables ---------------------


def _half_up(x: float) -> int:
    return math.floor(x + 0.5)


def to_srgb(color: SegColor) -> SrgbResult:
    """Express a color in sRGB, components in 0-1 (§3.2, Reading).

    A `color(srgb)` color is clamped, never gamut-mapped. A `lab()` or
    `color(xyz-d65)` color is converted as CSS Color 4 defines and brought
    into gamut by CSS's gamut-mapping algorithm.
    """
    if color.form == "hex":
        r, g, b = (n / 255 for n in color.coords)
        return SrgbResult((r, g, b), False, False)
    if color.form == "srgb":
        rgb = tuple(_clamp01(x) for x in color.coords)
        return SrgbResult(rgb, rgb != color.coords, False)  # type: ignore[arg-type]
    c = _Color(color.form, list(color.coords)).convert("srgb")
    mapped = not c.in_gamut()
    if mapped:
        c.fit(method="oklch-chroma")
    # Within tolerance of the gamut is in gamut; what is left is rounding.
    r, g, b = (_clamp01(x) for x in c.coords())
    return SrgbResult((r, g, b), False, mapped)


def to_rgb8(color: SegColor) -> tuple[tuple[int, int, int], bool, bool]:
    """A color-table entry: ((r, g, b) at 8 bits, clamped, gamut_mapped)."""
    if color.form == "hex":
        a, b, c = color.coords
        return (int(a), int(b), int(c)), False, False
    res = to_srgb(color)
    r, g, b = (min(255, _half_up(255 * x)) for x in res.rgb)
    return (r, g, b), res.clamped, res.gamut_mapped


def from_slicer_floats(rgb: Sequence[float]) -> SegColor:
    """A `.seg.nrrd` SegmentN_Color as hex or `color(srgb)` (§6.1).

    Hex when every channel formats, with %.6g, to the same string as its
    nearest 8-bit value does: a comparison of strings, not a tolerance.
    """
    r, g, b = (0.0 if x == 0 else float(x) for x in rgb)  # no negative zero
    ns = [_half_up(255 * x) if math.isfinite(x) else -1 for x in (r, g, b)]
    if all(0 <= n <= 255 and "%.6g" % x == "%.6g" % (n / 255)
           for x, n in zip((r, g, b), ns)):
        return SegColor("hex", (ns[0], ns[1], ns[2]))
    return SegColor("srgb", (r, g, b))


def slicer_floats_text(rgb: Sequence[float]) -> str:
    """Three sRGB floats as 3D Slicer writes them: %.6g, exponent included."""
    return " ".join("%.6g" % (0.0 if x == 0 else x) for x in rgb)


# --- DICOM RecommendedDisplayCIELabValue (§6.2) ------------------------------

# dcmqi's constants. Fixed by the spec; do not replace with a library's.
_XN, _YN, _ZN = 0.95047, 1.0, 1.08883
_EPS = 0.008856
_KAPPA, _OFFSET = 7.787, 16 / 116


def _lab_from_ints(v: Sequence[int]) -> tuple[float, float, float]:
    l, a, b = (int(x) for x in v)
    return l * 100 / 65535, a * 255 / 65535 - 128, b * 255 / 65535 - 128


def _ints_from_lab(lab: Sequence[float]) -> tuple[tuple[int, int, int], bool]:
    l, a, b = lab
    raw = (
        _half_up(l * 65535 / 100),
        _half_up((a + 128) * 65535 / 255),
        _half_up((b + 128) * 65535 / 255),
    )
    ints = tuple(min(65535, max(0, n)) for n in raw)
    return ints, ints != raw  # type: ignore[return-value]


def from_dicom_cielab(values: Sequence[int], *, d65: bool) -> SegColor:
    """Transcribe the three 16-bit integers; no color is converted.

    `d65=False` is the standard's reading (CIELab under D50, CSS `lab()`);
    `d65=True` is dcmqi's (CIELab under D65, written as `xyz-d65`).
    """
    l, a, b = _lab_from_ints(values)
    if not d65:
        return SegColor("lab", (l, a, b))
    fy = (l + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200

    def t(f: float) -> float:
        return f**3 if f**3 > _EPS else (f - _OFFSET) / _KAPPA

    return SegColor("xyz-d65", (_XN * t(fx), _YN * t(fy), _ZN * t(fz)))


def _rounded(color: SegColor) -> tuple[float, float, float]:
    """The numbers as the canonical spelling states them. Recovering the
    source integers exactly is promised of the written string."""
    places = 4 if color.form == "lab" else 7
    return tuple(float(_fixed(x, places)) for x in color.coords)  # type: ignore[return-value]


def to_dicom_cielab(
    color: SegColor, *, d65: bool = True
) -> tuple[tuple[int, int, int], bool]:
    """The three 16-bit integers, and whether any was clamped to 0-65535.

    `d65=True` is the dcmqi-compatible encoding, the default; the caller
    marks the object with `cielab-d65` in SoftwareVersions. `d65=False` is
    the encoding the standard intends.
    """
    if not d65:
        if color.form == "lab":
            lab = _rounded(color)
        else:
            lab = tuple(_as_coloraide(color).convert("lab").coords())
        return _ints_from_lab(lab)

    if color.form == "xyz-d65":
        x, y, z = _rounded(color)
    else:
        x, y, z = _as_coloraide(color).convert("xyz-d65").coords()

    def f(t: float) -> float:
        return math.cbrt(t) if t > _EPS else _KAPPA * t + _OFFSET

    fx, fy, fz = f(x / _XN), f(y / _YN), f(z / _ZN)
    return _ints_from_lab((116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)))


def _as_coloraide(color: SegColor) -> _Color:
    if color.form == "hex":
        return _Color("srgb", [n / 255 for n in color.coords])
    return _Color(color.form, list(color.coords))


DICOM_D65_MARKER = "cielab-d65"


def dicom_cielab_is_d65(manufacturer: object, software_versions: object) -> bool:
    """Which reading applies to an object's CIELab values (§6.2)."""
    if isinstance(manufacturer, str) and manufacturer.strip() == "QIICR":
        return True
    if software_versions is None:
        return False
    if isinstance(software_versions, str):
        software_versions = [software_versions]
    return any(str(v).strip() == DICOM_D65_MARKER for v in software_versions)
