import contextlib
import io
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from optimize_pdf import navigation_fingerprint, optimize
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, Fit, NameObject, NumberObject, TextStringObject
from navigation import reader_for, validate_input


def append_xref_revision(source, target, private_value):
    raw=source.read_bytes()
    with reader_for(source) as reader:
        number=int(reader.trailer['/Size']);root=reader.trailer.raw_get('/Root')
        root_ref=f'{root.idnum} {root.generation} R'
    previous=int(re.findall(rb'startxref\s+(\d+)',raw)[-1]);offset=len(raw)
    entry=struct.pack('>BIH',1,offset,0)
    header=(f'{number} 0 obj\n<< /Type /XRef /Size {number + 1} /Root {root_ref} '
            f'/Prev {previous} /W [1 4 2] /Index [{number} 1] /Length 7 '
            f'/PrivateRevision ({private_value}) >>\nstream\n').encode('ascii')
    tail=f'\nendstream\nendobj\nstartxref\n{offset}\n%%EOF\n'.encode('ascii')
    with target.open('xb') as output:output.write(raw+header+entry+tail)


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

    def test_newest_incremental_xref_stream_trailer_wins(self):
        from pdf_runtime import build_qpdf_candidate, check_qpdf, qpdf_identity
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);classic=root/'classic.pdf';stream=root/'stream.pdf'
            new=root/'new.pdf';newest=root/'newest.pdf';mixed=root/'mixed.pdf';out=root/'out.pdf'
            writer=PdfWriter();writer.add_blank_page(width=20,height=20);writer.write(classic)
            build_qpdf_candidate(classic,stream,strict=True)
            append_xref_revision(stream,new,'new-value')
            self.assertEqual(reader_for(new).trailer['/PrivateRevision'],'new-value')
            append_xref_revision(new,newest,'newest-value')
            self.assertEqual(reader_for(newest).trailer['/PrivateRevision'],'newest-value')
            raw=classic.read_bytes();marker=b'>>\nstartxref'
            classic.write_bytes(raw.replace(marker,b'/PrivateRevision (old-value)\n'+marker,1))
            append_xref_revision(classic,mixed,'new-value')
            self.assertEqual(reader_for(mixed).trailer['/PrivateRevision'],'new-value')
            identity=qpdf_identity()
            for path in (new,newest,mixed):check_qpdf(path,identity)
            original=newest.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'Table of contents'):
                optimize(newest,out)
            self.assertFalse(out.exists());self.assertEqual(newest.read_bytes(),original)

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

    def test_direct_signature_widgets_and_parents_are_rejected(self):
        from lossless_verify import validate_quick_input
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder)
            for parent_signature in (False,True):
                with self.subTest(parent_signature=parent_signature):
                    source=root/f'signature-{parent_signature}.pdf';out=root/f'out-{parent_signature}.pdf'
                    writer=PdfWriter();page=writer.add_blank_page(width=20,height=20)
                    parent=DictionaryObject({NameObject('/FT'):NameObject('/Sig')})
                    annotation=DictionaryObject({NameObject('/Subtype'):NameObject('/Widget'),
                        NameObject('/T'):TextStringObject('Signature'),
                        NameObject('/Rect'):ArrayObject([NumberObject(0)]*4)})
                    if parent_signature:annotation[NameObject('/Parent')]=parent
                    else:annotation[NameObject('/FT')]=NameObject('/Sig')
                    page[NameObject('/Annots')]=ArrayObject([annotation]);writer.write(source)
                    original=source.read_bytes()
                    with self.assertRaisesRegex(ValueError,'signatures'):validate_input(reader_for(source))
                    with self.assertRaisesRegex(ValueError,'signatures'):validate_quick_input(source)
                    with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'signatures'):
                        optimize(source,out)
                    self.assertFalse(out.exists());self.assertEqual(source.read_bytes(),original)

    def test_direct_annotation_parent_cycle_terminates(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'cycle.pdf';writer=PdfWriter();page=writer.add_blank_page(width=20,height=20)
            parent=DictionaryObject();annotation=DictionaryObject({NameObject('/Subtype'):NameObject('/Widget'),
                NameObject('/Rect'):ArrayObject([NumberObject(0)]*4),NameObject('/Parent'):parent})
            page[NameObject('/Annots')]=ArrayObject([annotation]);writer.write(source)
            reader=reader_for(source);loaded_parent=reader.pages[0]['/Annots'][0]['/Parent']
            loaded_parent[NameObject('/Parent')]=loaded_parent
            validate_input(reader)


if __name__=='__main__':unittest.main()
