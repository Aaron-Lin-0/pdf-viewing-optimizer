import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from optimize_pdf import navigation_fingerprint, optimize
from pypdf import PdfReader, PdfWriter
from pypdf.generic import Fit


class NavigationTests(unittest.TestCase):
    def fixture(self,path):
        w=PdfWriter()
        for _i in range(3):w.add_blank_page(width=100,height=100)
        parent=w.add_outline_item('Chapter 1',0,bold=True,is_open=False)
        w.add_outline_item('Small letters',1,parent=parent,italic=True,fit=Fit.xyz(left=12,top=70,zoom=1.2))
        w.add_outline_item('Chapter 2',2)
        w.add_named_destination('contents-target',1)
        w.add_attachment('Table of contents.txt',b'Chapter 1: page 1\nChapter 2: page 3\n')
        w.write(path)

    def test_nested_styled_toc_named_destinations_and_attachments_survive(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf';self.fixture(a)
            with contextlib.redirect_stdout(io.StringIO()):optimize(a,b)
            before,after=PdfReader(a),PdfReader(b)
            self.assertEqual(navigation_fingerprint(before),navigation_fingerprint(after))
            self.assertEqual(after.attachments['Table of contents.txt'][0],b'Chapter 1: page 1\nChapter 2: page 3\n')

    def test_toc_loss_is_rejected_even_when_pages_are_identical(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf';self.fixture(a)
            original=PdfWriter.compress_identical_objects
            def remove_toc(w,*args,**kwargs):
                original(w,*args,**kwargs);w.root_object.pop('/Outlines',None)
            with patch.object(PdfWriter,'compress_identical_objects',remove_toc):
                with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'Table of contents'):
                    optimize(a,b)
            self.assertFalse(b.exists())


if __name__=='__main__':unittest.main()
