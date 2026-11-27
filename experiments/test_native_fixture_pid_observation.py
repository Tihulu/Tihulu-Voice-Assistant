# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from unittest.mock import patch
from gateway import Refused
from native_fixture_pid_observation import process_identity, namespace_drained, private_process_snapshot


def stat(pid=17, name=b"worker ) with spaces", start=b"12345"):
    return str(pid).encode("ascii")+b" ("+name+b") S "+b" ".join([b"0"]*18+[start,b"0"])


class NativeFixturePidObservationTests(unittest.TestCase):
    def test_proc_comm_with_parentheses(self):
        self.assertEqual(process_identity(17,stat()),(17,12345))

    def test_pid_mismatch_refused(self):
        with self.assertRaises(Refused):
            process_identity(18,stat())

    def test_short_malformed_and_non_numeric_start_refused(self):
        for raw in (b"17 (x) S 0",stat(start=b"x"),stat(start=b"-1"),stat(start=b"9"*21),b"17 (x)"):
            with self.subTest(raw=raw),self.assertRaises(Refused):
                process_identity(17,raw)

    def test_start_time_overflow_refused(self):
        with self.assertRaises(Refused):
            process_identity(17,stat(start=str(2**64).encode()))

    def test_exact_baseline_order_independent(self):
        self.assertTrue(namespace_drained(((1,10),(2,20)),((2,20),(1,10))))

    def test_new_missing_and_reused_process_refused(self):
        base=((1,10),(2,20))
        for after in (((1,10),(2,20),(3,30)),((1,10),),((1,10),(2,21))):
            self.assertFalse(namespace_drained(base,after))

    def test_invalid_or_duplicate_baseline_refused(self):
        for snap in ((),((1,10),(1,11)),((True,10),),((1,-1),),((1,False),),[(1,10)]):
            self.assertFalse(namespace_drained(snap,snap))

    def test_same_host_namespace_refused_before_scan(self):
        with patch("native_fixture_pid_observation.os.readlink",return_value="pid:[17]"),patch("native_fixture_pid_observation.Path") as path:
            with self.assertRaises(Refused):
                private_process_snapshot("pid:[17]")
            path.assert_not_called()

    def test_invalid_host_marker_refused_before_scan(self):
        with patch("native_fixture_pid_observation.os.readlink") as readlink:
            with self.assertRaises(Refused):
                private_process_snapshot("bad")
            readlink.assert_not_called()


if __name__=="__main__":
    unittest.main()
