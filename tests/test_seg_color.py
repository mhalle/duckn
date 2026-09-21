"""Tests for segment colors: the four CSS forms (seg spec §3.2, §6.1, §6.2)."""

from __future__ import annotations

import glob
import os
import random
import re

import pytest

from duckn.diagnostics import About, Diagnostic, DiagnosticsError, raise_on
from duckn.seg_color import (
    SegColor,
    canonical_color,
    dicom_cielab_is_d65,
    format_color,
    from_dicom_cielab,
    from_slicer_floats,
    parse_color,
    slicer_floats_text,
    to_dicom_cielab,
    to_rgb8,
    to_srgb,
)

# The spec's worked example (§6.2).
INTS = (39330, 30580, 41942)
AS_LAB = "lab(60.0137 -9.0117 35.1984)"
AS_XYZ = "color(xyz-d65 0.2459822 0.2813858 0.1198888)"


def _hex(color: SegColor) -> str:
    return "#%02x%02x%02x" % to_rgb8(color)[0]


class TestParse:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("#dd8265", SegColor("hex", (0xDD, 0x82, 0x65))),
            ("  #DD8265\n", SegColor("hex", (0xDD, 0x82, 0x65))),
            ("lab(64.0631 33.8785 31.5159)", SegColor("lab", (64.0631, 33.8785, 31.5159))),
            ("LAB( 60 -9.5 +35 )", SegColor("lab", (60.0, -9.5, 35.0))),
            ("lab(60 -9 .5e1)", SegColor("lab", (60.0, -9.0, 5.0))),
            ("lab(60-9+35)", SegColor("lab", (60.0, -9.0, 35.0))),
            ("lab(60/**/-9 35)", SegColor("lab", (60.0, -9.0, 35.0))),
            ("color(srgb 0.5 0.333333 0.0784314)", SegColor("srgb", (0.5, 0.333333, 0.0784314))),
            ("Color(  sRGB 1 0 0)", SegColor("srgb", (1.0, 0.0, 0.0))),
            ("color(srgb 1.5 -0.25 0)", SegColor("srgb", (1.5, -0.25, 0.0))),
            ("color(xyz-d65 0.2459822 0.2813858 0.1198888)",
             SegColor("xyz-d65", (0.2459822, 0.2813858, 0.1198888))),
            ("color(XYZ 0.1 0.2 0.3)", SegColor("xyz-d65", (0.1, 0.2, 0.3))),
        ],
    )
    def test_accepted(self, text, expected):
        assert parse_color(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "#f00", "#dd8265ff", "#dd826", "#gg8265", "dd8265",
            "rgb(221 130 101)", "rgb(221, 130, 101)", "red", "currentColor",
            "lab(60% -9 35)", "lab(60 none 35)", "lab(60, -9, 35)",
            "lab(60 -9 35 / 0.5)", "lab(60 -9)", "lab(60 -9 35 1)",
            "lab (60 -9 35)", "lab(60 -9 35", "lab(60. -9 35)", "lab(60px -9 35)",
            "lab(1e999 0 0)",
            "color(display-p3 1 0 0)", "color(xyz-d50 0.1 0.2 0.3)",
            "color(srgb-linear 1 0 0)", "color(srgb 1 0 0 / 1)",
            "color(srgb 100% 0 0)", "color(srgb1 0 0)", "color(1 0 0)",
            "oklab(0.5 0 0)", "", None, [0.5, 0.5, 0.5],
        ],
    )
    def test_rejected(self, text):
        assert parse_color(text) is None


