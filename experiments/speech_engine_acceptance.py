# SPDX-License-Identifier: AGPL-3.0-only
"""Actual eSpeak NG offline TTS, fixed public CI fixtures, no audio device."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import time
import sys
from voice_quality import word_errors, acceptable, expanded_corpus, speech_filename, targeted_corpus, turbo_short_args, speech_corpus_profile, neural_sample_allowed
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from capabilities import CapabilityCall
from speech_result import SpeechFixture,SpeechResult,MAX_WAV
from conversion_result import ConversionResult
from fixture_lifetime import FixtureLifetime

TEXT={"tr":"Merhaba. Bugün güzel bir gün.","en":"Hello. Today is a beautiful day.",
      "de":"Guten Tag. Heute ist ein schöner Tag."}
WRAPPER="""import os,resource,sys
for kind,value in ((resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,1048576),
                   (resource.RLIMIT_CPU,10),(resource.RLIMIT_NOFILE,128)):
    resource.setrlimit(kind,(value,value))
os.execv(sys.argv[1],sys.argv[1:])
"""
PROBE="""import os,socket,sys,json
from pathlib import Path
status=dict(line.split(":",1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
try:
    with socket.create_connection(("127.0.0.1",int(sys.argv[2])),timeout=1):
        denied=False
except OSError:
    denied=True
try:
    with open("/work/text.txt","ab"):
        readonly=False
except OSError:
    readonly=True
print(json.dumps({"namespace":os.readlink("/proc/self/ns/net"),"denied":denied,
    "readonly":readonly,"hidden":not Path(sys.argv[1]).exists(),
    "caps":all(int(status[k].strip(),16)==0 for k in ("CapEff","CapPrm","CapAmb")),
    "no_device":not Path("/dev/snd").exists()}))
"""

# Pinned public model SHA-256 values from the upstream HF revision
# fb5ed9369c26eaf08392504d8446da431e0fffb9. No automatic fallback.
STT_MODELS={
    "tiny":("be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",85000000),
    "base":("60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",160000000),
    "small":("1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",500000000),
    "turbo-q5":("394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",574041195),
}

def main(stt_binary=None,stt_model=None,stt_profile="tiny",corpus="original",piper_root=None):
    if os.getuid()==0:
        raise RuntimeError("root fixture prohibited")
    stt_paths=None
    if stt_binary is not None or stt_model is not None:
        from whisper_engine_acceptance import digest as model_digest
        if stt_profile not in STT_MODELS:
            raise RuntimeError("unknown pinned STT profile")
        MODEL_SHA,model_limit=STT_MODELS[stt_profile]
        if not stt_binary or not stt_model:
            raise RuntimeError("incomplete STT fixture input")
        stt_paths=tuple(Path(p).resolve() for p in (stt_binary,stt_model))
        if (not all(p.is_file() for p in stt_paths) or not 0<stt_paths[1].stat().st_size<=model_limit
                or model_digest(stt_paths[1])!=MODEL_SHA):
            raise RuntimeError("invalid pinned STT model")
        print(json.dumps({"stt_profile":stt_profile,"model_digest":MODEL_SHA}),flush=True)
    mixed_preview,mixed_auto,mixed_extra=speech_corpus_profile(corpus,stt_profile,bool(stt_paths))
    if (corpus in ("mixed-auto-piper","mixed-auto-piper-short","expanded-piper-small"))!=(piper_root is not None):
        raise RuntimeError("invalid neural mixed input profile")
    if piper_root is not None:
        from whisper_engine_acceptance import WRAPPER as STT_WRAPPER_FOR_PIPER
        from piper_engine_acceptance import voice_profile,SYNTH,digest as piper_digest
        piper_root=Path(piper_root).resolve()
        site=piper_root/"site"
        if not site.is_dir():
            raise RuntimeError("missing pinned neural site")
    samples=tuple((lang,lang,text) for lang,text in TEXT.items())
    if corpus in ("expanded","turbo-expanded","expanded-small-short","expanded-piper-small"):
        samples+=expanded_corpus()
    elif corpus=="targeted":
        samples=targeted_corpus()
    print(json.dumps({"corpus":corpus,"sample_count":len(samples)}),flush=True)
    results=[]
    quality=[]
    def qrecord(case,ok,**extra):
        quality.append(bool(ok))
        print(json.dumps(dict(case=case,status="PASS" if ok else "FAIL",**extra)),flush=True)
    def record(case,ok,**extra):
        results.append(bool(ok))
        print(json.dumps(dict(case=case,status="PASS" if ok else "FAIL",**extra)),flush=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-speech-") as tmp, FixtureLifetime() as lifetime:
        root=Path(tmp)
        source=root/"text.txt"
        marker=root/"not-approved"
        marker.write_text("public synthetic marker")
        output=root/"out"
        output.mkdir(mode=0o700)
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
                 "--ro-bind",str(source),"/work/text.txt","--bind",str(output),"/work/out",
                 "--setenv","HOME","/tmp","--setenv","PATH","/usr/bin",
                 "--setenv","LC_ALL","C.UTF-8","--chdir","/work","--remount-ro","/"]
        def run(args,active_prefix=None,*,wrapper=WRAPPER,timeout=20,capture_stderr=False):
            with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
                proc=subprocess.Popen((prefix if active_prefix is None else active_prefix)+["--","/usr/bin/python3","-I","-c",wrapper]+args,
                    stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=True,
                    env={"PATH":"/usr/bin:/bin","LC_ALL":"C.UTF-8"})
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait(timeout=5)
                    raise RuntimeError("speech timeout")
                out.seek(0)
                err.seek(0)
                stdout,stderr=out.read(65537),err.read(65537)
                if len(stdout)>65536 or len(stderr)>65536:
                    raise RuntimeError("speech capture limit")
                if proc.returncode:
                    raise RuntimeError("speech child failed exit="+str(proc.returncode)+": "+stderr.decode("utf-8","replace")[-2000:])
                return stderr if capture_stderr else stdout
        source.write_text(TEXT["en"],encoding="utf-8")
        host=os.readlink("/proc/self/ns/net")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0))
            listener.listen(1)
            probe=json.loads(run(["/usr/bin/python3","-I","-c",PROBE,str(marker),
                                  str(listener.getsockname()[1])]))
        record("speech-isolated-network",probe["namespace"]!=host and probe["denied"])
        record("speech-private-readonly-input",probe["readonly"] and probe["hidden"])
        record("speech-no-audio-device-or-caps",probe["no_device"] and probe["caps"])
        if not all(results):
            return 1
        raw_version=run(["/usr/bin/espeak-ng","--version"]).decode("utf-8")
        version=re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b",raw_version)
        if not version:
            raise RuntimeError("unrecognized eSpeak version")
        print(json.dumps({"espeak_version":version.group(1)}),flush=True)
        ctx=Context("speech-fixture","session","profile",1,"local","LOCAL_ONLY")
        rules=tuple(profiles.Rule(a,(),"allow") for a in ("host_shell","filesystem_read","filesystem_write"))
        gate=lifetime.track(FixtureGateway(ctx,rules,clock=profiles.PolicyClock()))
        ffmpeg_prefix=prefix.copy()
        ffmpeg_prefix[-2:-2]=["--setenv","LD_LIBRARY_PATH",
                              "/usr/lib/x86_64-linux-gnu/blas:/usr/lib/x86_64-linux-gnu/lapack"]
        fv=run(["/usr/bin/ffmpeg","-version"],ffmpeg_prefix).decode("utf-8").splitlines()[0].split()[2]
        digests=set()
        retained=[]
        for case_id,lang,text in samples:
            payload=text.encode("utf-8")
            lang_source=root/("text-"+case_id+".txt")
            lang_source.write_bytes(payload)
            lang_output=root/("voice-"+case_id)
            lang_output.mkdir(mode=0o700)
            scope=prefix.copy()
            scope[scope.index(str(source))]=str(lang_source)
            scope[scope.index(str(output))]=str(lang_output)
            digest=hashlib.sha256(payload).hexdigest()
            dest=lang_output/speech_filename(case_id)
            model_pin=config_pin=None
            if piper_root is not None:
                model_pin,config_pin,phoneme_voice,reference=voice_profile(lang)
                neural_model=piper_root/(lang+".onnx")
                neural_config=piper_root/(lang+".json")
                if (not neural_model.is_file() or not neural_config.is_file()
                        or not 0<neural_model.stat().st_size<=70000000 or not 0<neural_config.stat().st_size<=16384
                        or piper_digest(neural_model)!=model_pin or piper_digest(neural_config)!=config_pin
                        or not neural_sample_allowed(corpus,case_id,lang,text,reference)):
                    raise RuntimeError("invalid pinned neural voice inputs")
                cfg=json.loads(neural_config.read_text(encoding="utf-8"))
                if (cfg["audio"]["sample_rate"]!=22050 or cfg["espeak"]["voice"]!=phoneme_voice
                        or cfg["phoneme_type"]!="espeak"):
                    raise RuntimeError("voice configuration mismatch")
                call=CapabilityCall("piper","1.8.0","neural_text_to_wav",
                      (("artifact","fixture-text-"+case_id),("digest",digest),
                       ("model_digest",model_pin),("config_digest",config_pin),
                       ("language",lang),("output","fixture-audio-"+case_id)))
            else:
                call=CapabilityCall("espeak_ng",version.group(1),"text_to_wav",
                    (("artifact","fixture-text-"+case_id),("digest",digest),("language",lang),
                     ("output","fixture-audio-"+case_id)))
            req=call.request(ctx)
            grant=gate.fixture_grant("voice-"+case_id,req,60)
            job=SpeechFixture(gate,ctx,call,grant)
            if dest.exists() or dest.is_symlink():
                job.cancel()
                return 1
            job.checkpoint()
            scope_probe=json.loads(run(["/usr/bin/python3","-I","-c",
                "import hashlib,json; from pathlib import Path; print(json.dumps({'digest':hashlib.sha256(Path('/work/text.txt').read_bytes()).hexdigest(),'work':sorted(p.name for p in Path('/work').iterdir()),'out':list(Path('/work/out').iterdir())}))"],scope))
            scoped=(scope_probe=={"digest":digest,"work":["out","text.txt"],"out":[]})
            record("speech-exact-private-language-scope-"+case_id,scoped)
            if not scoped:
                job.cancel()
                return 1
            job.checkpoint()
            if piper_root is not None:
                neural_scope=scope.copy()
                neural_scope[-2:-2]=["--ro-bind",str(site),"/work/site",
                    "--ro-bind",str(neural_model),"/work/voice.onnx",
                    "--ro-bind",str(neural_config),"/work/voice.json",
                    "--setenv","OPENBLAS_NUM_THREADS","2","--setenv","OMP_NUM_THREADS","2"]
                # SYNTH has its own fixed resource ceilings. Keep the original
                # per-sample grant TTL, no inherited audio device or network.
                synthesis=json.loads(run(["/usr/bin/python3","-I","-B","-c",SYNTH],neural_scope,
                                         wrapper=STT_WRAPPER_FOR_PIPER,timeout=120))
                raw_output=lang_output/"voice.wav"
                if (synthesis!={"piper":"1.8.0","onnxruntime":"1.22.1","providers":["CPUExecutionProvider"]}
                        or raw_output.is_symlink() or not raw_output.is_file() or dest.exists()
                        or piper_digest(neural_model)!=model_pin or piper_digest(neural_config)!=config_pin):
                    job.cancel()
                    return 1
                raw_output.rename(dest)
            else:
                run(["/usr/bin/espeak-ng","-v",lang,"-s","150","-f","/work/text.txt",
                 "-w","/work/out/"+dest.name],scope)
            if dest.is_symlink() or not dest.is_file() or not 44<dest.stat().st_size<=MAX_WAV:
                job.cancel()
                record("speech-wav-"+case_id,False)
                return 1
            data=dest.read_bytes()
            unchanged=lang_source.read_bytes()==payload
            record("speech-source-preserved-"+case_id,unchanged)
            if not unchanged:
                job.cancel()
                return 1
            job.complete(SpeechResult(req.target,digest,lang,"fixture-audio-"+case_id,data,model_pin,config_pin))
            receipt=job.result()
            result_digest=hashlib.sha256(data).hexdigest()
            record("speech-real-wav-"+case_id,receipt[4]==result_digest,
                   rate=receipt[5],frames=receipt[6],digest=result_digest)
            digests.add(result_digest)
            # Each conversion receives only its exact synthesized WAV and a
            # fresh output directory, not the text or other language files.
            converted=root/("converted-"+case_id)
            converted.mkdir(mode=0o700)
            scope=ffmpeg_prefix.copy()
            i=scope.index(str(source))
            scope[i:i+2]=[str(dest),"/work/input.wav"]
            i=scope.index(str(output))
            scope[i]=str(converted)
            call=job.prepare_conversion(fv,"fixture-flac-"+case_id)
            req=call.request(ctx)
            grant=gate.fixture_grant("convert-"+case_id,req,60)
            conversion=job.start_conversion(fv,"fixture-flac-"+case_id,grant)
            gate.checkpoint(grant,req)
            if hashlib.sha256(dest.read_bytes()).hexdigest()!=result_digest:
                conversion.cancel()
                return 1
            run(["/usr/bin/ffmpeg","-nostdin","-v","error","-n","-i","/work/input.wav",
                 "-c:a","flac","/work/out/audio.flac"],scope)
            flac=converted/"audio.flac"
            ok=not flac.is_symlink() and flac.is_file() and 4<flac.stat().st_size<=MAX_WAV
            record("speech-real-flac-"+case_id,ok)
            if not ok:
                conversion.cancel()
                return 1
            gate.checkpoint(grant,req)
            run(["/usr/bin/ffmpeg","-nostdin","-v","error","-n","-i","/work/out/audio.flac",
                 "-f","s16le","-c:a","pcm_s16le","/work/out/roundtrip.pcm"],scope)
            pcm=converted/"roundtrip.pcm"
            exact=(not pcm.is_symlink() and pcm.is_file() and pcm.stat().st_size==len(data)-44
                   and pcm.read_bytes()==data[44:] and dest.read_bytes()==data)
            record("speech-lossless-pcm-"+case_id,exact)
            if not exact:
                conversion.cancel()
                return 1
            flac_data=flac.read_bytes()
            conversion.complete(ConversionResult(req.target,result_digest,"fixture-flac-"+case_id,"flac",flac_data))
            record("speech-flac-dependent-receipt-"+case_id,conversion.result()[4]==hashlib.sha256(flac_data).hexdigest())
            transcript=None
            if stt_paths:
                from whisper_engine_acceptance import ENGINE_REV,WRAPPER as STT_WRAPPER,digest as model_digest
                from transcription_result import Transcript
                stt_out=root/("stt-"+case_id)
                stt_out.mkdir(mode=0o700)
                stt_scope=prefix.copy()
                i=stt_scope.index(str(source))
                stt_scope[i:i+2]=[str(dest),"/work/sample.wav"]
                stt_scope[stt_scope.index(str(output))]=str(stt_out)
                for path,target in zip(stt_paths,("/work/whisper-cli","/work/model.bin")):
                    stt_scope[-2:-2]=["--ro-bind",str(path),target]
                probe_code="""import hashlib,json
from pathlib import Path
h=hashlib.sha256()
with open('/work/model.bin','rb') as handle:
    for block in iter(lambda:handle.read(1048576),b''):
        h.update(block)
print(json.dumps({'model':h.hexdigest(),'audio':hashlib.sha256(Path('/work/sample.wav').read_bytes()).hexdigest(),
                  'work':sorted(p.name for p in Path('/work').iterdir()),'out':list(Path('/work/out').iterdir()),
                  'no_devices':not Path('/dev/snd').exists() and not Path('/dev/dri').exists()}))
"""
                probe=json.loads(run(["/usr/bin/python3","-I","-c",probe_code],stt_scope))
                scoped=probe=={"model":MODEL_SHA,"audio":result_digest,
                    "work":["model.bin","out","sample.wav","whisper-cli"],"out":[],"no_devices":True}
                qrecord("multilingual-stt-scope-"+case_id,scoped)
                if not scoped:
                    job.cancel()
                    return 1
                stt_call=job.prepare_transcription(ENGINE_REV,MODEL_SHA,"transcript-"+case_id)
                stt_req=stt_call.request(ctx)
                stt_grant=gate.fixture_grant("stt-"+case_id,stt_req,60)
                transcript=job.start_transcription(ENGINE_REV,MODEL_SHA,"transcript-"+case_id,stt_grant)
                transcript.checkpoint()
                args=["/work/whisper-cli","-m","/work/model.bin","-f","/work/sample.wav",
                     "-l",lang,"-t","2","-ng","-nt","-otxt","-of","/work/out/transcript"]
                if stt_profile=="turbo-q5" or corpus=="expanded-small-short":
                    # Separate explicitly labeled short-audio decoder profile.
                    # Preserve CPU, wall, grant and output ceilings.
                    args+=turbo_short_args(receipt[5],receipt[6])
                    print(json.dumps({"case_id":case_id,"decoder":"turbo-short-greedy" if stt_profile=="turbo-q5" else "small-short-greedy","extra_args":args[-6:]}),flush=True)
                try:
                    run(args,stt_scope,wrapper=STT_WRAPPER,timeout=120)
                except RuntimeError as exc:
                    transcript.cancel()
                    qrecord("multilingual-stt-engine-"+case_id,False,reason=str(exc)[:2500])
                    job.cancel()
                    return 1
                path=stt_out/"transcript.txt"
                if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=16384:
                    transcript.cancel()
                    return 1
                recognized=path.read_text(encoding="utf-8").strip()
                errors,count=word_errors(text,recognized,lang)
                ok=acceptable(errors,count)
                qrecord("multilingual-stt-semantic-"+case_id,ok,errors=errors,reference_words=count,language=lang,
                        recognized=recognized[:200])
                unchanged=(model_digest(stt_paths[1])==MODEL_SHA and dest.read_bytes()==data
                           and sorted(p.name for p in stt_out.iterdir())==["transcript.txt"])
                qrecord("multilingual-stt-inputs-unchanged-"+case_id,unchanged)
                if not unchanged:
                    transcript.cancel()
                    return 1
                if ok:
                    transcript.complete(Transcript(stt_req.target,result_digest,MODEL_SHA,lang,
                                                   "transcript-"+case_id,recognized))
                    qrecord("multilingual-stt-typed-receipt-"+case_id,
                            transcript.result()[1]==hashlib.sha256(recognized.encode()).hexdigest())
                else:
                    transcript.cancel()
                    qrecord("multilingual-stt-typed-receipt-"+case_id,False,reason="quality_gate_failed")
            if mixed_preview:
                if transcript is None or not all(quality):
                    job.cancel()
                    return 1
                retained.append((case_id,lang,data,job,transcript,conversion))
                continue
            job.cancel()
            if transcript is not None:
                try:
                    transcript.result()
                except Refused:
                    stt_denied=True
                else:
                    stt_denied=False
                qrecord("multilingual-stt-revoked-result-"+case_id,stt_denied)
            try:
                conversion.result()
            except Refused:
                denied=True
            else:
                denied=False
            record("speech-revoke-invalidates-conversion-"+case_id,denied)
            if not all(results):
                return 1
        if mixed_preview:
            import io,wave
            from mixed_voice_review import MixedVoiceReview,split_pcm
            # All original short WAVs are real engine outputs, with known
            # boundaries; assemble a single canonical PCM fixture in memory.
            cuts=[0]
            joined=io.BytesIO()
            with wave.open(joined,"wb") as out:
                out.setparams((1,2,22050,0,"NONE","not compressed"))
                for case_id,lang,data,job,transcript,conversion in retained:
                    receipt=job.result()
                    if receipt[5]!=22050 or receipt[6]*2!=len(data)-44:
                        raise RuntimeError("mixed segment format mismatch")
                    cuts.append(cuts[-1]+receipt[6])
                    out.writeframes(data[44:])
            mixed_data=joined.getvalue()
            pieces=split_pcm(mixed_data,tuple(cuts))
            record("mixed-real-exact-frame-coverage",len(pieces)==3 and all(
                   p.data==row[2] for p,row in zip(pieces,retained)))
            review=MixedVoiceReview(mixed_data,tuple(cuts),tuple(row[4] for row in retained),MODEL_SHA)
            preview=review.preview()
            record("mixed-real-ordered-language-preview",
                   [r.language for r in preview.segments]==[r[1] for r in retained]
                   and [(r.start_frame,r.end_frame) for r in preview.segments]==list(zip(cuts,cuts[1:])),
                   languages=[r.language for r in preview.segments],frames=cuts[-1],sample_rate=22050)
            # Separate artificial-pause experiment, not a claim about
            # continuous code-switching or automatic language boundaries.
            from mixed_voice_review import silence_boundaries
            paused=io.BytesIO()
            pause_frames=22050
            joins=[]
            with wave.open(paused,"wb") as out:
                out.setparams((1,2,22050,0,"NONE","not compressed"))
                total=0
                for i,row in enumerate(retained):
                    if i:
                        joins.append((total,total+pause_frames))
                        out.writeframes(b"\0\0"*pause_frames)
                        total+=pause_frames
                    out.writeframes(row[2][44:])
                    total+=(len(row[2])-44)//2
            pause_data=paused.getvalue()
            proposed=silence_boundaries(pause_data)
            record("mixed-pause-real-proposal",len(proposed)==4 and all(
                   start<=cut<=end for cut,(start,end) in zip(proposed[1:-1],joins)),
                   cuts=list(proposed),inserted_pause_ranges=joins)
            paused_pieces=split_pcm(pause_data,proposed)
            record("mixed-pause-full-frame-coverage",b"".join(piece.data[44:] for piece in paused_pieces)==pause_data[44:])
            # Previous receipts were for different WAV bytes. They cannot be
            # reused with new boundaries or padding.
            try:
                MixedVoiceReview(pause_data,proposed,tuple(row[4] for row in retained),MODEL_SHA)
            except Refused:
                stale_denied=True
            else:
                stale_denied=False
            record("mixed-pause-old-receipts-denied",stale_denied)
            expected="\n".join(row[4].result()[0].text for row in retained)
            record("mixed-real-exact-reviewed-text",preview.text==expected
                   and preview.text_digest==hashlib.sha256(expected.encode()).hexdigest())
            retained[1][3].cancel()
            try:
                review.preview()
            except Refused:
                denied=True
            else:
                denied=False
            record("mixed-real-revoked-segment-denies-review",denied)
            record("mixed-real-independent-segments-remain-live",
                   all(row[4].result()[0].language==row[1] for row in (retained[0],retained[2])))
            for case_id,lang,data,job,transcript,conversion in retained:
                job.cancel()
                try:
                    transcript.result()
                except Refused:
                    stt_denied=True
                else:
                    stt_denied=False
                qrecord("multilingual-stt-revoked-result-"+case_id,stt_denied)
                try:
                    conversion.result()
                except Refused:
                    denied=True
                else:
                    denied=False
                record("speech-revoke-invalidates-conversion-"+case_id,denied)
        if mixed_auto:
            # A fresh public fixture review session for the changed PCM bytes.
            # Do not revive or inherit the revoked original segment receipts.
            from language_detection import Detection,DetectionFixture,parse_detection,detection_observation
            auto_ctx=Context("mixed-auto-fixture","session","profile",1,"local","LOCAL_ONLY")
            auto_gate=lifetime.track(FixtureGateway(auto_ctx,rules,clock=profiles.PolicyClock()))
            detectors=[]
            transcripts=[]
            owner_choices={0:"tr"} if corpus in ("mixed-auto-reviewed","mixed-auto-reviewed-short") else {}
            reviewed=[]
            try:
                for i,(piece,(_,lang,text)) in enumerate(zip(paused_pieces,samples)):
                    artifact="auto-segment-"+str(i)
                    audio_path=root/(artifact+".wav")
                    audio_path.write_bytes(piece.data)
                    out=root/(artifact+"-out")
                    out.mkdir(mode=0o700)
                    scope=prefix.copy()
                    k=scope.index(str(source))
                    scope[k:k+2]=[str(audio_path),"/work/sample.wav"]
                    scope[scope.index(str(output))]=str(out)
                    for path,target in zip(stt_paths,("/work/whisper-cli","/work/model.bin")):
                        scope[-2:-2]=["--ro-bind",str(path),target]
                    seen=json.loads(run(["/usr/bin/python3","-I","-c",probe_code],scope))
                    scoped=seen=={"model":MODEL_SHA,"audio":piece.digest,
                        "work":["model.bin","out","sample.wav","whisper-cli"],"out":[],"no_devices":True}
                    record("mixed-auto-exact-scope-"+str(i),scoped)
                    if not scoped:
                        return 1
                    call=CapabilityCall("whisper_cpp",ENGINE_REV,"detect_language",
                         (("artifact",artifact),("digest",piece.digest),("model_digest",MODEL_SHA),
                          ("output","auto-language-"+str(i))))
                    req=call.request(auto_ctx)
                    detector=DetectionFixture(auto_gate,auto_ctx,call,auto_gate.fixture_grant("detect-"+str(i),req,60))
                    detectors.append(detector)
                    detector.checkpoint()
                    common=["/work/whisper-cli","-m","/work/model.bin","-f","/work/sample.wav","-t","2","-ng","-nt"]
                    started=time.monotonic()
                    try:
                        raw=run(common+["-l","auto","-dl"],scope,wrapper=STT_WRAPPER,timeout=120,capture_stderr=True)
                    except RuntimeError as exc:
                        detector.cancel()
                        record("mixed-auto-real-detection-"+str(i),False,reason=str(exc)[:2500],
                               elapsed_seconds=round(time.monotonic()-started,3))
                        continue
                    print(json.dumps({"segment":i,"phase":"detect","elapsed_seconds":round(time.monotonic()-started,3)}),flush=True)
                    observed,score=None,None
                    target="auto-text-"+str(i)
                    manual=False
                    try:
                        observed,score=detection_observation(raw)
                        detected,ppm=parse_detection(raw)
                    except Refused as exc:
                        if i not in owner_choices or observed is None:
                            detector.cancel()
                            record("mixed-auto-real-detection-"+str(i),False,reason=str(exc),
                                   observed_language=observed,confidence_ppm=score)
                            continue
                        # Explicit fixed public test operator choice, not
                        # inference or automatic fallback.
                        hold=detector.hold_observation(raw)
                        try:
                            detector.result()
                        except Refused:
                            denied=True
                        else:
                            denied=False
                        record("mixed-reviewed-auto-held-"+str(i),denied and hold[:2]==(observed,score),
                               observed_language=observed,confidence_ppm=score)
                        selected=owner_choices[i]
                        call,review_req=detector.prepare_override(selected,target)
                        req=call.request(auto_ctx)
                        trans_grant=auto_gate.fixture_grant("transcribe-"+str(i),req,60)
                        try:
                            detector.start_override(selected,target,trans_grant,None)
                        except Refused:
                            denied=True
                        else:
                            denied=False
                        record("mixed-reviewed-no-transcribe-before-owner-"+str(i),denied and not list(out.iterdir()))
                        if not denied:
                            return 1
                        approval=auto_gate.fixture_grant("language-owner-"+str(i),review_req,60)
                        trans=detector.start_override(selected,target,trans_grant,approval)
                        reviewed.append((i,approval))
                        detected=selected
                        manual=True
                    if not manual:
                        correct=detected==lang and not list(out.iterdir()) and audio_path.read_bytes()==piece.data
                        record("mixed-auto-real-detection-"+str(i),correct,detected=detected,confidence_ppm=ppm)
                        if not correct:
                            detector.cancel()
                            continue
                        detector.complete(Detection(req.target,piece.digest,MODEL_SHA,"auto-language-"+str(i),detected,ppm))
                        call=detector.prepare_transcription(target)
                        req=call.request(auto_ctx)
                        trans=detector.start_transcription(target,auto_gate.fixture_grant("transcribe-"+str(i),req,60))
                    else:
                        record("mixed-reviewed-exact-owner-choice-"+str(i),detected==lang and
                               dict(call.args)["digest"]==piece.digest and audio_path.read_bytes()==piece.data)
                    transcripts.append(trans)
                    trans.checkpoint()
                    decode=common+["-l",dict(call.args)["language"],"-otxt","-of","/work/out/transcript"]
                    if stt_profile=="turbo-q5" or corpus in ("mixed-auto-reviewed-short","mixed-auto-piper-short"):
                        from speech_result import pcm_info
                        decode+=turbo_short_args(*pcm_info(piece.data))
                    started=time.monotonic()
                    try:
                        run(decode,scope,wrapper=STT_WRAPPER,timeout=120)
                    except RuntimeError as exc:
                        trans.cancel()
                        detector.cancel()
                        record("mixed-auto-real-quality-"+str(i),False,reason=str(exc)[:2500])
                        return 1
                    print(json.dumps({"segment":i,"phase":"transcribe","elapsed_seconds":round(time.monotonic()-started,3),
                                      "decoder":"bounded-short" if "-ac" in decode else "default"}),flush=True)
                    path=out/"transcript.txt"
                    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=16384:
                        return 1
                    recognized=path.read_text(encoding="utf-8").strip()
                    errors,count=word_errors(text,recognized,lang)
                    ok=acceptable(errors,count)
                    record("mixed-auto-real-quality-"+str(i),ok,errors=errors,reference_words=count,recognized=recognized[:200])
                    if not ok:
                        return 1
                    unchanged=(audio_path.read_bytes()==piece.data and model_digest(stt_paths[1])==MODEL_SHA
                               and sorted(p.name for p in out.iterdir())==["transcript.txt"])
                    record("mixed-auto-inputs-unchanged-"+str(i),unchanged)
                    if not unchanged:
                        return 1
                    trans.complete(Transcript(req.target,piece.digest,MODEL_SHA,detected,target,recognized))
                if len(transcripts)!=len(paused_pieces):
                    record("mixed-auto-fresh-ordered-review",False,reason="segment_detection_hold")
                    return 1
                from mixed_voice_review import inspect_mixed_review
                outcome=inspect_mixed_review(pause_data,proposed,tuple(transcripts),MODEL_SHA)
                if outcome.status!="READY":
                    record("mixed-auto-fresh-ordered-review",False,reason=outcome.reason,
                           review_status=outcome.status)
                    return 1
                mixed=MixedVoiceReview(pause_data,proposed,tuple(transcripts),MODEL_SHA)
                view=outcome.preview
                record("mixed-auto-fresh-ordered-review",
                       [row.language for row in view.segments]==[lang for _,lang,_ in samples]
                       and view.text=="\n".join(t.result()[0].text for t in transcripts),
                       languages=[row.language for row in view.segments])
                if corpus in ("mixed-auto-reviewed","mixed-auto-reviewed-short"):
                    if len(reviewed)!=1 or reviewed[0][0]!=0:
                        return 1
                    auto_gate.revoke(reviewed[0][1].identity)
                    revoked_index=0
                else:
                    detectors[1].cancel()
                    revoked_index=1
                try:
                    mixed.preview()
                except Refused:
                    denied=True
                else:
                    denied=False
                record("mixed-auto-revoke-invalidates-review",denied and all(t.result()[0].language==samples[i][1]
                       for i,t in enumerate(transcripts) if i!=revoked_index))
            finally:
                for detector in detectors:
                    detector.cancel()
        record("speech-distinct-multilingual-output",len(digests)==len(samples))
        record("speech-exact-output-set",all(sorted(p.name for p in (root/("voice-"+case_id)).iterdir())==[speech_filename(case_id)] for case_id,_,_ in samples) and not list(output.iterdir()))
    return 0 if len(results)==5+7*len(samples)+mixed_extra and all(results) and (not stt_paths or (len(quality)==5*len(samples) and all(quality))) else 1

if __name__=="__main__":
    raise SystemExit(main(*sys.argv[1:]))
