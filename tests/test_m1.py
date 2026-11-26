# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
import unittest

from tihulu_jarvis.voice.m1 import (
    Hold,
    LanguageContext,
    Message,
    Voice,
    complete_turn,
    lexical_identifier,
    route_language,
    speech_text,
    valid_response,
)


VOICES = {lang: Voice("fixture-" + lang, lang) for lang in ("tr", "en", "de")}


def fake_chat(replies, requests):
    iterator = iter(replies)

    async def call(messages):
        requests.append(messages)
        return next(iterator)

    return call


class LanguageRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_language_regressions(self):
        cases = {
            "naber nasilsin": "tr",
            "Naber, nasılsın?": "tr",
            "merhaba ne yapiyorsun": "tr",
            "Evet, test kelimen mavi.": "tr",
            "Peki test kelimemi hâlâ hatırlıyor musun?": "tr",
            "hello how are you": "en",
            "What is 3 plus 8?": "en",
            "wie geht es dir": "de",
            "Was machst du?": "de",
            "Danke schön.": "de",
            "Tschüss!": "de",
            "Ja, dein Testwort war mavi.": "de",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    lexical_identifier(text).language, expected
                )

    def test_ambiguity_and_technical_names(self):
        context = LanguageContext("tr", "tr")
        decision = route_language("OpenCode Python GitHub Whisper", context)
        self.assertIsNone(decision.evidence.language)
        self.assertEqual(decision.response_language, "tr")
        with self.assertRaises(Hold):
            route_language("OpenCode", LanguageContext())

    def test_quoted_foreign_text_is_not_language_evidence(self):
        evidence = lexical_identifier('What does "naber nasilsin" mean?')
        self.assertEqual(evidence.language, "en")
        decision = route_language(
            'What does "naber nasilsin" mean?', LanguageContext("tr", "tr")
        )
        self.assertEqual(decision.evidence.language, "en")
        self.assertEqual(decision.response_language, "en")

    def test_technical_reply_is_not_rejected(self):
        self.assertTrue(
            valid_response(
                "OpenCode uses Python and GitHub.", "en", lexical_identifier
            )
        )

    def test_explicit_requests_are_separate_from_input_language(self):
        decision = route_language("Please respond in German.", LanguageContext())
        self.assertEqual(decision.evidence.language, "en")
        self.assertEqual(decision.response_language, "de")
        for text, expected in (
            ("Lutfen Ingilizce cevap ver.", "en"),
            ("Bitte antworte auf Türkisch.", "tr"),
            ("Bundan sonra türkçe konuş.", "tr"),
        ):
            self.assertEqual(
                route_language(text, LanguageContext()).response_language,
                expected,
            )

    def test_quoted_and_negated_instructions_do_not_switch_language(self):
        quoted = route_language(
            'What does "respond in German" mean?', LanguageContext("tr", "tr")
        )
        self.assertEqual(quoted.evidence.language, "en")
        self.assertEqual(quoted.response_language, "en")
        negated = route_language(
            "Do not respond in German.", LanguageContext("tr", "tr")
        )
        self.assertIsNone(negated.evidence.language)
        self.assertEqual(negated.response_language, "tr")

    def test_persistent_request_and_reset(self):
        first = route_language("From now on, respond in German.", LanguageContext())
        second = route_language("hello how are you", first.next_context)
        self.assertEqual(second.evidence.language, "en")
        self.assertEqual(second.response_language, "de")
        reset = route_language("match my language", second.next_context)
        self.assertEqual(reset.response_language, "en")
        self.assertIsNone(reset.next_context.persistent_response_language)

    def test_unrepresentable_user_input_is_refused(self):
        for text in ("hello\x00world", "hello\ud800"):
            with self.subTest(text=repr(text)), self.assertRaisesRegex(Hold, "invalid_input"):
                route_language(text, LanguageContext())

    def test_empty_and_conflicting_input(self):
        for text in ("", " \n ", "..."):
            with self.assertRaises(Hold):
                route_language(text, LanguageContext("tr", "tr"))
        with self.assertRaises(Hold):
            route_language("Speak English. Speak German.", LanguageContext())


class SpeechNormalizationTests(unittest.TestCase):
    def test_speech_normalization_preserves_content(self):
        original = "# Result\nYour word is **mavi**. 😊"
        self.assertEqual(speech_text(original), "Result\nYour word is mavi.")
        self.assertIn("**mavi**", original)
        self.assertEqual(
            speech_text("Value: **3.14**; use `foo_bar` with C++."),
            "Value: 3.14; use foo_bar with C++.",
        )
        self.assertEqual(
            speech_text("[OpenCode](https://example.test/docs)"), "OpenCode"
        )

    def test_unrepresentable_response_is_refused(self):
        for text in ("hello\x00world", "hello\ud800"):
            with self.subTest(text=repr(text)), self.assertRaisesRegex(Hold, "invalid_response_text"):
                speech_text(text)
            self.assertFalse(valid_response(text, "en", lexical_identifier))

    def test_unsupported_speech_is_not_silently_corrupted(self):
        for text in (
            "I ❤️ you.",
            "2 * 3",
            "```python\nx = 1\n```",
            "<think>private draft</think>Hello.",
        ):
            with self.assertRaises(Hold):
                speech_text(text)


