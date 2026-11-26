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

def main():
    if os.getuid()==0:
        raise RuntimeError("root fixture prohibited")
    results=[]
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
        def run(args,active_prefix=None):
            with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
                proc=subprocess.Popen((prefix if active_prefix is None else active_prefix)+["--","/usr/bin/python3","-I","-c",WRAPPER]+args,
                    stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=True,
                    env={"PATH":"/usr/bin:/bin","LC_ALL":"C.UTF-8"})
                try:
                    proc.wait(timeout=20)
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
                    raise RuntimeError("speech child failed: "+stderr.decode("utf-8","replace")[:1000])
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
        for lang,text in TEXT.items():
            payload=text.encode("utf-8")
            source.write_bytes(payload)
            digest=hashlib.sha256(payload).hexdigest()
            dest=output/(lang+".wav")
            call=CapabilityCall("espeak_ng",version.group(1),"text_to_wav",
                    (("artifact","fixture-text-"+lang),("digest",digest),("language",lang),
                     ("output","fixture-audio-"+lang)))
            req=call.request(ctx)
            grant=gate.fixture_grant("voice-"+lang,req,60)
            job=SpeechFixture(gate,ctx,call,grant)
            if dest.exists() or dest.is_symlink():
                job.cancel()
                return 1
            job.checkpoint()
            run(["/usr/bin/espeak-ng","-v",lang,"-s","150","-f","/work/text.txt",
                 "-w","/work/out/"+lang+".wav"])
            if dest.is_symlink() or not dest.is_file() or not 44<dest.stat().st_size<=MAX_WAV:
                job.cancel()
                record("speech-wav-"+lang,False)
                return 1
            data=dest.read_bytes()
            unchanged=source.read_bytes()==payload
            record("speech-source-preserved-"+lang,unchanged)
            if not unchanged:
                job.cancel()
                return 1
            job.complete(SpeechResult(req.target,digest,lang,"fixture-audio-"+lang,data))
            receipt=job.result()
            result_digest=hashlib.sha256(data).hexdigest()
            record("speech-real-wav-"+lang,receipt[4]==result_digest,
                   rate=receipt[5],frames=receipt[6],digest=result_digest)
            digests.add(result_digest)
            # Each conversion receives only its exact synthesized WAV and a
            # fresh output directory, not the text or other language files.
            converted=root/("converted-"+lang)
            converted.mkdir(mode=0o700)
            scope=ffmpeg_prefix.copy()
            i=scope.index(str(source))
            scope[i:i+2]=[str(dest),"/work/input.wav"]
            i=scope.index(str(output))
            scope[i]=str(converted)
            call=job.prepare_conversion(fv,"fixture-flac-"+lang)
            req=call.request(ctx)
            grant=gate.fixture_grant("convert-"+lang,req,60)
            conversion=job.start_conversion(fv,"fixture-flac-"+lang,grant)
            gate.checkpoint(grant,req)
            if hashlib.sha256(dest.read_bytes()).hexdigest()!=result_digest:
                conversion.cancel()
                return 1
            run(["/usr/bin/ffmpeg","-nostdin","-v","error","-n","-i","/work/input.wav",
                 "-c:a","flac","/work/out/audio.flac"],scope)
            flac=converted/"audio.flac"
            ok=not flac.is_symlink() and flac.is_file() and 4<flac.stat().st_size<=MAX_WAV
            record("speech-real-flac-"+lang,ok)
            if not ok:
                conversion.cancel()
                return 1
            gate.checkpoint(grant,req)
            run(["/usr/bin/ffmpeg","-nostdin","-v","error","-n","-i","/work/out/audio.flac",
                 "-f","s16le","-c:a","pcm_s16le","/work/out/roundtrip.pcm"],scope)
            pcm=converted/"roundtrip.pcm"
            exact=(not pcm.is_symlink() and pcm.is_file() and pcm.stat().st_size==len(data)-44
                   and pcm.read_bytes()==data[44:] and dest.read_bytes()==data)
            record("speech-lossless-pcm-"+lang,exact)
            if not exact:
                conversion.cancel()
                return 1
            flac_data=flac.read_bytes()
            conversion.complete(ConversionResult(req.target,result_digest,"fixture-flac-"+lang,"flac",flac_data))
            record("speech-flac-dependent-receipt-"+lang,conversion.result()[4]==hashlib.sha256(flac_data).hexdigest())
            job.cancel()
            try:
                conversion.result()
            except Refused:
                denied=True
            else:
                denied=False
            record("speech-revoke-invalidates-conversion-"+lang,denied)
            if not all(results):
                return 1
        record("speech-distinct-multilingual-output",len(digests)==3)
        record("speech-exact-output-set",sorted(p.name for p in output.iterdir())==["de.wav","en.wav","tr.wav"])
    return 0 if len(results)==23 and all(results) else 1

if __name__=="__main__":
    raise SystemExit(main())
