# SPDX-License-Identifier: AGPL-3.0-only
"""Owner-approved non-shipping cross-motor experiment.

No network, process, credentials, shell, model inference or filesystem action.
Only trusted synthetic inputs and the existing deterministic R1 engine.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from gateway import Request, Refused
from research import canonical
from research_session import ResearchSession
from inference import choose, envelope
from adapter_protocol import approved_payload, disclosure_target
from scheduler import Scheduler, Job
from task_chain import Chain, Step, Artifact, step_target

REPORT_STEP = Step("temperature-report", "temperature", "csv", "report")
from tihulu_jarvis.temperature_engine import analyze_temperature, render_svg
from tihulu_jarvis.voice.m1 import Hold


@dataclass(frozen=True)
class Report:
    source: str
    source_digest: str
    model: str
    table: bytes
    svg: bytes
    disclosure: bytes
    table_digest: str
    chart_digest: str
    report_digest: str


class FixtureWorkflow:
    """Single-use, bounded workflow with exact input and separate scoped grants.

    The model selection/lease is simulated. No model is loaded; a deterministic
    local engine computes the result. Returned bytes are not a real egress effect.
    """
    def __init__(self, gateway, context, inventory, candidates):
        self._g, self._ctx = gateway, context
        self._inventory, self._candidates = inventory, candidates
        self._state = "ready"
        self._report = None

    @property
    def state(self):
        return self._state

    def result(self):
        if self._state != "completed" or self._report is None:
            raise Refused("no_current_result")
        try:
            for grant, req in self._checks:
                self._g.checkpoint(grant, req)
        except Refused:
            self._report = None
            self._state = "held"
            raise
        return self._report

    def run(self, *, url, root, body, addresses, peer, headers, budget,
            destination, grants):
        if self._state != "ready":
            raise Refused("workflow_not_ready")
        if type(grants) is not dict or set(grants) != {"research","engine","agent","disclose","payload","publish"}:
            raise Refused("invalid_grant_set")
        self._state = "running"
        session = None
        scheduler = None
        lease = None
        chain = None
        try:
            if type(body) is not bytes or not 0 < len(body) <= 65536:
                raise Refused("invalid_workflow_body")
            if type(budget) is not int or budget < 1:
                raise Refused("invalid_workflow_budget")
            target = canonical(url).identity
            digest = sha256(body).hexdigest()
            engine_target = step_target(REPORT_STEP, Artifact("csv", digest, ""))
            checks = (
                (grants["research"], Request(self._ctx,"research",target,"local","public")),
                (grants["engine"], Request(self._ctx,"engine",engine_target,"local")),
                (grants["agent"], Request(self._ctx,"agent","analysis-fixture","local")),
                (grants["disclose"], Request(self._ctx,"disclose",destination,"local","minimized")),
                (grants["publish"], Request(self._ctx,"publish","temperature-report","local")),
            )
            if self._ctx.mode != "SECURE_HYBRID":
                raise Refused("secure_hybrid_required")

            def checkpoint():
                if self._state != "running":
                    raise Refused("workflow_closed_or_cancelled")
                for grant, req in checks:
                    self._g.checkpoint(grant, req)
                if self._state != "running":
                    raise Refused("workflow_closed_or_cancelled")
                return False

            checkpoint()
            candidate = choose(self._ctx,self._candidates,"llm",budget,"local")
            lease = self._inventory.lease_candidate(candidate)
            scheduler = Scheduler(self._g,self._ctx,1,max(1,budget))
            scheduler.queue(Job("analysis","analysis-fixture",max(1,candidate.memory_mib),50),grants["agent"])
            selected = scheduler.dispatch()
            if len(selected) != 1 or selected[0][1] != "analysis":
                raise Refused("dispatch_refused")
            handle, _ = selected[0]
            session = ResearchSession(self._g,self._ctx,root)
            identity = session.collect(grants["research"],url,addresses,peer,headers,body)
            # Evidence span API is bounded to 1000 Unicode code points. The
            # first integrated slice refuses larger documents, never truncates.
            try:
                chars = len(body.decode("utf-8"))
            except UnicodeError:
                raise Refused("invalid_utf8") from None
            if not 0 < chars <= 1000:
                raise Refused("workflow_source_limit")
            source = session.cite(identity,digest,0,chars)
            data = source.quote.encode("utf-8")
            if sha256(data).hexdigest() != digest:
                raise Refused("source_digest_mismatch")
            chain = Chain(self._g, self._ctx, (REPORT_STEP,), "csv", data)
            step_handle = chain.begin(grants["engine"], chain.current)
            series = analyze_temperature(chain.input_bytes(step_handle),cancelled=checkpoint)
            if not series.minimum_c.is_integer() or not series.maximum_c.is_integer():
                raise Refused("fixture_disclosure_integer_required")
            values = {"count":series.count,"min":int(series.minimum_c),"max":int(series.maximum_c)}
            table = json.dumps({"count":series.count,"mean_c":series.mean_c,
                                "min_c":series.minimum_c,"max_c":series.maximum_c,
                                "delta_c":series.delta_c,"duration_s":series.duration_s},
                                sort_keys=True,separators=(",",":")).encode("ascii")
            svg = render_svg(series,cancelled=checkpoint)
            receipt = chain.complete(step_handle, "report", table + b"\n" + svg)
            checkpoint()
            disclosure = envelope(self._g,grants["disclose"],self._ctx,destination,values,("count",))
            # A provider-wide permission only permits preparation. Explicit
            # exact destination + canonical bytes consent is separate.
            payload = approved_payload(self._g, grants["payload"], self._ctx, disclosure)
            payload_req = Request(self._ctx,"disclose",disclosure_target(disclosure),"local","minimized")
            checks += ((grants["payload"],payload_req),)
            scheduler.finish(handle)
            checkpoint()
            self._checks = checks
            self._report = Report(source.url,digest,candidate.identity,table,svg,payload,
                                  sha256(table).hexdigest(),sha256(svg).hexdigest(),receipt.digest)
            self._state = "completed"
            return self._report
        except Exception:
            self._report = None
            if self._state != "closed":
                self._state = "held"
            raise
        finally:
            if chain is not None:
                chain.cancel()
            if session is not None:
                session.close()
            if scheduler is not None:
                scheduler.close()
            if lease is not None:
                self._inventory.release(lease)

    def close(self):
        self._report = None
        self._state = "closed"
