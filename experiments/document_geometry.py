# SPDX-License-Identifier: AGPL-3.0-only
"""Geometry conversion for controlled synthetic engine output only."""
import csv
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import io
import xml.etree.ElementTree as ET
from document_result import TextBlock

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
    pages = list(root.findall(".//{*}page"))
    if len(pages) != 1:
        raise ValueError("unexpected fixture page count")
    page = pages[0]
    return tuple(TextBlock(word.text or "", normalize_box(
        word.attrib["xMin"],word.attrib["yMin"],word.attrib["xMax"],word.attrib["yMax"],
        page.attrib["width"],page.attrib["height"]))
        for word in page.findall(".//{*}word"))


