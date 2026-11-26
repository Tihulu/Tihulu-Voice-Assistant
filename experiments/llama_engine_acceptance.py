# SPDX-License-Identifier: AGPL-3.0-only
"""Actual CPU-only llama.cpp with public tiny GGUF in a disposable namespace.
Not Jan, Qwen, chat quality acceptance, a general network client or a runtime
authorization mechanism. Dependency provisioning is separate from inference.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time

MODEL_SHA="047bf46455a544931cff6fef14d7910154c56afbc23ab1c5e56a72e69912c04b"
ENGINE_REV="4762ad7316dcdec20016ab5985fb46a27902204d"
MODEL="stories260k-"+MODEL_SHA
MAX_REPLY=32768
PROMPTS=("Once upon a time, there was a little bird named","A little girl found a small dog and")

def parse_reply(raw):
    if type(raw) is not bytes or not 0<len(raw)<=MAX_REPLY:
        raise ValueError("llm_response_size")
    def unique(pairs):
        obj={}
        for k,v in pairs:
            if k in obj:
                raise ValueError("llm_duplicate_field")
            obj[k]=v
        return obj
    obj=json.loads(raw.decode("utf-8"),object_pairs_hook=unique)
    if (type(obj) is not dict or set(obj)!={"content","tokens","stop","stop_type","model","truncated"}
            or obj["model"]!=MODEL or obj["stop"] is not True or obj["truncated"] is not False
            or obj["stop_type"] not in ("eos","limit") or type(obj["content"]) is not str
            or not obj["content"].strip() or len(obj["content"].encode("utf-8"))>4096
            or type(obj["tokens"]) is not list or not 1<=len(obj["tokens"])<=32
            or any(type(t) is not int or not 0<=t<=1000000 for t in obj["tokens"])):
        raise ValueError("llm_response_contract")
    return obj["content"],tuple(obj["tokens"])

STT_SHA="be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21"
STT_BLOB="3184d372cd2f8b804d3a540c70ec50d927b335d2"
STT_REV="4979e04f5dcaccb36057e059bbaed8a2f5288315"

def file_digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda:handle.read(1048576),b""):
            h.update(block)
    return h.hexdigest()

def child(host_net,host_port,mode="basic"):
    sys.path.insert(0,"/work/code")
    from tihulu_jarvis import profiles
    from gateway import Context,FixtureGateway,Refused
    from inference import Candidate
    from adapter_protocol import LocalAdapter,prepare,local_request
    from speech_result import SpeechResult,MAX_WAV
    from capabilities import CapabilityCall
    from transcription_result import TranscriptFixture,Transcript
    import resource
    results=[]
    def record(case,ok,**extra):
        results.append(bool(ok))
        print(json.dumps(dict(case=case,status="PASS" if ok else "FAIL",**extra)),flush=True)
    record("llm-network-namespace",os.readlink("/proc/self/ns/net")!=host_net)
    try:
        with socket.create_connection(("127.0.0.1",int(host_port)),timeout=1):
            denied=False
    except OSError:
        denied=True
    record("llm-host-loopback-denied",denied)
    record("llm-pinned-model",hashlib.sha256(Path("/work/model.gguf").read_bytes()).hexdigest()==MODEL_SHA)
    status=dict(line.split(":",1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    record("llm-no-host-capabilities",all(int(status[k].strip(),16)==0 for k in ("CapEff","CapPrm","CapAmb"))
           and not Path("/dev/dri").exists() and not Path("/dev/snd").exists()
           and not Path("/work/not-approved").exists())
    for kind,value in ((resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,4194304),
                       (resource.RLIMIT_CPU,60),(resource.RLIMIT_NOFILE,256)):
        resource.setrlimit(kind,(value,value))
    if not all(results):
        return 1
    def request(method,path,payload=None):
        conn=http.client.HTTPConnection("127.0.0.1",8080,timeout=8)
        try:
            conn.request(method,path,body=payload,headers={"Content-Type":"application/json",
                                                          "Connection":"close"})
            response=conn.getresponse()
            raw=response.read(MAX_REPLY+1)
            if response.status!=200 or len(raw)>MAX_REPLY:
                raise RuntimeError("llm_http_status_"+str(response.status))
            return raw
        finally:
            conn.close()
    # WAV file synthesis only; no PulseAudio server or shared memory.
    config=Path("/tmp/pulse-client.conf")
    config.write_text("autospawn = no\nenable-shm = no\n",encoding="ascii")
    os.environ["PULSE_CLIENTCONFIG"]=str(config)
    os.environ["PULSE_SERVER"]="unix:/tmp/no-pulse-server"
    with tempfile.TemporaryFile() as version_out,tempfile.TemporaryFile() as version_err:
        voice_version=subprocess.run(["/usr/bin/espeak-ng","--version"],stdin=subprocess.DEVNULL,
                                     stdout=version_out,stderr=version_err,timeout=8,check=False)
        version_out.seek(0)
        version_err.seek(0)
        info,detail=version_out.read(4097),version_err.read(4097)
        if voice_version.returncode or len(info)>4096 or len(detail)>4096:
            print(json.dumps({"case":"llm-speech-version","status":"HOLD",
                              "exit":voice_version.returncode,"reason":detail.decode("utf-8","replace")[:1000]}),flush=True)
            return 1
        version=re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b",info.decode("utf-8"))
        if not version:
            return 1
        version=version.group(1)
    ctx=Context("llm-fixture","session","profile",1,"local","LOCAL_ONLY")
    rules=tuple(profiles.Rule(x,(),"allow") for x in ("filesystem_read","filesystem_write","host_shell"))
    gate=FixtureGateway(ctx,rules,clock=profiles.PolicyClock())
    stt=None
    if mode=="voice-chain":
        audio=Path("/work/sample.wav").read_bytes()
        pinned=(file_digest("/work/stt.bin")==STT_SHA
                and hashlib.sha1(("blob "+str(len(audio))+"\0").encode()+audio).hexdigest()==STT_BLOB)
        record("voice-chain-pinned-stt-inputs",pinned)
        if not pinned:
            return 1
        digest=hashlib.sha256(audio).hexdigest()
        call=CapabilityCall("whisper_cpp",STT_REV,"transcribe_wav",
             (("artifact","public-sample"),("digest",digest),("model_digest",STT_SHA),
              ("language","en"),("output","chain-transcript")))
        req=call.request(ctx)
        grant=gate.fixture_grant("chain-stt",req,180)
        stt=TranscriptFixture(gate,ctx,call,grant)
        stt.checkpoint()
        with open("/tmp/stt.log","xb") as stt_log:
            subprocess.run(["/work/whisper-cli","-m","/work/stt.bin","-f","/work/sample.wav",
                           "-l","en","-t","2","-ng","-nt","-otxt","-of","/tmp/chain-transcript"],
                          stdin=subprocess.DEVNULL,stdout=stt_log,stderr=stt_log,timeout=60,check=True)
        dest=Path("/tmp/chain-transcript.txt")
        if dest.is_symlink() or not dest.is_file() or not 0<dest.stat().st_size<=1024:
            stt.cancel()
            return 1
        text=dest.read_text(encoding="utf-8").strip()
        norm=" ".join(re.findall(r"[a-z]+",text.lower()))
        semantic=("ask not what your country can do for you" in norm
                  and "ask what you can do for your country" in norm
                  and file_digest("/work/stt.bin")==STT_SHA and file_digest("/work/sample.wav")==digest)
        record("voice-chain-real-transcript",semantic)
        if not semantic:
            stt.cancel()
            return 1
        stt.complete(Transcript(req.target,digest,STT_SHA,"en","chain-transcript",text))
    with open("/tmp/llama.log","wb") as log:
        proc=subprocess.Popen(["/work/llama-server","--host","127.0.0.1","--port","8080",
                               "--model","/work/model.gguf","--alias",MODEL,
                               "--threads","2","--threads-http","2","--ctx-size","128",
                               "--parallel","1","--n-gpu-layers","0","--no-webui"],
                              stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+25
            while True:
                if proc.poll() is not None or time.monotonic()>=deadline:
                    raise RuntimeError("llm_startup_hold")
                try:
                    if json.loads(request("GET","/health")).get("status")=="ok":
                        break
                except (OSError,RuntimeError,http.client.HTTPException):
                    pass
                time.sleep(.1)
            record("llm-real-server-ready",True)
            candidate=Candidate(MODEL,"llm","local","llama-"+ENGINE_REV,True,True,16,80)
            def synthesize(adapter,identity,result,digest):
                # Explicit additional fixture permission, never auto-play
                # or an inherited write grant from the text response.
                artifact=identity+"-text"
                output=identity+"-wav"
                speech_call=adapter.prepare_speech(version,"en",artifact,output)
                speech_req=speech_call.request(ctx)
                speech_grant=gate.fixture_grant(identity+"-speech",speech_req,60)
                voice,text=adapter.start_speech(version,"en",artifact,output,speech_grant)
                source=Path("/tmp/"+artifact+".txt")
                dest=Path("/tmp/"+output+".wav")
                with source.open("xb") as handle:
                    handle.write(text)
                if dest.exists() or dest.is_symlink():
                    raise RuntimeError("speech destination already exists")
                voice.checkpoint()
                subprocess.run(["/usr/bin/espeak-ng","-v","en","-s","150","-f",str(source),
                                "-w",str(dest)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                               timeout=12,check=True)
                if dest.is_symlink() or not dest.is_file() or not 44<dest.stat().st_size<=MAX_WAV:
                    voice.cancel()
                    raise RuntimeError("speech output invalid")
                data=dest.read_bytes()
                if source.read_bytes()!=result or hashlib.sha256(text).hexdigest()!=digest:
                    voice.cancel()
                    raise RuntimeError("speech source mismatch")
                voice.complete(SpeechResult(speech_req.target,digest,"en",output,data))
                receipt=voice.result()
                return voice,receipt,hashlib.sha256(data).hexdigest()
            digests=[]
            for i,prompt in enumerate(PROMPTS):
                payload=json.dumps({"prompt":prompt,"n_predict":16,"temperature":0,"seed":42,
                       "stream":False,"return_tokens":True,"cache_prompt":False,
                       "response_fields":["content","tokens","stop","stop_type","model","truncated"]},
                       separators=(",",":")).encode("utf-8")
                identity="turn"+str(i)
                call=prepare(candidate,identity,"llm",payload)
                req=local_request(ctx,call)
                grant=gate.fixture_grant(identity,req,60)
                adapter=LocalAdapter(gate,ctx,candidate,identity,"llm",payload,grant)
                adapter.begin_fixture_transport(payload)
                adapter.checkpoint_fixture_transport()
                raw=request("POST","/completion",payload)
                adapter.checkpoint_fixture_transport()
                content,tokens=parse_reply(raw)
                adapter.event(identity,1,"text",content)
                adapter.event(identity,2,"completed",None)
                result=adapter.result()
                digest=hashlib.sha256(result).hexdigest()
                record("llm-real-inference-"+str(i),result==content.encode("utf-8"),
                       tokens=len(tokens),result_digest=digest)
                digests.append(digest)
                voice,receipt,actual=synthesize(adapter,identity,result,digest)
                record("llm-real-dependent-speech-"+str(i),receipt[4]==actual,
                       rate=receipt[5],frames=receipt[6])
                gate.revoke(identity)
                try:
                    voice.result()
                except Refused:
                    speech_denied=True
                else:
                    speech_denied=False
                record("llm-revoked-dependent-speech-"+str(i),speech_denied)
                try:
                    adapter.result()
                except Refused:
                    revoked=True
                else:
                    revoked=False
                record("llm-revoked-result-"+str(i),revoked)
            record("llm-distinct-real-results",len(set(digests))==2)
            if stt is not None:
                identity="chain-turn"
                req=stt.prepare_local_turn(candidate,identity,"en",native_completion=True)
                grant=gate.fixture_grant(identity,req,60)
                adapter,wire=stt.start_local_turn(candidate,identity,"en",grant,native_completion=True)
                verified=stt.result()[0].text
                exact=json.loads(wire)["prompt"]==verified
                try:
                    adapter.begin_fixture_transport(wire+b" ")
                except Refused:
                    rejected=True
                else:
                    rejected=False
                record("voice-chain-exact-native-wire",exact and rejected)
                if not exact or not rejected:
                    return 1
                adapter.begin_fixture_transport(wire)
                adapter.checkpoint_fixture_transport()
                raw=request("POST","/completion",wire)
                adapter.checkpoint_fixture_transport()
                content,tokens=parse_reply(raw)
                adapter.event(identity,1,"text",content)
                adapter.event(identity,2,"completed",None)
                result=adapter.result()
                digest=hashlib.sha256(result).hexdigest()
                record("voice-chain-real-inference",result==content.encode("utf-8"),tokens=len(tokens))
                voice,receipt,actual=synthesize(adapter,identity,result,digest)
                record("voice-chain-real-speech",receipt[4]==actual,rate=receipt[5],frames=receipt[6])
                stt.cancel()
                denied=0
                for check in (stt.result,adapter.result,voice.result):
                    try:
                        check()
                    except Refused:
                        denied+=1
                record("voice-chain-transitive-revoke",denied==3)
        except (ValueError,RuntimeError,OSError,http.client.HTTPException,subprocess.SubprocessError) as exc:
            print(json.dumps({"case":"llm-runtime","status":"HOLD","reason":str(exc)[:1000]}),flush=True)
            with open("/tmp/llama.log","rb") as detail:
                print(detail.read(4096).decode("utf-8","replace"),flush=True)
            return 1
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    record("llm-server-stopped",proc.poll() is not None)
    return 0 if len(results)==(21 if mode=="voice-chain" else 15) and all(results) else 1

def main(binary,model,*stt_inputs):
    binary,model=Path(binary).resolve(),Path(model).resolve()
    if os.getuid()==0 or not binary.is_file() or not model.is_file():
        raise RuntimeError("invalid fixture inputs")
    if model.stat().st_size>2*1024*1024 or hashlib.sha256(model.read_bytes()).hexdigest()!=MODEL_SHA:
        raise RuntimeError("model digest mismatch")
    if stt_inputs and len(stt_inputs)!=3:
        raise RuntimeError("invalid voice-chain fixture inputs")
    stt_paths=tuple(Path(p).resolve() for p in stt_inputs)
    if stt_paths:
        cli,stt_model,sample=stt_paths
        if (not all(p.is_file() for p in stt_paths) or not 0<sample.stat().st_size<=1048576
                or not 0<stt_model.stat().st_size<=85000000 or file_digest(stt_model)!=STT_SHA):
            raise RuntimeError("invalid pinned STT inputs")
    with tempfile.TemporaryDirectory(prefix="jarvis-llm-") as tmp:
        root=Path(tmp)
        marker=root/"not-approved"
        marker.write_text("public fixture marker")
        repo=Path(__file__).resolve().parents[1]
        code=root/"code"
        import shutil
        for name in ("gateway.py","inference.py","adapter_protocol.py","capabilities.py","speech_result.py","transcription_result.py"):
            dst=code/name
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(repo/"experiments"/name,dst)
        for name in ("__init__.py","profiles.py","selected_csv.py","temperature_engine.py","voice/__init__.py","voice/m1.py"):
            dst=code/"tihulu_jarvis"/name
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(repo/"src/tihulu_jarvis"/name,dst)
        prefix=["/usr/bin/bwrap","--unshare-all","--die-with-parent","--new-session",
                "--cap-drop","ALL","--clearenv","--ro-bind","/usr","/usr"]
        for name in ("bin","sbin","lib","lib64"):
            path=Path("/"+name)
            if path.is_symlink():
                target=os.readlink(path)
                if target not in ("usr/"+name,"/usr/"+name):
                    raise RuntimeError("unexpected system layout")
                prefix+=["--symlink",target,"/"+name]
            elif path.exists():
                raise RuntimeError("non-merged system layout")
        prefix+=["--proc","/proc","--dev","/dev","--tmpfs","/tmp","--dir","/work",
                 "--ro-bind",str(binary),"/work/llama-server","--ro-bind",str(model),"/work/model.gguf",
                 "--ro-bind",str(code),"/work/code","--ro-bind",str(Path(__file__).resolve()),"/work/runner.py",
                 "--setenv","HOME","/tmp","--setenv","PATH","/usr/bin","--setenv","LC_ALL","C.UTF-8",
                 "--chdir","/work","--remount-ro","/"]
        if stt_paths:
            for p,destination in zip(stt_paths,("/work/whisper-cli","/work/stt.bin","/work/sample.wav")):
                prefix[-2:-2]=["--ro-bind",str(p),destination]
        host_net=os.readlink("/proc/self/ns/net")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0))
            listener.listen(1)
            port=str(listener.getsockname()[1])
            if port=="8080":
                raise RuntimeError("port collision")
            proc=subprocess.Popen(prefix+["--","/usr/bin/python3","-I","/work/runner.py","--child",host_net,port,
                                         "voice-chain" if stt_paths else "basic"],
                                  stdin=subprocess.DEVNULL,start_new_session=True,
                                  env={"PATH":"/usr/bin:/bin","LC_ALL":"C.UTF-8"})
            try:
                code_status=proc.wait(timeout=150 if stt_paths else 90)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL)
                proc.wait(timeout=5)
                raise
            listener.setblocking(False)
            try:
                connection,_=listener.accept()
            except BlockingIOError:
                untouched=True
            else:
                connection.close()
                untouched=False
            print(json.dumps({"case":"llm-host-listener-untouched","status":"PASS" if untouched else "FAIL"}),flush=True)
        unchanged=hashlib.sha256(model.read_bytes()).hexdigest()==MODEL_SHA
        print(json.dumps({"case":"llm-model-unchanged","status":"PASS" if unchanged else "FAIL"}),flush=True)
        return 0 if code_status==0 and untouched and unchanged else 1

if __name__=="__main__":
    raise SystemExit(child(*sys.argv[2:]) if len(sys.argv)==5 and sys.argv[1]=="--child" else main(*sys.argv[1:]))
