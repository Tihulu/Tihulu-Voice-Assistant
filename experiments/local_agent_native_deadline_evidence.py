# SPDX-License-Identifier: AGPL-3.0-only
"""Actual fixed CPU inference with a deliberately late owner observation.

Uses a real monotonic clock. Delays publication, not model execution. This
does not prove that an OS timer can forcibly terminate a running provider.
"""
from hashlib import sha256
from time import monotonic_ns, sleep
import subprocess

from gateway import Context,FixtureGateway,Refused
from inference import Candidate
from tihulu_jarvis import profiles
from local_agent_planner import Worker
from local_agent_replan import AttemptCoordinator
from local_agent_resources import ResourceLedger
from local_agent_retry_budget_session import RetryBudgetSession
from local_agent_model_fixture_contract import bound_reply
from native_fixture_pid_observation import private_process_snapshot,namespace_drained
from llama_engine_acceptance import MODEL,ENGINE_REV


def run_deadline(record,host_pid_namespace):
    ctx=Context("native-deadline","session","profile",1,"local","LOCAL_ONLY")
    gate=FixtureGateway(ctx,(profiles.Rule("host_shell",(),"allow"),),clock=profiles.PolicyClock())
    models=(Candidate(MODEL,"llm","local","llama-"+ENGINE_REV,True,True,16,80),)
    workers=(Worker("plan",(),0,"plan"),Worker("a",("plan",),0,"review"),
             Worker("b",("plan",),0,"review"),Worker("review",("a","b"),0,"review"))
    memory=ResourceLedger(32)
    coordinator=AttemptCoordinator(gate,memory,"native-deadline",workers,32,2,owned_drain=True,strict_results=True)
    # Plenty of session time for a normal fixture; the worker budget
    # deliberately expires before the owner consumes the actual result.
    session=RetryBudgetSession(coordinator,80,prompt_total=600,total_ns=90_000_000_000,worker_ns=2_000_000_000)
    baseline=private_process_snapshot(host_pid_namespace)
    proc=None
    try:
        offer=session.prepare(ctx,models,"standard")
        run=session.confirm(offer,ctx,models)
        (lease,wire),=session.reserve_wave(offer,("plan",),ctx,models)
        grant=gate.fixture_grant("deadline-plan",run.request("plan"),60)
        handle=run.start("plan",grant,ctx,models)
        start=monotonic_ns()
        session.dispatch(lease,wire,ctx,models)
        remaining=session.remaining_ns(lease)
        record("deadline-native-dispatch-with-live-budget",0<remaining<=2_000_000_000)
        proc=subprocess.Popen(["/usr/bin/python3","-I","/work/runner.py","--worker","plan","8080","normal"],
                              stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,close_fds=True)
        envelope=proc.communicate(input=wire,timeout=35)[0]
        if proc.returncode!=0:
            raise RuntimeError("native_deadline_wrapper_exit")
        data,tokens,prompt=bound_reply("plan",wire,envelope)
        record("deadline-native-real-output",bool(data) and len(tokens)==16 and prompt==14,
               generated_tokens=len(tokens),prompt_tokens=prompt)
        record("deadline-native-pid-baseline",namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
        # Delay observation until the real deadline, if inference was faster.
        # A bounded wait is intentionally part of this negative test.
        delay=max(0, start+2_100_000_000-monotonic_ns())
        if delay:
            sleep(delay/1_000_000_000)
        try:
            session.poll_deadline()
        except Refused as exc:
            denied=str(exc)=="retry_deadline_worker_expired"
        else:
            denied=False
        record("deadline-native-expired-retains-reservations",denied and session.snapshot()==(80,0,16,64)
               and session.prompt_snapshot()==(600,0,112,488) and memory.snapshot()==(32,16,16))
        generated=session.settle_reaped(lease,envelope)
        record("deadline-native-late-measured-charge",generated==16 and session.snapshot()==(80,16,0,64)
               and session.prompt_snapshot()==(600,14,0,586))
        outcome=run.observe_exit(handle,proc.returncode,ctx,models,payload=data)
        record("deadline-native-drained",outcome=="DRAINED" and run.drained() and memory.snapshot()==(32,0,32))
        try:
            run.output("plan",ctx,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("deadline-native-no-late-publication",denied)
        retry=session.prepare(ctx,models,"standard")
        record("deadline-native-retry-retains-charge",retry.remaining==64 and retry.prompt_remaining==586
               and retry.attempt!=offer.attempt and namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
    finally:
        session.close()
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
    return 0
