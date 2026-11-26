# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable CI feasibility test, not a production sandbox or adapter."""
import hashlib
import json
import os
import stat
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
        mixed = root/"mixed.pdf"
        doc = canvas.Canvas(str(mixed),pagesize=(900,160),invariant=True)
        doc.setFont("IsolatedFixtureFont",23)
        doc.drawString(30,100,TEXT["deu"])
        doc.showPage()
        doc.drawImage(str(images["tur"]),0,0,width=900,height=160)
        doc.showPage()
        doc.showPage()
        doc.save()
        source_hashes = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (pdf,mixed,*images.values())}
        office_source = root/"office.txt"
        office_text = "Synthetic fixture report. Istanbul, Berlin, London."
        office_source.write_text(office_text+"\n",encoding="utf-8")
        office_hash = hashlib.sha256(office_source.read_bytes()).hexdigest()
        # Minimal explicit font configuration; never bind host /etc or HOME.
        font_config = root/"fonts.conf"
        font_config.write_text('<?xml version="1.0"?><fontconfig>'
                               '<dir>/usr/share/fonts/truetype/dejavu</dir>'
                               '<cachedir>/tmp/font-cache</cachedir></fontconfig>',encoding="utf-8")
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
                   "--ro-bind",str(mixed),"/work/mixed.pdf",
                   "--ro-bind",str(office_source),"/work/office.txt",
                   "--ro-bind",str(font_config),"/work/fonts.conf",
                   "--bind",str(output),"/work/out","--chdir","/work",
                   "--setenv","PATH","/usr/bin","--setenv","HOME","/tmp",
                   "--setenv","LC_ALL","C.UTF-8",
                   "--setenv","FONTCONFIG_FILE","/work/fonts.conf",
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
            job.complete(DocumentResult(req.target,dict(args)["digest"],page,blocks,
                                       dict(args).get("render_digest")))
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
        if not all(row["status"]=="PASS" for row in results):
            return 1
        # Real isolated mixed PDF fallback. The render is generated from the
        # fixed source/page; the OCR grant includes its actual digest.
        def page_text(path,page):
            proc = run(["/usr/bin/pdftotext","-f",str(page),"-l",str(page),
                        "-enc","UTF-8",path,"-"])
            if proc.returncode:
                raise RuntimeError("isolated page extraction failed")
            return norm(proc.stdout.decode("utf-8"))
        def page_count(path):
            proc = run(["/usr/bin/pdfinfo",path])
            if proc.returncode:
                return []
            return [line.split(":",1)[1].strip() for line in proc.stdout.decode().splitlines()
                    if line.startswith("Pages:")]
        record("sandbox-mixed-pdf-text-and-empty-pages",
               page_count("/work/mixed.pdf")==["3"] and
               [page_text("/work/mixed.pdf",p) for p in (1,2,3)]==[norm(TEXT["deu"]),"",""])
        rendered = output/"mixed-render.png"
        raster = run(["/usr/bin/pdftoppm","-f","2","-l","2","-r","144","-png",
                      "-singlefile","/work/mixed.pdf","/work/out/mixed-render"])
        render_ok = (raster.returncode==0 and not rendered.is_symlink() and rendered.is_file()
                     and 0<rendered.stat().st_size<=MAX_OUTPUT)
        record("sandbox-mixed-pdf-render",render_ok,exit=raster.returncode)
        if not render_ok:
            return 1
        rh = hashlib.sha256(rendered.read_bytes()).hexdigest()
        with Image.open(rendered) as image:
            dims=image.size
        passed,text,receipt = checked_doc("tesseract",ocr_version,"pdf_page_ocr",
              (("artifact","fixture-mixed-pdf"),("digest",source_hashes[mixed]),("page",2),
               ("render_digest",rh),("language","tr")),2,
              ["/usr/bin/tesseract","/work/out/mixed-render.png","stdout","-l","tur","--psm","6","tsv"],
              lambda raw:ocr_blocks(raw.decode("utf-8"),*dims),TEXT["tur"])
        record("sandbox-mixed-pdf-scan-receipt",passed and
               hashlib.sha256(rendered.read_bytes()).hexdigest()==rh,actual=text,result_sha256=receipt)
        # Fixed split and reorder operations, not arbitrary page selection or
        # runtime export authorization. Never write the original input.
        from pdf_manipulation import PDFManipulation
        split_ctx=Context("isolated-pdf-manipulation","session","profile",1,"local","LOCAL_ONLY")
        split_call=CapabilityCall("poppler",pdf_version,"pdf_split",
                    (("artifact","fixture-pdf"),("digest",source_hashes[pdf]),
                     ("page_order",(1,2,3)),("output","fixture-parts")))
        # Fixture grants only, no actual user permission.
        split_gate=FixtureGateway(split_ctx,tuple(profiles.Rule(x,(),"allow")
                    for x in ("host_shell","filesystem_read","filesystem_write")),clock=profiles.PolicyClock())
        split_req=split_call.request(split_ctx)
        split_job=PDFManipulation(split_gate,split_gate.fixture_grant("split",split_req,60),split_ctx,split_call)
        split = run(["/usr/bin/pdfseparate","-f","1","-l","3","/work/document.pdf",
                     "/work/out/selected-%d.pdf"])
        parts=[output/("selected-"+str(i)+".pdf") for i in (1,2,3)]
        split_ok=(split.returncode==0 and
                  sorted(p.name for p in output.glob("selected-*.pdf"))==[p.name for p in parts]
                  and all(not p.is_symlink() and p.is_file() and 0<p.stat().st_size<=MAX_OUTPUT for p in parts))
        record("sandbox-pdf-split",split_ok and all(
               page_count("/work/out/"+p.name)==["1"] and
               page_text("/work/out/"+p.name,1)==norm(text)
               for p,text in zip(parts,TEXT.values())))
        if not split_ok:
            return 1
        split_job.complete(tuple(p.read_bytes() for p in parts))
        ph=list(split_job.result())
        merge_call=CapabilityCall("poppler",pdf_version,"pdf_merge",
                    (("artifact","fixture-parts"),("digest",source_hashes[pdf]),
                     ("page_order",(3,1,2)),("part_digests",(ph[2],ph[0],ph[1])),("output","fixture-merged")))
        merge_req=merge_call.request(split_ctx)
        merge_job=PDFManipulation(split_gate,split_gate.fixture_grant("merge",merge_req,60),
                                  split_ctx,merge_call,parent=split_job)
        merged=output/"reordered.pdf"
        merge=run(["/usr/bin/pdfunite","/work/out/selected-3.pdf","/work/out/selected-1.pdf",
                   "/work/out/selected-2.pdf","/work/out/reordered.pdf"])
        merge_ok=(merge.returncode==0 and not merged.is_symlink() and merged.is_file()
                  and 0<merged.stat().st_size<=MAX_OUTPUT)
        expected=list(TEXT.values())
        record("sandbox-pdf-reorder",merge_ok and page_count("/work/out/reordered.pdf")==["3"]
               and all(page_text("/work/out/reordered.pdf",p)==norm(expected[i])
                       for p,i in enumerate((2,0,1),1)))
        if merge_ok:
            merge_job.complete((merged.read_bytes(),))
        record("sandbox-pdf-manipulation-typed-receipt",merge_ok and
               merge_job.result()==(hashlib.sha256(merged.read_bytes()).hexdigest(),))
        record("sandbox-pdf-manipulation-sources-unchanged",
               ph==[hashlib.sha256(p.read_bytes()).hexdigest() for p in parts] and
               all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in source_hashes.items()))
        if not all(row["status"]=="PASS" for row in results):
            return 1
        # Debian/Ubuntu package configuration lives outside /usr. Copy only
        # registered, regular stock package files into a disposable fixture;
        # never mount the runner's /etc or any user configuration directory.
        packages = ("libreoffice-common","libreoffice-core","libreoffice-writer")
        listing = subprocess.run(["/usr/bin/dpkg-query","-L",*packages],check=True,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        config = root/"office-package-config"
        config.mkdir(mode=0o700)
        config_count=0
        for entry in sorted(set(listing.stdout.decode("utf-8").splitlines())):
            if not entry.startswith("/etc/libreoffice/"):
                continue
            src=Path(entry)
            rel=src.relative_to("/etc/libreoffice")
            if ".." in rel.parts:
                raise RuntimeError("invalid package configuration path")
            if src.is_symlink():
                # No traversal or arbitrary host symlink target.
                continue
            if not src.is_file():
                continue
            info=src.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_size>MAX_OUTPUT:
                raise RuntimeError("unexpected package configuration")
            dst=config/rel
            dst.parent.mkdir(parents=True,exist_ok=True)
            dst.write_bytes(src.read_bytes())
            config_count+=1
        print(json.dumps({"office_package_config_files":config_count}),flush=True)
        if not config_count:
            print(json.dumps({"case":"sandbox-office-config","status":"HOLD"}),flush=True)
            return 1
        prefix[-2:-2]=["--dir","/etc","--ro-bind",str(config),"/etc/libreoffice",
                        "--setenv","SAL_USE_VCLPLUGIN","svp"]
        office_version = run(["/usr/bin/libreoffice","-env:UserInstallation=file:///tmp/office-profile",
                              "--headless","--version"])
        if office_version.returncode:
            print(json.dumps({"case":"sandbox-office-version","status":"HOLD",
                              "stderr":office_version.stderr.decode("utf-8","replace")[:1000]}),flush=True)
            return 1
        ov = office_version.stdout.decode().strip().split()[1]
        office_call = CapabilityCall("libreoffice",ov,"text_to_pdf",
                         (("artifact","fixture-office-text"),("digest",office_hash),
                          ("output","fixture-office-pdf")))
        office_ctx = Context("isolated-office","session","profile",1,"local","LOCAL_ONLY")
        office_req = office_call.request(office_ctx)
        office_gate = FixtureGateway(office_ctx,tuple(profiles.Rule(x,(office_req.target,),"allow")
                      for x in ("host_shell","filesystem_read","filesystem_write")),
                      clock=profiles.PolicyClock())
        office_grant = office_gate.fixture_grant("office",office_req,60)
        office_job = ConversionFixture(office_gate,office_grant,office_ctx,office_call)
        office_target = output/"office.pdf"
        if office_target.exists() or office_target.is_symlink():
            office_job.cancel()
            return 1
        office_gate.checkpoint(office_grant,office_req)
        proc = run(["/usr/bin/libreoffice","-env:UserInstallation=file:///tmp/office-profile",
                    "--headless","--nologo","--nodefault","--norestore","--convert-to",
                    "pdf:writer_pdf_Export","--outdir","/work/out","/work/office.txt"])
        ok = (proc.returncode==0 and not office_target.is_symlink() and office_target.is_file()
              and 0 < office_target.stat().st_size <= MAX_OUTPUT
              and office_target.read_bytes().startswith(b"%PDF-"))
        record("sandbox-office-pdf-export",ok,exit=proc.returncode,
               stdout=proc.stdout.decode("utf-8","replace")[:1000],
               stderr=proc.stderr.decode("utf-8","replace")[:1000])
        extracted = run(["/usr/bin/pdftotext","-enc","UTF-8","/work/out/office.pdf","-"]) if ok else None
        preserved = (extracted is not None and extracted.returncode==0
                     and norm(extracted.stdout.decode("utf-8"))==norm(office_text))
        record("sandbox-office-text-preserved",preserved)
        if ok and preserved and hashlib.sha256(office_source.read_bytes()).hexdigest()==office_hash:
            data = office_target.read_bytes()
            office_job.complete(ConversionResult(office_req.target,office_hash,"fixture-office-pdf","pdf",data))
            record("sandbox-office-typed-receipt",office_job.result()[4]==hashlib.sha256(data).hexdigest())
        else:
            office_job.cancel()
            record("sandbox-office-typed-receipt",False)
    return 0 if len(results)==33 and all(r["status"]=="PASS" for r in results) else 1

if __name__=="__main__":
    raise SystemExit(main())
