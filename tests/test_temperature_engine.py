# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic fixtures only; no credentials, user files, models or devices."""
import hashlib
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from tihulu_jarvis.temperature_engine import (
    MAX_BYTES, MAX_ROWS, analyze_temperature, render_svg,
)
from tihulu_jarvis.voice.m1 import Hold

HEADER = b"time_s,temperature_c\n"
FIXTURE = HEADER + b"0,20\n10,22\n20,24\n30,26\n40,28\n"
NS = {"s": "http://www.w3.org/2000/svg"}


class TemperatureEngineTests(unittest.TestCase):
    def test_reference_fixture(self):
        result = analyze_temperature(FIXTURE)
        self.assertEqual(result.count, 5)
        self.assertEqual((result.mean_c, result.minimum_c, result.maximum_c,
                          result.delta_c, result.duration_s), (24, 20, 28, 8, 40))
        self.assertEqual(result.input_sha256, hashlib.sha256(FIXTURE).hexdigest())
        self.assertEqual(result.points[-1], (40, 28))

    def test_crlf_bom_negative_values_and_decimal(self):
        result = analyze_temperature(b"\xef\xbb\xbftime_s,temperature_c\r\n"
                                     b"0,-1.5\r\n0.5,-2.5\r\n")
        self.assertEqual((result.mean_c, result.delta_c, result.duration_s),
                         (-2, -1, .5))

    def test_size_encoding_header_and_empty(self):
        cases = [
            (None, "csv_invalid_size"),
            (b"", "csv_invalid_size"),
            (b"a" * (MAX_BYTES + 1), "csv_invalid_size"),
            (HEADER + b"0,\xff\n", "csv_invalid_encoding"),
            (HEADER + b"0,\x00\n", "csv_invalid_encoding"),
            (b"time,temperature\n0,20\n", "csv_invalid_header"),
            (HEADER, "csv_empty"),
        ]
        for data, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(Hold, "^" + reason + "$"):
                    analyze_temperature(data)

    def test_invalid_rows_are_not_silently_dropped(self):
        for row, reason in [
            (b"1\n", "csv_invalid_row"),
            (b"1,2,3\n", "csv_invalid_row"),
            (b"\n", "csv_invalid_row"),
            (b"1,\n", "csv_invalid_number"),
            (b"1,NaN\n", "csv_invalid_number"),
            (b"1,inf\n", "csv_invalid_number"),
            (b"1,1e309\n", "csv_invalid_number"),
            (b"1,1000000000001\n", "csv_invalid_number"),
            (b"1,=SHELL()\n", "csv_invalid_number"),
            (b'1,"2\n', "csv_malformed"),
            (b"0,30\n", "csv_time_not_increasing"),
            (b"-1,30\n", "csv_time_not_increasing"),
        ]:
            with self.subTest(reason=reason, row=row):
                with self.assertRaisesRegex(Hold, "^" + reason + "$"):
                    analyze_temperature(HEADER + b"0,20\n" + row)

    def test_row_budget(self):
        data = HEADER + b"".join(f"{i},20\n".encode() for i in range(MAX_ROWS + 1))
        with self.assertRaisesRegex(Hold, "csv_too_many_rows"):
            analyze_temperature(data)

    def test_cancellation_during_analysis(self):
        with self.assertRaisesRegex(Hold, "analysis_cancelled"):
            analyze_temperature(FIXTURE, cancelled=lambda: True)
        calls = iter([False, False, False, True])
        with self.assertRaisesRegex(Hold, "analysis_cancelled"):
            analyze_temperature(FIXTURE, cancelled=lambda: next(calls))

    def test_svg_real_points_recolor_and_no_external_references(self):
        result = analyze_temperature(FIXTURE)
        original = ET.fromstring(render_svg(result))
        recolored = ET.fromstring(render_svg(result, color="green"))
        a = original.find("s:polyline", NS)
        b = recolored.find("s:polyline", NS)
        self.assertEqual(a.attrib["points"],
                         "64.000,254.000 199.000,201.500 334.000,149.000 "
                         "469.000,96.500 604.000,44.000")
        self.assertEqual(a.attrib["points"], b.attrib["points"])
        self.assertNotEqual(a.attrib["stroke"], b.attrib["stroke"])
        for element in original.iter():
            self.assertNotIn(element.tag.split("}")[-1], ("script", "image", "a"))
            self.assertFalse(any("href" in k or k.startswith("on")
                                 for k in element.attrib))

    def test_constant_and_single_point(self):
        result = analyze_temperature(HEADER + b"0,20\n1,20\n")
        line = ET.fromstring(render_svg(result)).find("s:polyline", NS)
        self.assertEqual(line.attrib["points"], "64.000,149.000 604.000,149.000")
        single = analyze_temperature(HEADER + b"2,20\n")
        self.assertEqual(single.duration_s, 0)
        self.assertIsNotNone(ET.fromstring(render_svg(single)).find("s:circle", NS))

    def test_chart_rejects_markup_and_can_cancel(self):
        result = analyze_temperature(FIXTURE)
        with self.assertRaisesRegex(Hold, "chart_invalid_color"):
            render_svg(result, color='"><script/>')
        with self.assertRaisesRegex(Hold, "analysis_cancelled"):
            render_svg(result, cancelled=lambda: True)

    def test_no_file_or_network_access(self):
        with patch("builtins.open", side_effect=AssertionError("file access")), \
             patch("socket.socket", side_effect=AssertionError("network access")):
            self.assertIn(b"<polyline", render_svg(analyze_temperature(FIXTURE)))


if __name__ == "__main__":
    unittest.main()
