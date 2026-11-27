# SPDX-License-Identifier: AGPL-3.0-only
"""Fixed local tiny-model review of deterministic public data results.
No network, process launch, arbitrary text prompt or model-issued tool use.
Native envelope is trusted fixture evidence, not independent exit proof.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import current_thread
import json
from gateway import Refused,Request
from heterogeneous_data_team import DataTeamRun
from heterogeneous_data_tools import encode
from llama_engine_acceptance import MODEL,MODEL_SHA,ENGINE_REV
from local_agent_model_fixture_contract import PREFIX,validate_wire,bound_reply

def digest(data):
    return sha256(data).hexdigest()

def review_wire(payload):
    """Only bounded numeric aggregates, never raw OCR, filenames or free text."""
    if type(payload) is not bytes or not 1<=len(payload)<=16384:
        raise Refused("mixed_review_payload")
    try:
        obj=json.loads(payload,parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if encode(obj)!=payload:
            raise Refused("mixed_review_canonical")
    except (ValueError,UnicodeError,RecursionError) as exc:
        raise Refused("mixed_review_payload") from exc
    if type(obj) is not dict or set(obj)!={"kind","results"} or obj["kind"]!="fixture_report" or type(obj["results"]) is not list or len(obj["results"])!=3:
        raise Refused("mixed_review_shape")
    rows={}
    for row in obj["results"]:
        if type(row) is not dict or set(row)!={"task","digest","result"} or type(row["task"]) is not str or row["task"] not in ("csv","text","image") or row["task"] in rows or type(row["result"]) is not dict or digest(encode(row["result"]))!=row["digest"]:
            raise Refused("mixed_review_row")
        rows[row["task"]]=row["result"]
    if set(rows)!={"csv","text","image"}:
        raise Refused("mixed_review_shape")
    a,b,c=rows["csv"],rows["text"],rows["image"]
    if (a.get("kind"),b.get("kind"),c.get("kind"))!=("integer_csv_stats","text_stats","png_stats"):
        raise Refused("mixed_review_kind")
    values=(a.get("count"),a.get("sum"),b.get("words"),c.get("width"),c.get("height"))
    if any(type(x) is not int or not -256000000<=x<=256000000 for x in values):
        raise Refused("mixed_review_numbers")
    n,total,words,w,h=values
    if not 1<=n<=256 or not 1<=words<=8192 or not 1<=w<=4096 or not 1<=h<=4096:
        raise Refused("mixed_review_numbers")
    # Small stories model is not an instruction model. This checks input
    # transport and generation only, not mathematical or review accuracy.
    prompt=PREFIX["a"]+f"There were {n} numbers, total {total}, {words} words, and a {w} by {h} picture. "
    wire=json.dumps({"prompt":prompt,"n_predict":16,"temperature":0,"seed":42,
        "stream":False,"return_tokens":True,"cache_prompt":False,
        "response_fields":["content","tokens","stop","stop_type","model","truncated","tokens_evaluated"]},
        separators=(",",":"),ensure_ascii=True).encode("ascii")
    validate_wire("a",wire)
    return wire

@dataclass(frozen=True)
class ReviewReceipt:
    request_digest: str
    source_digest: str
    wire_digest: str
    output_digest: str
    generated_tokens: int
    prompt_tokens: int

class MixedModelReview:
    def __init__(self,team,context,available,models):
        if type(team) is not DataTeamRun:
            raise Refused("mixed_review_team")
        self._owner=current_thread()
        self._team=team
        self._g=team._g
        self._source=team.output("review",context,available,models)
        self._wire=review_wire(self._source[1])
        target=digest(encode(("mixed-model-review-v1",team._attempt,team._plan.digest,
                             self._source[0].request_digest,self._source[0].payload_digest,
                             self._source[0].input_digest,MODEL,ENGINE_REV,digest(self._wire))))
        self._request=Request(context,"document_engine",target,"local","private").checked()
        self._g.register_agent_attempt(target)
        self._grant=None
        self._state="READY"
        self._result=None

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("mixed_review_thread")

    def request(self):
        self._thread()
        return self._request

    def stop(self):
        self._thread()
        if self._grant is not None:
            self._g.revoke(self._grant.identity)
        self._result=None
        self._state="STOPPED"

    def _check(self,context,available,models):
        self._thread()
        try:
            if self._team.output("review",context,available,models)!=self._source:
                raise Refused("mixed_review_source_changed")
            if self._grant is not None:
                self._g.checkpoint_claimed_group(((self._grant,self._request),))
        except Refused:
            self.stop()
            raise

    def begin(self,grant,context,available,models):
        self._thread()
        if self._state!="READY":
            raise Refused("mixed_review_state")
        try:
            self._check(context,available,models)
            self._g.claim_with_dependencies(grant,self._request,tuple(self._team._claims.values()))
            self._grant=grant
            self._check(context,available,models)
            self._state="DISPATCHED"
            return self._wire
        except Refused:
            self.stop()
            raise

    def complete(self,envelope,context,available,models):
        self._thread()
        if self._state!="DISPATCHED":
            raise Refused("mixed_review_state")
        try:
            self._check(context,available,models)
            data,tokens,count=bound_reply("a",self._wire,envelope)
            self._check(context,available,models)
            self._result=(ReviewReceipt(self._request.target,self._source[0].payload_digest,
                          digest(self._wire),digest(data),len(tokens),count),data)
            self._state="COMPLETED"
        except (ValueError,RecursionError) as exc:
            self.stop()
            raise Refused("mixed_review_completion") from exc

    def output(self,context,available,models):
        self._thread()
        if self._state!="COMPLETED":
            raise Refused("mixed_review_state")
        self._check(context,available,models)
        return self._result
