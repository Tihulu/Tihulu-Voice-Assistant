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
from types import MappingProxyType
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from capabilities import CapabilityCall
from speech_result import SpeechFixture,SpeechResult,MAX_WAV
from fixture_lifetime import FixtureLifetime

MODEL_SHA="2844717f524ab965d3fe86e60562cbb601d3e456836efcc2196cc3a14112a8fb"
CONFIG_SHA="13ebd7810f1b61b5027583cf3131a0a233b6ea81c38f2200ebc4ff41c3cca039"
VERSION="1.8.0"
VOICES=MappingProxyType({
    "tr":("2844717f524ab965d3fe86e60562cbb601d3e456836efcc2196cc3a14112a8fb","13ebd7810f1b61b5027583cf3131a0a233b6ea81c38f2200ebc4ff41c3cca039","tr","Merhaba. Bugün güzel bir gün."),
    "en":("5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f","efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0","en-us","Hello. Today is a beautiful day."),
    "de":("7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819","34342a789afa94f724419fac8ce5f1387f12f5968644ef5894866145e463e004","de","Guten Tag. Heute ist ein schöner Tag."),
})

def voice_profile(language):
    if type(language) is not str or language not in VOICES:
        raise Refused("unknown_piper_voice")
    return VOICES[language]
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

