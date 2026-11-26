# SPDX-License-Identifier: AGPL-3.0-only
"""CI-only document-engine evidence on self-generated public fixtures.

No user input, runtime gateway activation, arbitrary command or user path.
All engines receive fixed argv inside a disposable runner directory.
"""
import hashlib
import csv
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import io
import xml.etree.ElementTree as ET
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unicodedata

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway
from capabilities import CapabilityCall
from document_result import DocumentFixture, DocumentResult, TextBlock

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
TEXT = {"tur": "İstanbul güzel bir şehir. Gökyüzü mavi.",
        "eng": "The sky is blue. The sun is shining.",
        "deu": "München ist schön. Die Sonne scheint."}


def norm(text):
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv, root, *, success=True):
    result = subprocess.run(argv, cwd=root, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=20, check=False,
                            env={**os.environ, "LC_ALL": "C.UTF-8"})
    if success and result.returncode:
        raise RuntimeError(f"{argv[0]}: exit {result.returncode}")
    if len(result.stdout) > 65536 or len(result.stderr) > 65536:
        raise RuntimeError("fixture output limit")
    return result


def normalize_box(left, top, right, bottom, width, height):
    left, top, right, bottom, width, height = map(
        lambda value: Decimal(str(value)), (left, top, right, bottom, width, height))
    if (not all(v.is_finite() for v in (left,top,right,bottom,width,height))
            or not 0 <= left < right <= width or not 0 <= top < bottom <= height):
        raise ValueError("invalid fixture box")
    return tuple(int((v * 10000 / limit).to_integral_value(rounding=rounding))
                 for v,limit,rounding in ((left,width,ROUND_FLOOR),(top,height,ROUND_FLOOR),
                                           (right,width,ROUND_CEILING),(bottom,height,ROUND_CEILING)))


def ocr_blocks(tsv, width, height):
    blocks = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        if row["level"] != "5" or not row["text"].strip():
            continue
        left, top, w, h = (int(row[key]) for key in ("left","top","width","height"))
        confidence = Decimal(row["conf"])
        if not confidence.is_finite() or not 0 <= confidence <= 100:
            raise ValueError("invalid fixture confidence")
        blocks.append(TextBlock(row["text"], normalize_box(left,top,left+w,top+h,width,height),
                                int(confidence)))
    return tuple(blocks)


def pdf_blocks(xml):
    root = ET.fromstring(xml)
    pages = list(root.iter("{*}page"))
    if len(pages) != 1:
        raise ValueError("unexpected fixture page count")
    page = pages[0]
    return tuple(TextBlock(word.text or "", normalize_box(
        word.attrib["xMin"],word.attrib["yMin"],word.attrib["xMax"],word.attrib["yMax"],
        page.attrib["width"],page.attrib["height"]))
        for word in page.iter("{*}word"))


def validate_output(call, page, blocks, expected):
    # Trusted public fixture, not a live runtime grant or OS effect.
    context = Context("document-fixture","session","profile",1,"local","LOCAL_ONLY")
    request = call.request(context)
    gateway = FixtureGateway(context, (profiles.Rule("filesystem_read",(request.target,),"allow"),
                                       profiles.Rule("host_shell",(request.target,),"allow")),
                             clock=profiles.PolicyClock())
    grant = gateway.fixture_grant("fixture",request,60)
    job = DocumentFixture(gateway,grant,context,call)
    result = DocumentResult(request.target,dict(call.args)["digest"],page,blocks)
    job.complete(result)
    receipt = job.result()
    actual = " ".join(block.text for block in receipt[3].blocks)
    return norm(actual) == norm(expected), actual, receipt[4]


