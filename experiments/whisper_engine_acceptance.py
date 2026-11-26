# SPDX-License-Identifier: AGPL-3.0-only
"""Actual CPU Whisper on the pinned upstream public sample, no microphone."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from capabilities import CapabilityCall
from transcription_result import TranscriptFixture,Transcript

MODEL_SHA="be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21"
SOURCE_BLOB="3184d372cd2f8b804d3a540c70ec50d927b335d2"
ENGINE_REV="4979e04f5dcaccb36057e059bbaed8a2f5288315"
WRAPPER="""import os,resource,sys
for kind,value in ((resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,1048576),
                   (resource.RLIMIT_CPU,90),(resource.RLIMIT_NOFILE,256)):
    resource.setrlimit(kind,(value,value))
os.execv(sys.argv[1],sys.argv[1:])
"""
PROBE="""import hashlib,json,os,socket,sys
from pathlib import Path
data={}
data["network"]=os.readlink("/proc/self/ns/net")
try:
    with socket.create_connection(("127.0.0.1",int(sys.argv[1])),timeout=1):
        data["host_denied"]=False
except OSError:
    data["host_denied"]=True
data["no_devices"]=not Path("/dev/snd").exists() and not Path("/dev/dri").exists()
data["marker_hidden"]=not Path("/work/not-approved").exists()
data["readonly"]=True
for path in ("/work/model.bin","/work/sample.wav"):
    try:
        with open(path,"ab"):
            data["readonly"]=False
    except OSError:
        pass
data["audio"]=hashlib.sha256(Path("/work/sample.wav").read_bytes()).hexdigest()
h=hashlib.sha256()
with open("/work/model.bin","rb") as handle:
    for block in iter(lambda:handle.read(1048576),b""):
        h.update(block)
data["model"]=h.hexdigest()
print(json.dumps(data))
"""
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(1048576),b""):
            h.update(block)
    return h.hexdigest()

def main(binary,model,audio):
    binary,model,audio=map(lambda p:Path(p).resolve(),(binary,model,audio))
    if os.getuid()==0 or not all(p.is_file() for p in (binary,model,audio)):
        raise RuntimeError("invalid fixture inputs")
    if not 0<audio.stat().st_size<=1048576 or not 0<model.stat().st_size<=85000000:
        raise RuntimeError("fixture input limit")
    raw=audio.read_bytes()
    if hashlib.sha1(("blob "+str(len(raw))+"\0").encode()+raw).hexdigest()!=SOURCE_BLOB or digest(model)!=MODEL_SHA:
        raise RuntimeError("upstream input mismatch")
    source_sha=digest(audio)
    results=[]
    def record(case,ok,**extra):
        results.append(bool(ok))
        print(json.dumps(dict(case=case,status="PASS" if ok else "FAIL",**extra)),flush=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-whisper-") as temp:
        root=Path(temp)
        out=root/"out"
        out.mkdir(mode=0o700)
        (root/"not-approved").write_text("public synthetic marker")
        prefix=["/usr/bin/bwrap","--unshare-all","--die-with-parent","--new-session",
                "--cap-drop","ALL","--clearenv","--ro-bind","/usr","/usr"]
        for name in ("bin","sbin","lib","lib64"):
            p=Path("/"+name)
            if p.is_symlink():
                target=os.readlink(p)
                if target not in ("usr/"+name,"/usr/"+name):
                    raise RuntimeError("unexpected system layout")
                prefix+=["--symlink",target,"/"+name]
            elif p.exists():
                raise RuntimeError("non-merged system layout")
        prefix+=["--proc","/proc","--dev","/dev","--tmpfs","/tmp","--dir","/work",
                 "--ro-bind",str(binary),"/work/whisper-cli","--ro-bind",str(model),"/work/model.bin",
                 "--ro-bind",str(audio),"/work/sample.wav","--bind",str(out),"/work/out",
                 "--setenv","HOME","/tmp","--setenv","PATH","/usr/bin","--setenv","LC_ALL","C.UTF-8",
                 "--chdir","/work","--remount-ro","/"]
        def run(args,timeout=30):
            with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
                proc=subprocess.Popen(prefix+["--","/usr/bin/python3","-I","-c",WRAPPER]+args,
                        stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True,
                        env={"PATH":"/usr/bin:/bin","LC_ALL":"C.UTF-8"})
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait(timeout=5)
                    raise RuntimeError("whisper timeout")
                stdout.seek(0)
                stderr.seek(0)
                data,err=stdout.read(65537),stderr.read(65537)
                if len(data)>65536 or len(err)>65536:
                    raise RuntimeError("whisper capture limit")
                if proc.returncode:
                    raise RuntimeError("whisper failed: "+err.decode("utf-8","replace")[:2000])
                return data
        host_net=os.readlink("/proc/self/ns/net")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0))
            listener.listen(1)
            probe=json.loads(run(["/usr/bin/python3","-I","-c",PROBE,str(listener.getsockname()[1])]))
        record("whisper-pinned-model-and-audio",probe["model"]==MODEL_SHA and probe["audio"]==source_sha)
        record("whisper-private-network",probe["network"]!=host_net and probe["host_denied"])
        record("whisper-private-input-scope",probe["readonly"] and probe["marker_hidden"])
        record("whisper-no-gpu-or-microphone",probe["no_devices"])
        if not all(results):
            return 1
        ctx=Context("whisper-fixture","session","profile",1,"local","LOCAL_ONLY")
        rules=tuple(profiles.Rule(a,(),"allow") for a in ("host_shell","filesystem_read","filesystem_write"))
        g=FixtureGateway(ctx,rules,clock=profiles.PolicyClock())
        call=CapabilityCall("whisper_cpp",ENGINE_REV,"transcribe_wav",
                        (("artifact","public-upstream-sample"),("digest",source_sha),
                         ("model_digest",MODEL_SHA),("language","en"),("output","fixture-transcript")))
        req=call.request(ctx)
        grant=g.fixture_grant("transcribe",req,180)
        job=TranscriptFixture(g,ctx,call,grant)
        job.checkpoint()
        run(["/work/whisper-cli","-m","/work/model.bin","-f","/work/sample.wav","-l","en",
             "-t","2","-ng","-nt","-otxt","-of","/work/out/transcript"],timeout=120)
        dest=out/"transcript.txt"
        if dest.is_symlink() or not dest.is_file() or not 0<dest.stat().st_size<=16384:
            job.cancel()
            return 1
        text=dest.read_text(encoding="utf-8").strip()
        norm=" ".join(re.findall(r"[a-z]+",text.lower()))
        semantic=("ask not what your country can do for you" in norm
                  and "ask what you can do for your country" in norm)
        record("whisper-real-english-transcript",semantic,text_digest=hashlib.sha256(text.encode()).hexdigest())
        unchanged=digest(audio)==source_sha and digest(model)==MODEL_SHA
        record("whisper-inputs-unchanged",unchanged)
        record("whisper-exact-output-set",sorted(p.name for p in out.iterdir())==["transcript.txt"])
        if not all(results):
            job.cancel()
            return 1
        value=Transcript(req.target,source_sha,MODEL_SHA,"en","fixture-transcript",text)
        job.complete(value)
        record("whisper-exact-typed-receipt",job.result()[1]==hashlib.sha256(text.encode()).hexdigest())
        job.cancel()
        try:
            job.result()
        except Refused:
            denied=True
        else:
            denied=False
        record("whisper-revoked-transcript-denied",denied)
    return 0 if len(results)==9 and all(results) else 1

if __name__=="__main__":
    raise SystemExit(main(*sys.argv[1:]))