def main(site,model,config,language="tr",*stt_inputs):
    model_sha,config_pin,phoneme_voice,text=voice_profile(language)
    if stt_inputs:
        from voice_quality import targeted_corpus
        text=next(s[2] for s in targeted_corpus() if s[1]==language)
        if len(stt_inputs) not in (2,3) or (len(stt_inputs)==3 and stt_inputs[2]!="auto"):
            raise RuntimeError("invalid STT roundtrip inputs")
    site,model,config=map(lambda p:Path(p).resolve(),(site,model,config))
    if (os.getuid()==0 or not site.is_dir() or not model.is_file() or not config.is_file()
            or not 0<model.stat().st_size<=70000000 or digest(model)!=model_sha
            or not 0<config.stat().st_size<=16384 or digest(config)!=config_pin):
        raise RuntimeError("invalid pinned voice inputs")
    cfg=json.loads(config.read_text(encoding="utf-8"))
    if (cfg["audio"]["sample_rate"]!=22050 or cfg["espeak"]["voice"]!=phoneme_voice
            or cfg["phoneme_type"]!="espeak"):
        raise RuntimeError("voice config mismatch")
    config_sha=digest(config)
    auto_detection=len(stt_inputs)==3
    stt_paths=tuple(Path(p).resolve() for p in stt_inputs[:2])
    STT_SHA="1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b"
    if stt_paths and (not all(p.is_file() for p in stt_paths) or not 0<stt_paths[1].stat().st_size<=500000000 or digest(stt_paths[1])!=STT_SHA):
        raise RuntimeError("invalid pinned STT model")
    source_sha=hashlib.sha256(text.encode()).hexdigest()
    results=[]
    def record(case,ok,**extra):
        results.append(bool(ok))
        print(json.dumps(dict(case=case,language=language,status="PASS" if ok else "FAIL",**extra)),flush=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-piper-") as temp, FixtureLifetime() as lifetime:
        root=Path(temp)
        out=root/"out"
        out.mkdir(mode=0o700)
        source=root/"text.txt"
        source.write_text(text,encoding="utf-8")
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
        system_prefix=prefix.copy()
        prefix+=["--proc","/proc","--dev","/dev","--tmpfs","/tmp","--dir","/work",
                 "--ro-bind",str(site),"/work/site","--ro-bind",str(model),"/work/voice.onnx",
                 "--ro-bind",str(config),"/work/voice.json","--ro-bind",str(source),"/work/text.txt",
                 "--bind",str(out),"/work/out","--setenv","HOME","/tmp","--setenv","PATH","/usr/bin",
                 "--setenv","LC_ALL","C.UTF-8","--setenv","OPENBLAS_NUM_THREADS","2",
                 "--setenv","OMP_NUM_THREADS","2","--chdir","/work","--remount-ro","/"]
        def run(code,*args,timeout=120,scope=None,parse_json=True,capture_stderr=False):
            with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
                proc=subprocess.Popen((prefix if scope is None else scope)+["--","/usr/bin/python3","-I","-B","-c",code]+list(args),
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
                return err if capture_stderr else json.loads(data) if parse_json else data
        host=os.readlink("/proc/self/ns/net")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0))
            listener.listen(1)
            probe=run(PROBE,str(listener.getsockname()[1]),timeout=15)
        record("piper-private-network",probe["network"]!=host and probe["denied"])
        record("piper-private-readonly-inputs",probe["readonly"] and probe["hidden"])
        record("piper-no-device-or-capabilities",probe["devices"] and probe["caps"])
        record("piper-exact-pinned-inputs",probe["model"]==model_sha and probe["config"]==config_sha
               and probe["text"]==source_sha,config_digest=config_sha)
        if not all(results):
            return 1
        ctx=Context("piper-fixture","session","profile",1,"local","LOCAL_ONLY")
        rules=tuple(profiles.Rule(a,(),"allow") for a in ("host_shell","filesystem_read","filesystem_write"))
        gate=lifetime.track(FixtureGateway(ctx,rules,clock=profiles.PolicyClock()))
        call=CapabilityCall("piper",VERSION,"neural_text_to_wav",
                  (("artifact","public-text"),("digest",source_sha),("model_digest",model_sha),
                   ("config_digest",config_sha),("language",language),("output","fixture-wav")))
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
        record("piper-inputs-unchanged",digest(model)==model_sha and digest(config)==config_sha
               and digest(source)==source_sha)
        record("piper-exact-output-set",sorted(p.name for p in out.iterdir())==["voice.wav"])
        if not all(results):
            job.cancel()
            return 1
        job.complete(SpeechResult(req.target,source_sha,language,"fixture-wav",data,model_sha,config_sha))
        receipt=job.result()
        record("piper-real-typed-pcm-receipt",receipt[4]==hashlib.sha256(data).hexdigest() and receipt[5]==22050,
               rate=receipt[5],frames=receipt[6])
        transcript=None
        detector=None
        if stt_paths:
            from whisper_engine_acceptance import ENGINE_REV,WRAPPER as STT_WRAPPER
            from transcription_result import Transcript
            from voice_quality import word_errors,acceptable
            stt_out=root/"stt-out"
            stt_out.mkdir(mode=0o700)
            # Do not mount Piper dependencies, text or neural voice model into STT.
            stt_scope=system_prefix+["--proc","/proc","--dev","/dev","--tmpfs","/tmp","--dir","/work",
                "--ro-bind",str(stt_paths[0]),"/work/whisper-cli","--ro-bind",str(stt_paths[1]),"/work/model.bin",
                "--ro-bind",str(path),"/work/audio.wav","--bind",str(stt_out),"/work/out",
                "--setenv","HOME","/tmp","--setenv","PATH","/usr/bin","--setenv","LC_ALL","C.UTF-8",
                "--chdir","/work","--remount-ro","/"]
            stt_probe="""import hashlib,json
from pathlib import Path
h=hashlib.sha256()
with open('/work/model.bin','rb') as f:
    for block in iter(lambda:f.read(1048576),b''):
        h.update(block)
print(json.dumps({'model':h.hexdigest(),'audio':hashlib.sha256(Path('/work/audio.wav').read_bytes()).hexdigest(),
   'work':sorted(p.name for p in Path('/work').iterdir()),'out':list(Path('/work/out').iterdir()),
   'devices':not Path('/dev/snd').exists() and not Path('/dev/dri').exists()}))
"""
            seen=run(stt_probe,scope=stt_scope,timeout=15)
            scoped=seen=={"model":STT_SHA,"audio":receipt[4],"work":["audio.wav","model.bin","out","whisper-cli"],"out":[],"devices":True}
            record("piper-stt-exact-private-scope",scoped)
            if not scoped:
                job.cancel()
                return 1
            if auto_detection:
                from language_detection import Detection,parse_detection
                detection_call=job.prepare_detection(ENGINE_REV,STT_SHA,"detected-language")
                detection_req=detection_call.request(ctx)
                detection_grant=gate.fixture_grant("piper-detect",detection_req,60)
                detector=job.start_detection(ENGINE_REV,STT_SHA,"detected-language",detection_grant)
                detector.checkpoint()
                log=run(STT_WRAPPER,"/work/whisper-cli","-m","/work/model.bin","-f","/work/audio.wav",
                        "-l","auto","-dl","-t","2","-ng","-nt",scope=stt_scope,parse_json=False,capture_stderr=True)
                try:
                    detected,ppm=parse_detection(log)
                except Refused as exc:
                    detector.cancel()
                    job.cancel()
                    record("piper-stt-auto-detection",False,reason=str(exc))
                    return 1
                record("piper-stt-auto-detection",detected==language,detected=detected,confidence_ppm=ppm)
                unchanged=(not list(stt_out.iterdir()) and digest(stt_paths[1])==STT_SHA and path.read_bytes()==data)
                record("piper-stt-detection-no-output-mutation",unchanged)
                if detected!=language or not unchanged:
                    detector.cancel()
                    job.cancel()
                    return 1
                detector.complete(Detection(detection_req.target,receipt[4],STT_SHA,"detected-language",detected,ppm))
                stt_call=detector.prepare_transcription("recognized-text")
                stt_req=stt_call.request(ctx)
                stt_grant=gate.fixture_grant("piper-stt",stt_req,60)
                transcript=detector.start_transcription("recognized-text",stt_grant)
            else:
                stt_call=job.prepare_transcription(ENGINE_REV,STT_SHA,"recognized-text")
                stt_req=stt_call.request(ctx)
                stt_grant=gate.fixture_grant("piper-stt",stt_req,60)
                transcript=job.start_transcription(ENGINE_REV,STT_SHA,"recognized-text",stt_grant)
            transcript.checkpoint()
            run(STT_WRAPPER,"/work/whisper-cli","-m","/work/model.bin","-f","/work/audio.wav",
                "-l",dict(stt_call.args)["language"],"-t","2","-ng","-nt","-otxt","-of","/work/out/transcript",
                scope=stt_scope,parse_json=False)
            transcript_path=stt_out/"transcript.txt"
            if transcript_path.is_symlink() or not transcript_path.is_file() or not 0<transcript_path.stat().st_size<=16384:
                transcript.cancel()
                job.cancel()
                return 1
            recognized=transcript_path.read_text(encoding="utf-8").strip()
            errors,count=word_errors(text,recognized,language)
            ok=acceptable(errors,count)
            record("piper-stt-semantic",ok,errors=errors,reference_words=count,recognized=recognized[:200])
            preserved=digest(stt_paths[1])==STT_SHA and path.read_bytes()==data and digest(source)==source_sha and sorted(p.name for p in stt_out.iterdir())==["transcript.txt"]
            record("piper-stt-inputs-unchanged",preserved)
            if not preserved:
                transcript.cancel()
                job.cancel()
                return 1
            if ok:
                transcript.complete(Transcript(stt_req.target,receipt[4],STT_SHA,language,"recognized-text",recognized))
                record("piper-stt-typed-receipt",transcript.result()[1]==hashlib.sha256(recognized.encode()).hexdigest())
            else:
                transcript.cancel()
                record("piper-stt-typed-receipt",False,reason="quality_gate_failed")
        job.cancel()
        if detector is not None:
            try:
                detector.result()
            except Refused:
                detection_denied=True
            else:
                detection_denied=False
            record("piper-stt-revoke-invalidates-detection",detection_denied)
        if transcript is not None:
            try:
                transcript.result()
            except Refused:
                denied=True
            else:
                denied=False
            record("piper-stt-revoke-invalidates-child",denied)
        try:
            job.result()
        except Refused:
            denied=True
        else:
            denied=False
        record("piper-revoked-result-denied",denied)
    return 0 if len(results)==(17 if auto_detection else 14 if stt_paths else 9) and all(results) else 1

if __name__=="__main__":
    raise SystemExit(main(*sys.argv[1:]))
