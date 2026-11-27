# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable fixed public four-worker tiny LLM chain. Never a host executor."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

def record(case,ok,**extra):
    print(json.dumps(dict(case=case,status="PASS" if ok else "FAIL",**extra)),flush=True)
    if not ok:
        raise RuntimeError(case)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def worker(name,port,mode="normal"):
    if mode not in ("normal","stream") or (mode=="stream" and name!="plan"):
        raise RuntimeError("fixture_worker_mode")
    sys.path.insert(0,"/work/code")
    from llama_engine_acceptance import MODEL,MODEL_SHA,MAX_REPLY,stop_fixture_server
    from local_agent_model_fixture_contract import MAX_WIRE,validate_wire,tokenizer_wire,prompt_count,measured_reply,wire_ceiling
    if port not in ("8080","8081"):
        raise RuntimeError("fixture_port")
    if mode=="stream":
        frame=sys.stdin.buffer.readline(MAX_WIRE+2)
        if not frame.endswith(b"\n"):
            raise RuntimeError("fixture_stream_input")
        wire=frame[:-1]
    else:
        wire=sys.stdin.buffer.read(MAX_WIRE+1)
    if not 0<len(wire)<=MAX_WIRE:
        raise RuntimeError("fixture_wire_size")
    wire_digest=validate_wire(name,wire)
    if digest(Path("/work/model.gguf").read_bytes())!=MODEL_SHA:
        raise RuntimeError("fixture_wire_contract")
    def request(method,path,payload=None):
        conn=http.client.HTTPConnection("127.0.0.1",int(port),timeout=8)
        try:
            conn.request(method,path,body=payload,headers={"Content-Type":"application/json","Connection":"close"})
            r=conn.getresponse()
            data=r.read(MAX_REPLY+1)
            if r.status!=200 or len(data)>MAX_REPLY:
                raise RuntimeError("fixture_http_status")
            return data
        finally:
            conn.close()
    with open("/tmp/server-"+port+".log","wb") as log:
        proc=subprocess.Popen(["/work/llama-server","--host","127.0.0.1","--port",port,
                               "--model","/work/model.gguf","--alias",MODEL,
                               "--threads","2","--threads-http","2","--ctx-size","128",
                               "--parallel","1","--n-gpu-layers","0","--no-webui"],
                               stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True)
        try:
            deadline=time.monotonic()+20
            while True:
                if proc.poll() is not None or time.monotonic()>=deadline:
                    raise RuntimeError("fixture_server_startup")
                try:
                    if json.loads(request("GET","/health")).get("status")=="ok":
                        break
                except (OSError,RuntimeError,http.client.HTTPException):
                    pass
                time.sleep(.1)
            count=prompt_count(request("POST","/tokenize",tokenizer_wire(name,wire)))
            if mode=="stream":
                from local_agent_model_stream_contract import stream_wire,marker,MAX_EVENT
                stream=stream_wire(name,wire)
                conn=http.client.HTTPConnection("127.0.0.1",int(port),timeout=8)
                try:
                    conn.request("POST","/completion",body=stream,headers={"Content-Type":"application/json",
                                 "Connection":"close"})
                    response=conn.getresponse()
                    if response.status!=200 or not response.getheader("Content-Type","").startswith("text/event-stream"):
                        raise RuntimeError("fixture_stream_status")
                    line=response.readline(MAX_EVENT+1)
                    # Trusted fixed server event; never print generated text.
                    print(json.dumps(marker(name,wire,line),separators=(",",":")),flush=True)
                    if sys.stdin.buffer.readline(8)!=b"CANCEL\n":
                        raise RuntimeError("fixture_stream_cancel_command")
                finally:
                    conn.close()
            else:
                raw=request("POST","/completion",wire)
                measured_reply(raw,count,ceiling=wire_ceiling(name,wire))
        finally:
            stop_fixture_server(proc,lambda:None)
    # Exit 0 only after the exact owned server has been waited for.
    if proc.poll() is None:
        raise RuntimeError("fixture_server_not_reaped")
    if mode=="stream":
        print(json.dumps({"state":"CANCELLED","worker":name,"wire_digest":digest(stream),
                          "server_reaped":True},separators=(",",":")),flush=True)
        return 0
    sys.stdout.write(json.dumps({"worker":name,"wire_digest":wire_digest,"model_digest":MODEL_SHA,
                                 "reply":raw.decode("utf-8"),"server_reaped":True,"prompt_tokens":count},separators=(",",":")))
    return 0

