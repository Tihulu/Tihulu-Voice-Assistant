# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable pinned Piper Turkish CPU synthesis. No physical audio device."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from capabilities import CapabilityCall
from speech_result import SpeechFixture,SpeechResult,MAX_WAV

MODEL_SHA="2844717f524ab965d3fe86e60562cbb601d3e456836efcc2196cc3a14112a8fb"
VERSION="1.8.0"
TEXT="Merhaba. Bugün güzel bir gün."
PROBE="""import hashlib,json,os,socket,sys
from pathlib import Path
h=hashlib.sha256()
with open('/work/voice.onnx','rb') as f:
    for block in iter(lambda:f.read(1048576),b''):
        h.update(block)
try:
    with socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=1):
        denied=False
except OSError:
    denied=True
status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
readonly=True
for p in ('/work/voice.onnx','/work/voice.json','/work/text.txt','/work/site'):
    try:
        with open(p,'ab'):
            readonly=False
    except OSError:
        pass
print(json.dumps({'model':h.hexdigest(),'config':hashlib.sha256(Path('/work/voice.json').read_bytes()).hexdigest(),
    'text':hashlib.sha256(Path('/work/text.txt').read_bytes()).hexdigest(),
    'network':os.readlink('/proc/self/ns/net'),'denied':denied,'readonly':readonly,
    'hidden':not Path('/work/not-approved').exists(),
    'devices':not Path('/dev/snd').exists() and not Path('/dev/dri').exists(),
    'caps':all(int(status[k].strip(),16)==0 for k in ('CapEff','CapPrm','CapAmb'))}))
"""
SYNTH="""import resource,sys
for k,v in ((resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,1048576),(resource.RLIMIT_CPU,90),(resource.RLIMIT_NOFILE,256)):
    resource.setrlimit(k,(v,v))
sys.path.insert(0,'/work/site')
from importlib.metadata import version
from pathlib import Path
import wave,json
from piper import PiperVoice
import onnxruntime
if version('piper-tts')!='1.8.0' or version('onnxruntime')!='1.22.1':
    raise RuntimeError('dependency version mismatch')
voice=PiperVoice.load('/work/voice.onnx',config_path='/work/voice.json',use_cuda=False,
                     download_dir='/tmp')
if voice.session.get_providers()!=['CPUExecutionProvider']:
    raise RuntimeError('CPU provider mismatch')
text=Path('/work/text.txt').read_text(encoding='utf-8')
with open('/work/out/voice.wav','xb') as handle:
    with wave.open(handle,'wb') as wav:
        voice.synthesize_wav(text,wav)
print(json.dumps({'piper':version('piper-tts'),'onnxruntime':version('onnxruntime'),'providers':voice.session.get_providers()}))
"""
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1048576),b""):
            h.update(block)
    return h.hexdigest()

