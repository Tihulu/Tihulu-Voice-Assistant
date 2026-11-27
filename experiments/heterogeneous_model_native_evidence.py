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
from gateway import Context,FixtureGateway,Refused
from tihulu_jarvis import profiles
from agent_custom_profiles import AgentProfile
from agent_model_capability import ModelOffer
from heterogeneous_team import TeamTask
from heterogeneous_team_admission import TeamAdmission
from heterogeneous_data_tools import DataCall
from heterogeneous_data_team import DataTeamRun
from heterogeneous_model_review import MixedModelReview
from local_agent_resources import ResourceLedger
from vision_image_input import SIGNATURE

def png():
    def chunk(kind,data):
        return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data))
    return SIGNATURE+chunk(b"IHDR",struct.pack(">IIBBBBB",2,1,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(b"\0\1\2\3\4\5\6"))+chunk(b"IEND",b"")

def run_mixed(scenario,record):
    if scenario not in ("mixed-data","mixed-revoke"):
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
    wire=review.begin(grant,ctx,available,models)
    record("mixed-model-projected-input","3 numbers, total 15, 2 words, and a 2 by 1 picture" in json.loads(wire)["prompt"]
           and "public fixture" not in json.loads(wire)["prompt"])
    # Fixed worker owns and waits for the fixed server, inside existing namespace.
    p=subprocess.Popen(["/usr/bin/python3","-I","/work/runner.py","--worker","a","8080","normal"],
                       stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,close_fds=True)
    try:
        out=p.communicate(input=wire,timeout=35)[0]
    except BaseException:
        review.stop()
        # Namespace teardown is the ultimate descendant barrier on failure.
        if p.poll() is None:
            p.kill()
        p.wait(timeout=5)
        raise
    if p.returncode!=0 or not 0<len(out)<=65536:
        review.stop()
        raise RuntimeError("mixed_fixture_worker_exit")
    from local_agent_model_fixture_contract import bound_reply
    data,tokens,count=bound_reply("a",wire,out)
    record("mixed-model-actual-native-output",bool(data) and 1<=len(tokens)<=16 and p.poll() is not None,
           generated_tokens=len(tokens),prompt_tokens=count,wire_digest=sha256(wire).hexdigest())
    if scenario=="mixed-revoke":
        gate.revoke("data-csv")
        try:
            review.complete(out,ctx,available,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("mixed-model-revoked-before-publication",denied and not run._results)
        try:
            review.output(ctx,available,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("mixed-model-revoked-no-output",denied)
    else:
        review.complete(out,ctx,available,models)
        result,actual=review.output(ctx,available,models)
        record("mixed-model-bound-final-output",actual==data and result.source_digest==source.payload_digest
               and result.wire_digest==sha256(wire).hexdigest() and result.output_digest==sha256(data).hexdigest())
        gate.revoke("data-text")
        try:
            review.output(ctx,available,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("mixed-model-revoked-after-publication",denied)
    record("mixed-model-no-data-reservations",ledger.snapshot()==(64,0,64))
    return 0