def child(host_net,host_port,scenario,host_pid_namespace):
    if scenario not in ("normal","revoke","stream-cancel","budget-hold","economy","plan-hold","mixed-data","mixed-revoke","mixed-shared","mixed-shared-revoke","mixed-shared-hold","shared-chain","shared-chain-revoke","shared-chain-hold","retry-cumulative","retry-dual","retry-prompt-hold","retry-wave-prompt","retry-wave-prompt-hold"):
        raise RuntimeError("fixture_scenario")
    sys.path.insert(0,"/work/code")
    import resource
    from gateway import Context,FixtureGateway,Refused
    from inference import Candidate
    from local_agent_planner import Worker
    from local_agent_replan import AttemptCoordinator
    from local_agent_resources import ResourceLedger
    from local_agent_launch import LaunchBoundary
    from local_agent_model_fixture_contract import wire_for,bound_reply,DEFAULT_CEILINGS,ECONOMY_CEILINGS,PARENTS,wire_ceiling
    from local_agent_model_token_ledger import TokenLedger
    from local_agent_model_budget_admission import BudgetAdmission
    from llama_engine_acceptance import MODEL,MODEL_SHA,ENGINE_REV
    from tihulu_jarvis import profiles
    from concurrent.futures import ThreadPoolExecutor
    record("agent-model-private-network",os.readlink("/proc/self/ns/net")!=host_net)
    try:
        with socket.create_connection(("127.0.0.1",int(host_port)),timeout=1):
            denied=False
    except OSError:
        denied=True
    record("agent-model-host-loopback-denied",denied)
    status=dict(line.split(":",1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    record("agent-model-no-host-capabilities",all(int(status[k].strip(),16)==0 for k in ("CapEff","CapPrm","CapAmb"))
           and not Path("/dev/dri").exists() and not Path("/dev/snd").exists()
           and not Path("/work/not-approved").exists())
    record("agent-model-pinned-input",digest(Path("/work/model.gguf").read_bytes())==MODEL_SHA)
    for kind,value in ((resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,4194304),
                       (resource.RLIMIT_CPU,60),(resource.RLIMIT_NOFILE,256)):
        resource.setrlimit(kind,(value,value))
    if scenario in ("mixed-data","mixed-revoke","mixed-shared","mixed-shared-revoke","mixed-shared-hold"):
        from heterogeneous_model_native_evidence import run_mixed
        return run_mixed(scenario,record,host_pid_namespace)
    if scenario in ("retry-cumulative","retry-dual","retry-prompt-hold","retry-wave-prompt","retry-wave-prompt-hold"):
        from local_agent_native_retry_evidence import run_retry
        return run_retry(record,host_pid_namespace,scenario)
    ctx=Context("agent-model-fixture","session","profile",1,"local","LOCAL_ONLY")
    # Synthetic grant mapping only. No shell effects, all roles are text inference.
    gate=FixtureGateway(ctx,(profiles.Rule("host_shell",(),"allow"),),clock=profiles.PolicyClock())
    models=(Candidate(MODEL,"llm","local","llama-"+ENGINE_REV,True,True,16,80),)
    workers=(Worker("plan",(),0,"plan"),Worker("a",("plan",),0,"review"),
             Worker("b",("plan",),0,"review"),Worker("review",("a","b"),0,"review"))
    ledger=ResourceLedger(32)
    ceilings=ECONOMY_CEILINGS if scenario=="economy" else DEFAULT_CEILINGS
    limits=dict(zip(PARENTS,ceilings))
    total_budget=32 if scenario in ("budget-hold","plan-hold","shared-chain-hold") else sum(ceilings)
    leases={}
    elapsed={}
    prompt_counts={}
    started_ns=time.monotonic_ns()
    coordinator=AttemptCoordinator(gate,ledger,"native-chain",workers,32,2,owned_drain=True,strict_results=True)
    if scenario=="plan-hold":
        admission=BudgetAdmission(coordinator,total_budget)
        for profile in ("standard","economy"):
            try:
                admission.prepare(ctx,models,profile)
            except Refused as exc:
                denied=str(exc)=="fixture_budget_plan_unaffordable"
            else:
                denied=False
            record("agent-model-plan-hold-"+profile,denied)
        children=Path("/proc/self/task/"+str(os.getpid())+"/children").read_text().strip()
        record("agent-model-plan-hold-no-spawn",not children and not tuple(Path("/tmp").glob("server-*.log"))
               and coordinator._issued==0 and ledger.snapshot()==(32,0,32))
        return 0
    if scenario in ("normal","economy"):
        admission=BudgetAdmission(coordinator,total_budget)
        if scenario=="economy":
            try:
                admission.prepare(ctx,models,"standard")
            except Refused as exc:
                denied=str(exc)=="fixture_budget_plan_unaffordable"
            else:
                denied=False
            record("agent-model-no-silent-economy-downgrade",denied and coordinator._issued==0)
        offer=admission.prepare(ctx,models,"economy" if scenario=="economy" else "standard")
        run,token_ledger=admission.confirm(offer,ctx,models)
        record("agent-model-explicit-profile-confirmed",offer.ceilings==ceilings and offer.required==total_budget
               and token_ledger.snapshot()==(total_budget,0,0,total_budget),profile=offer.profile)
        # Admission enforces the exact three waves before confirmation.
        record("agent-model-planned-waves",len(offer.plan_digest)==64)
    else:
        # Preserve earlier partial-run negative cases as comparators.
        token_ledger=TokenLedger(total_budget,strict_dispatch=True,ceilings=ceilings)
        preview=coordinator.prepare(ctx,models)
        run=coordinator.confirm(preview,ctx,models)
        record("agent-model-planned-waves",tuple(tuple(a.worker for a in w) for w in preview.plan.waves)==(("plan",),("a","b"),("review",)))
    boundary=LaunchBoundary(run)
    grants={w.identity:gate.fixture_grant("native-"+w.identity,run.request(w.identity),60) for w in workers}
    owned={}
    results={}
    launched=[]
    shared=scenario.startswith("shared-chain")
    shared_leases={}
    expected_wires={}
    if shared:
        from heterogeneous_team_token_budget import Attempt,TeamTokenBudget
        from native_fixture_pid_observation import private_process_snapshot,namespace_drained
        cumulative=TeamTokenBudget(preview.plan.digest,total_budget)
        baseline=private_process_snapshot(host_pid_namespace)
    def shared_wave(names):
        if not shared:
            return
        # Trusted coordinator projects exact completed parent receipts before
        # any wrapper is started, then checks the same wire after grant claim.
        run._run.validate_plan(ctx,models)
        if run._run._claims:
            gate.checkpoint_claimed_group(tuple(run._run._claims.values()))
        specs=[]
        wires={}
        for name in names:
            rows=tuple(run._run._outputs[parent] for parent in PARENTS[name])
            wire=wire_for(name,rows,ceiling=limits[name])
            wires[name]=wire
            specs.append(Attempt(preview.attempt+":"+name,name,digest(wire),limits[name]))
        for lease in cumulative.reserve_wave(preview.plan.digest,tuple(specs)):
            shared_leases[lease.attempt.worker]=lease
        expected_wires.update(wires)
        record("shared-chain-atomic-"+ "-".join(names),cumulative.snapshot()[2]==sum(limits[n] for n in names))
    def shared_drained(label):
        if shared:
            record("shared-chain-pid-drained-"+label,namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
    def shared_settle(name,tokens):
        if shared:
            cumulative.settle_reaped(shared_leases.pop(name),len(tokens))
    def shared_equal(label):
        if shared:
            record("shared-chain-accounting-"+label,cumulative.snapshot()==token_ledger.snapshot())
    def launch(name,port,mode="normal"):
        if shared and name not in shared_leases:
            raise RuntimeError("shared_chain_unreserved")
        token_ledger.preflight((name,))
        t=boundary.prepare(name,grants[name],ctx,models)
        try:
            boundary.before_start(t,ctx,models)
        except Refused:
            boundary.not_started(t,ctx,models)
            raise
        try:
            p=subprocess.Popen(["/usr/bin/python3","-I","/work/runner.py","--worker",name,port,mode],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,close_fds=True)
        except BaseException:
            boundary.uncertain(t)
            raise
        owned[t]=p
        launched.append(name)
        boundary.started(t,ctx,models)
        rows=boundary.inputs(t,ctx,models)
        if tuple(data for _,data in rows)!=tuple(results[parent] for parent in next(w.depends for w in workers if w.identity==name)):
            raise RuntimeError("fixture_dependency_bytes")
        wire=wire_for(name,rows,ceiling=limits[name])
        if shared and wire!=expected_wires[name]:
            raise RuntimeError("shared_chain_wire_drift")
        leases[t]=token_ledger.reserve(name,wire,streaming=(mode=="stream"))
        return t,wire
    def dispatch(t,wire):
        from local_agent_model_stream_contract import stream_wire
        lease=leases[t]
        transport=stream_wire(t.worker,wire) if lease.streaming else wire
        if shared:
            cumulative.dispatch(shared_leases[t.worker],transport)
        token_ledger.dispatch(lease,wire,transport)

    def invoke(t,wire):
        begin=time.monotonic_ns()
        out=owned[t].communicate(input=wire,timeout=35)[0]
        return out,max(0,time.monotonic_ns()-begin)
    def finish(t,wire,response):
        out,duration_ns=response
        p=owned[t]
        if p.returncode!=0 or not 0<len(out)<=65536:
            raise RuntimeError("fixture_worker_failed")
        data,tokens,count=bound_reply(t.worker,wire,out)
        prompt_counts[t.worker]=count
        token_ledger.settle(leases[t],wire,tokens)
        shared_settle(t.worker,tokens)
        del leases[t]
        elapsed[t.worker]=duration_ns
        record("agent-model-"+t.worker+"-real-output",bool(data) and 1<=len(tokens)<=wire_ceiling(t.worker,wire),
               input_digest=digest(wire),output_digest=digest(data),tokens=len(tokens),prompt_tokens=count,
               elapsed_ms=round(duration_ns/1000000,3))
        # Exact wrapper exited 0 after its owned server was reaped.
        outcome=boundary.observe_reaped(t,p.returncode,ctx,models,payload=data)
        del owned[t]
        record("agent-model-"+t.worker+"-owned-exit",outcome=="COMPLETED")
        results[t.worker]=data
    try:
        if scenario=="stream-cancel":
            import select
            from local_agent_model_stream_contract import verify_marker,verify_cancel
            t,wire=launch("plan","8080","stream")
            p=owned[t]
            dispatch(t,wire)
            p.stdin.write(wire+b"\n")
            p.stdin.flush()
            ready,_,_=select.select([p.stdout],[],[],30)
            if not ready:
                raise RuntimeError("fixture_stream_marker_timeout")
            line=p.stdout.readline(2049)
            if not line.endswith(b"\n") or len(line)>2048:
                raise RuntimeError("fixture_stream_marker_frame")
            observed=verify_marker(t.worker,wire,json.loads(line))
            record("agent-model-stream-first-event",1<=observed<=16 and p.poll() is None,
                   observed_tokens=observed)
            gate.revoke("native-plan")
            boundary.stop()
            record("agent-model-stream-revoke-retains-ownership",ledger.snapshot()==(32,16,16)
                   and token_ledger.snapshot()==(64,0,16,48) and not run.drained())
            try:
                boundary.inputs(t,ctx,models)
            except Refused:
                denied=True
            else:
                denied=False
            record("agent-model-stream-revoked-input-denied",denied)
            p.stdin.write(b"CANCEL\n")
            p.stdin.flush()
            out,_=p.communicate(timeout=12)
            if p.returncode!=0 or not 0<len(out)<=2048:
                raise RuntimeError("fixture_stream_cancel_exit")
            verify_cancel(t.worker,wire,json.loads(out))
            record("agent-model-stream-owned-server-and-wrapper-reaped",p.poll() is not None)
            token_ledger.abandon(leases.pop(t))
            record("agent-model-stream-unknown-usage-charged",token_ledger.snapshot()==(64,16,0,48))
            outcome=boundary.observe_reaped(t,p.returncode,ctx,models)
            del owned[t]
            record("agent-model-stream-drained",outcome=="DRAINED" and ledger.snapshot()==(32,0,32)
                   and not boundary.observations() and run.drained())
            try:
                run.output("plan",ctx,models)
            except Refused:
                denied=True
            else:
                denied=False
            record("agent-model-stream-no-result-publication",denied and not results)
            try:
                boundary.prepare("a",grants["a"],ctx,models)
            except Refused:
                denied=True
            else:
                denied=False
            record("agent-model-stream-dependent-not-started",denied and not owned)
            return 0
        shared_wave(("plan",))
        t,wire=launch("plan","8080")
        dispatch(t,wire)
        response=invoke(t,wire)
        shared_drained("plan")
        finish(t,wire,response)
        shared_equal("plan")
        if scenario in ("budget-hold","shared-chain-hold"):
            try:
                if shared:
                    shared_wave(("a","b"))
                else:
                    token_ledger.preflight(("a","b"))
            except Refused:
                denied=True
            else:
                denied=False
            record("agent-model-wave-budget-refused-before-launch",denied and launched==["plan"]
                   and not owned and not boundary.observations() and ledger.snapshot()==(32,0,32))
            record("agent-model-wave-budget-no-phantom-charge",token_ledger.snapshot()==(32,16,0,16))
            if shared:
                record("shared-chain-hold-no-new-reservations",cumulative.snapshot()==(32,16,0,16) and not shared_leases)
                shared_drained("hold")
            boundary.stop()
            try:
                run.output("plan",ctx,models)
            except Refused:
                denied=True
            else:
                denied=False
            record("agent-model-wave-budget-no-partial-publication",denied and run.drained())
            return 0
        shared_wave(("a","b"))
        token_ledger.preflight(("a","b"))
        a,wa=launch("a","8080")
        b,wb=launch("b","8081")
        record("agent-model-parallel-reservations",ledger.snapshot()==(32,32,0) and all(p.poll() is None for p in owned.values()))
        record("agent-model-parallel-token-ceiling",token_ledger.snapshot()==(total_budget,16,limits["a"]+limits["b"],16))
        shared_equal("parallel-reserved")
        # Recheck both exact scoped inputs immediately before dispatch;
        # worker threads never access the gateway or choose requests.
        for ticket,wire in ((a,wa),(b,wb)):
            if wire_for(ticket.worker,boundary.inputs(ticket,ctx,models),ceiling=limits[ticket.worker])!=wire:
                raise RuntimeError("fixture_pre_dispatch_drift")
        for ticket,wire in ((a,wa),(b,wb)):
            dispatch(ticket,wire)
        with ThreadPoolExecutor(max_workers=2) as pool:
            fa=pool.submit(invoke,a,wa)
            fb=pool.submit(invoke,b,wb)
            oa,ob=fa.result(),fb.result()
        shared_drained("parallel")
        if scenario in ("revoke","shared-chain-revoke"):
            # Both fixed wrappers have actually returned from inference.
            # Revoke the parent before publishing either validated result.
            gate.revoke("native-plan")
            if shared:
                for lease in shared_leases.values():
                    cumulative.cancel(lease)
                shared_equal("revoked-reservations")
            try:
                finish(a,wa,oa)
            except Refused:
                rejected=True
            else:
                rejected=False
            record("agent-model-revoke-before-publication",rejected and
                   all(o.worker!="a" for o in boundary.observations()))
            del owned[a]
            record("agent-model-revoke-keeps-peer-reservation",ledger.snapshot()==(32,16,16))
            out,_=ob
            p=owned[b]
            if p.returncode!=0:
                raise RuntimeError("fixture_peer_exit")
            data,tokens,count=bound_reply(b.worker,wb,out)
            prompt_counts[b.worker]=count
            token_ledger.settle(leases[b],wb,tokens)
            shared_settle(b.worker,tokens)
            del leases[b]
            record("agent-model-revoked-peer-actual-output",bool(data),tokens=len(tokens),prompt_tokens=count)
            outcome=boundary.observe_reaped(b,p.returncode,ctx,models,payload=data)
            del owned[b]
            record("agent-model-revoked-peer-drained",outcome=="DRAINED")
            record("agent-model-revoked-all-ownership-cleared",ledger.snapshot()==(32,0,32)
                   and not boundary.observations() and not owned and run.drained())
            record("agent-model-revoked-usage-not-refunded",token_ledger.snapshot()==(64,48,0,16))
            shared_equal("revoked-final")
            try:
                run.output("plan",ctx,models)
            except Refused:
                denied=True
            else:
                denied=False
            record("agent-model-revoked-result-unavailable",denied)
            try:
                boundary.prepare("review",grants["review"],ctx,models)
            except Refused:
                denied=True
            else:
                denied=False
            record("agent-model-revoked-review-not-started",denied and not boundary.observations())
            return 0
        finish(a,wa,oa)
        record("agent-model-peer-reservation-retained",ledger.snapshot()==(32,16,16))
        finish(b,wb,ob)
        shared_equal("parallel-completed")
        shared_wave(("review",))
        t,wire=launch("review","8080")
        dispatch(t,wire)
        response=invoke(t,wire)
        shared_drained("review")
        finish(t,wire,response)
        shared_equal("final")
        receipt,data=run.output("review",ctx,models)
        budget,spent,reserved,remaining=token_ledger.snapshot()
        record("agent-model-prompt-context-accounting",len(prompt_counts)==4 and all(1<=n<=112 for n in prompt_counts.values()),
               prompt_tokens=sum(prompt_counts.values()),per_worker=prompt_counts)
        record("agent-model-token-accounting",budget==total_budget and spent==total_budget and reserved==0 and remaining==0 and not leases,
               generated_tokens=spent,reserved_tokens=reserved,remaining_tokens=remaining,
               wall_ms=round((time.monotonic_ns()-started_ns)/1000000,3),
               sum_worker_ms=round(sum(elapsed.values())/1000000,3))
        if scenario=="economy":
            record("agent-model-economy-ceiling-verified",spent==48 and len(results)==4,
                   baseline_ceiling=64,actual_generated=spent,per_worker_ceiling=limits)
        record("agent-model-final-receipt",data==results["review"] and receipt.payload_digest==digest(data)
               and len(receipt.input_digest)==64)
        record("agent-model-clean-ownership",ledger.snapshot()==(32,0,32) and not boundary.observations() and not owned)
        gate.revoke("native-plan")
        try:
            run.output("review",ctx,models)
        except Refused:
            rejected=True
        else:
            rejected=False
        record("agent-model-transitive-revoke",rejected)
    finally:
        if shared:
            cumulative.close()
        token_ledger.close()
        boundary.stop()
        # On failure, never assert descendants are reaped or release uncertain
        # reservations. Outer disposable PID namespace teardown is containment.
        for p in owned.values():
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=3)
    return 0

def main(binary,model,scenario="normal"):
    if scenario not in ("normal","revoke","stream-cancel","budget-hold","economy","plan-hold","mixed-data","mixed-revoke","mixed-shared","mixed-shared-revoke","mixed-shared-hold","shared-chain","shared-chain-revoke","shared-chain-hold","retry-cumulative","retry-dual","retry-prompt-hold","retry-wave-prompt","retry-wave-prompt-hold"):
        raise RuntimeError("fixture_scenario")
    from llama_engine_acceptance import MODEL_SHA
    import shutil
    binary,model=Path(binary).resolve(),Path(model).resolve()
    if os.getuid()==0 or not binary.is_file() or not model.is_file() or not 0<model.stat().st_size<=2097152 or digest(model.read_bytes())!=MODEL_SHA:
        raise RuntimeError("fixture_inputs")
    repo=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="jarvis-agent-model-") as tmp:
        root=Path(tmp)
        (root/"not-approved").write_text("public marker")
        code=root/"code"
        for name in ("gateway.py","inference.py","local_agent_planner.py","local_agent_wave_run.py",
                     "local_agent_resources.py","local_agent_owned_drain.py","local_agent_replan.py","local_agent_launch.py",
                     "local_agent_model_fixture_contract.py","local_agent_model_token_ledger.py","local_agent_model_stream_contract.py","local_agent_model_budget_admission.py","llama_engine_acceptance.py",
                     "heterogeneous_model_native_evidence.py","heterogeneous_model_review.py","heterogeneous_model_owned_review.py",
                     "heterogeneous_team_token_budget.py","heterogeneous_team_review_token_adapter.py","native_fixture_pid_observation.py",
                     "local_agent_retry_budget_session.py","local_agent_native_retry_evidence.py",
                     "heterogeneous_agents.py","agent_custom_profiles.py","agent_model_capability.py",
                     "heterogeneous_team.py","heterogeneous_team_admission.py","heterogeneous_data_tools.py",
                     "heterogeneous_data_team.py","vision_image_input.py","document_result.py",
                     "capabilities.py","adapter_protocol.py"):
            dst=code/name
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(repo/"experiments"/name,dst)
        for name in ("__init__.py","profiles.py","selected_csv.py","temperature_engine.py","voice/__init__.py","voice/m1.py"):
            dst=code/"tihulu_jarvis"/name
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(repo/"src/tihulu_jarvis"/name,dst)
        prefix=["/usr/bin/bwrap","--unshare-all","--die-with-parent","--new-session","--cap-drop","ALL","--clearenv","--ro-bind","/usr","/usr"]
        for name in ("bin","sbin","lib","lib64"):
            p=Path("/"+name)
            if p.is_symlink():
                target=os.readlink(p)
                if target not in ("usr/"+name,"/usr/"+name):
                    raise RuntimeError("fixture_system_layout")
                prefix+=["--symlink",target,"/"+name]
            elif p.exists():
                raise RuntimeError("fixture_system_layout")
        prefix+=["--proc","/proc","--dev","/dev","--tmpfs","/tmp","--dir","/work",
                 "--ro-bind",str(binary),"/work/llama-server","--ro-bind",str(model),"/work/model.gguf",
                 "--ro-bind",str(code),"/work/code","--ro-bind",str(Path(__file__).resolve()),"/work/runner.py",
                 "--setenv","HOME","/tmp","--setenv","PATH","/usr/bin","--setenv","LC_ALL","C.UTF-8",
                 "--chdir","/work","--remount-ro","/"]
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0))
            listener.listen(1)
            port=str(listener.getsockname()[1])
            if port in ("8080","8081"):
                raise RuntimeError("fixture_port_collision")
            p=subprocess.Popen(prefix+["--","/usr/bin/python3","-I","/work/runner.py","--child",os.readlink("/proc/self/ns/net"),port,scenario,os.readlink("/proc/self/ns/pid")],
                               stdin=subprocess.DEVNULL,start_new_session=True,env={"PATH":"/usr/bin:/bin","LC_ALL":"C.UTF-8"})
            try:
                status=p.wait(timeout=140)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL)
                p.wait(timeout=5)
                raise
            listener.setblocking(False)
            try:
                conn,_=listener.accept()
            except BlockingIOError:
                untouched=True
            else:
                conn.close()
                untouched=False
            record("agent-model-host-listener-untouched",untouched)
        record("agent-model-model-unchanged",digest(model.read_bytes())==MODEL_SHA)
        return status

if __name__=="__main__":
    if len(sys.argv)==5 and sys.argv[1]=="--worker":
        raise SystemExit(worker(*sys.argv[2:]))
    if len(sys.argv)==6 and sys.argv[1]=="--child":
        raise SystemExit(child(*sys.argv[2:]))
    raise SystemExit(main(*sys.argv[1:]))
