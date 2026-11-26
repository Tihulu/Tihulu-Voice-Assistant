# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable CI feasibility test, not a production sandbox or adapter."""
import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import time
import struct
import subprocess
import tempfile
import wave
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway
from capabilities import CapabilityCall
from conversion_result import ConversionFixture, ConversionResult, MAX_OUTPUT

# Fixed disposable child wrapper. Limits survive exec and apply to engine
# outputs and captured regular files. Not a total disk/memory cgroup quota.
LIMIT_WRAPPER = """
import os, resource, sys
for kind, value in ((resource.RLIMIT_CORE,0), (resource.RLIMIT_FSIZE,1048576),
                    (resource.RLIMIT_CPU,10), (resource.RLIMIT_NOFILE,128)):
    resource.setrlimit(kind,(value,value))
os.execv(sys.argv[1],sys.argv[1:])
"""

class FixtureLimit(RuntimeError):
    pass

PROBE = """
import json, os, socket, sys
data = {}
data['hidden'] = not os.path.exists(sys.argv[1])
data['read'] = open('/work/input.wav','rb').read(4) == b'RIFF'
try:
    with open('/work/input.wav','ab') as handle:
        handle.write(b'bad')
    data['readonly'] = False
except OSError:
    data['readonly'] = True
data['netns'] = os.readlink('/proc/self/ns/net')
status = dict(line.split(':',1) for line in open('/proc/self/status') if ':' in line)
data['caps_dropped'] = all(int(status[key].strip(),16)==0
                           for key in ('CapEff','CapPrm','CapAmb'))
data['host_env_hidden'] = 'JARVIS_FIXTURE_HOST_MARKER' not in os.environ
import resource
data['limits'] = all(resource.getrlimit(kind)==(value,value)
                     for kind,value in ((resource.RLIMIT_CORE,0),
                         (resource.RLIMIT_FSIZE,1048576),(resource.RLIMIT_CPU,10),
                         (resource.RLIMIT_NOFILE,128)))
try:
    with socket.create_connection(('127.0.0.1',int(sys.argv[2])),timeout=1):
        data['host_loopback_denied'] = False
except OSError:
    data['host_loopback_denied'] = True
with open('/work/out/probe.txt','x') as handle:
    handle.write('fixture')
print(json.dumps(data))
"""

