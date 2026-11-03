import contextlib
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from optimize_pdf import navigation_fingerprint, optimize
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, Fit, NameObject, NumberObject, TextStringObject
from navigation import reader_for, validate_input


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

    def test_custom_classic_trailer_loss_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'custom.pdf',Path(folder)/'out.pdf'
            writer=PdfWriter();writer.add_blank_page(width=20,height=20)
            private=writer._add_object(DictionaryObject({NameObject('/Value'):NumberObject(7)}));writer.write(source)
            raw=source.read_bytes();marker=b'>>\nstartxref'
            source.write_bytes(raw.replace(marker,f'/PrivateDirect (kept) /PrivateTrailer {private.idnum} 0 R\n'.encode()+marker,1))
            before=reader_for(source)
            self.assertIn('/PrivateTrailer',before.trailer);self.assertEqual(before.trailer['/PrivateDirect'],'kept')
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'Table of contents'):
                optimize(source,out)
            self.assertFalse(out.exists())

    def test_custom_xref_stream_trailer_is_fingerprinted(self):
        from pdf_runtime import build_qpdf_candidate
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,stream,changed=(Path(folder)/name for name in ('source.pdf','stream.pdf','changed.pdf'))
            writer=PdfWriter();writer.add_blank_page(width=20,height=20);writer.write(source)
            marker=b'>>\nstartxref';raw=source.read_bytes()
            source.write_bytes(raw.replace(marker,b'/PrivateDirect (kept)\n'+marker,1))
            build_qpdf_candidate(source,stream,strict=True);shutil.copyfile(stream,changed)
            reader=reader_for(stream);self.assertEqual(reader.trailer['/PrivateDirect'],'kept')
            changed.write_bytes(changed.read_bytes().replace(b'/PrivateDirect (kept)',b'/PrivateDirect (lost)',1))
            self.assertNotEqual(navigation_fingerprint(reader),navigation_fingerprint(reader_for(changed)))

    def test_orphan_signature_objects_are_rejected(self):
        signatures=(DictionaryObject({NameObject('/Type'):NameObject('/Sig')}),
                    DictionaryObject({NameObject('/FT'):NameObject('/Sig')}),
                    DictionaryObject({NameObject('/ByteRange'):NumberObject(1),
                                      NameObject('/Contents'):TextStringObject('signed')}))
        for index,signature in enumerate(signatures):
            with self.subTest(index=index),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                source=Path(folder)/'signature.pdf';writer=PdfWriter();writer.add_blank_page(width=20,height=20)
                writer._add_object(signature);writer.write(source)
                with self.assertRaisesRegex(ValueError,'signatures'):validate_input(reader_for(source))

    def test_signature_in_object_stream_is_rejected(self):
        from pdf_runtime import build_qpdf_candidate
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,compressed=Path(folder)/'source.pdf',Path(folder)/'compressed.pdf'
            writer=PdfWriter();writer.add_blank_page(width=20,height=20)
            writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Sig')}));writer.write(source)
            build_qpdf_candidate(source,compressed,strict=True)
            reader=reader_for(compressed);self.assertTrue(reader.xref_objStm)
            with self.assertRaisesRegex(ValueError,'signatures'):validate_input(reader)


if __name__=='__main__':unittest.main()
