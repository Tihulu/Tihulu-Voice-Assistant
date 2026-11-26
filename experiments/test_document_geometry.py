# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from document_geometry import pdf_blocks, normalize_box, ocr_blocks

class GeometryTests(unittest.TestCase):
    def test_poppler_namespaced_and_plain_xhtml(self):
        for ns in (' xmlns="http://www.w3.org/1999/xhtml"', ''):
            xml = ('<html'+ns+'><body><doc><page width="100" height="200">'
                   '<word xMin="10" yMin="20" xMax="50" yMax="60">İstanbul</word>'
                   '</page></doc></body></html>')
            blocks = pdf_blocks(xml)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0].text, "İstanbul")
            self.assertEqual(blocks[0].box, (1000,1000,5000,3000))

    def test_zero_or_multiple_pages_refused(self):
        for xml in ("<html/>", '<html><page/><page/></html>'):
            with self.assertRaisesRegex(ValueError, "page count"):
                pdf_blocks(xml)

    def test_normalized_geometry_rejects_nonfinite_and_outside(self):
        for args in ((0,0,101,100,100,100), (0,0,0,1,100,100),
                     ("NaN",0,1,1,100,100)):
            with self.assertRaises(ValueError):
                normalize_box(*args)

    def test_tsv_word_level_confidence_and_geometry(self):
        tsv = "level\tleft\ttop\twidth\theight\tconf\ttext\n5\t10\t20\t40\t40\t93.9\tMünchen\n"
        block, = ocr_blocks(tsv,100,200)
        self.assertEqual((block.text,block.box,block.confidence),
                         ("München",(1000,1000,5000,3000),93))
