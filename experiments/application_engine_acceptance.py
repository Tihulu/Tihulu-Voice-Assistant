# SPDX-License-Identifier: AGPL-3.0-only
"""CI-only fixed public synthetic app engine fixtures; no owner paths or UI."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import wave
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway
from capabilities import CapabilityCall
from conversion_result import ConversionFixture, ConversionResult

def conversion_receipt(adapter,version,operation,source,output,fmt):
    call = CapabilityCall(adapter,version,operation,
                          (("artifact","fixture-source"),("digest",digest(source)),
                           ("output","fixture-output")))
    context = Context("conversion-fixture","session","profile",1,"local","LOCAL_ONLY")
    request = call.request(context)
    gateway = FixtureGateway(context,tuple(profiles.Rule(a,(request.target,),"allow") for a in
                             ("host_shell","filesystem_read","filesystem_write")),
                             clock=profiles.PolicyClock())
    grant = gateway.fixture_grant("conversion",request,60)
    job = ConversionFixture(gateway,grant,context,call)
    result = ConversionResult(request.target,digest(source),"fixture-output",fmt,output.read_bytes())
    job.complete(result)
    return job.result()[4]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(argv, root, timeout=30):
    env = {**os.environ, "LC_ALL":"C.UTF-8", "HOME":str(root / "home"),
           "XDG_CONFIG_HOME":str(root / "config"), "XDG_CACHE_HOME":str(root / "cache")}
    result = subprocess.run(argv,cwd=root,env=env,stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode or len(result.stdout)>65536 or len(result.stderr)>65536:
        raise RuntimeError(f"{argv[0]} failed: exit {result.returncode}")
    return result.stdout

def main():
    rows = []
    def record(case, passed, **extra):
        row = dict(case=case,status="PASS" if passed else "FAIL",**extra)
        rows.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)

    with tempfile.TemporaryDirectory(prefix="jarvis-engine-fixtures-") as tmp:
        root = Path(tmp)
        for name in ("home","config","cache","output"):
            (root/name).mkdir()
        ffmpeg_version = run(["ffmpeg","-version"],root).decode().splitlines()[0]
        office_version = run(["libreoffice","--version"],root).decode().strip()
        print(ffmpeg_version)
        print(office_version)
        ffmpeg_version = ffmpeg_version.split()[2]
        office_version = office_version.split()[1]
        wav = root/"source.wav"
        pcm = struct.pack("<hhhh",1000,0,-1000,0)*4000
        with wave.open(str(wav),"wb") as out:
            out.setparams((1,2,16000,0,"NONE","not compressed"))
            out.writeframes(pcm)
        source = digest(wav)
        flac = root/"output"/"converted.flac"
        run(["ffmpeg","-nostdin","-v","error","-n","-i",str(wav),
             "-map","0:a:0","-c:a","flac",str(flac)],root)
        record("ffmpeg-convert",flac.is_file() and flac.stat().st_size>0 and digest(wav)==source,
               source_sha256=source,output_sha256=digest(flac))
        decoded = run(["ffmpeg","-nostdin","-v","error","-i",str(flac),
                       "-map","0:a:0","-f","s16le","-acodec","pcm_s16le","-"],root)
        record("ffmpeg-lossless-roundtrip",decoded==pcm,frames=len(decoded)//2,
               pcm_sha256=hashlib.sha256(decoded).hexdigest())
        receipt = conversion_receipt("ffmpeg",ffmpeg_version,"wav_to_flac",wav,flac,"flac")
        record("ffmpeg-conversion-contract",receipt==digest(flac) and digest(wav)==source,
               result_sha256=receipt)
        text = root/"report.txt"
        expected = "Synthetic fixture report. Istanbul, Berlin, London."
        text.write_text(expected+"\n",encoding="utf-8")
        before = digest(text)
        # Disposable isolated profile and output directory; no existing office
        # process or personal profile. Fixed public text only.
        run(["libreoffice","-env:UserInstallation="+(root/"office-profile").as_uri(),
             "--headless","--nologo","--nodefault","--norestore",
             "--convert-to","pdf:writer_pdf_Export","--outdir",str(root/"output"),str(text)],
            root,timeout=60)
        pdf = root/"output"/"report.pdf"
        record("libreoffice-pdf-export",pdf.is_file() and pdf.stat().st_size>0 and digest(text)==before,
               source_sha256=before,output_sha256=digest(pdf))
        actual = run(["pdftotext","-enc","UTF-8",str(pdf),"-"],root).decode("utf-8")
        record("libreoffice-export-text-preserved"," ".join(actual.split())==expected,
               actual=" ".join(actual.split()))
        receipt = conversion_receipt("libreoffice",office_version,"text_to_pdf",text,pdf,"pdf")
        record("libreoffice-conversion-contract",receipt==digest(pdf) and digest(text)==before,
               result_sha256=receipt)
    return 0 if len(rows)==6 and all(row["status"]=="PASS" for row in rows) else 1

if __name__=="__main__":
    raise SystemExit(main())