def main(site,model,config):
    site,model,config=map(lambda p:Path(p).resolve(),(site,model,config))
    if (os.getuid()==0 or not site.is_dir() or not model.is_file() or not config.is_file()
            or not 0<model.stat().st_size<=70000000 or digest(model)!=MODEL_SHA
            or not 0<config.stat().st_size<=16384):
        raise RuntimeError("invalid pinned voice inputs")
    cfg=json.loads(config.read_text(encoding="utf-8"))
    if (cfg["audio"]["sample_rate"]!=22050 or cfg["espeak"]["voice"]!="tr"
            or cfg["phoneme_type"]!="espeak"):
        raise RuntimeError("voice config mismatch")
    config_sha=digest(config)
    source_sha=hashlib.sha256(TEXT.encode()).hexdigest()
    results=[]
    def record(case,ok,**extra):
        results.append(bool(ok))
        print(json.dumps(dict(case=case,status="PASS" if ok else "FAIL",**extra)),flush=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-piper-") as temp:
        root=Path(temp)
        out=root/"out"
        out.mkdir(mode=0o700)
        source=root/"text.txt"
        source.write_text(TEXT,encoding="utf-8")
        (root/"not-approved").write_text("public fixture marker")
        prefix=["/usr/bin/bwrap","--unshare-all","--die-with-parent","--new-session","--cap-drop","ALL",
                "--clearenv","--ro-bind","/usr","/usr"]
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
                 "--ro-bind",str(site),"/work/site","--ro-bind",str(model),"/work/voice.onnx",
                 "--ro-bind",str(config),"/work/voice.json","--ro-bind",str(source),"/work/text.txt",
                 "--bind",str(out),"/work/out","--setenv","HOME","/tmp","--setenv","PATH","/usr/bin",
                 "--setenv","LC_ALL","C.UTF-8","--setenv","OPENBLAS_NUM_THREADS","2",
                 "--setenv","OMP_NUM_THREADS","2","--chdir","/work","--remount-ro","/"]
        def run(code,*args,timeout=120):
            with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
                proc=subprocess.Popen(prefix+["--","/usr/bin/python3","-I","-B","-c",code]+list(args),
                    stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True,
                    env={"PATH":"/usr/bin:/bin","LC_ALL":"C.UTF-8"})
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait(timeout=5)
                    raise RuntimeError("piper timeout")
                stdout.seek(0)
                stderr.seek(0)
                data,err=stdout.read(65537),stderr.read(65537)
                if len(data)>65536 or len(err)>65536 or proc.returncode:
                    raise RuntimeError("piper child failed: "+err.decode("utf-8","replace")[:2000])
                return json.loads(data)
        host=os.readlink("/proc/self/ns/net")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0))
            listener.listen(1)
            probe=run(PROBE,str(listener.getsockname()[1]),timeout=15)
        record("piper-private-network",probe["network"]!=host and probe["denied"])
        record("piper-private-readonly-inputs",probe["readonly"] and probe["hidden"])
        record("piper-no-device-or-capabilities",probe["devices"] and probe["caps"])
        record("piper-exact-pinned-inputs",probe["model"]==MODEL_SHA and probe["config"]==config_sha
               and probe["text"]==source_sha,config_digest=config_sha)
        if not all(results):
            return 1
        ctx=Context("piper-fixture","session","profile",1,"local","LOCAL_ONLY")
        rules=tuple(profiles.Rule(a,(),"allow") for a in ("host_shell","filesystem_read","filesystem_write"))
        gate=FixtureGateway(ctx,rules,clock=profiles.PolicyClock())
        call=CapabilityCall("piper",VERSION,"neural_text_to_wav",
                  (("artifact","public-text"),("digest",source_sha),("model_digest",MODEL_SHA),
                   ("config_digest",config_sha),("language","tr"),("output","fixture-wav")))
        req=call.request(ctx)
        grant=gate.fixture_grant("piper-voice",req,180)
        job=SpeechFixture(gate,ctx,call,grant)
        job.checkpoint()
        info=run(SYNTH)
        record("piper-real-cpu-engine",info=={"piper":VERSION,"onnxruntime":"1.22.1","providers":["CPUExecutionProvider"]})
        path=out/"voice.wav"
        if path.is_symlink() or not path.is_file() or not 44<path.stat().st_size<=MAX_WAV:
            job.cancel()
            return 1
        data=path.read_bytes()
        record("piper-inputs-unchanged",digest(model)==MODEL_SHA and digest(config)==config_sha
               and digest(source)==source_sha)
        record("piper-exact-output-set",sorted(p.name for p in out.iterdir())==["voice.wav"])
        if not all(results):
            job.cancel()
            return 1
        job.complete(SpeechResult(req.target,source_sha,"tr","fixture-wav",data,MODEL_SHA,config_sha))
        receipt=job.result()
        record("piper-real-typed-pcm-receipt",receipt[4]==hashlib.sha256(data).hexdigest() and receipt[5]==22050,
               rate=receipt[5],frames=receipt[6])
        job.cancel()
        try:
            job.result()
        except Refused:
            denied=True
        else:
            denied=False
        record("piper-revoked-result-denied",denied)
    return 0 if len(results)==9 and all(results) else 1

if __name__=="__main__":
    raise SystemExit(main(*sys.argv[1:]))
