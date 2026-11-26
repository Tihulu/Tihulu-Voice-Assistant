# SPDX-License-Identifier: AGPL-3.0-only
import json
import unittest
from llama_engine_acceptance import MODEL,parse_reply

class LlamaReplyTests(unittest.TestCase):
    def payload(self,**changes):
        obj={"content":" a small bird","tokens":[1,2],"stop":True,
             "stop_type":"limit","model":MODEL,"truncated":False}
        obj.update(changes)
        return json.dumps(obj).encode()
    def test_closed_native_response(self):
        self.assertEqual(parse_reply(self.payload()),(" a small bird",(1,2)))
    def test_wrong_model_or_partial_result_denied(self):
        for value in (self.payload(model="other"),self.payload(stop=False),self.payload(truncated=True),
                      self.payload(stop_type="none"),self.payload(tokens=[True]),self.payload(tokens=[])):
            with self.assertRaises(ValueError):
                parse_reply(value)
    def test_extra_tool_fields_duplicate_and_oversize_denied(self):
        for value in (self.payload(tool_calls=[]),self.payload()+b"x",b"x"*32769,
                      self.payload().replace(b'"stop": true',b'"stop": false,"stop": true')):
            with self.assertRaises(ValueError):
                parse_reply(value)