def main():
    rows = []
    def record(case, passed, **extra):
        row = dict(case=case, status="PASS" if passed else "FAIL", **extra)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    with tempfile.TemporaryDirectory(prefix="jarvis-doc-fixtures-") as tmp:
        root = Path(tmp)
        pdfmetrics.registerFont(TTFont("FixtureFont", FONT))
        ocr_version = run(["tesseract", "--version"], root).stdout.decode().splitlines()[0]
        pdf_version = run(["pdftotext", "-v"], root).stderr.decode().splitlines()[0]
        print(ocr_version)
        print(pdf_version)
        ocr_version = ocr_version.split()[1]
        pdf_version = pdf_version.split()[2]
        images = {}
        for lang, text in TEXT.items():
            image = Image.new("RGB", (1800, 320), "white")
            draw = ImageDraw.Draw(image)
            draw.text((55, 100), text, font=ImageFont.truetype(FONT, 52), fill="black")
            path = root / (lang + ".png")
            image.save(path)
            images[lang] = path
            original = digest(path)
            actual = run(["tesseract", str(path), "stdout", "-l", lang, "--psm", "6"], root).stdout.decode("utf-8")
            record("ocr-" + lang, norm(actual) == norm(text) and digest(path) == original,
                   expected=text, actual=actual.strip(), sha256=original)
            tsv = run(["tesseract", str(path), "stdout", "-l", lang, "--psm", "6", "tsv"],
                      root).stdout.decode("utf-8")
            blocks = ocr_blocks(tsv, *image.size)
            call = CapabilityCall("tesseract", ocr_version, "ocr_image",
                                  (("artifact", lang+"-image"), ("digest", original),
                                   ("language", {"tur":"tr","eng":"en","deu":"de"}[lang])))
            passed, actual, result_digest = validate_output(call,1,blocks,text)
            record("ocr-contract-"+lang, passed and digest(path)==original,
                   actual=actual, blocks=len(blocks), result_sha256=result_digest)

        pdf = root / "text.pdf"
        out = canvas.Canvas(str(pdf), pagesize=(900, 300), invariant=True)
        for text in TEXT.values():
            out.setFont("FixtureFont", 23)
            out.drawString(30, 180, text)
            out.showPage()
        out.save()
        source = digest(pdf)
        for page, (lang, text) in enumerate(TEXT.items(), 1):
            actual = run(["pdftotext", "-f", str(page), "-l", str(page), "-enc", "UTF-8",
                          str(pdf), "-"], root).stdout.decode("utf-8")
            record("pdf-extract-" + lang, norm(actual) == norm(text) and digest(pdf) == source,
                   page=page, expected=text, actual=actual.strip(), sha256=source)
            xml = run(["pdftotext", "-f", str(page), "-l", str(page), "-bbox",
                       "-enc", "UTF-8", str(pdf), "-"], root).stdout
            blocks = pdf_blocks(xml)
            call = CapabilityCall("poppler", pdf_version, "pdf_extract",
                                  (("artifact","text-pdf"),("digest",source),("page",page)))
            passed, actual, result_digest = validate_output(call,page,blocks,text)
            record("pdf-contract-"+lang, passed and digest(pdf)==source, page=page,
                   actual=actual, blocks=len(blocks), result_sha256=result_digest)
        # Scan-only page: extraction must be empty, then actual raster/OCR.
        scan = root / "scan.pdf"
        out = canvas.Canvas(str(scan), pagesize=(900, 160), invariant=True)
        out.drawImage(str(images["tur"]), 0, 0, width=900, height=160)
        out.showPage()
        out.save()
        source = digest(scan)
        direct = run(["pdftotext", str(scan), "-"], root).stdout.decode("utf-8")
        record("scan-no-text-layer", not direct.strip(), sha256=source)
        prefix = root / "rendered"
        run(["pdftoppm", "-f", "1", "-l", "1", "-r", "144", "-png", "-singlefile",
             str(scan), str(prefix)], root)
        rendered = prefix.with_suffix(".png")
        actual = run(["tesseract", str(rendered), "stdout", "-l", "tur", "--psm", "6"], root).stdout.decode("utf-8")
        record("scan-render-ocr-tur", norm(actual) == norm(TEXT["tur"]) and digest(scan) == source,
               expected=TEXT["tur"], actual=actual.strip(), sha256=source, render_sha256=digest(rendered))
        corrupt = root / "bad.pdf"
        corrupt.write_bytes(b"%PDF-1.4\nnot a PDF\n")
        bad = run(["pdftotext", str(corrupt), "-"], root, success=False)
        record("corrupt-pdf-refused", bad.returncode != 0 and not bad.stdout)
        encrypted = root / "encrypted.pdf"
        from reportlab.lib.pdfencrypt import StandardEncryption
        out = canvas.Canvas(str(encrypted), encrypt=StandardEncryption("fixture-password",
                            ownerPassword="fixture-owner", canPrint=0, strength=128))
        out.drawString(30, 180, "Public synthetic text")
        out.save()
        locked = run(["pdftotext", str(encrypted), "-"], root, success=False)
        record("encrypted-pdf-without-password-refused", locked.returncode != 0 and not locked.stdout)
    return 0 if len(rows) == 16 and all(row["status"] == "PASS" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
