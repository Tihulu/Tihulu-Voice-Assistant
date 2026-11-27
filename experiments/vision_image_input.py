# SPDX-License-Identifier: AGPL-3.0-only
"""Closed RGB/RGBA PNG fixture decoder. No file reads, model calls or capture.
Rejects ancillary chunks (including metadata), animation and interlacing.
Not a general image decoder; production needs reviewed isolated codecs.
"""
from dataclasses import dataclass
from hashlib import sha256
import struct
import zlib
from gateway import Refused

SIGNATURE=b"\x89PNG\r\n\x1a\n"
MAX_FILE=4*1024*1024
MAX_PIXELS=1024*1024
MAX_DIM=4096
MAX_CHUNKS=256

@dataclass(frozen=True)
class Raster:
    width: int
    height: int
    channels: int
    pixels: bytes
    source_digest: str
    pixel_digest: str

def _paeth(a,b,c):
    p=a+b-c
    pa,pb,pc=abs(p-a),abs(p-b),abs(p-c)
    return a if pa<=pb and pa<=pc else b if pb<=pc else c

def decode_png(raw):
    if type(raw) is not bytes or not 45<=len(raw)<=MAX_FILE or not raw.startswith(SIGNATURE):
        raise Refused("vision_png_input")
    pos=8
    header=None
    data=[]
    count=0
    while pos<len(raw):
        count+=1
        if count>MAX_CHUNKS or pos+12>len(raw):
            raise Refused("vision_png_chunk")
        n=struct.unpack_from(">I",raw,pos)[0]
        kind=raw[pos+4:pos+8]
        end=pos+12+n
        if end>len(raw):
            raise Refused("vision_png_chunk")
        body=raw[pos+8:pos+8+n]
        if zlib.crc32(kind+body)!=struct.unpack_from(">I",raw,pos+8+n)[0]:
            raise Refused("vision_png_crc")
        pos=end
        if header is None:
            if kind!=b"IHDR" or n!=13:
                raise Refused("vision_png_header")
            w,h,depth,color,compression,filtering,interlace=struct.unpack(">IIBBBBB",body)
            if not (1<=w<=MAX_DIM and 1<=h<=MAX_DIM and w*h<=MAX_PIXELS and
                    depth==8 and color in (2,6) and compression==filtering==interlace==0):
                raise Refused("vision_png_format")
            header=(w,h,3 if color==2 else 4)
        elif kind==b"IDAT":
            data.append(body)
        elif kind==b"IEND" and n==0 and data and pos==len(raw):
            break
        else:
            raise Refused("vision_png_chunk")
    else:
        raise Refused("vision_png_end")
    w,h,c=header
    stride=w*c
    expected=(stride+1)*h
    decoder=zlib.decompressobj()
    try:
        filtered=decoder.decompress(b"".join(data),expected+1)
    except zlib.error as exc:
        raise Refused("vision_png_compression") from exc
    if len(filtered)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise Refused("vision_png_size")
    out=bytearray()
    prev=bytearray(stride)
    for offset in range(0,expected,stride+1):
        method=filtered[offset]
        row=bytearray(filtered[offset+1:offset+1+stride])
        if method>4:
            raise Refused("vision_png_filter")
        if method:
            for i in range(stride):
                a=row[i-c] if i>=c else 0
                b=prev[i]
                d=prev[i-c] if i>=c else 0
                predictor=a if method==1 else b if method==2 else (a+b)//2 if method==3 else _paeth(a,b,d)
                row[i]=(row[i]+predictor)&255
        out.extend(row)
        prev=row
    pixels=bytes(out)
    pixel_digest=sha256(struct.pack(">IIB",w,h,c)+pixels).hexdigest()
    return Raster(w,h,c,pixels,sha256(raw).hexdigest(),pixel_digest)
