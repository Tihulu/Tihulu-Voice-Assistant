# SPDX-License-Identifier: AGPL-3.0-only
import unittest
import struct
import zlib
from gateway import Refused
from vision_image_input import decode_png,SIGNATURE

def chunk(kind,data):
    return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data))

def png(w=2,h=1,color=2,rows=b"\0\x01\x02\x03\x04\x05\x06",extra=b"",interlace=0):
    header=struct.pack(">IIBBBBB",w,h,8,color,0,0,interlace)
    return SIGNATURE+chunk(b"IHDR",header)+extra+chunk(b"IDAT",zlib.compress(rows))+chunk(b"IEND",b"")

class VisionImageTests(unittest.TestCase):
    def test_rgb_and_rgba_exact_pixels(self):
        a=decode_png(png())
        self.assertEqual((a.width,a.height,a.channels,a.pixels),(2,1,3,b"\1\2\3\4\5\6"))
        b=decode_png(png(1,1,6,b"\0\x10\x20\x30\xff"))
        self.assertEqual((b.channels,b.pixels),(4,b"\x10\x20\x30\xff"))
    def test_all_filters_reconstruct_identical_pixels(self):
        # first row is 1,2,3,4,5,6; second is identical.
        first=b"\0\1\2\3\4\5\6"
        for method,residual in ((0,b"\1\2\3\4\5\6"),(1,b"\1\2\3\3\3\3"),
                                 (2,b"\0"*6),(3,b"\1\1\2\2\2\2"),(4,b"\0"*6)):
            a=decode_png(png(2,2,rows=first+bytes([method])+residual))
            self.assertEqual(a.pixels,b"\1\2\3\4\5\6"*2)
    def test_digest_binds_geometry_and_normalized_pixels(self):
        a=decode_png(png())
        b=decode_png(png(rows=b"\1\1\2\3\3\3\3"))
        self.assertEqual(a.pixel_digest,b.pixel_digest)
        self.assertNotEqual(a.source_digest,b.source_digest)
        self.assertNotEqual(a.pixel_digest,decode_png(png(1,2,rows=b"\0\1\2\3\0\4\5\6")).pixel_digest)
    def test_metadata_animation_interlace_rejected(self):
        for raw in (png(extra=chunk(b"tEXt",b"secret")),png(extra=chunk(b"acTL",b"\0"*8)),
                    png(interlace=1),png(color=3)):
            with self.assertRaises(Refused):
                decode_png(raw)
    def test_trailing_truncated_crc_and_wrong_type(self):
        raw=png()
        for bad in (raw+b"x",raw[:-1],raw[:-5]+b"x"+raw[-4:],bytearray(raw),b"GIF89a"):
            with self.assertRaises(Refused):
                decode_png(bad)
    def test_dimension_and_decompression_bounds(self):
        for raw in (png(4096,4096),png(0,1),png(rows=b"\0"*100000),png(rows=b"\0"),png(rows=b"\5"*7)):
            with self.assertRaises(Refused):
                decode_png(raw)
    def test_multiple_zlib_members_and_chunk_flood_refused(self):
        header=chunk(b"IHDR",struct.pack(">IIBBBBB",1,1,8,2,0,0,0))
        raw=SIGNATURE+header+chunk(b"IDAT",zlib.compress(b"\0"*4)+zlib.compress(b"x"))+chunk(b"IEND",b"")
        with self.assertRaises(Refused):
            decode_png(raw)
        raw=SIGNATURE+header+chunk(b"IDAT",b"")*256+chunk(b"IEND",b"")
        with self.assertRaises(Refused):
            decode_png(raw)
