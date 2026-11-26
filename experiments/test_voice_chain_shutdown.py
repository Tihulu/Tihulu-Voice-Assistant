# SPDX-License-Identifier: AGPL-3.0-only
import subprocess
import unittest
from unittest.mock import Mock
from llama_engine_acceptance import stop_fixture_server

class VoiceChainShutdownTests(unittest.TestCase):
    def test_cleanup_failure_still_reaps_owned_child(self):
        p=Mock()
        p.poll.return_value=None
        cleanup=Mock(side_effect=OSError("fixture unlink"))
        with self.assertRaisesRegex(OSError,"fixture unlink"):
            stop_fixture_server(p,cleanup)
        p.terminate.assert_called_once_with()
        p.wait.assert_called_once_with(timeout=3)
        p.kill.assert_not_called()

    def test_timeout_kill_and_second_wait_even_on_cleanup_error(self):
        p=Mock()
        p.poll.return_value=None
        p.wait.side_effect=[subprocess.TimeoutExpired("fixture",3),0]
        with self.assertRaises(OSError):
            stop_fixture_server(p,Mock(side_effect=OSError("fixture unlink")))
        p.terminate.assert_called_once_with()
        p.kill.assert_called_once_with()
        self.assertEqual(p.wait.call_count,2)

    def test_already_exited_process_is_reaped_without_signal(self):
        p=Mock()
        p.poll.return_value=0
        cleanup=Mock()
        stop_fixture_server(p,cleanup)
        cleanup.assert_called_once_with()
        p.terminate.assert_not_called()
        p.kill.assert_not_called()
        p.wait.assert_called_once_with(timeout=3)

    def test_process_exit_race_still_waits(self):
        p=Mock()
        p.poll.return_value=None
        p.terminate.side_effect=ProcessLookupError()
        stop_fixture_server(p,Mock())
        p.wait.assert_called_once_with(timeout=3)
