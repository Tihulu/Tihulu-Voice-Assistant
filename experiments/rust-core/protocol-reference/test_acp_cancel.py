# SPDX-License-Identifier: AGPL-3.0-only
import json
import unittest

from acp_cancel import MAX_PERMISSION_REPLIES, Conversation, Decision
from acp_json import Refused
from test_acp_permission import request, wire
from test_acp_json import result, update


def ready():
    source = object()
    c = Conversation(source)
    c.receive(source, result(0, {"protocolVersion": 1}))
    c.expect_new_session(source)
    c.receive(source, result(1, {"sessionId": "sess_1"}))
    c.prompt_sent(source)
    return c, source


class CancellationReference(unittest.TestCase):
    def test_permission_yields_only_cancel_and_exact_reply(self):
        c, s = ready()
        decision = c.receive(s, wire(request()))
        self.assertEqual(decision.event, "hold")
        self.assertEqual(c.phase, "cancelling")
        self.assertEqual(tuple(map(json.loads, decision.outbound)), (
            {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "sess_1"}},
            {"jsonrpc": "2.0", "id": 5, "result": {"outcome": {"outcome": "cancelled"}}},
        ))
        self.assertTrue(all(b.endswith(b"\n") for b in decision.outbound))
        self.assertEqual(c.receive(s, update("late")), Decision("discard"))
        self.assertEqual(c.receive(s, result(2, {"stopReason": "cancelled"})),
                         Decision("reported_failure"))
        self.assertEqual(c.eof(s), Decision("transport_closed"))

    def test_success_after_cancel_never_publishes(self):
        c, s = ready()
        c.cancel(s)
        with self.assertRaises(Refused):
            c.receive(s, result(2, {"stopReason": "end_turn"}))
        self.assertEqual(c.phase, "closed")

    def test_duplicate_permission_cannot_generate_second_reply(self):
        c, s = ready()
        c.receive(s, wire(request()))
        with self.assertRaises(Refused):
            c.receive(s, wire(request()))
        self.assertEqual(c.phase, "closed")

    def test_distinct_pending_requests_after_cancel_get_one_cancelled_reply(self):
        c, s = ready()
        first = c.receive(s, wire(request()))
        self.assertEqual(len(first.outbound), 2)
        second = request()
        second["id"] = 6
        decision = c.receive(s, wire(second))
        self.assertEqual(tuple(map(json.loads, decision.outbound)), (
            {"jsonrpc": "2.0", "id": 6, "result": {"outcome": {"outcome": "cancelled"}}},
        ))
        self.assertEqual(c.phase, "cancelling")

    def test_permission_after_owner_cancel_does_not_repeat_cancel_notification(self):
        c, s = ready()
        self.assertEqual(len(c.cancel(s).outbound), 1)
        decision = c.receive(s, wire(request()))
        self.assertEqual(len(decision.outbound), 1)
        self.assertEqual(json.loads(decision.outbound[0])["id"], 5)

    def test_permission_reply_budget_never_evicts_tombstones(self):
        c, s = ready()
        for ident in range(MAX_PERMISSION_REPLIES):
            data = request()
            data["id"] = ident
            c.receive(s, wire(data))
        data = request()
        data["id"] = MAX_PERMISSION_REPLIES
        with self.assertRaises(Refused):
            c.receive(s, wire(data))
        self.assertEqual(c.phase, "closed")

    def test_foreign_source_does_not_mutate_state(self):
        c, s = ready()
        with self.assertRaises(Refused):
            c.receive(object(), wire(request()))
        self.assertEqual(c.phase, "prompting")
        self.assertEqual(c.receive(s, update("ok")), Decision("progress"))

    def test_wrong_session_closes_without_output(self):
        c, s = ready()
        data = request()
        data["params"]["sessionId"] = "other"
        with self.assertRaises(Refused):
            c.receive(s, wire(data))
        self.assertEqual(c.phase, "closed")

    def test_no_early_permission_or_repeat_prompt(self):
        s = object()
        c = Conversation(s)
        with self.assertRaises(Refused):
            c.receive(s, wire(request()))
        c, s = ready()
        c.receive(s, result(2, {"stopReason": "end_turn"}))
        with self.assertRaises(Refused):
            c.prompt_sent(s)
        self.assertEqual(c.phase, "closed")

    def test_version_auth_and_order_fail_closed(self):
        for value in ({"protocolVersion": 2}, {"protocolVersion": 1, "authMethods": [{}]}):
            s = object()
            c = Conversation(s)
            with self.assertRaises(Refused):
                c.receive(s, result(0, value))
            self.assertEqual(c.phase, "closed")
        s = object()
        c = Conversation(s)
        with self.assertRaises(Refused):
            c.receive(s, result(1, {"sessionId": "s"}))

    def test_eof_is_not_completion_or_resume(self):
        c, s = ready()
        self.assertEqual(c.eof(s), Decision("transport_closed"))
        with self.assertRaises(Refused):
            c.receive(s, result(2, {"stopReason": "end_turn"}))

    def test_normal_end_turn_is_only_report(self):
        c, s = ready()
        self.assertEqual(c.receive(s, result(2, {"stopReason": "end_turn"})),
                         Decision("reported_done"))
        self.assertEqual(c.phase, "terminal")


if __name__ == "__main__":
    unittest.main()
