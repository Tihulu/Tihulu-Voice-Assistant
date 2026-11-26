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
import sys
from voice_quality import word_errors, acceptable, expanded_corpus, speech_filename, targeted_corpus, turbo_short_args
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from capabilities import CapabilityCall
from speech_result import SpeechFixture,SpeechResult,MAX_WAV
from conversion_result import ConversionResult

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

def main(stt_binary=None,stt_model=None,stt_profile="tiny",corpus="original"):
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
    if (corpus not in ("original","expanded","targeted","turbo-expanded","mixed-preview")
            or (corpus in ("expanded","mixed-preview") and (not stt_paths or stt_profile!="small"))
            or (corpus in ("targeted","turbo-expanded") and (not stt_paths or stt_profile!="turbo-q5"))):
        raise RuntimeError("invalid corpus profile")
    samples=tuple((lang,lang,text) for lang,text in TEXT.items())
    if corpus in ("expanded","turbo-expanded"):
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
    with tempfile.TemporaryDirectory(prefix="jarvis-speech-") as tmp:
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
        def run(args,active_prefix=None,*,wrapper=WRAPPER,timeout=20):
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
                return stdout
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
        gate=FixtureGateway(ctx,rules,clock=profiles.PolicyClock())
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
            job.complete(SpeechResult(req.target,digest,lang,"fixture-audio-"+case_id,data))
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
                if stt_profile=="turbo-q5":
                    # Separate explicitly labeled short-audio decoder profile.
                    # Preserve CPU, wall, grant and output ceilings.
                    args+=turbo_short_args(receipt[5],receipt[6])
                    print(json.dumps({"case_id":case_id,"decoder":"turbo-short-greedy","extra_args":args[-6:]}),flush=True)
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
            if corpus=="mixed-preview":
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
        if corpus=="mixed-preview":
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
        record("speech-distinct-multilingual-output",len(digests)==len(samples))
        record("speech-exact-output-set",all(sorted(p.name for p in (root/("voice-"+case_id)).iterdir())==[speech_filename(case_id)] for case_id,_,_ in samples) and not list(output.iterdir()))
    return 0 if len(results)==5+7*len(samples)+(8 if corpus=="mixed-preview" else 0) and all(results) and (not stt_paths or (len(quality)==5*len(samples) and all(quality))) else 1

if __name__=="__main__":
    raise SystemExit(main(*sys.argv[1:]))
