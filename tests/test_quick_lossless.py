"""Compression proof, mutation rejection and quick publication lifecycle."""
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
    EncodedStreamObject, IndirectObject, NameObject, NumberObject, TextStringObject)
from lossless_verify import (UnsupportedPDF, _stream_digest, strict_parsing,
                             validate_quick_input, verify_lossless)
from pdf_runtime import build_qpdf_candidate, qpdf_identity, TimeBudgetExceeded
from quick_lossless import optimize_quick as optimize_direct


def optimize_quick(*args,**kwargs):
    # Keep the original strict compression lane covered independently.
    return optimize_direct(*args,optimizer='qpdf',**kwargs)


def rich_fixture(path):
    writer=PdfWriter();page=writer.add_blank_page(100,100)
    font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    image=EncodedStreamObject();image._data=zlib.compress(b'\0\xff\0\xff')
    image.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),
        NameObject('/Width'):NumberObject(2),NameObject('/Height'):NumberObject(2),
        NameObject('/BitsPerComponent'):NumberObject(8),NameObject('/ColorSpace'):NameObject('/DeviceGray'),
        NameObject('/Filter'):NameObject('/FlateDecode')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font}),
        NameObject('/XObject'):DictionaryObject({NameObject('/Im'):writer._add_object(image)})})
    content=DecodedStreamObject();content.set_data(b'0.2 w 1 1 m 99 99 l S\nBT /F1 8 Tf (Searchable text) Tj ET\n'*500)
    page[NameObject('/Contents')]=writer._add_object(content)
    appearance=DecodedStreamObject();appearance.set_data(b'1 1 m 2 2 l S')
    appearance.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Form'),
                       NameObject('/BBox'):ArrayObject([NumberObject(v) for v in (0,0,100,100)])})
    annots=ArrayObject()
    for title in ('one','two'):
        field=DictionaryObject({NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Widget'),
            NameObject('/FT'):NameObject('/Tx'),NameObject('/T'):TextStringObject(title),
            NameObject('/Rect'):ArrayObject([NumberObject(v) for v in (0,0,10,10)]),
            NameObject('/AP'):DictionaryObject({NameObject('/N'):writer._add_object(appearance)})})
        annots.append(writer._add_object(field))
    page[NameObject('/Annots')]=annots
    writer._root_object[NameObject('/AcroForm')]=DictionaryObject({NameObject('/Fields'):annots})
    writer._root_object[NameObject('/Private')]=DictionaryObject({NameObject('/Keep'):TextStringObject('private data')})
    layer=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/OCG'),NameObject('/Name'):TextStringObject('Ink')}))
    writer._root_object[NameObject('/OCProperties')]=DictionaryObject({NameObject('/OCGs'):ArrayObject([layer])})
    writer.add_outline_item('Start',0);writer.add_attachment('data.bin',b'attachment data')
    writer.add_metadata({'/Author':'Fixture author'})
    writer.write(path)


def write_raw(path, number):
    bodies=[b'<< /Type /Catalog /Pages 2 0 R /Precise '+number+b' >>',
            b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>']
    data=bytearray(b'%PDF-1.4\n');offsets=[]
    for index,body in enumerate(bodies,1):
        offsets.append(len(data));data.extend(f'{index} 0 obj\n'.encode()+body+b'\nendobj\n')
    xref=len(data);data.extend(b'xref\n0 4\n0000000000 65535 f \n')
    for offset in offsets:data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode());path.write_bytes(data)


class QuickLosslessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'source.pdf';rich_fixture(self.source)
        self.output=self.root/'out.pdf'

    def rewrite(self,change):
        writer=PdfWriter(clone_from=self.source);change(writer);writer.write(self.output)

    def test_qpdf_repacking_is_verified_without_rendering(self):
        build_qpdf_candidate(self.source,self.output,strict=True)
        evidence=verify_lossless(self.source,self.output)
        self.assertTrue(evidence['complete']);self.assertGreater(evidence['streams'],2)
        self.assertEqual(evidence['visual_checks'],'not run')
        self.assertLess(self.output.stat().st_size,self.source.stat().st_size)

    def test_png_predictor_modes_preserve_exact_image_bytes(self):
        rows=[bytes([3,17,101,250]),bytes([5,20,99,240]),bytes([9,25,100,230]),
              bytes([1,2,3,4]),bytes([210,30,160,80])]
        encoded=bytearray();previous=bytes(4)
        for mode,row in enumerate(rows):
            encoded.append(mode)
            for col,value in enumerate(row):
                left=row[col-1] if col else 0;up=previous[col];corner=previous[col-1] if col else 0
                estimate=left+up-corner;distances=[abs(estimate-v) for v in (left,up,corner)]
                prediction=[0,left,up,(left+up)//2,(left,up,corner)[distances.index(min(distances))]][mode]
                encoded.append((value-prediction)&255)
            previous=row
        writer=PdfWriter(clone_from=self.source);image=writer.pages[0]['/Resources']['/XObject']['/Im']
        image._data=zlib.compress(encoded)
        image[NameObject('/Width')]=NumberObject(4);image[NameObject('/Height')]=NumberObject(5)
        image[NameObject('/DecodeParms')]=DictionaryObject({NameObject('/Predictor'):NumberObject(15),
            NameObject('/Columns'):NumberObject(4)})
        writer.write(self.source)
        def decode(w):
            image=w.pages[0]['/Resources']['/XObject']['/Im'];image._data=b''.join(rows)
            del image['/Filter'];del image['/DecodeParms']
        self.rewrite(decode)
        self.assertTrue(verify_lossless(self.source,self.output)['complete'])
        def damage(w):
            decode(w);image=w.pages[0]['/Resources']['/XObject']['/Im'];image._data=bytes([image._data[0]^1])+image._data[1:]
        self.rewrite(damage)
        with self.assertRaises(ValueError):verify_lossless(self.source,self.output)

    def test_first_document_id_is_preserved(self):
        from pypdf.generic import ByteStringObject
        writer=PdfWriter(clone_from=self.source)
        writer._ID=ArrayObject([ByteStringObject(b'first document id'),ByteStringObject(b'update id')])
        writer.write(self.source)
        def change(w):w._ID[0]=ByteStringObject(b'wrong identity')
        self.rewrite(change)
        with self.assertRaises(ValueError):verify_lossless(self.source,self.output)

    def test_document_ids_cannot_hide_unverified_dictionary_data(self):
        from pypdf.generic import ByteStringObject
        writer=PdfWriter(clone_from=self.source)
        writer._ID=ArrayObject([ByteStringObject(b'identity'),DictionaryObject({NameObject('/Payload'):TextStringObject('keep')})])
        writer.write(self.source);shutil.copyfile(self.source,self.output)
        with self.assertRaisesRegex(UnsupportedPDF,'ID representation'):verify_lossless(self.source,self.output)

    def test_identical_independent_objects_cannot_be_merged(self):
        writer=PdfWriter(clone_from=self.source)
        first=writer._add_object(DictionaryObject({NameObject('/Value'):NumberObject(1)}))
        second=writer._add_object(DictionaryObject({NameObject('/Value'):NumberObject(1)}))
        writer._root_object[NameObject('/Identities')]=ArrayObject([first,second]);writer.write(self.source)
        def merge(w):w._root_object['/Identities'][1]=w._root_object['/Identities'][0]
        self.rewrite(merge)
        with self.assertRaisesRegex(ValueError,'merged'):verify_lossless(self.source,self.output)

    def test_mutations_to_content_functions_and_private_data_are_rejected(self):
        def content(w):w.pages[0]['/Contents'].set_data(b'0 0 m 0 0 l S')
        def image(w):w.pages[0]['/Resources']['/XObject']['/Im']._data=zlib.compress(b'\xff'*4)
        def image_params(w):w.pages[0]['/Resources']['/XObject']['/Im'][NameObject('/Width')]=NumberObject(4)
        def font(w):w.pages[0]['/Resources']['/Font']['/F1'][NameObject('/BaseFont')]=NameObject('/Courier')
        def annotation(w):w.pages[0]['/Annots'][0].get_object()['/AP']['/N'].set_data(b'')
        def hit_area(w):w.pages[0]['/Annots'][0].get_object()[NameObject('/Rect')][2]=NumberObject(90)
        def field(w):w.pages[0]['/Annots'][0].get_object()[NameObject('/T')]=TextStringObject('changed')
        def bookmark(w):w._root_object['/Outlines']['/First'][NameObject('/Title')]=TextStringObject('changed')
        def attachment(w):w._root_object['/Names']['/EmbeddedFiles']['/Names'][1]['/EF']['/F'].set_data(b'changed')
        def metadata(w):w.add_metadata({'/Author':'changed'})
        def private(w):del w._root_object['/Private']['/Keep']
        def layer(w):w._root_object['/OCProperties']['/OCGs'][0].get_object()[NameObject('/Name')]=TextStringObject('changed')
        for change in (content,image,image_params,font,annotation,hit_area,field,bookmark,attachment,metadata,private,layer):
            with self.subTest(change=change.__name__):
                self.rewrite(change)
                with self.assertRaises(ValueError):verify_lossless(self.source,self.output)

    def test_reference_identity_merge_and_split_are_rejected(self):
        def merge(w):w.pages[0]['/Annots'][1]=w.pages[0]['/Annots'][0]
        self.rewrite(merge)
        with self.assertRaisesRegex(ValueError,'merged|shared object split'):verify_lossless(self.source,self.output)
        def split(w):
            # The array shared by /Annots and /Fields must stay shared.
            w._root_object['/AcroForm'][NameObject('/Fields')]=ArrayObject([w.pages[0]['/Annots'][0]])
        self.rewrite(split)
        with self.assertRaises(ValueError):verify_lossless(self.source,self.output)

    def test_cycles_and_aliases_survive_repacking(self):
        writer=PdfWriter(clone_from=self.source)
        obj=DictionaryObject();ref=writer._add_object(obj);obj[NameObject('/Self')]=ref
        writer._root_object[NameObject('/Cycle')]=ref;writer.write(self.source)
        build_qpdf_candidate(self.source,self.output,strict=True)
        self.assertTrue(verify_lossless(self.source,self.output)['identity_preserved'])

    def test_decimal_changes_below_float_precision_are_rejected(self):
        write_raw(self.source,b'0.12345678901234567891')
        write_raw(self.output,b'0.12345678901234567892')
        with self.assertRaisesRegex(ValueError,'numeric'):verify_lossless(self.source,self.output)

    def test_pdf_version_downgrade_is_unsupported(self):
        write_raw(self.source,b'1.0');write_raw(self.output,b'1.0')
        self.output.write_bytes(self.output.read_bytes().replace(b'%PDF-1.4',b'%PDF-1.3'))
        with self.assertRaisesRegex(UnsupportedPDF,'version'):verify_lossless(self.source,self.output)

    def test_custom_trailer_entries_are_not_dropped_by_stream_xrefs(self):
        write_raw(self.source,b'1.0')
        self.source.write_bytes(self.source.read_bytes().replace(b'/Size 4 /Root',b'/PrivateTrailer (kept) /Size 4 /Root'))
        build_qpdf_candidate(self.source,self.output,strict=True)
        # If the tool drops the key, the original comparison must reject it.
        # Otherwise mutate the key's value in the generated xref stream object.
        raw=self.output.read_bytes()
        if b'/PrivateTrailer (kept)' in raw:
            self.output.write_bytes(raw.replace(b'/PrivateTrailer (kept)',b'/PrivateTrailer (lost)'))
        with self.assertRaises(ValueError):verify_lossless(self.source,self.output)

    def test_quick_recipe_preserves_encodings_and_unreferenced_objects(self):
        identity=qpdf_identity();success=subprocess.CompletedProcess([],0,'','')
        with patch('pdf_runtime.subprocess.run',return_value=success) as run:
            self.output.write_bytes(b'candidate')
            build_qpdf_candidate(self.source,self.output,identity=identity,strict=True)
        command=run.call_args.args[0]
        self.assertIn('--decode-level=none',command);self.assertIn('--preserve-unreferenced',command)
        self.assertNotIn('--recompress-flate',command)

    def test_unreachable_payloads_are_preserved_and_dropping_them_is_rejected(self):
        writer=PdfWriter(clone_from=self.source);writer._add_object(TextStringObject('private orphan'));writer.write(self.source)
        build_qpdf_candidate(self.source,self.output,strict=True)
        self.assertEqual(verify_lossless(self.source,self.output)['unreferenced_objects_preserved'],1)
        self.output.unlink();build_qpdf_candidate(self.source,self.output)
        with self.assertRaisesRegex(UnsupportedPDF,'Unreferenced'):verify_lossless(self.source,self.output)
        self.source.write_bytes(self.source.read_bytes()+b'payload')
        with self.assertRaisesRegex(UnsupportedPDF,'Unaccounted'):validate_quick_input(self.source)

    def test_dangling_references_are_not_normalized(self):
        self.rewrite(lambda w:w._root_object.__setitem__(NameObject('/Broken'),IndirectObject(99999,0,w)))
        with self.assertRaises(ValueError):verify_lossless(self.source,self.output)

    def test_parser_warnings_fail_and_handler_is_restored(self):
        handlers=list(logging.getLogger('pypdf').handlers)
        with self.assertRaisesRegex(UnsupportedPDF,'warning'),strict_parsing():
            logging.getLogger('pypdf.generic').warning('fixture warning')
        self.assertEqual(logging.getLogger('pypdf').handlers,handlers)

    def test_decompression_and_graph_limits_fail_closed(self):
        build_qpdf_candidate(self.source,self.output,strict=True)
        for name,limit in [('MAX_STREAM_BYTES',10),('MAX_DECODED_BYTES',10),('MAX_DEPTH',1),('MAX_OBJECTS',1)]:
            with self.subTest(limit=name),patch('lossless_verify.'+name,limit),self.assertRaises(UnsupportedPDF):
                verify_lossless(self.source,self.output)
        with self.assertRaises(UnsupportedPDF):_stream_digest(zlib.compress(b'a'*100)+b'junk',True,lambda:None,[0])

    def test_incremental_encrypted_and_signature_inputs_are_declined(self):
        for kind in ('encrypted','signature','incremental'):
            with self.subTest(kind=kind):
                writer=PdfWriter(clone_from=self.source)
                if kind=='encrypted':writer.encrypt('secret')
                if kind=='signature':writer._root_object[NameObject('/Perms')]=DictionaryObject()
                if kind=='incremental':
                    # Use pypdf's actual incremental writer, preserving a valid xref chain.
                    writer=PdfWriter(self.source,incremental=True);writer.add_metadata({'/New':'revision'})
                changed=self.root/(kind+'.pdf');writer.write(changed)
                with self.assertRaises(ValueError):validate_quick_input(changed)

    def test_quick_success_does_not_import_renderer_dependencies(self):
        command=[sys.executable,'-B','-c',
            'import sys; sys.modules["PIL"]=None; from optimize_pdf import optimize; '
            'optimize(sys.argv[1],sys.argv[2],viewing_copy=True,viewing_profile="quick",viewing_optimizer="qpdf"); '
            'assert not any(x in sys.modules for x in ("viewing_copy","pypdfium2","pymupdf","browser_check"))',
            str(self.source),str(self.output)]
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['classification'],'smaller-lossless')
        self.assertEqual(report['rendering_performance'],'not measured')

    def test_no_size_benefit_skips_graph_verification(self):
        with patch('quick_lossless.build_qpdf_candidate',side_effect=lambda a,b,*args,**kwargs:shutil.copyfile(a,b)),\
             patch('quick_lossless.verify_lossless',side_effect=AssertionError('must not verify')):
            optimize_quick(self.source,self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(json.loads(self.output.with_suffix('.json').read_text())['classification'],'no size benefit')

    def test_verification_failure_writes_reports_only(self):
        with patch('quick_lossless.verify_lossless',side_effect=UnsupportedPDF('unsupported codec')):
            optimize_quick(self.source,self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(json.loads(self.output.with_suffix('.json').read_text())['classification'],'unsupported verification')

    def test_tool_warning_blocks_publication(self):
        identity=qpdf_identity()
        warning=subprocess.CompletedProcess([],3,'','warning: damaged stream')
        with patch('pdf_runtime.subprocess.run',return_value=warning),self.assertRaisesRegex(ValueError,'warnings'):
            build_qpdf_candidate(self.source,self.output,identity=identity,strict=True)

    def test_source_hash_output_collision_and_budget_are_enforced(self):
        with self.assertRaisesRegex(ValueError,'reviewed source hash'):
            optimize_quick(self.source,self.output,'f'*64)
        self.output.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):optimize_quick(self.source,self.output)
        self.assertEqual(self.output.read_bytes(),b'existing');self.output.unlink()
        with self.assertRaises(TimeBudgetExceeded):optimize_quick(self.source,self.output,time_budget_seconds=.000001)
        self.assertFalse(self.output.exists())

    def test_mutation_during_verification_prevents_publication(self):
        actual=verify_lossless
        def mutate(a,b,check):
            evidence=actual(a,b,check);a.write_bytes(b'changed');return evidence
        with patch('quick_lossless.verify_lossless',side_effect=mutate),self.assertRaisesRegex(ValueError,'Original changed'):
            optimize_quick(self.source,self.output)
        self.assertFalse(self.output.exists())

    def test_interrupted_publication_and_report_failure_remove_owned_files(self):
        with patch('quick_lossless.shutil.copyfileobj',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):
            optimize_quick(self.source,self.output)
        self.assertFalse(any(self.output.with_suffix(s).exists() for s in ('.pdf','.json','.md')))
        original=Path.open
        def fail_report(path,*args,**kwargs):
            if path==self.output.with_suffix('.md'):raise OSError('report write failed')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',fail_report),self.assertRaisesRegex(OSError,'report write'):
            optimize_quick(self.source,self.output)
        self.assertFalse(any(self.output.with_suffix(s).exists() for s in ('.pdf','.json','.md')))

    def test_api_keeps_thorough_default_and_rejects_incompatible_quick_options(self):
        from optimize_pdf import optimize
        for options in ({'viewing_optimizer':'pymupdf'},{'parallel_candidates':True},{'verification_workers':2}):
            with self.subTest(options=options),self.assertRaisesRegex(ValueError,'Quick mode'):
                optimize(self.source,self.output,viewing_copy=True,viewing_profile='quick',**options)
        with patch('viewing_copy.optimize_viewing',return_value=(1,1)) as thorough:
            optimize(self.source,self.output,viewing_copy=True)
            thorough.assert_called_once()


class DirectOptimizationTests(unittest.TestCase):
    def setUp(self):
        from test_inline_handwriting import fixture
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'input.pdf';self.output=self.root/'output.pdf'
        writer,_,_=fixture()
        # An obsolete, unreachable editor object must not dominate a viewing copy.
        writer._add_object(TextStringObject('obsolete editor recovery data '*3000));writer.write(self.source)

    def test_inlining_cleanup_and_packing_remain_without_any_benchmarks(self):
        original=self.source.read_bytes()
        with patch('viewing_copy.benchmarks',side_effect=AssertionError('benchmark ran')),\
             patch('viewing_copy.choose_candidate',side_effect=AssertionError('candidate tournament ran')),\
             patch('viewing_copy.benchmark_worker',side_effect=AssertionError('render benchmark ran')):
            optimize_direct(self.source,self.output)
        report=json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['status'],'optimized');self.assertEqual(report['optimizer'],'adaptive')
        self.assertGreater(report['transformations']['inlined_form_calls'],0)
        self.assertLess(self.output.stat().st_size,self.source.stat().st_size*.5)
        self.assertEqual(self.source.read_bytes(),original)
        self.assertTrue(report['verification']['complete']);self.assertTrue(report['verification']['exact'])
        self.assertTrue(all(page['exact'] for page in report['pdfjs_all_pages']))
        self.assertEqual(report['rendering_performance'],'not measured')

    def test_no_inline_opportunity_keeps_deduplication_and_cleanup(self):
        rich_fixture(self.source)
        optimize_direct(self.source,self.output)
        report=json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['status'],'optimized')
        self.assertEqual(report['transformations']['inlined_form_calls'],0)
        self.assertIn('safe deduplication',report['pipeline'])

    def test_unexpected_inlining_error_is_not_treated_as_ineligible(self):
        with patch('handwriting.inline_handwriting',side_effect=ValueError('unexpected transform error')):
            optimize_direct(self.source,self.output)
        self.assertFalse(self.output.exists())
        report=json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['classification'],'verification failed')
        self.assertIn('unexpected transform error',report['reason'])

    def test_failed_packing_keeps_the_verified_transformed_copy(self):
        with patch('quick_lossless.build_qpdf_candidate',side_effect=ValueError('packing unavailable')):
            optimize_direct(self.source,self.output)
        report=json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['status'],'optimized');self.assertIn('packing unavailable',report['packing_skipped'])
        self.assertTrue(report['verification']['complete'])

    def test_preservation_failure_does_not_publish(self):
        with patch('viewing_copy.verify_candidate',side_effect=ValueError('a stroke changed')):
            optimize_direct(self.source,self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(json.loads(self.output.with_suffix('.json').read_text())['classification'],'verification failed')

    def test_thirty_seconds_is_not_a_publication_gate(self):
        from pdf_runtime import RunBudget
        def already_elapsed(*args,**kwargs):
            budget=RunBudget(*args,**kwargs);budget.started-=31;return budget
        with patch('quick_lossless.RunBudget',side_effect=already_elapsed):optimize_direct(self.source,self.output)
        report=json.loads(self.output.with_suffix('.json').read_text())
        self.assertEqual(report['status'],'optimized');self.assertGreater(report['total_elapsed_seconds'],30)


if __name__=='__main__':unittest.main()