class TestFormat:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("#DD8265", "#dd8265"),
            ("lab(60 -9.01171 35.19836)", "lab(60.0000 -9.0117 35.1984)"),
            ("lab(50 -0.00001 0)", "lab(50.0000 0.0000 0.0000)"),
            ("color(srgb 0.4980395 0 0)", "color(srgb 0.49804 0 0)"),
            ("color(srgb 0.50000 1.0 1e-5)", "color(srgb 0.5 1 0.00001)"),
            ("color(srgb 0.5 -0 0)", "color(srgb 0.5 0 0)"),
            ("color(xyz 0.1 0.2 -0.00000001)", "color(xyz-d65 0.1000000 0.2000000 0.0000000)"),
        ],
    )
    def test_canonical(self, text, expected):
        assert canonical_color(text) == (expected, False)

    def test_srgb_clamped_and_flagged(self):
        assert canonical_color("color(srgb 1.5 -0.25 0.5)") == ("color(srgb 1 0 0.5)", True)

    def test_unreadable(self):
        assert canonical_color("rgb(1 2 3)") == (None, False)

    def test_canonical_is_a_fixed_point(self):
        for text in ["#dd8265", AS_LAB, AS_XYZ, "color(srgb 0.5 0.333333 0.0784314)"]:
            assert canonical_color(text) == (text, False)


class TestSlicer:
    def test_eight_bit_becomes_hex(self):
        c = from_slicer_floats([0.501961, 0.682353, 0.501961])
        assert format_color(c)[0] == "#80ae80"

    def test_other_floats_stay_floats(self):
        c = from_slicer_floats([0.5, 0.333333, 0.0784314])
        assert format_color(c)[0] == "color(srgb 0.5 0.333333 0.0784314)"

    def test_hex_test_is_string_equality(self):
        # 0.501961 is 128/255 at six digits; 0.50196 is not.
        assert from_slicer_floats([0.501961, 0, 1]).form == "hex"
        assert from_slicer_floats([0.50196, 0, 1]).form == "srgb"

    def test_out_of_range_is_not_hex(self):
        assert from_slicer_floats([1.001, 0, 0]).form == "srgb"
        assert from_slicer_floats([-0.001, 0, 0]).form == "srgb"
        assert from_slicer_floats([-0.0, 0, 0]) == SegColor("hex", (0, 0, 0))

    def test_every_eight_bit_value_round_trips(self):
        for n in range(256):
            text = slicer_floats_text(to_srgb(SegColor("hex", (n, n, n))).rgb)
            back = from_slicer_floats([float(x) for x in text.split()])
            assert back == SegColor("hex", (n, n, n))

    def test_slicer_floats_return_byte_for_byte(self):
        rng = random.Random(0)
        for _ in range(2000):
            text = " ".join("%.6g" % rng.random() for _ in range(3))
            c = from_slicer_floats([float(x) for x in text.split()])
            c = parse_color(format_color(c)[0])  # through the file
            assert slicer_floats_text(to_srgb(c).rgb) == text

    def test_export_keeps_the_exponent(self):
        assert slicer_floats_text((1e-05, 0.5, 1.0)) == "1e-05 0.5 1"

    def test_real_world_fixtures_are_all_hex(self):
        here = os.path.dirname(__file__)
        files = glob.glob(os.path.join(here, "data", "real-world", "*.seg.nrrd"))
        seen = 0
        for path in files:
            with open(path, "rb") as f:
                header = f.read(200_000).split(b"\n\n")[0].decode("latin-1")
            for m in re.finditer(r"Segment\d+_Color:=(.+)", header):
                floats = [float(x) for x in m.group(1).split()]
                c = from_slicer_floats(floats)
                assert c.form == "hex", (path, m.group(1))
                assert slicer_floats_text(to_srgb(c).rgb) == m.group(1).strip()
                seen += 1
        if files:
            assert seen


class TestSrgb:
    def test_lab_in_gamut(self):
        res = to_srgb(parse_color("lab(64.0631 33.8785 31.5159)"))
        assert not res.gamut_mapped and not res.clamped
        assert _hex(parse_color("lab(64.0631 33.8785 31.5159)")) == "#dd8265"

    def test_lab_out_of_gamut_is_mapped_not_clipped(self):
        res = to_srgb(parse_color("lab(50 100 -100)"))
        assert res.gamut_mapped
        assert all(0 <= x <= 1 for x in res.rgb)
        # Clipping channels would leave green at 0.
        assert res.rgb[1] > 0.05

    def test_srgb_is_clamped_never_mapped(self):
        res = to_srgb(parse_color("color(srgb 1.5 0.5 -1)"))
        assert res == ((1.0, 0.5, 0.0), True, False)

    def test_hex(self):
        assert to_rgb8(parse_color("#dd8265")) == ((0xDD, 0x82, 0x65), False, False)


