# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded offline knowledge index. Owner-selected synthetic text only.

No file opening, disk persistence, network, embeddings or model calls.
Text remains in this owned session; permission checks at ingest and retrieval.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
import unicodedata
from gateway import Request, Refused, token

MAX_DOCS=16
MAX_BYTES=262144
MAX_DOC=65536
MAX_HITS=24

def normalized(value,limit):
    if type(value) is not str or not 0<len(value)<=limit:
        raise Refused("invalid_text")
    if any((ord(c)<32 and c not in "\n\t") or 127<=ord(c)<=159
           or 0xd800<=ord(c)<=0xdfff or unicodedata.category(c)=="Cf" for c in value):
        raise Refused("invalid_text")
    return unicodedata.normalize("NFC",value).casefold()

def document_target(identity,data):
    token(identity)
    if type(data) is not bytes or not 0<len(data)<=MAX_DOC:
        raise Refused("invalid_document")
    return sha256(repr(("offline-document",identity,sha256(data).hexdigest())).encode("ascii")).hexdigest()

@dataclass(frozen=True)
class Hit:
    document: str
    digest: str
    line: int
    excerpt: str

class OfflineIndex:
    def __init__(self,gateway,context):
        self._g,self._ctx=gateway,context.checked()
        if context.mode=="CLOUD_CHAT":
            raise Refused("local_index_required")
        self._docs={}
        self._bytes=0
        self._closed=False
        self._lock=Lock()

    def add(self,identity,data,grant):
        target=document_target(identity,data)
        req=Request(self._ctx,"engine",target,"local","private")
        with self._lock:
            if (self._closed or identity in self._docs or len(self._docs)>=MAX_DOCS
                    or self._bytes+len(data)>MAX_BYTES):
                raise Refused("index_limit_or_closed")
            self._g.checkpoint(grant,req)
            try:
                text=data.decode("utf-8")
            except UnicodeError:
                raise Refused("invalid_utf8") from None
            normalized(text,MAX_DOC)
            lines=tuple((line,normalized(line,MAX_DOC) if line else "") for line in text.split("\n"))
            if len(lines)>4096:
                raise Refused("line_limit")
            self._g.claim(grant,req)
            self._docs[identity]=(sha256(data).hexdigest(),lines,grant,req,len(data))
            self._bytes+=len(data)

    def _prune(self):
        for identity,doc in tuple(self._docs.items()):
            try:
                self._g.checkpoint(doc[2],doc[3])
            except Refused:
                self._bytes-=doc[4]
                del self._docs[identity]

    def search(self,query):
        needle=normalized(query,128)
        if not needle.strip() or "\n" in needle or "\t" in needle:
            raise Refused("invalid_query")
        with self._lock:
            if self._closed:
                raise Refused("index_closed")
            self._prune()
            hits=[]
            for identity,(digest,lines,grant,req,_) in self._docs.items():
                for number,(original,folded) in enumerate(lines,1):
                    if needle in folded:
                        # Preserve exact original line, not casefold offsets.
                        if len(original)<=512:
                            hits.append(Hit(identity,digest,number,original))
                        if len(hits)>=MAX_HITS:
                            break
                if len(hits)>=MAX_HITS:
                    break
            self._prune()
            return tuple(h for h in hits if h.document in self._docs)

    def close(self):
        with self._lock:
            self._closed=True
            self._docs.clear()
            self._bytes=0
