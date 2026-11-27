# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
import json
from gateway import Refused
from heterogeneous_data_tools import DataCall,evaluate
from test_vision_image_input import png

class HeterogeneousDataToolTests(unittest.TestCase):
    def test_real_integer_csv_stats(self):
        v=json.loads(evaluate(DataCall("csv","csv_stats",b"value\n7\n-2\n10\n")))
        self.assertEqual((v["count"],v["min"],v["max"],v["sum"]),(3,-2,10,15))
    def test_real_utf8_text_stats(self):
        v=json.loads(evaluate(DataCall("text","read_text","Merhaba dünya\nGuten Tag".encode())))
        self.assertEqual((v["lines"],v["words"]),(2,4))
    def test_real_png_geometry_and_digest(self):
        v=json.loads(evaluate(DataCall("image","png_stats",png())))
        self.assertEqual((v["width"],v["height"],v["channels"]),(2,1,3))
        self.assertEqual(len(v["pixel_digest"]),64)
    def test_report_consumes_actual_parent_bytes(self):
        a=evaluate(DataCall("csv","csv_stats",b"value\n7\n"))
        b=evaluate(DataCall("image","png_stats",png()))
        v=json.loads(evaluate(DataCall("review","report",b""),(("image",b),("csv",a))))
        self.assertEqual([r["task"] for r in v["results"]],["csv","image"])
        self.assertEqual(v["results"][0]["result"]["sum"],7)
        self.assertEqual(v["results"][1]["result"]["width"],2)
    def test_no_arbitrary_commands_or_paths(self):
        for op in ("shell","/usr/bin/date","python","read_file"):
            with self.assertRaises(Refused):
                evaluate(DataCall("x",op,b"x"))
    def test_csv_input_strict_bounded(self):
        for raw in (b"value\n",b"value\n=1+1\n",b"value\n1,2\n",b"value\n1000001\n",b"value\n"+b"1\n"*257):
            with self.assertRaises(Refused):
                evaluate(DataCall("x","csv_stats",raw))
    def test_bad_text_and_image_refused(self):
        for raw in (b"\xff",b"\0",b" "):
            with self.assertRaises(Refused):
                evaluate(DataCall("x","read_text",raw))
        with self.assertRaises(Refused):
            evaluate(DataCall("x","png_stats",b"bad"))
    def test_report_rejects_duplicate_noncanonical_and_extra_input(self):
        a=evaluate(DataCall("csv","csv_stats",b"value\n1\n"))
        for rows in ((),(("csv",a),("csv",a)),(("csv",a+b" "),),(("csv",b"[]"),)):
            with self.assertRaises(Refused):
                evaluate(DataCall("r","report",b""),rows)
        with self.assertRaises(Refused):
            evaluate(DataCall("r","report",b"extra"),(("csv",a),))
    def test_digest_binds_task_operation_and_bytes(self):
        a=DataCall("x","read_text",b"x")
        for b in (replace(a,task="y"),replace(a,data=b"y"),replace(a,operation="csv_stats")):
            self.assertNotEqual(a.digest(),b.digest())
