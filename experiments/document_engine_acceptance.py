# SPDX-License-Identifier: AGPL-3.0-only
"""CI-only document-engine evidence on self-generated public fixtures.

No user input, runtime gateway activation, arbitrary command or user path.
All engines receive fixed argv inside a disposable runner directory.
"""
import hashlib
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


def main():
    rows = []
    def record(case, passed, **extra):
        row = dict(case=case, status="PASS" if passed else "FAIL", **extra)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    with tempfile.TemporaryDirectory(prefix="jarvis-doc-fixtures-") as tmp:
        root = Path(tmp)
        pdfmetrics.registerFont(TTFont("FixtureFont", FONT))
        print(run(["tesseract", "--version"], root).stdout.decode().splitlines()[0])
        print(run(["pdftotext", "-v"], root).stderr.decode().splitlines()[0])
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
    return 0 if len(rows) == 10 and all(row["status"] == "PASS" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
