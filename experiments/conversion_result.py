# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping conversion receipt contract, no files or processes."""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from types import MappingProxyType
from gateway import FixtureGateway, Refused
from capabilities import CapabilityCall

FORMATS = MappingProxyType({("ffmpeg","wav_to_flac"): "flac", ("libreoffice","text_to_pdf"): "pdf"})
MAX_OUTPUT = 1024 * 1024

@dataclass(frozen=True)
class ConversionResult:
    request_target: str
    source_digest: str
    output: str
    format: str
    data: bytes

    def checked(self, call, request):
        args = dict(call.args)
        for value in (self.request_target,self.source_digest,self.output,self.format):
            if type(value) is not str:
                raise Refused("invalid_conversion_identity")
        if (self.request_target != request.target or self.source_digest != args["digest"]
                or self.output != args["output"]
                or self.format != FORMATS[(call.adapter,call.operation)]):
            raise Refused("conversion_provenance_mismatch")
        if type(self.data) is not bytes or not 0 < len(self.data) <= MAX_OUTPUT:
            raise Refused("conversion_output_limit")
        # Narrow signature check, not a media/document parser or format proof.
        signature = b"fLaC" if self.format == "flac" else b"%PDF-"
        if not self.data.startswith(signature) or len(self.data) <= len(signature):
            raise Refused("conversion_signature_mismatch")
        return sha256(self.data).hexdigest()

class ConversionFixture:
    def __init__(self,gateway,grant,context,call):
        if (type(gateway) is not FixtureGateway or type(call) is not CapabilityCall
                or (call.adapter,call.operation) not in FORMATS):
            raise Refused("unsupported_conversion")
        self._g,self._grant,self._call = gateway,grant,call
        self._request = call.request(context)
        gateway.claim(grant,self._request)
        self._lock = Lock()
        self._state = "ready"
        self._result = None
        self._published = False

    def complete(self,result):
        with self._lock:
            if self._state != "ready":
                raise Refused("terminal_call")
            try:
                self._g.checkpoint(self._grant,self._request)
                if type(result) is not ConversionResult:
                    raise Refused("invalid_conversion_result")
                digest = result.checked(self._call,self._request)
                self._g.checkpoint(self._grant,self._request)
                self._result = (result,digest)
                self._state = "completed"
            except Refused:
                self._result = None
                self._state = "held"
                raise

    def result(self):
        with self._lock:
            if self._state != "completed":
                raise Refused("no_completed_result")
            try:
                self._g.checkpoint(self._grant,self._request)
            except Refused:
                self._result = None
                self._state = "held"
                raise
            result,digest = self._result
            return ("SIMULATED_ONLY",self._call.adapter,self._call.version,result,digest)

    def _publication_call(self, version, destination):
        if self._state != "completed" or self._published:
            raise Refused("conversion_not_publishable")
        self._g.checkpoint(self._grant,self._request)
        result,digest = self._result
        args = dict(self._call.args)
        # Separate logical destination from the conversion's staging handle.
        # A real store must still resolve and pin actual filesystem identity.
        if destination in (args["artifact"],args["output"]):
            raise Refused("publication_destination_conflict")
        return CapabilityCall("artifact_store",version,"publish_conversion",
                              (("artifact",result.output),("digest",result.source_digest),
                               ("result_digest",digest),("output",destination))).checked()

    def prepare_publication(self,version,destination):
        with self._lock:
            return self._publication_call(version,destination)

    def simulate_publication(self,version,destination,grant):
        with self._lock:
            call = self._publication_call(version,destination)
            request = call.request(self._request.context)
            self._g.claim(grant,request)
            self._g.checkpoint(self._grant,self._request)
            self._g.checkpoint(grant,request)
            self._published = True
            return ("SIMULATED_ONLY",call.adapter,call.operation,request.target)

    def cancel(self):
        with self._lock:
            self._result = None
            self._state = "cancelled"
            self._g.revoke(self._grant.identity)