def main():
    results = []
    def record(case,passed,**details):
        result = dict(case=case,status="PASS" if passed else "FAIL",**details)
        results.append(result)
        print(json.dumps(result),flush=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-isolation-") as tmp:
        root = Path(tmp)
        source = root/"source.wav"
        output = root/"out"
        output.mkdir(mode=0o700)
        marker = root/"not-approved.txt"
        marker.write_text("public fixture marker",encoding="ascii")
        pcm = struct.pack("<hhhh",1000,0,-1000,0)*4000
        with wave.open(str(source),"wb") as out:
            out.setparams((1,2,16000,0,"NONE","not compressed"))
            out.writeframes(pcm)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        prefix = ["bwrap","--unshare-all","--die-with-parent","--new-session",
                  "--cap-drop","ALL","--clearenv","--ro-bind","/usr","/usr"]
        # Ubuntu merged-/usr layout. Refuse rather than mount an unreviewed
        # host directory on a different distribution.
        for name in ("bin","sbin","lib","lib64"):
            path = Path("/"+name)
            if path.is_symlink():
                target = os.readlink(path)
                if target not in ("usr/"+name,"/usr/"+name):
                    raise RuntimeError("unexpected system symlink")
                prefix += ["--symlink",target,"/"+name]
            elif path.exists():
                raise RuntimeError("non-merged system layout")
        prefix += ["--proc","/proc","--dev","/dev","--tmpfs","/tmp",
                   "--dir","/work","--ro-bind",str(source),"/work/input.wav",
                   "--bind",str(output),"/work/out","--chdir","/work",
                   "--setenv","PATH","/usr/bin","--setenv","HOME","/tmp",
                   "--setenv","LC_ALL","C.UTF-8",
                   # Ubuntu 22.04 BLAS/LAPACK alternatives normally resolve via
                   # /etc/ld.so.cache. Keep /etc hidden; only fixed read-only
                   # library directories inside the already mounted /usr.
                   "--setenv","LD_LIBRARY_PATH",
                   "/usr/lib/x86_64-linux-gnu/blas:/usr/lib/x86_64-linux-gnu/lapack"]
        def run(args, timeout=20):
            argv = prefix+["--","/usr/bin/python3","-I","-c",LIMIT_WRAPPER]+args
            # Regular files avoid unbounded PIPE buffering in the host.
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                with subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                                      start_new_session=True,
                                      env={**os.environ,"JARVIS_FIXTURE_HOST_MARKER":"synthetic-only"}) as proc:
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid,signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        proc.wait(timeout=5)
                        raise FixtureLimit("fixture_timeout") from None
                stdout.seek(0)
                stderr.seek(0)
                out, err = stdout.read(65537), stderr.read(65537)
                if len(out)>65536 or len(err)>65536:
                    raise FixtureLimit("fixture_capture_limit")
                return subprocess.CompletedProcess(argv,proc.returncode,out,err)
        # Listening only on the disposable host's loopback; no external traffic.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0))
            listener.listen(1)
            host_ns = os.readlink("/proc/self/ns/net")
            probe = run(["/usr/bin/python3","-I","-c",PROBE,str(marker),
                         str(listener.getsockname()[1])])
        if probe.returncode:
            print(json.dumps({"case":"sandbox-availability","status":"HOLD",
                              "exit":probe.returncode,
                              "reason":probe.stderr.decode("utf-8","replace")[:2000]}))
            return 1
        data = json.loads(probe.stdout)
        record("sandbox-input-scope",data["hidden"] and data["read"])
        record("sandbox-input-readonly",data["readonly"] and hashlib.sha256(source.read_bytes()).hexdigest()==before)
        record("sandbox-network-namespace",data["netns"]!=host_ns and data["host_loopback_denied"])
        record("sandbox-output-scope",(output/"probe.txt").read_text()=="fixture")
        record("sandbox-capabilities-dropped",data["caps_dropped"])
        record("sandbox-host-environment-hidden",data["host_env_hidden"])
        record("sandbox-process-resource-limits",data["limits"])
        oversized = run(["/usr/bin/python3","-I","-c",
                         "open('/work/out/oversized','wb').write(b'x'*1048577)"])
        large = output/"oversized"
        record("sandbox-file-size-limit",oversized.returncode!=0 and large.is_file()
               and large.stat().st_size<=1048576)
        try:
            run(["/usr/bin/python3","-I","-c","import os; os.write(1,b'x'*65537)"])
        except FixtureLimit as exc:
            record("sandbox-capture-limit",str(exc)=="fixture_capture_limit")
        else:
            record("sandbox-capture-limit",False)
        try:
            run(["/usr/bin/python3","-I","-c",
                 "import time; time.sleep(2); open('/work/out/late','x').close()"],timeout=0.3)
        except FixtureLimit as exc:
            time.sleep(2.1)
            record("sandbox-timeout-cleanup",str(exc)=="fixture_timeout" and not (output/"late").exists())
        else:
            record("sandbox-timeout-cleanup",False)
        # The version and conversion both execute in the same fixed mount/network
        # boundary. The host owns the synthetic path; no model or owner input.
        version_probe = run(["/usr/bin/ffmpeg","-version"])
        if version_probe.returncode:
            record("sandbox-ffmpeg-version",False,exit=version_probe.returncode)
            return 1
        version = version_probe.stdout.decode("utf-8").splitlines()[0].split()[2]
        context = Context("isolated-engine","session","profile",1,"local","LOCAL_ONLY")
        call = CapabilityCall("ffmpeg",version,"wav_to_flac",
                              (("artifact","fixture-source"),("digest",before),
                               ("output","fixture-output")))
        request = call.request(context)
        gateway = FixtureGateway(context,tuple(profiles.Rule(x,(request.target,),"allow")
                                 for x in ("host_shell","filesystem_read","filesystem_write")),
                                 clock=profiles.PolicyClock())
        grant = gateway.fixture_grant("convert",request,60)
        job = ConversionFixture(gateway,grant,context,call)
        target = output/"converted.flac"
        if target.exists() or target.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest()!=before:
            job.cancel()
            record("sandbox-preflight",False)
            return 1
        gateway.checkpoint(grant,request)
        conversion = run(["/usr/bin/ffmpeg","-nostdin","-v","error","-n","-i","/work/input.wav",
                          "-map","0:a:0","-c:a","flac","/work/out/converted.flac"])
        valid = (conversion.returncode==0 and not target.is_symlink() and target.is_file()
                 and 0 < target.stat().st_size <= MAX_OUTPUT)
        record("sandbox-ffmpeg-conversion",valid
               and target.read_bytes().startswith(b"fLaC")
               and hashlib.sha256(source.read_bytes()).hexdigest()==before,
               exit=conversion.returncode,stderr=conversion.stderr.decode("utf-8","replace")[:1000])
        if valid:
            decoded = run(["/usr/bin/ffmpeg","-nostdin","-v","error","-i","/work/out/converted.flac",
                           "-map","0:a:0","-f","s16le","-acodec","pcm_s16le","-"])
            record("sandbox-ffmpeg-lossless-roundtrip",decoded.returncode==0 and decoded.stdout==pcm
                   and hashlib.sha256(source.read_bytes()).hexdigest()==before,
                   exit=decoded.returncode)
        else:
            record("sandbox-ffmpeg-lossless-roundtrip",False,reason="conversion_failed")
        if valid:
            result = ConversionResult(request.target,before,"fixture-output","flac",target.read_bytes())
            job.complete(result)
            record("sandbox-typed-conversion-receipt",job.result()[4]==hashlib.sha256(target.read_bytes()).hexdigest()
                   and hashlib.sha256(source.read_bytes()).hexdigest()==before,
                   engine_version=version)
        else:
            job.cancel()
            record("sandbox-typed-conversion-receipt",False,reason="conversion_failed")
    return 0 if len(results)==13 and all(r["status"]=="PASS" for r in results) else 1

if __name__=="__main__":
    raise SystemExit(main())
