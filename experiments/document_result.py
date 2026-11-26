# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded, synthetic OCR/PDF text-result contract; no parser or engine I/O."""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
import json

from gateway import FixtureGateway, Refused
from capabilities import CapabilityCall
from adapter_protocol import text_bytes

MAX_BLOCKS = 256
MAX_RESULT_BYTES = 16384
# Integer normalized coordinates avoid NaN/float ambiguity.
COORD_LIMIT = 10000


@dataclass(frozen=True)
class TextBlock:
    text: str
    box: tuple
    confidence: int | None = None

    def checked(self, *, ocr):
        data = text_bytes(self.text)
        if (type(self.box) is not tuple or len(self.box) != 4
                or any(type(v) is not int or not 0 <= v <= COORD_LIMIT for v in self.box)):
            raise Refused("invalid_block_box")
        left, top, right, bottom = self.box
        if left >= right or top >= bottom:
            raise Refused("invalid_block_box")
        if ocr:
            if type(self.confidence) is not int or not 0 <= self.confidence <= 100:
                raise Refused("invalid_confidence")
        elif self.confidence is not None:
            raise Refused("unexpected_confidence")
        return len(data)


@dataclass(frozen=True)
class DocumentResult:
    call_target: str
    source_digest: str
    page: int
    blocks: tuple

    def checked(self, call, request):
        args = dict(call.args)
        if (self.call_target != request.target or self.source_digest != args["digest"]
                or type(self.page) is not int or self.page != args.get("page", 1)):
            raise Refused("result_provenance_mismatch")
        if type(self.blocks) is not tuple or not 1 <= len(self.blocks) <= MAX_BLOCKS:
            raise Refused("invalid_blocks")
        total = 0
        for block in self.blocks:
            if type(block) is not TextBlock:
                raise Refused("invalid_block")
            total += block.checked(ocr=call.operation == "ocr_image")
            if total > MAX_RESULT_BYTES:
                raise Refused("output_limit")
        # Include identity/geometry/confidence in the output digest, not just text.
        encoded = json.dumps(
            (request.target, self.source_digest, self.page,
             tuple((b.text, b.box, b.confidence) for b in self.blocks)),
            ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_RESULT_BYTES:
            raise Refused("output_limit")
        return sha256(encoded).hexdigest()


class DocumentFixture:
    """One synthetic result for an exact authorized call; no arbitrary callbacks."""
    def __init__(self, gateway, grant, context, call):
        if (type(gateway) is not FixtureGateway or type(call) is not CapabilityCall
                or call.operation not in ("ocr_image", "pdf_extract")):
            raise Refused("unsupported_document_text_fixture")
        self._gateway, self._grant, self._call = gateway, grant, call
        self._request = call.request(context)
        gateway.claim(grant, self._request)
        self._lock = Lock()
        self._state = "ready"
        self._result = None

    def complete(self, result):
        with self._lock:
            if self._state != "ready":
                raise Refused("terminal_call")
            try:
                self._gateway.checkpoint(self._grant, self._request)
                if type(result) is not DocumentResult:
                    raise Refused("invalid_document_result")
                digest = result.checked(self._call, self._request)
                self._gateway.checkpoint(self._grant, self._request)
                self._result = (result, digest)
                self._state = "completed"
            except Refused:
                self._state = "held"
                self._result = None
                raise

    def result(self):
        with self._lock:
            if self._state != "completed":
                raise Refused("no_completed_result")
            try:
                self._gateway.checkpoint(self._grant, self._request)
            except Refused:
                self._state = "held"
                self._result = None
                raise
            result, digest = self._result
            return ("SIMULATED_ONLY", self._call.adapter, self._call.version, result, digest)

    def cancel(self):
        with self._lock:
            self._result = None
            self._state = "cancelled"
            self._gateway.revoke(self._grant.identity)