class ResponseValidationTests(unittest.TestCase):
    def test_short_informal_reply_is_not_rejected(self):
        self.assertTrue(valid_response("Yeah.", "en", lexical_identifier))
        self.assertTrue(valid_response("Merhaba.", "tr", lexical_identifier))

    def test_numeric_reply_is_language_neutral(self):
        self.assertTrue(valid_response("3 + 8 = 11", "tr", lexical_identifier))

    def test_wrong_language_reply_is_rejected(self):
        self.assertFalse(
            valid_response("Merhaba nasilsin.", "en", lexical_identifier)
        )
        self.assertFalse(
            valid_response("Your word was mavi.", "de", lexical_identifier)
        )

    def test_mixed_language_reply_is_rejected(self):
        self.assertFalse(
            valid_response(
                "Your word was mavi. Bunu hatirla.", "en", lexical_identifier
            )
        )

    def test_repeated_long_sentence_is_rejected(self):
        long_sentence = "your test word was mavi in the list"
        self.assertFalse(
            valid_response(
                long_sentence + ". " + long_sentence + ".",
                "en",
                lexical_identifier,
            )
        )


class TurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_retry_without_history_mutation(self):
        history = (Message("user", "Remember mavi."),)
        requests = []
        result = await complete_turn(
            "Hello! What was my test word?",
            LanguageContext("tr", "tr"),
            history,
            fake_chat(
                [
                    "Evet, test kelimen mavi.",
                    "Your test word was **mavi**.",
                ],
                requests,
            ),
            VOICES,
        )
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0][-1], requests[1][-1])
        self.assertEqual(history, (Message("user", "Remember mavi."),))
        self.assertEqual(result.speech.language, "en")
        self.assertEqual(result.speech.voice_id, "fixture-en")
        self.assertNotIn("*", result.speech.text)
        self.assertEqual(len(result.history), len(history) + 2)

    async def test_numeric_answer_completes_turn(self):
        requests = []
        result = await complete_turn(
            "What is 3 plus 8?",
            LanguageContext(),
            (),
            fake_chat(["3 + 8 = 11"], requests),
            VOICES,
        )
        self.assertEqual(result.speech.text, "3 + 8 = 11")
        self.assertEqual(result.speech.language, "en")
        self.assertEqual(len(requests), 1)

    async def test_wrong_response_never_becomes_an_accepted_turn(self):
        requests = []
        with self.assertRaises(Hold):
            await complete_turn(
                "hello how are you",
                LanguageContext(),
                (),
                fake_chat(["Merhaba nasilsin."] * 2, requests),
                VOICES,
            )
        self.assertEqual(len(requests), 2)

    async def test_missing_voice_blocks_before_inference(self):
        requests = []
        with self.assertRaises(Hold):
            await complete_turn(
                "naber nasilsin",
                LanguageContext(),
                (),
                fake_chat([], requests),
                {"tr": Voice("wrong", "en")},
            )
        self.assertEqual(requests, [])

    async def test_cancelled_response_is_discarded(self):
        stopped = False

        async def chat(messages):
            nonlocal stopped
            stopped = True
            return "Hello, how are you?"

        with self.assertRaises(Hold):
            await complete_turn(
                "hello how are you",
                LanguageContext(),
                (),
                chat,
                VOICES,
                cancelled=lambda: stopped,
            )

    async def test_four_turn_history_and_routing_contract(self):
        inputs = [
            "Merhaba, test kelimem mavi. Bunu hatırla.",
            "Hello! What was my test word?",
            "Grüß dich! Erinnerst du dich an mein Testwort?",
            "Peki test kelimemi hâlâ hatırlıyor musun?",
        ]
        replies = [
            "Merhaba, test kelimen mavi.",
            "Your test word was mavi.",
            "Ja, dein Testwort war mavi.",
            "Evet, test kelimen mavi.",
        ]
        requests, history, context = [], (), LanguageContext()
        chat = fake_chat(replies, requests)
        for index, (text, lang) in enumerate(
            zip(inputs, ("tr", "en", "de", "tr"))
        ):
            result = await complete_turn(
                text, context, history, chat, VOICES
            )
            self.assertEqual(result.speech.language, lang)
            self.assertEqual(len(result.history), 2 * (index + 1))
            history, context = result.history, result.context
        self.assertEqual(requests[-1][1:-1], history[:-2])


if __name__ == "__main__":
    unittest.main()