class TestDicom:
    def test_standard_reading(self):
        c = from_dicom_cielab(INTS, d65=False)
        assert format_color(c)[0] == AS_LAB
        assert _hex(c) == "#919450"

    def test_dcmqi_reading(self):
        c = from_dicom_cielab(INTS, d65=True)
        assert format_color(c)[0] == AS_XYZ
        assert _hex(c) == "#969451"

    def test_strings_return_the_integers(self):
        assert to_dicom_cielab(parse_color(AS_LAB), d65=False) == (INTS, False)
        assert to_dicom_cielab(parse_color(AS_XYZ), d65=True) == (INTS, False)

    def test_transcription_is_exact(self):
        rng = random.Random(1)
        samples = [(0, 0, 0), (65535, 65535, 65535), (65535, 0, 65535), (0, 65535, 0)]
        samples += [tuple(rng.randrange(65536) for _ in range(3)) for _ in range(5000)]
        for v in samples:
            for d65 in (False, True):
                text = format_color(from_dicom_cielab(v, d65=d65))[0]
                assert to_dicom_cielab(parse_color(text), d65=d65) == (v, False), (v, d65)

    def test_hex_to_standard_encoding(self):
        ints, clamped = to_dicom_cielab(parse_color("#dd8265"), d65=False)
        assert not clamped
        # #dd8265 is lab(64.0631 33.8785 31.5159); the integers quantize it.
        back = from_dicom_cielab(ints, d65=False)
        assert back.coords == pytest.approx((64.0631, 33.8785, 31.5159), abs=0.003)
        assert _hex(back) == "#dd8265"

    def test_hex_survives_the_default_encoding(self):
        rng = random.Random(2)
        for _ in range(500):
            rgb = tuple(rng.randrange(256) for _ in range(3))
            ints, clamped = to_dicom_cielab(SegColor("hex", rgb))
            assert not clamped
            assert to_rgb8(from_dicom_cielab(ints, d65=True))[0] == rgb

    def test_out_of_range_is_clamped_and_flagged(self):
        ints, clamped = to_dicom_cielab(parse_color("lab(150 300 -300)"), d65=False)
        assert clamped and ints == (65535, 65535, 0)

    @pytest.mark.parametrize(
        "manufacturer, versions, expected",
        [
            ("QIICR", "0.3.1-abc", True),
            ("Acme", ["1.0", "cielab-d65"], True),
            ("Acme", "cielab-d65", True),
            ("Acme", ["1.0"], False),
            ("Acme", None, False),
            (None, None, False),
        ],
    )
    def test_which_reading(self, manufacturer, versions, expected):
        assert dicom_cielab_is_d65(manufacturer, versions) is expected


class TestDiagnostics:
    def test_str_and_raise(self):
        d = Diagnostic("rule-14", "error", About.value(0, 3), "listed twice")
        w = Diagnostic("color-unreadable", "warning", About.segment("liver"))
        assert str(d) == "error rule-14 (layer 0, value 3): listed twice"
        assert str(w) == "warning color-unreadable (segment liver)"
        raise_on([w])
        with pytest.raises(DiagnosticsError) as e:
            raise_on([w, d])
        assert e.value.diagnostics == [d]
        with pytest.raises(DiagnosticsError):
            raise_on([w], warnings=True)

    def test_about_is_hashable_and_comparable(self):
        assert About.segment("a") == About.segment("a")
        assert About.segment("a", 2) != About.segment("a")
        assert len({About.extension(), About.extension(), About.scheme("SCT")}) == 2
