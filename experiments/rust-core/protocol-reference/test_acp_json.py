# SPDX-License-Identifier: AGPL-3.0-only
import json
import unittest

from acp_json import MAX_DEPTH, MAX_LINE, MAX_TEXT, Message, Refused, decode


def wire(data):
    return json.dumps(data, ensure_ascii=True).encode()


def result(ident, value):
    return wire({"jsonrpc": "2.0", "id": ident, "result": value})


def update(text, session="sess_1"):
    return wire({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": session, "update": {"sessionUpdate": "agent_message_chunk",
                                         "content": {"type": "text", "text": text}}}})


class AcpJsonTests(unittest.TestCase):
    def test_initialization_metadata_does_not_enable_capabilities(self):
        message = decode(result(0, {"protocolVersion": 1,
                                    "agentCapabilities": {"loadSession": True},
                                    "authMethods": []}))
        self.assertEqual(message, Message("initialize", 0, version=1))
        self.assertTrue(decode(result(0, {"protocolVersion": 1,
                                          "authMethods": [{"id": "fake"}]})).auth_offered)

    def test_session_and_stop_reason_mapping(self):
        self.assertEqual(decode(result(1, {"sessionId": "sess_1"})),
                         Message("session", 1, session="sess_1"))
        for reason in ("end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"):
            self.assertEqual(decode(result(2, {"stopReason": reason})).reason, reason)
        self.assertEqual(decode(result(2, {"stopReason": "future"})).reason, "unknown")

    def test_text_metadata_only_and_utf8_byte_count(self):
        text = "Türkçe { [ \" } ]"
        self.assertEqual(decode(update(text)),
                         Message("text", session="sess_1", text_bytes=len(text.encode())))
        self.assertNotIn(text, repr(decode(update(text))))

    def test_duplicate_keys_including_escaped_and_nested(self):
        for line in (
            b'{"jsonrpc":"2.0","id":1,"id":2,"result":{"sessionId":"s"}}',
            br'{"jsonrpc":"2.0","id":1,"\u0069d":1,"result":{"sessionId":"s"}}',
            b'{"jsonrpc":"2.0","id":1,"result":{"sessionId":"a","sessionId":"b"}}',
        ):
            with self.subTest(line=line), self.assertRaises(Refused):
                decode(line)

    def test_wrong_rpc_types_and_ids(self):
        for ident in (True, False, None, "1", -1, 3, 2**64):
            with self.subTest(ident=ident), self.assertRaises(Refused):
                decode(result(ident, {"sessionId": "s"}))

    def test_no_batch_error_or_ambiguous_response(self):
        for data in (
            [], [1], {"jsonrpc": "2.0", "id": 2, "error": {}},
            {"jsonrpc": "2.0", "id": 2, "result": {"stopReason": "end_turn"}, "error": {}},
            {"jsonrpc": "1.0", "id": 2, "result": {"stopReason": "end_turn"}},
        ):
            with self.assertRaises(Refused):
                decode(wire(data))

    def test_inbound_methods_and_thought_or_tool_update_are_refused(self):
        for method in ("fs/read_text_file", "terminal/create", "session/request_permission"):
            with self.assertRaises(Refused):
                decode(wire({"jsonrpc": "2.0", "id": 8, "method": method, "params": {}}))
        for variant in ("agent_thought_chunk", "tool_call", "plan"):
            with self.assertRaises(Refused):
                decode(update("x").replace(b"agent_message_chunk", variant.encode()))

    def test_limits_depth_nodes_line_text(self):
        for line in (
            b"x" * (MAX_LINE + 1),
            b"[" * (MAX_DEPTH + 1) + b"]" * (MAX_DEPTH + 1),
            result(0, {"protocolVersion": 1, "agentCapabilities": {"x": [0] * 4097}}),
            update("x" * (MAX_TEXT + 1)),
        ):
            with self.assertRaises(Refused):
                decode(line)
        self.assertEqual(decode(update("x" * MAX_TEXT)).text_bytes, MAX_TEXT)

    def test_invalid_encoding_control_and_nonfinite_numbers(self):
        for line in (b"\xff", b"{}\n", b"\x00", br'{"x":"\ud800"}',
                     b'{"jsonrpc":"2.0","id":NaN,"result":{}}',
                     b'{"jsonrpc":"2.0","id":1.0,"result":{}}'):
            with self.assertRaises(Refused):
                decode(line)

    def test_invalid_session_or_version_and_unknown_fields(self):
        for value in ("", "/path", "x\n", "x" * 257):
            with self.assertRaises(Refused):
                decode(result(1, {"sessionId": value}))
        for version in (True, -1, 65536):
            with self.assertRaises(Refused):
                decode(result(0, {"protocolVersion": version}))
        with self.assertRaises(Refused):
            decode(result(2, {"stopReason": "end_turn", "unexpected": True}))

    def test_errors_do_not_echo_payload(self):
        with self.assertRaises(Refused) as error:
            decode(b'{"secret-canary": "private-test-only"}')
        self.assertNotIn("secret-canary", str(error.exception))


if __name__ == "__main__":
    unittest.main()
