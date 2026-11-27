# SPDX-License-Identifier: AGPL-3.0-only
"""Closed in-memory public-data tool fixture. No shell, paths, plugins or model I/O."""
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
import json
import re
from gateway import Refused
from agent_custom_profiles import ident
from vision_image_input import decode_png,MAX_FILE

# Exactly declared interfaces; no automatic fallbacks.
OPERATIONS=MappingProxyType({"read_text":"file_read","csv_stats":"typed_engine",
                             "png_stats":"typed_engine","report":"reasoning"})
MAX_RESULT=16384
INTEGER=re.compile(r"-?(?:0|[1-9][0-9]{0,6})\Z")

def encode(value):
    data=json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode("ascii")
    if len(data)>MAX_RESULT:
        raise Refused("team_tool_result_size")
    return data

@dataclass(frozen=True)
class DataCall:
    task: str
    operation: str
    data: bytes

    def checked(self):
        ident(self.task)
        if type(self.operation) is not str or self.operation not in OPERATIONS or type(self.data) is not bytes:
            raise Refused("team_tool_call")
        limit=MAX_FILE if self.operation=="png_stats" else 8192
        if self.operation=="report":
            if self.data:
                raise Refused("team_tool_report_input")
        elif not 1<=len(self.data)<=limit:
            raise Refused("team_tool_input_size")
        return self

    def digest(self):
        self.checked()
        return sha256(encode((self.task,self.operation,sha256(self.data).hexdigest()))).hexdigest()

def _text(data):
    try:
        s=data.decode("utf-8")
    except UnicodeError as exc:
        raise Refused("team_tool_text") from exc
    if not s.strip() or any(ord(c)<32 and c not in "\n\t" or 127<=ord(c)<=159 for c in s):
        raise Refused("team_tool_text")
    return s

def evaluate(call,parents=()):
    """Deterministic real data computation, not LLM reasoning or file access."""
    if type(call) is not DataCall or type(parents) is not tuple:
        raise Refused("team_tool_call")
    call.checked()
    if call.operation=="report":
        if not 1<=len(parents)<=9:
            raise Refused("team_tool_parent")
        rows=[]
        seen=set()
        for pair in parents:
            if type(pair) is not tuple or len(pair)!=2:
                raise Refused("team_tool_parent")
            name,payload=pair
            ident(name)
            if name in seen or type(payload) is not bytes or not 1<=len(payload)<=MAX_RESULT:
                raise Refused("team_tool_parent")
            try:
                value=json.loads(payload,parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
                canonical=encode(value)
            except (ValueError,UnicodeError,RecursionError) as exc:
                raise Refused("team_tool_parent") from exc
            if type(value) is not dict or canonical!=payload:
                raise Refused("team_tool_parent")
            rows.append({"task":name,"digest":sha256(payload).hexdigest(),"result":value})
            seen.add(name)
        return encode({"kind":"fixture_report","results":sorted(rows,key=lambda r:r["task"])})
    if parents:
        raise Refused("team_tool_unexpected_parent")
    if call.operation=="read_text":
        text=_text(call.data)
        return encode({"kind":"text_stats","characters":len(text),"lines":len(text.splitlines()),
                       "words":len(text.split()),"source_digest":sha256(call.data).hexdigest()})
    if call.operation=="csv_stats":
        rows=_text(call.data).splitlines()
        if not 2<=len(rows)<=257 or rows[0]!="value" or any(not INTEGER.fullmatch(r) for r in rows[1:]):
            raise Refused("team_tool_csv_schema")
        values=[int(r) for r in rows[1:]]
        if any(abs(v)>1000000 for v in values):
            raise Refused("team_tool_csv_range")
        return encode({"kind":"integer_csv_stats","count":len(values),"min":min(values),"max":max(values),
                       "sum":sum(values),"source_digest":sha256(call.data).hexdigest()})
    raster=decode_png(call.data)
    return encode({"kind":"png_stats","width":raster.width,"height":raster.height,"channels":raster.channels,
                   "pixel_digest":raster.pixel_digest,"source_digest":raster.source_digest})
