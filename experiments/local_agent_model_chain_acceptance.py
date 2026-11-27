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

def worker(name,port):
    sys.path.insert(0,"/work/code")
    from llama_engine_acceptance import MODEL,MODEL_SHA,MAX_REPLY,stop_fixture_server
    from local_agent_model_fixture_contract import MAX_WIRE,validate_wire,reply_for
    if port not in ("8080","8081"):
        raise RuntimeError("fixture_port")
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
            raw=request("POST","/completion",wire)
            reply_for(raw)
        finally:
            stop_fixture_server(proc,lambda:None)
    # Exit 0 only after the exact owned server has been waited for.
    if proc.poll() is None:
        raise RuntimeError("fixture_server_not_reaped")
    sys.stdout.write(json.dumps({"worker":name,"wire_digest":wire_digest,"model_digest":MODEL_SHA,
                                 "reply":raw.decode("utf-8"),"server_reaped":True},separators=(",",":")))
    return 0

def child(host_net,host_port,scenario):
    if scenario not in ("normal","revoke"):
        raise RuntimeError("fixture_scenario")
    sys.path.insert(0,"/work/code")
    import resource
    from gateway import Context,FixtureGateway,Refused
    from inference import Candidate
    from local_agent_planner import Worker
    from local_agent_replan import AttemptCoordinator
    from local_agent_resources import ResourceLedger
    from local_agent_launch import LaunchBoundary
    from local_agent_model_fixture_contract import wire_for,bound_reply
    from local_agent_model_token_ledger import TokenLedger
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
    ctx=Context("agent-model-fixture","session","profile",1,"local","LOCAL_ONLY")
    # Synthetic grant mapping only. No shell effects, all roles are text inference.
    gate=FixtureGateway(ctx,(profiles.Rule("host_shell",(),"allow"),),clock=profiles.PolicyClock())
    models=(Candidate(MODEL,"llm","local","llama-"+ENGINE_REV,True,True,16,80),)
    workers=(Worker("plan",(),0,"plan"),Worker("a",("plan",),0,"review"),
             Worker("b",("plan",),0,"review"),Worker("review",("a","b"),0,"review"))
    ledger=ResourceLedger(32)
    token_ledger=TokenLedger(64)
    leases={}
    elapsed={}
    started_ns=time.monotonic_ns()
    coordinator=AttemptCoordinator(gate,ledger,"native-chain",workers,32,2,owned_drain=True,strict_results=True)
    preview=coordinator.prepare(ctx,models)
    run=coordinator.confirm(preview,ctx,models)
    boundary=LaunchBoundary(run)
    record("agent-model-planned-waves",tuple(tuple(a.worker for a in w) for w in preview.plan.waves)==(("plan",),("a","b"),("review",)))
    grants={w.identity:gate.fixture_grant("native-"+w.identity,run.request(w.identity),60) for w in workers}
    owned={}
    results={}
    def launch(name,port):
        t=boundary.prepare(name,grants[name],ctx,models)
        try:
            boundary.before_start(t,ctx,models)
        except Refused:
            boundary.not_started(t,ctx,models)
            raise
        try:
            p=subprocess.Popen(["/usr/bin/python3","-I","/work/runner.py","--worker",name,port],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,close_fds=True)
        except BaseException:
            boundary.uncertain(t)
            raise
        owned[t]=p
        boundary.started(t,ctx,models)
        rows=boundary.inputs(t,ctx,models)
        if tuple(data for _,data in rows)!=tuple(results[parent] for parent in next(w.depends for w in workers if w.identity==name)):
            raise RuntimeError("fixture_dependency_bytes")
        wire=wire_for(name,rows)
        leases[t]=token_ledger.reserve(name,wire)
        return t,wire
    def invoke(t,wire):
        begin=time.monotonic_ns()
        out=owned[t].communicate(input=wire,timeout=35)[0]
        return out,max(0,time.monotonic_ns()-begin)
    def finish(t,wire,response):
        out,duration_ns=response
        p=owned[t]
        if p.returncode!=0 or not 0<len(out)<=65536:
            raise RuntimeError("fixture_worker_failed")
        data,tokens=bound_reply(t.worker,wire,out)
        token_ledger.settle(leases.pop(t),wire,tokens)
        elapsed[t.worker]=duration_ns
        record("agent-model-"+t.worker+"-real-output",bool(data) and 1<=len(tokens)<=16,
               input_digest=digest(wire),output_digest=digest(data),tokens=len(tokens),
               elapsed_ms=round(duration_ns/1000000,3))
        # Exact wrapper exited 0 after its owned server was reaped.
        outcome=boundary.observe_reaped(t,p.returncode,ctx,models,payload=data)
        del owned[t]
        record("agent-model-"+t.worker+"-owned-exit",outcome=="COMPLETED")
        results[t.worker]=data
    try:
        t,wire=launch("plan","8080")
        finish(t,wire,invoke(t,wire))
        a,wa=launch("a","8080")
        b,wb=launch("b","8081")
        record("agent-model-parallel-reservations",ledger.snapshot()==(32,32,0) and all(p.poll() is None for p in owned.values()))
        record("agent-model-parallel-token-ceiling",token_ledger.snapshot()==(64,16,32,16))
        # Recheck both exact scoped inputs immediately before dispatch;
        # worker threads never access the gateway or choose requests.
        for ticket,wire in ((a,wa),(b,wb)):
            if wire_for(ticket.worker,boundary.inputs(ticket,ctx,models))!=wire:
                raise RuntimeError("fixture_pre_dispatch_drift")
        with ThreadPoolExecutor(max_workers=2) as pool:
            fa=pool.submit(invoke,a,wa)
            fb=pool.submit(invoke,b,wb)
            oa,ob=fa.result(),fb.result()
        if scenario=="revoke":
            # Both fixed wrappers have actually returned from inference.
            # Revoke the parent before publishing either validated result.
            gate.revoke("native-plan")
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
            data,tokens=bound_reply(b.worker,wb,out)
            token_ledger.settle(leases.pop(b),wb,tokens)
            record("agent-model-revoked-peer-actual-output",bool(data),tokens=len(tokens))
            outcome=boundary.observe_reaped(b,p.returncode,ctx,models,payload=data)
            del owned[b]
            record("agent-model-revoked-peer-drained",outcome=="DRAINED")
            record("agent-model-revoked-all-ownership-cleared",ledger.snapshot()==(32,0,32)
                   and not boundary.observations() and not owned and run.drained())
            record("agent-model-revoked-usage-not-refunded",token_ledger.snapshot()==(64,48,0,16))
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
        t,wire=launch("review","8080")
        finish(t,wire,invoke(t,wire))
        receipt,data=run.output("review",ctx,models)
        budget,spent,reserved,remaining=token_ledger.snapshot()
        record("agent-model-token-accounting",budget==64 and spent==64 and reserved==0 and remaining==0 and not leases,
               generated_tokens=spent,reserved_tokens=reserved,remaining_tokens=remaining,
               wall_ms=round((time.monotonic_ns()-started_ns)/1000000,3),
               sum_worker_ms=round(sum(elapsed.values())/1000000,3))
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
    if scenario not in ("normal","revoke"):
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
                     "local_agent_model_fixture_contract.py","local_agent_model_token_ledger.py","llama_engine_acceptance.py"):
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
            p=subprocess.Popen(prefix+["--","/usr/bin/python3","-I","/work/runner.py","--child",os.readlink("/proc/self/ns/net"),port,scenario],
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
    if len(sys.argv)==4 and sys.argv[1]=="--worker":
        raise SystemExit(worker(*sys.argv[2:]))
    if len(sys.argv)==5 and sys.argv[1]=="--child":
        raise SystemExit(child(*sys.argv[2:]))
    raise SystemExit(main(*sys.argv[1:]))
