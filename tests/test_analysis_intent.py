# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from tihulu_jarvis.analysis_intent import direct, explicit_task, parse_proposal, propose, Intent
from tihulu_jarvis.voice.m1 import Hold


class IntentTests(unittest.TestCase):
    def test_direct_multilingual(self):
        cases = {
            "Bu dosyayı analiz et.": Intent("analyze", "tr"),
            "Analyze this file and create a chart": Intent("analyze", "en"),
            "Analysiere diese CSV Datei": Intent("analyze", "de"),
            "Grafiği yeşil yap": Intent("recolor", "tr", "green"),
            "Change the chart to orange": Intent("recolor", "en", "orange"),
            "Färbe das Diagramm blau": Intent("recolor", "de", "blue"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(direct(text), expected)

    def test_not_accidental_chat_or_compound_commands(self):
        for text in ('"Analyze this file"', "Analyze this file?",
                     "Explain 'analyze this file'", "Analyze this file and delete it",
                     "Grafiği yeşil yap sonra sil", "Analyze /private/example.csv"):
            self.assertIsNone(direct(text), text)

    def test_explicit_prefix(self):
        self.assertEqual(explicit_task("Görev: sıcaklığı çiz"), ("tr", "sıcaklığı çiz"))
        self.assertEqual(explicit_task("Task: plot it"), ("en", "plot it"))
        self.assertIsNone(explicit_task("What is a task: example?"))

    def test_valid_proposals(self):
        self.assertEqual(parse_proposal('{"action":"analyze"}', "en"), Intent("analyze", "en"))
        self.assertEqual(parse_proposal('{"action":"recolor","color":"green"}', "tr"),
                         Intent("recolor", "tr", "green"))

    def test_untrusted_proposals_rejected(self):
        for raw in ('{"action":"analyze","path":"/private"}',
                    '{"action":"recolor","artifact_id":"other","color":"blue"}',
                    '{"action":"shell"}', '{"action":"recolor","color":["blue"]}',
                    '{"action":"recolor","color":"red"}',
                    '{"action":"analyze","action":"recolor"}',
                    '["analyze"]', '<think>analysis</think>{"action":"analyze"}',
                    'x' * 257, None):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(Hold, "analysis_invalid_proposal"):
                    parse_proposal(raw, "en")

    def test_unsupported(self):
        with self.assertRaisesRegex(Hold, "analysis_unsupported_task"):
            parse_proposal('{"action":"unsupported"}', "de")


class JanProposalTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_explicit_request_sent(self):
        requests = []
        async def jan(messages):
            requests.append(messages)
            return '{"action":"recolor","color":"green"}'
        result = await propose("Task: please use green for the plot", jan)
        self.assertEqual(result, Intent("recolor", "en", "green"))
        self.assertEqual(len(requests[0]), 2)
        self.assertEqual(requests[0][1].content, "please use green for the plot")

    async def test_unprefixed_empty_and_oversized_never_sent(self):
        async def jan(messages):
            self.fail("unexpected request")
        for text in ("Analyze this file", "Task:", "Task: " + "x" * 513):
            with self.assertRaises(Hold):
                await propose(text, jan)
