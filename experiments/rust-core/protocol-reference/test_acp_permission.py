# SPDX-License-Identifier: AGPL-3.0-only
import json
import unittest

from acp_json import MAX_OPTIONS, Message, Refused, decode


def request():
    return {"jsonrpc": "2.0", "id": 5, "method": "session/request_permission",
            "params": {"sessionId": "sess_1", "toolCall": {"toolCallId": "call_1"},
                       "options": [{"optionId": "approve", "name": "Private canary",
                                    "kind": "allow_once"},
                                   {"optionId": "deny", "name": "Reject",
                                    "kind": "reject_once"}]}}


def wire(value):
    return json.dumps(value).encode()


class PermissionReference(unittest.TestCase):
    def test_permission_is_metadata_only(self):
        data = request()
        data["params"]["toolCall"]["title"] = "secret-title"
        msg = decode(wire(data))
        self.assertEqual(msg, Message("permission", 5, session="sess_1", tool_call="call_1"))
        for secret in ("secret-title", "Private canary", "approve", "deny"):
            self.assertNotIn(secret, repr(msg))

    def test_option_labels_and_kinds_never_authorize(self):
        data = request()
        for kind in ("allow_once", "allow_always", "reject_once", "reject_always"):
            data["params"]["options"] = [{"optionId": "x", "name": "Allow everything",
                                          "kind": kind}]
            self.assertEqual(decode(wire(data)).kind, "permission")

    def test_duplicate_and_excess_options_denied(self):
        data = request()
        options = data["params"]["options"]
        for bad in ([], [options[0], options[0]], [
                {"optionId": str(i), "name": "Reject", "kind": "reject_once"}
                for i in range(MAX_OPTIONS + 1)]):
            data["params"]["options"] = bad
            with self.assertRaises(Refused):
                decode(wire(data))

    def test_invalid_option_metadata(self):
        for key, value in (("name", ""), ("name", "x" * 1025),
                           ("kind", []), ("kind", "future"), ("optionId", "/path")):
            data = request()
            data["params"]["options"][0][key] = value
            with self.assertRaises(Refused):
                decode(wire(data))

    def test_invalid_request_id_and_scope(self):
        for key, value in (("id", True), ("id", -1), ("id", "5"), ("id", None)):
            data = request()
            data[key] = value
            with self.assertRaises(Refused):
                decode(wire(data))
        data = request()
        data["params"]["sessionId"] = "/path"
        with self.assertRaises(Refused):
            decode(wire(data))

    def test_no_tool_payload_or_unknown_method(self):
        data = request()
        data["params"]["toolCall"]["rawInput"] = {"secret": "canary"}
        with self.assertRaises(Refused):
            decode(wire(data))
        data = request()
        data["method"] = "terminal/create"
        with self.assertRaises(Refused):
            decode(wire(data))

    def test_escaped_duplicate_request_key_is_refused(self):
        data = wire(request()).replace(b'"id": 5', br'"id": 5, "\u0069d": 5')
        with self.assertRaises(Refused):
            decode(data)


if __name__ == "__main__":
    unittest.main()
