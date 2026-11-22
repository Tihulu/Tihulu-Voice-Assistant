# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Approved-source tests: no host is contacted and no byte of a model is read."""
import hashlib
import unittest

from tihulu_jarvis import catalog_sources as cs
from tihulu_jarvis.voice.m1 import Hold

DIGEST = hashlib.sha256(b"fixture").hexdigest()


def listing(repo, pipeline, tags, size=141000000, digest=DIGEST, model_id=None):
    return {"modelId": model_id or repo, "pipeline_tag": pipeline, "tags": tags,
            "files": [{"path": "ggml-base.bin", "size": size, "lfs": {"sha256": digest}}]}


class SourceTests(unittest.TestCase):
    def test_a_public_host_with_a_kind_and_filters_is_registered(self):
        source = cs.checked({"host": "huggingface.co", "kind": "hf",
                             "filter": "whisper ggml"})
        self.assertEqual(source.search_path, "/api/models?search=whisper%20ggml&limit=20")
        self.assertEqual(cs.listing_url(source),
                         "https://huggingface.co/api/models?search=whisper%20ggml&limit=20")

    def test_a_private_host_or_unknown_kind_is_refused(self):
        for entry in ({"host": "localhost", "kind": "hf"}, {"host": "127.0.0.1", "kind": "hf"},
                      {"host": "example.local", "kind": "hf"},
                      {"host": "huggingface.co", "kind": "magnet"},
                      {"host": "", "kind": "hf"}, {"host": "huggingface.co"},
                      "huggingface.co", None, [], {"host": "huggingface.co", "kind": "hf",
                                                   "filter": "x" * 97}):
            with self.subTest(entry=entry):
                with self.assertRaises(Hold):
                    cs.checked(entry)

    def test_sources_are_replaced_not_duplicated_and_bounded(self):
        known = cs.register([], {"host": "huggingface.co", "kind": "hf", "filter": "whisper"})
        same = cs.register(known, {"host": "huggingface.co", "kind": "hf", "filter": "whisper"})
        self.assertEqual(len(same), 1)
        two = cs.register(same, {"host": "huggingface.co", "kind": "hf", "filter": "piper"})
        self.assertEqual(len(two), 2)
        with self.assertRaisesRegex(Hold, "too_many_sources"):
            cs.register([{"host": "h%02d.test" % index, "kind": "http"}
                         for index in range(cs.MAX_SOURCES)],
                        {"host": "one-more.test", "kind": "http"})
        self.assertEqual(cs.forget(two, {"host": "huggingface.co", "kind": "hf",
                                         "filter": "whisper"})[0]["filter"], "piper")
        self.assertEqual(cs.forget(two, {"host": "absent.test", "kind": "http"}), two)


class SlugTests(unittest.TestCase):
    def test_a_repository_id_is_escaped_without_a_collision(self):
        self.assertEqual(cs.slug("ggerganov/whisper.cpp"), "ggerganov:whisper.cpp")
        # The two possible collisions of a dash-based mapping stay distinct.
        self.assertNotEqual(cs.slug("a/b"), cs.slug("a-b"))
        self.assertEqual(cs.slug("plain"), "plain")
        self.assertEqual(cs.slug(None), "")


class CardTests(unittest.TestCase):
    def test_a_verifiable_listing_row_becomes_a_truthful_card(self):
        cards, refused = cs.cards(
            [listing("ggerganov/whisper.cpp", "automatic-speech-recognition",
                     ["ggml", "tr", "en", "license:mit"],
                     model_id="ggerganov/whisper.cpp")], cs.Source("huggingface.co", "hf"))
        self.assertEqual(refused, 0)
        card = cards[0]
        self.assertEqual((card.model_id, card.category, card.artifact_format,
                          card.license_id, card.provider, card.source_size),
                         ("ggerganov:whisper.cpp", "stt", "ggml", "mit",
                          "huggingface.co", 141000000))
        self.assertEqual(card.languages, ("en", "tr"))
        self.assertEqual(card.source_ref, "https://huggingface.co/ggerganov/whisper.cpp")
        self.assertTrue(card.source_checksum)

    def test_an_unverifiable_or_uncategorisable_row_is_refused(self):
        source = cs.Source("huggingface.co", "hf")
        rows = [listing("x/no-digest", "automatic-speech-recognition", [], digest=""),
                listing("y/no-size", "automatic-speech-recognition", [], size=0),
                listing("z/no-category", "text-ranking", ["ggml"]),
                listing("w/short-digest", "automatic-speech-recognition", [], digest="abc"),
                "not-a-mapping", 42]
        cards, refused = cs.cards(rows, source)
        self.assertEqual(cards, ())
        self.assertEqual(refused, len(rows))

    def test_a_payload_that_is_not_a_list_is_refused(self):
        for bad in ({}, "cards", None, 42):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    cs.cards(bad, cs.Source("huggingface.co", "hf"))

    def test_the_listing_is_bounded(self):
        rows = [listing("repo/%d" % index, "automatic-speech-recognition", ["ggml"])
                for index in range(80)]
        cards, refused = cs.cards(rows, cs.Source("huggingface.co", "hf"))
        self.assertEqual(len(cards), 50)
        self.assertGreater(refused, 0)
