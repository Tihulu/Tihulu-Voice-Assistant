# SPDX-License-Identifier: AGPL-3.0-only
"""CI-only fixed public real OCR -> heterogeneous data team evidence.
The native OCR phase is independently owned by the existing document fixture.
The team consumes exact completed document receipts; no general OS executor.
"""
import json
from hashlib import sha256
from pathlib import Path
import tempfile
from PIL import Image,ImageDraw,ImageFont
from gateway import Context,FixtureGateway,Refused
from tihulu_jarvis import profiles
from capabilities import CapabilityCall
from document_result import DocumentFixture,DocumentResult
from document_geometry import ocr_blocks
from document_engine_acceptance import FONT,TEXT,norm,run
from agent_custom_profiles import AgentProfile
from agent_model_capability import ModelOffer
from heterogeneous_team import TeamTask
from heterogeneous_team_admission import TeamAdmission
from heterogeneous_data_tools import DataCall
from heterogeneous_data_team import DataTeamRun
from local_agent_resources import ResourceLedger

def record(case,ok,**extra):
    print(json.dumps(dict(case=case,status="PASS" if ok else "FAIL",**extra)),flush=True)
    if not ok:
        raise RuntimeError(case)

def main():
    ctx=Context("public-doc-team","session","profile",1,"local","LOCAL_ONLY")
    gate=FixtureGateway(ctx,tuple(profiles.Rule(a,(),"allow") for a in
                        ("filesystem_read","host_shell","publish_artifact")),clock=profiles.PolicyClock())
    models=(ModelOffer("fixture-text","fixture","local",("text","structured_tools"),1024,80,True,True),)
    available=("file_read","typed_engine","reasoning")
    ledger=ResourceLedger(8192)
    def task(name,role,channel,depends=()):
        p=AgentProfile(name,1,role,"local",(channel,),"economy","","",0,128)
        return TeamTask(name,p,(channel,),depends)
    tasks=[]
    calls=[]
    sources=[]
    originals={}
    with tempfile.TemporaryDirectory(prefix="jarvis-mixed-doc-team-") as tmp:
        root=Path(tmp)
        version=run(["tesseract","--version"],root).stdout.decode().splitlines()[0].split()[1]
        for lang,text in TEXT.items():
            name="ocr-"+lang
            image=Image.new("RGB",(1800,320),"white")
            ImageDraw.Draw(image).text((55,100),text,font=ImageFont.truetype(FONT,52),fill="black")
            path=root/(lang+".png")
            image.save(path)
            raw=path.read_bytes()
            digest=sha256(raw).hexdigest()
            originals[path]=digest
            call=CapabilityCall("tesseract",version,"ocr_image",(("artifact",name),("digest",digest),
                                ("language",{"tur":"tr","eng":"en","deu":"de"}[lang])))
            req=call.request(ctx)
            grant=gate.fixture_grant("native-"+name,req,120)
            job=DocumentFixture(gate,grant,ctx,call)
            gate.checkpoint_claimed_group(((grant,req),))
            tsv=run(["tesseract",str(path),"stdout","-l",lang,"--psm","6","tsv"],root).stdout.decode("utf-8")
            blocks=ocr_blocks(tsv,*image.size)
            job.complete(DocumentResult(req.target,digest,1,blocks))
            receipt=job.result()
            text_out=" ".join(b.text for b in receipt[3].blocks)
            record("mixed-team-native-"+lang,norm(text_out)==norm(text) and sha256(path.read_bytes()).hexdigest()==digest,
                   result_digest=receipt[4],words=len(text_out.split()))
            tasks.append(task(name,"file","file_read"))
            calls.append(DataCall(name,"read_text",text_out.encode("utf-8")))
            sources.append((name,job))
        tasks.extend((task("csv","data_science","typed_engine"),task("image","application","typed_engine")))
        calls.extend((DataCall("csv","csv_stats",b"value\n7\n-2\n10\n"),
                      DataCall("image","png_stats",(root/"eng.png").read_bytes())))
        tasks.append(task("review","reviewer","reasoning",tuple(t.identity for t in tasks)))
        calls.append(DataCall("review","report",b""))
        tasks,calls,sources=tuple(tasks),tuple(calls),tuple(sources)
        def make(name):
            a=TeamAdmission(gate,name)
            args=(ctx,tasks,available,models,8192,768)
            offer=a.prepare(*args)
            plan=a.confirm(offer,*args)
            r=DataTeamRun(a,plan,calls,ledger,memory_budget=8192,token_budget=768,document_sources=sources)
            grants={t.identity:gate.fixture_grant(name+"-"+t.identity,r.request(t.identity),120) for t in tasks}
            return r,grants,plan
        r,grants,plan=make("mixed")
        record("mixed-team-plan-three-channels",set(x.route.channel for w in plan.waves for x in w)==set(available)
               and len(plan.waves)==2 and len(plan.waves[0])==5)
        for w in plan.waves:
            for assignment in w:
                name=assignment.route.agent
                receipt=r.execute(name,grants[name],ctx,available,models)
                record("mixed-team-compute-"+name,receipt.size>0 and len(receipt.input_digest)==64
                       and ledger.snapshot()==(8192,0,8192))
        receipt,payload=r.output("review",ctx,available,models)
        report={x["task"]:x["result"] for x in json.loads(payload)["results"]}
        record("mixed-team-final-report",receipt.payload_digest==sha256(payload).hexdigest()
               and report["csv"]["sum"]==15 and report["image"]["width"]==1800
               and report["image"]["height"]==320 and len(report)==5)
        for c in calls:
            if c.operation=="read_text":
                record("mixed-team-source-"+c.task,report[c.task]["source_digest"]==sha256(c.data).hexdigest()
                       and report[c.task]["words"]==len(c.data.decode("utf-8").split()))
        # Create a second confirmed consumer before revoking the source.
        other,other_grants,_=make("other")
        sources[0][1].cancel()
        try:
            r.output("review",ctx,available,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("mixed-team-revoked-final-report",denied and not r._results)
        try:
            other.execute("csv",other_grants["csv"],ctx,available,models)
        except Refused:
            denied=True
        else:
            denied=False
        record("mixed-team-revoked-source-before-unrelated-compute",denied and not other._results
               and ledger.snapshot()==(8192,0,8192))
        record("mixed-team-originals-unchanged",all(sha256(p.read_bytes()).hexdigest()==d for p,d in originals.items()))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
