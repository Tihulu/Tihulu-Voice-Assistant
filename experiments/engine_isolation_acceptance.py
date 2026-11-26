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
data['pidns'] = os.readlink('/proc/self/ns/pid')
data['ipcns'] = os.readlink('/proc/self/ns/ipc')
data['outside_denied'] = True
for path in ('/fixture-forbidden','/work/fixture-forbidden'):
    try:
        with open(path,'x') as handle:
            handle.write('synthetic')
        data['outside_denied'] = False
    except OSError:
        pass
with open('/tmp/fixture-private','x') as handle:
    handle.write('private scratch')
data['scratch_available'] = open('/tmp/fixture-private').read() == 'private scratch'
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

def preflight_passed(results, expected):
    """Only the exact completed preflight set can enable fixture engines."""
    return (type(results) is list and len(results)==len(expected)
            and all(type(row) is dict and row.get("status")=="PASS" for row in results)
            and tuple(row.get("case") for row in results)==expected)


PREFLIGHT = ("sandbox-input-scope","sandbox-input-readonly","sandbox-network-namespace",
             "sandbox-output-scope","sandbox-capabilities-dropped","sandbox-host-environment-hidden",
             "sandbox-process-resource-limits","sandbox-root-and-work-readonly",
             "sandbox-private-scratch","sandbox-pid-ipc-namespaces","sandbox-file-size-limit",
             "sandbox-capture-limit","sandbox-timeout-cleanup")


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
        # Fixed public fixtures generated outside the sandbox; only these exact
        # files are mounted. Imports stay inside main for dependency-free tests.
        from PIL import Image, ImageDraw, ImageFont
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from document_geometry import ocr_blocks, pdf_blocks
        from document_result import DocumentFixture, DocumentResult
        from document_engine_acceptance import FONT, TEXT, norm
        pdfmetrics.registerFont(TTFont("IsolatedFixtureFont",FONT))
        pdf = root/"document.pdf"
        doc = canvas.Canvas(str(pdf),pagesize=(900,300),invariant=True)
        for text in TEXT.values():
            doc.setFont("IsolatedFixtureFont",23)
            doc.drawString(30,180,text)
            doc.showPage()
        doc.save()
        images = {}
        for lang,text in TEXT.items():
            image = Image.new("RGB",(1800,320),"white")
            ImageDraw.Draw(image).text((55,100),text,font=ImageFont.truetype(FONT,52),fill="black")
            path = root/(lang+".png")
            image.save(path)
            images[lang] = path
        source_hashes = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (pdf,*images.values())}
        prefix = ["/usr/bin/bwrap","--unshare-all","--die-with-parent","--new-session",
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
                   "--ro-bind",str(pdf),"/work/document.pdf",
                   "--bind",str(output),"/work/out","--chdir","/work",
                   "--setenv","PATH","/usr/bin","--setenv","HOME","/tmp",
                   "--setenv","LC_ALL","C.UTF-8",
                   # Ubuntu 22.04 BLAS/LAPACK alternatives normally resolve via
                   # /etc/ld.so.cache. Keep /etc hidden; only fixed read-only
                   # library directories inside the already mounted /usr.
                   "--setenv","LD_LIBRARY_PATH",
                   "/usr/lib/x86_64-linux-gnu/blas:/usr/lib/x86_64-linux-gnu/lapack",
                   "--remount-ro","/"]
        # Insert explicit read-only image mounts before final root remount.
        for lang,path in images.items():
            prefix[-2:-2] = ["--ro-bind",str(path),"/work/"+lang+".png"]
        def run(args, timeout=20):
            argv = prefix+["--","/usr/bin/python3","-I","-c",LIMIT_WRAPPER]+args
            # Regular files avoid unbounded PIPE buffering in the host.
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                with subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                                      start_new_session=True,
                                      env={"PATH":"/usr/bin:/bin","LC_ALL":"C.UTF-8",
                                           "JARVIS_FIXTURE_HOST_MARKER":"synthetic-only"}) as proc:
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
            host_pid = os.readlink("/proc/self/ns/pid")
            host_ipc = os.readlink("/proc/self/ns/ipc")
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
        record("sandbox-root-and-work-readonly",data["outside_denied"])
        record("sandbox-private-scratch",data["scratch_available"] and not (output/"fixture-private").exists())
        record("sandbox-pid-ipc-namespaces",data["pidns"]!=host_pid and data["ipcns"]!=host_ipc)
        # A single buffered write can legally be short and the temporary
        # object's destructor may suppress a flush error. Force a second
        # unbuffered write after reaching the exact limit and verify EFBIG.
        oversized = run(["/usr/bin/python3","-I","-c",
                         """import errno, os
with open('/work/out/oversized','xb',buffering=0) as handle:
    remaining = 1048576
    while remaining:
        count = handle.write(b'x'*min(remaining,65536))
        if not count:
            raise RuntimeError('unexpected short write')
        remaining -= count
    try:
        handle.write(b'x')
    except OSError as exc:
        if exc.errno != errno.EFBIG:
            raise
        print('EFBIG')
    else:
        raise RuntimeError('file size limit bypassed')
"""])
        large = output/"oversized"
        record("sandbox-file-size-limit",oversized.returncode==0
               and oversized.stdout.strip()==b"EFBIG" and large.is_file()
               and large.stat().st_size==1048576,
               exit=oversized.returncode,size=large.stat().st_size if large.exists() else None,
               stderr=oversized.stderr.decode("utf-8","replace")[:1000])
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
        if not preflight_passed(results,PREFLIGHT):
            print(json.dumps({"case":"sandbox-engine-preflight","status":"HOLD"}),flush=True)
            return 1
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
        if valid and results[-1]["status"]=="PASS":
            result = ConversionResult(request.target,before,"fixture-output","flac",target.read_bytes())
            job.complete(result)
            record("sandbox-typed-conversion-receipt",job.result()[4]==hashlib.sha256(target.read_bytes()).hexdigest()
                   and hashlib.sha256(source.read_bytes()).hexdigest()==before,
                   engine_version=version)
        else:
            job.cancel()
            record("sandbox-typed-conversion-receipt",False,reason="conversion_or_roundtrip_failed")
        if not all(row["status"]=="PASS" for row in results):
            return 1
        # Claim typed document grants before fixed isolated engine calls.
        # This is CI fixture policy, not a runtime authorization mechanism.
        def checked_doc(adapter,version,operation,args,page,argv,parse,expected):
            call = CapabilityCall(adapter,version,operation,args)
            ctx = Context("isolated-document","session","profile",1,"local","LOCAL_ONLY")
            req = call.request(ctx)
            gate = FixtureGateway(ctx,tuple(profiles.Rule(x,(req.target,),"allow")
                                  for x in ("filesystem_read","host_shell")),
                                  clock=profiles.PolicyClock())
            grant = gate.fixture_grant("document",req,60)
            job = DocumentFixture(gate,grant,ctx,call)
            gate.checkpoint(grant,req)
            proc = run(argv)
            if proc.returncode:
                job.cancel()
                return False, "", ""
            blocks = parse(proc.stdout)
            job.complete(DocumentResult(req.target,dict(args)["digest"],page,blocks))
            receipt = job.result()
            text = " ".join(b.text for b in receipt[3].blocks)
            return norm(text)==norm(expected),text,receipt[4]
        pv = run(["/usr/bin/pdftotext","-v"])
        tv = run(["/usr/bin/tesseract","--version"])
        if pv.returncode or tv.returncode:
            print(json.dumps({"case":"sandbox-document-versions","status":"HOLD",
                              "pdf":pv.stderr.decode("utf-8","replace")[:1000],
                              "ocr":tv.stderr.decode("utf-8","replace")[:1000]}),flush=True)
            return 1
        pdf_version = pv.stderr.decode().splitlines()[0].split()[2]
        ocr_version = tv.stdout.decode().splitlines()[0].split()[1]
        for page,(lang,expected) in enumerate(TEXT.items(),1):
            passed,text,receipt = checked_doc("poppler",pdf_version,"pdf_extract",
                (("artifact","fixture-pdf"),("digest",source_hashes[pdf]),("page",page)),
                page,["/usr/bin/pdftotext","-f",str(page),"-l",str(page),"-bbox",
                      "-enc","UTF-8","/work/document.pdf","-"],pdf_blocks,expected)
            record("sandbox-pdf-"+lang,passed,actual=text,result_sha256=receipt)
            passed,text,receipt = checked_doc("tesseract",ocr_version,"ocr_image",
                (("artifact",lang+"-image"),("digest",source_hashes[images[lang]]),
                 ("language",{"tur":"tr","eng":"en","deu":"de"}[lang])),
                1,["/usr/bin/tesseract","/work/"+lang+".png","stdout","-l",lang,
                   "--psm","6","tsv"],lambda raw:ocr_blocks(raw.decode("utf-8"),1800,320),expected)
            record("sandbox-ocr-"+lang,passed,actual=text,result_sha256=receipt)
        record("sandbox-document-sources-unchanged",all(
               hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in source_hashes.items()))
    return 0 if len(results)==23 and all(r["status"]=="PASS" for r in results) else 1

if __name__=="__main__":
    raise SystemExit(main())
