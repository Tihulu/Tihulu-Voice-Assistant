# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable fixed-model retry evidence; no host execution or real provider switch."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
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


def run_retry(record,host_pid_namespace,scenario="retry-cumulative"):
    if scenario not in ("retry-cumulative","retry-dual","retry-prompt-hold","retry-wave-prompt","retry-wave-prompt-hold","retry-vector-cpu","retry-vector-hold"):
        raise RuntimeError("native_retry_scenario")
    dual=scenario!="retry-cumulative"
    vector=scenario in ("retry-vector-cpu","retry-vector-hold")
    vector_hold=scenario=="retry-vector-hold"
    prompt_hold=scenario=="retry-prompt-hold"
    wave_policy=scenario in ("retry-wave-prompt","retry-wave-prompt-hold")
    wave_hold=scenario=="retry-wave-prompt-hold"
    prompt_total=(251 if wave_hold else 252) if wave_policy else (461 if prompt_hold else (462 if dual else None))
    generated_total=80 if prompt_hold else 64
    ctx=Context("native-retry","session","profile",1,"local","LOCAL_ONLY")
    gate=FixtureGateway(ctx,(profiles.Rule("host_shell",(),"allow"),),clock=profiles.PolicyClock())
    models=(Candidate(MODEL,"llm","local","llama-"+ENGINE_REV,True,True,16,80),)
    workers=(Worker("plan",(),0,"plan"),Worker("a",("plan",),0,"review"),
             Worker("b",("plan",),0,"review"),Worker("review",("a","b"),0,"review"))
    memory=ResourceLedger(32)
    coordinator=AttemptCoordinator(gate,memory,"native-retry",workers,32,2,owned_drain=True,strict_results=True)
    session=RetryBudgetSession(coordinator,generated_total,prompt_total=prompt_total)
    measured_prompts=[]
    baseline=private_process_snapshot(host_pid_namespace)
    owned=[]
    if vector:
        from local_agent_vector_resources import Capacity,VectorResources,Demand
        from local_agent_vector_planner import ModelMemory
        from local_agent_vector_admission import VectorAdmission
        vector_ledger=VectorResources(Capacity(32,0))
        inventory=(ModelMemory(MODEL,"cpu",Capacity(16,0)),)
        vector_owner=VectorAdmission("native-vector",vector_ledger,workers,limit=2,backend="cpu")
        accelerator=VectorAdmission("native-accelerator",vector_ledger,workers,limit=2)
        try:
            accelerator.prepare(ctx,models,inventory)
        except Refused:
            denied=True
        else:
            denied=False
        record("vector-native-no-implicit-cpu-fallback",denied and not owned
               and vector_ledger.snapshot()[1]==Capacity(0,0)
               and namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
        vector_offer=None
        def new_vector_offer():
            offer=vector_owner.prepare(ctx,models,inventory)
            vector_owner.confirm(offer,ctx,models,inventory)
            record("vector-native-confirmed-cpu-plan",all(a.backend=="cpu" and a.capacity==Capacity(16,0)
                   for w in offer.plan.waves for a in w))
            return offer
    def wave(offer,run,names):
        vector_rows={}
        if vector:
            # Reserve the vector wave first, so a refused resource wave cannot
            # claim a model worker or launch any process.
            reserved=vector_owner.reserve_next(vector_offer,ctx,models,inventory)
            record("vector-native-atomic-before-claim-"+ "-".join(names),
                   tuple(r.demand.worker for r in reserved)==names
                   and vector_ledger.snapshot()[1]==Capacity(16*len(names),0)
                   and not run.pending())
            vector_rows={r.demand.worker:r for r in reserved}
        rows=session.reserve_wave(offer,names,ctx,models)
        record("retry-wave-reserved-"+ "-".join(names),
               session.snapshot()[2]==sum(l.attempt.ceiling for l,_ in rows))
        if dual:
            record("retry-dual-prompt-reserved-"+ "-".join(names),session.prompt_snapshot()[2]==112*len(rows))
        prepared=[]
        for lease,wire in rows:
            worker=lease.attempt.worker
            grant=gate.fixture_grant(offer.attempt+"-"+worker,run.request(worker),60)
            handle=run.start(worker,grant,ctx,models)
            session.dispatch(lease,wire,ctx,models)
            prepared.append((lease,wire,handle))
        record("retry-wave-memory-"+ "-".join(names),memory.snapshot()[1]==16*len(rows))
        active=[]
        for i,(lease,wire,handle) in enumerate(prepared):
            proc=subprocess.Popen(["/usr/bin/python3","-I","/work/runner.py","--worker",lease.attempt.worker,
                                   str(8080+i),"normal"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,close_fds=True)
            owned.append(proc)
            active.append((lease,wire,handle,proc))
        if len(active)==2:
            record("retry-parallel-wrappers-live",all(row[3].poll() is None for row in active))
        def invoke(row):
            return row[3].communicate(input=row[1],timeout=35)[0]
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            envelopes=tuple(pool.map(invoke,active))
        record("retry-wave-private-pid-drained-"+ "-".join(names),
               namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
        if vector:
            record("vector-native-retained-until-private-drain-"+ "-".join(names),
                   vector_ledger.snapshot()[1]==Capacity(16*len(names),0))
        total=0
        for (lease,wire,handle,proc),envelope in zip(active,envelopes):
            if proc.returncode!=0:
                raise RuntimeError("native_retry_wrapper_exit")
            data,tokens,prompt=bound_reply(lease.attempt.worker,wire,envelope)
            generated=session.settle_reaped(lease,envelope)
            measured_prompts.append(prompt)
            if dual:
                record("retry-dual-measured-prompt-"+lease.attempt.worker,session.prompt_snapshot()[1]==sum(measured_prompts),
                       prompt_tokens=prompt,total_prompt=sum(measured_prompts))
            record("retry-native-"+lease.attempt.worker,generated==len(tokens)==lease.attempt.ceiling,
                   generated_tokens=generated,prompt_tokens=prompt,attempt=offer.attempt)
            outcome=run.observe_exit(handle,proc.returncode,ctx,models,payload=data)
            record("retry-owned-exit-"+lease.attempt.worker,outcome=="COMPLETED")
            if vector:
                vector_owner.observe_reaped(vector_rows[lease.attempt.worker],success=True)
                record("vector-native-post-reap-matches-managed-"+lease.attempt.worker,
                       vector_ledger.snapshot()[1]==Capacity(memory.snapshot()[1],0))
            total+=generated
            owned.remove(proc)
        record("retry-wave-cleared-"+ "-".join(names),memory.snapshot()==(32,0,32) and session.snapshot()[2]==0)
        if dual:
            record("retry-dual-wave-prompt-cleared-"+ "-".join(names),session.prompt_snapshot()[2]==0)
        return total
    try:
        first=session.prepare(ctx,models,"standard",prompt_policy="wave" if wave_policy else "whole-plan")
        first_run=session.confirm(first,ctx,models)
        if vector:
            vector_offer=new_vector_offer()
        used=wave(first,first_run,("plan",))
        session.stop()
        if vector:
            vector_owner.stop()
        record("retry-first-drained",used==16 and first_run.drained() and session.snapshot()==(generated_total,16,0,generated_total-16))
        try:
            first_run.output("plan",ctx,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("retry-old-output-denied",denied)
        issued=coordinator._issued
        if dual:
            record("retry-dual-first-prompt",measured_prompts==[14] and session.prompt_snapshot()==(prompt_total,14,0,prompt_total-14))
        if prompt_hold:
            for profile in ("standard","economy"):
                try:
                    session.prepare(ctx,models,profile)
                except Refused as exc:
                    denied=str(exc)=="retry_budget_prompt_unaffordable"
                else:
                    denied=False
                record("retry-dual-prompt-only-refusal-"+profile,denied and coordinator._issued==issued
                       and session.snapshot()==(80,16,0,64) and session.prompt_snapshot()==(461,14,0,447)
                       and memory.snapshot()==(32,0,32) and not owned
                       and namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
            return 0
        try:
            session.prepare(ctx,models,"standard")
        except Refused as exc:
            denied=str(exc)=="retry_budget_unaffordable"
        else:
            denied=False
        record("retry-standard-refused-before-launch",denied and coordinator._issued==issued and not owned
               and namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
        if wave_policy:
            try:
                session.prepare(ctx,models,"economy")
            except Refused as exc:
                denied=str(exc)=="retry_budget_prompt_unaffordable"
            else:
                denied=False
            record("retry-wave-default-whole-plan-refused",denied and coordinator._issued==issued)
        second=session.prepare(ctx,models,"economy",prompt_policy="wave" if wave_policy else "whole-plan")
        second_run=session.confirm(second,ctx,models)
        if vector:
            previous_vector=vector_offer
            vector_offer=new_vector_offer()
            record("vector-native-fresh-confirmed-attempt",vector_offer.identity!=previous_vector.identity)
        record("retry-explicit-economy-fresh-identity",first.attempt!=second.attempt
               and first_run.request("plan")!=second_run.request("plan")
               and second.ceilings==(16,8,8,16) and second.remaining==48)
        if wave_policy:
            record("retry-wave-explicit-admission",second.prompt_policy=="wave" and second.prompt_required_at_admission==112
                   and second.prompt_remaining==prompt_total-14)
        elif dual:
            record("retry-dual-exact-prompt-admission",second.prompt_remaining==448 and session.prompt_snapshot()==(462,14,0,448))
        actual=wave(second,second_run,("plan",))
        if vector_hold:
            external,=vector_ledger.reserve_wave("competing",(Demand("other",Capacity(1,0)),))
            try:
                wave(second,second_run,("a","b"))
            except Refused as exc:
                denied=str(exc)=="vector_resource_exhausted"
            else:
                denied=False
            session.stop()
            record("vector-native-peer-hold-before-claim-and-spawn",denied and second_run.drained()
                   and set(second_run._run._claims)=={"plan"} and not owned
                   and vector_ledger.snapshot()[1]==Capacity(1,0)
                   and session.snapshot()==(64,32,0,32) and memory.snapshot()==(32,0,32)
                   and namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
            vector_ledger.release_reaped(external)
            record("vector-native-hold-cleared",vector_ledger.snapshot()[1]==Capacity(0,0))
            return 0
        if wave_hold:
            try:
                wave(second,second_run,("a","b"))
            except Refused as exc:
                denied=str(exc)=="team_budget_prompt_exhausted"
            else:
                denied=False
            record("retry-wave-peer-refused-before-claim-and-spawn",denied and second_run.drained()
                   and set(second_run._run._claims)=={"plan"} and not owned
                   and session.snapshot()==(64,32,0,32) and session.prompt_snapshot()==(251,28,0,223)
                   and memory.snapshot()==(32,0,32) and namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
            try:
                second_run.output("plan",ctx,models)
            except Refused:
                denied=True
            else:
                denied=False
            record("retry-wave-hold-no-intermediate-output",denied)
            return 0
        for names in (("a","b"),("review",)):
            if wave_policy and names==("a","b"):
                record("retry-wave-exact-peer-boundary",session.prompt_snapshot()==(252,28,0,224))
            actual+=wave(second,second_run,names)
        if vector:
            record("vector-native-completed",vector_owner.completed(vector_offer) and vector_ledger.snapshot()[1]==Capacity(0,0))
        receipt,data=second_run.output("review",ctx,models)
        record("retry-final-bound-output",bool(data) and receipt.payload_digest==sha256(data).hexdigest())
        record("retry-cumulative-total",actual==48 and session.snapshot()==(64,64,0,0),
               first_generated=used,retry_generated=actual,total_generated=used+actual)
        if dual:
            record("retry-dual-final-actual-prompt",len(measured_prompts)==5 and all(1<=p<=112 for p in measured_prompts)
                   and session.prompt_snapshot()==(prompt_total,sum(measured_prompts),0,prompt_total-sum(measured_prompts)),
                   per_call_prompt=measured_prompts,total_prompt=sum(measured_prompts))
        session.stop()
        issued=coordinator._issued
        try:
            session.prepare(ctx,models,"economy")
        except Refused:
            denied=True
        else:
            denied=False
        record("retry-exhausted-no-third-attempt",denied and coordinator._issued==issued
               and memory.snapshot()==(32,0,32) and not owned
               and namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
    finally:
        session.close()
        if vector:
            vector_owner.close()
            vector_ledger.close()
        for proc in owned:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
        # Failure retains uncertain ownership; disposable namespace teardown
        # is containment, not a fabricated terminal observation.
    return 0
