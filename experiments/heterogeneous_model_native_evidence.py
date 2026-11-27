# SPDX-License-Identifier: AGPL-3.0-only
"""Fixed public CSV, PNG and text -> pinned native CPU completion.
Called only inside the existing disposable private-network fixture.
No user data, actual OCR, semantic quality assertion or general executor.
"""
import json
from hashlib import sha256
import struct
import zlib
import subprocess
import os
from pathlib import Path
from gateway import Context,FixtureGateway,Refused,Request
from tihulu_jarvis import profiles
from agent_custom_profiles import AgentProfile
from agent_model_capability import ModelOffer
from heterogeneous_team import TeamTask
from heterogeneous_team_admission import TeamAdmission
from heterogeneous_data_tools import DataCall
from heterogeneous_data_team import DataTeamRun
from heterogeneous_model_review import MixedModelReview
from heterogeneous_model_owned_review import OwnedMixedReview
from local_agent_resources import ResourceLedger
from vision_image_input import SIGNATURE

def png():
    def chunk(kind,data):
        return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data))
    return SIGNATURE+chunk(b"IHDR",struct.pack(">IIBBBBB",2,1,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(b"\0\1\2\3\4\5\6"))+chunk(b"IEND",b"")

def run_mixed(scenario,record,host_pid_namespace):
    if scenario not in ("mixed-data","mixed-revoke","mixed-shared","mixed-shared-revoke","mixed-shared-hold"):
        raise RuntimeError("mixed_fixture_scenario")
    ctx=Context("mixed-model","session","profile",1,"local","LOCAL_ONLY")
    gate=FixtureGateway(ctx,tuple(profiles.Rule(a,(),"allow") for a in
                        ("filesystem_read","host_shell","publish_artifact")),clock=profiles.PolicyClock())
    models=(ModelOffer("fixture-data","fixture","local",("text","structured_tools"),16,80,True,True),)
    available=("file_read","typed_engine","reasoning")
    ledger=ResourceLedger(64)
    def task(name,role,channel,depends=()):
        p=AgentProfile(name,1,role,"local",(channel,),"economy","","",0,16)
        return TeamTask(name,p,(channel,),depends)
    tasks=(task("csv","data_science","typed_engine"),task("image","application","typed_engine"),
           task("text","file","file_read"),task("review","reviewer","reasoning",("csv","image","text")))
    calls=(DataCall("csv","csv_stats",b"value\n7\n-2\n10\n"),
           DataCall("image","png_stats",png()),DataCall("text","read_text",b"public fixture"),
           DataCall("review","report",b""))
    admission=TeamAdmission(gate,"mixed")
    args=(ctx,tasks,available,models,64,64)
    plan=admission.confirm(admission.prepare(*args),*args)
    run=DataTeamRun(admission,plan,calls,ledger,memory_budget=64,token_budget=64)
    for w in plan.waves:
        for assignment in w:
            name=assignment.route.agent
            grant=gate.fixture_grant("data-"+name,run.request(name),60)
            receipt=run.execute(name,grant,ctx,available,models)
            record("mixed-model-data-"+name,receipt.size>0 and ledger.snapshot()==(64,0,64))
    source,payload=run.output("review",ctx,available,models)
    report={x["task"]:x["result"] for x in json.loads(payload)["results"]}
    record("mixed-model-source-data",report["csv"]["sum"]==15 and report["image"]["width"]==2 and report["text"]["words"]==2)
    review=MixedModelReview(run,ctx,available,models)
    grant=gate.fixture_grant("native-review",review.request(),60)
    shared=scenario.startswith("mixed-shared")
    cumulative=run.generated_budget() if shared else None
    prior=0
    if shared:
        from native_fixture_pid_observation import private_process_snapshot,namespace_drained
        baseline=private_process_snapshot(host_pid_namespace)
        record("mixed-shared-private-pid-baseline",bool(baseline),process_count=len(baseline))
        from heterogeneous_team_token_budget import Attempt
        from local_agent_model_fixture_contract import wire_for,bound_reply
        previous_wire=(b"synthetic prior accounting fixture" if scenario=="mixed-shared-hold"
                       else wire_for("plan",()))
        # The HOLD variant uses a synthetic prior charge, expressly not a model
        # execution. It must refuse the review before any wrapper/server starts.
        ceiling=49 if scenario=="mixed-shared-hold" else 16
        spec=Attempt("prior-fixed", "planner", sha256(previous_wire).hexdigest(), ceiling)
        (previous,)=cumulative.reserve_wave(plan.digest,(spec,))
        if scenario=="mixed-shared-hold":
            cumulative.dispatch(previous,previous_wire)
            cumulative.abandon_reaped(previous)
            prior=49
            record("mixed-shared-synthetic-prior-charge",cumulative.snapshot()==(64,49,0,15),synthetic=True)
        else:
            # Fixed prior fixture has its own exact synthetic grant and memory
            # reservation. It is not a new unrestricted team execution path.
            request=Request(ctx,"document_engine",
                            sha256(("prior:"+plan.digest+":"+run._attempt+":"+spec.wire_digest).encode("ascii")).hexdigest(),
                            "local","private").checked()
            prior_grant=gate.fixture_grant("native-prior",request,60)
            memory=ledger.reserve(run._attempt,"prior-fixed",16)
            gate.claim_with_dependencies(prior_grant,request,tuple(run._claims.values()))
            gate.checkpoint_claimed_group(((prior_grant,request),))
            cumulative.dispatch(previous,previous_wire)
            record("mixed-shared-prior-reserved",ledger.snapshot()==(64,16,48)
                   and cumulative.snapshot()==(64,0,16,48))
            p=subprocess.Popen(["/usr/bin/python3","-I","/work/runner.py","--worker","plan","8080","normal"],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,close_fds=True)
            try:
                out=p.communicate(input=previous_wire,timeout=35)[0]
            except BaseException:
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=5)
                raise
            if p.returncode!=0:
                raise RuntimeError("mixed_shared_prior_exit")
            _,previous_tokens,previous_prompt=bound_reply("plan",previous_wire,out)
            prior=len(previous_tokens)
            record("mixed-shared-prior-pid-drained",namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
            cumulative.settle_reaped(previous,prior)
            ledger.release(memory)
            gate.checkpoint_claimed_group(((prior_grant,request),))
            record("mixed-shared-prior-native-charge",prior==16 and cumulative.snapshot()==(64,prior,0,64-prior)
                   and ledger.snapshot()==(64,0,64),
                   generated_tokens=prior,prompt_tokens=previous_prompt)
    owned=OwnedMixedReview(review,use_team_budget=shared)
    if scenario=="mixed-shared-hold":
        try:
            owned.prepare(grant,ctx,available,models)
        except Refused as exc:
            denied=str(exc)=="team_budget_exhausted"
        else:
            denied=False
        record("mixed-shared-hold-before-claim",denied and review._grant is None and owned.pending() is None)
        children=Path("/proc/self/task/"+str(os.getpid())+"/children").read_text().strip()
        record("mixed-shared-hold-pid-unchanged",namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
        record("mixed-shared-hold-no-spawn",not children and not tuple(Path("/tmp").glob("server-*.log"))
               and ledger.snapshot()==(64,0,64) and cumulative.snapshot()==(64,49,0,15))
        return 0
    ticket,wire=owned.prepare(grant,ctx,available,models)
    if shared:
        record("mixed-shared-reservation",cumulative.snapshot()==(64,prior,16,48-prior))
    record("mixed-model-reserved-before-dispatch",owned.usage()==(16,0,16,0)
           and ledger.snapshot()==(64,16,48) and owned.pending() is ticket)
    owned.dispatch(ticket,wire,ctx,available,models)
    record("mixed-model-projected-input","3 numbers, total 15, 2 words, and a 2 by 1 picture" in json.loads(wire)["prompt"]
           and "public fixture" not in json.loads(wire)["prompt"])
    # Fixed worker owns and waits for the fixed server, inside existing namespace.
    try:
        p=subprocess.Popen(["/usr/bin/python3","-I","/work/runner.py","--worker","a","8080","normal"],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,close_fds=True)
    except BaseException:
        owned.stop()
        raise
    try:
        out=p.communicate(input=wire,timeout=35)[0]
    except BaseException:
        owned.stop()
        # Namespace teardown is the ultimate descendant barrier on failure.
        if p.poll() is None:
            p.kill()
        p.wait(timeout=5)
        raise
    if p.returncode!=0 or not 0<len(out)<=65536:
        owned.stop()
        raise RuntimeError("mixed_fixture_worker_exit")
    from local_agent_model_fixture_contract import bound_reply
    try:
        data,tokens,count=bound_reply("a",wire,out)
    except BaseException:
        owned.stop()
        raise
    record("mixed-model-actual-native-output",bool(data) and 1<=len(tokens)<=16 and p.poll() is not None,
           generated_tokens=len(tokens),prompt_tokens=count,wire_digest=sha256(wire).hexdigest())
    if shared:
        record("mixed-shared-review-pid-drained",namespace_drained(baseline,private_process_snapshot(host_pid_namespace)))
    if scenario in ("mixed-revoke","mixed-shared-revoke"):
        gate.revoke("data-csv")
        record("mixed-model-revoke-retains-reservation",ledger.snapshot()==(64,16,48)
               and owned.pending() is ticket and owned.usage()==(16,0,16,0))
        try:
            owned.observe_reaped(ticket,p.returncode,out,ctx,available,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("mixed-model-revoked-before-publication",denied and not run._results)
        try:
            owned.output(ctx,available,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("mixed-model-revoked-no-output",denied)
    else:
        outcome=owned.observe_reaped(ticket,p.returncode,out,ctx,available,models)
        record("mixed-model-owned-completion",outcome=="COMPLETED")
        result,actual=owned.output(ctx,available,models)
        record("mixed-model-bound-final-output",actual==data and result.source_digest==source.payload_digest
               and result.wire_digest==sha256(wire).hexdigest() and result.output_digest==sha256(data).hexdigest())
        gate.revoke("data-text")
        try:
            owned.output(ctx,available,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("mixed-model-revoked-after-publication",denied)
    record("mixed-model-actual-token-settlement",owned.usage()==(16,len(tokens),0,16-len(tokens))
           and owned.pending() is None,generated_tokens=len(tokens))
    if shared:
        record("mixed-shared-final-accounting",cumulative.snapshot()==(64,prior+len(tokens),0,64-prior-len(tokens)),
               prior_generated=prior,review_generated=len(tokens),total_generated=prior+len(tokens))
    record("mixed-model-no-data-reservations",ledger.snapshot()==(64,0,64))
    return 0
