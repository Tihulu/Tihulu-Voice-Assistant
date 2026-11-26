# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable CI feasibility test, not a production sandbox or adapter."""
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import wave

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
                   "--setenv","LC_ALL","C.UTF-8"]
        def run(args):
            return subprocess.run(prefix+["--"]+args,stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  timeout=20,check=False)
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
        conversion = run(["/usr/bin/ffmpeg","-nostdin","-v","error","-n","-i","/work/input.wav",
                          "-map","0:a:0","-c:a","flac","/work/out/converted.flac"])
        target = output/"converted.flac"
        record("sandbox-ffmpeg-conversion",conversion.returncode==0 and target.is_file()
               and target.read_bytes().startswith(b"fLaC")
               and hashlib.sha256(source.read_bytes()).hexdigest()==before,
               exit=conversion.returncode,stderr=conversion.stderr.decode("utf-8","replace")[:1000])
    return 0 if len(results)==5 and all(r["status"]=="PASS" for r in results) else 1

if __name__=="__main__":
    raise SystemExit(main())
