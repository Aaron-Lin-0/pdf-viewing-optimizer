import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
import json
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from pypdf import PdfReader,PdfWriter
from pypdf.generic import ArrayObject,DictionaryObject,NameObject,NumberObject,TextStringObject,DecodedStreamObject
from annotation_ink import flatten_ink,consolidate_ink,verify_flattened,verify_consolidated
from navigation import navigation_fingerprint
from viewing_copy import verify_candidate,choose_candidate,visual_delta,reader_for,build_candidate,benchmark_pages,optimize_viewing
from test_inline_handwriting import fixture
from PIL import Image


def annotation_fixture():
    w,parents,leaf=fixture()
    page=w.pages[0]
    page[NameObject('/Contents')]=w._add_object(DecodedStreamObject())
    ann=DictionaryObject({NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Stamp'),
                          NameObject('/Name'):NameObject('/#ONYX-STROKE'),NameObject('/F'):NumberObject(4),
                          NameObject('/Rect'):ArrayObject([NumberObject(x) for x in [0,0,60,60]]),
                          NameObject('/AP'):DictionaryObject({NameObject('/N'):leaf.indirect_reference})})
    page[NameObject('/Annots')]=ArrayObject([w._add_object(ann)])
    return w,page,ann


class ViewingTests(unittest.TestCase):
    def test_nonpainted_link_retained_between_converted_strokes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf';w,page,ann=annotation_fixture()
            link=DictionaryObject({NameObject('/Subtype'):NameObject('/Link'),NameObject('/Rect'):ann['/Rect'],NameObject('/Border'):ArrayObject([NumberObject(0)]*3),NameObject('/A'):DictionaryObject({NameObject('/S'):NameObject('/URI'),NameObject('/URI'):TextStringObject('https://example.com')})})
            page['/Annots'].extend([w._add_object(link),w._add_object(DictionaryObject(ann))]);w.write(a)
            manifest=flatten_ink(w);self.assertEqual(manifest['converted'],{0:[0,2]})
            consolidate_ink(w,manifest);w.write(b)
            self.assertEqual(PdfReader(b).pages[0]['/Annots'][0]['/Subtype'],'/Link')
            with contextlib.redirect_stdout(io.StringIO()):self.assertTrue(verify_candidate(a,b,manifest)['exact'])

    def test_unfamiliar_static_appearance_and_opacity_keep_form_calls(self):
        from pypdf.generic import FloatObject
        for change in ['wrapper','opacity','open-path']:
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
                w,page,ann=annotation_fixture();form=ann['/AP']['/N']
                if change=='wrapper':form.set_data(b'q\n'+form.get_data()+b'Q\n')
                elif change=='opacity':form['/Resources']['/ExtGState']['/GS'][NameObject('/CA')]=FloatObject(.5)
                else:form.set_data(form.get_data()+b'70 70 m\n')
                w.write(a);manifest=flatten_ink(w);consolidate_ink(w,manifest);w.write(b)
                self.assertEqual(len(manifest['consolidated'][0]['ordinary_calls']),1)
                with contextlib.redirect_stdout(io.StringIO()):self.assertTrue(verify_candidate(a,b,manifest)['exact'])
                from viewing_copy import verify_pdfjs_page
                self.assertTrue(verify_pdfjs_page(a,b,1)['exact'])

    def test_transformed_appearance_stays_a_form_call_with_exact_geometry(self):
        for matrix in [[2,0,0,2,0,0],[0,1,-1,0,60,0]]:
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
                w,page,ann=annotation_fixture();ann['/AP']['/N'][NameObject('/Matrix')]=ArrayObject([NumberObject(n) for n in matrix])
                w.write(a);manifest=flatten_ink(w);consolidate_ink(w,manifest);w.write(b)
                self.assertEqual(len(manifest['consolidated'][0]['ordinary_calls']),1)
                with contextlib.redirect_stdout(io.StringIO()):self.assertTrue(verify_candidate(a,b,manifest)['exact'])
                from viewing_copy import verify_pdfjs_page
                self.assertTrue(verify_pdfjs_page(a,b,1)['exact'])
    def test_browser_profile_cleanup_retries_transient_file_locks(self):
        from browser_check import profile_directory
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            native=tempfile.TemporaryDirectory.cleanup;attempts=[]
            def delayed(directory):
                attempts.append(directory.name)
                if len(attempts)<3:raise PermissionError('Transient browser cache lock')
                return native(directory)
            with patch.object(tempfile.TemporaryDirectory,'cleanup',delayed),patch('browser_check.time.sleep'):
                with profile_directory(folder) as profile:Path(profile,'cache').write_text('test')
            self.assertEqual(len(attempts),3);self.assertFalse(Path(profile).exists())

    def test_rotated_cropped_page_faint_ink_and_shared_resources(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
            w,page,ann=annotation_fixture();page[NameObject('/Rotate')]=NumberObject(90)
            page[NameObject('/CropBox')]=ArrayObject([NumberObject(n) for n in [5,5,95,95]])
            form=ann['/AP']['/N'];form.set_data(form.get_data().replace(b'0 0 0 RG',b'0.8 G'))
            w.write(a);manifest=flatten_ink(w);consolidate_ink(w,manifest);w.write(b)
            with contextlib.redirect_stdout(io.StringIO()):self.assertTrue(verify_candidate(a,b,manifest)['exact'])

    def test_mixed_page_combines_ordinary_forms_and_static_ink(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
            w,page,ann=annotation_fixture()
            stream=DecodedStreamObject();stream.set_data(b'q /Layer Do Q\n')
            page[NameObject('/Contents')]=w._add_object(stream);w.write(a)
            with contextlib.redirect_stdout(io.StringIO()):manifest=build_candidate(a,b,'combined')
            self.assertEqual(manifest['count'],1)
            with contextlib.redirect_stdout(io.StringIO()):self.assertTrue(verify_candidate(a,b,manifest)['exact'])

    def test_viewing_protection_and_report_collisions(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
            w=PdfWriter();p=w.add_blank_page(width=60,height=60);w.encrypt('secret');w.write(a)
            with self.assertRaisesRegex(ValueError,'Encrypted'):optimize_viewing(a,b)
            w=PdfWriter();p=w.add_blank_page(width=60,height=60)
            signature=DictionaryObject({NameObject('/Subtype'):NameObject('/Widget'),NameObject('/FT'):NameObject('/Sig')})
            p[NameObject('/Annots')]=ArrayObject([w._add_object(signature)]);w.write(a)
            with self.assertRaisesRegex(ValueError,'Signature'):optimize_viewing(a,b)
            b.with_suffix('.json').write_text('keep')
            with self.assertRaises(FileExistsError):optimize_viewing(a,b)
            self.assertEqual(b.with_suffix('.json').read_text(),'keep')

    def test_missing_dependencies_leave_no_outputs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
            w=PdfWriter();w.add_blank_page(width=60,height=60);w.write(a);original=a.read_bytes()
            with patch('viewing_copy.benchmark_worker',side_effect=ValueError('missing canvas')):
                with self.assertRaisesRegex(ValueError,'missing canvas'):optimize_viewing(a,b)
            self.assertEqual(list(Path(folder).iterdir()),[a]);self.assertEqual(a.read_bytes(),original)

    def test_interrupted_report_write_cleans_only_its_new_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
            w=PdfWriter();w.add_blank_page(width=60,height=60);w.write(a);original=a.read_bytes()
            native_open=Path.open
            def interrupted(path,*args,**kwargs):
                if path==b.with_suffix('.md'):raise KeyboardInterrupt()
                return native_open(path,*args,**kwargs)
            with patch('viewing_copy.benchmark_worker',return_value={}),patch('viewing_copy.build_candidate',side_effect=ValueError('unsupported')),patch('pymupdf.open',side_effect=ValueError('packing unsupported')),patch.object(Path,'open',interrupted):
                with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(KeyboardInterrupt):optimize_viewing(a,b)
            self.assertEqual(list(Path(folder).iterdir()),[a]);self.assertEqual(a.read_bytes(),original)

    def test_unknown_broken_layer_blocks_transform(self):
        from pypdf.generic import IndirectObject
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a=Path(folder)/'a.pdf';w,page,ann=annotation_fixture()
            ann[NameObject('/OC')]=IndirectObject(999999,0,w);w.write(a)
            with self.assertRaisesRegex(ValueError,'Unrecognized broken layer'):reader_for(a)

    def test_geometry_accepts_inlined_run_after_transformed_call(self):
        from geometry import page_signature
        from handwriting import inline_handwriting
        from test_inline_handwriting import TRANSFORMED,IDENTITY
        w,parents,leaf=fixture();parents[0].set_data(TRANSFORMED+IDENTITY)
        a=io.BytesIO();w.write(a);a.seek(0);before=PdfReader(a)
        with contextlib.redirect_stdout(io.StringIO()):inline_handwriting(w)
        b=io.BytesIO();w.write(b);b.seek(0)
        self.assertEqual(page_signature(before,0),page_signature(PdfReader(b),0))
    def test_appearance_failure_restores_original_page(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b=[Path(folder)/n for n in ['a.pdf','b.pdf']]
            w,page,ann=annotation_fixture()
            second=w.add_blank_page(width=60,height=60)
            second[NameObject('/Annots')]=ArrayObject([w._add_object(DictionaryObject(ann))])
            w.write(a)
            with patch('viewing_copy.visual_delta',side_effect=[ValueError('off-edge pixels'),{'exact':True}]):
                manifest=build_candidate(a,b,'annotations')
            self.assertEqual(manifest['converted'],{2:[0]})
            self.assertEqual(PdfReader(b).pages[0]['/Annots'][0]['/Subtype'],'/Stamp')
            with contextlib.redirect_stdout(io.StringIO()):self.assertTrue(verify_candidate(a,b,manifest)['exact'])

    def test_density_uses_nested_forms_and_repeated_calls(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a=Path(folder)/'density.pdf';w,parents,leaf=fixture()
            for _ in range(7):w.add_blank_page(width=60,height=60)
            page=w.pages[3];page[NameObject('/Resources')]=w.pages[0]['/Resources']
            content=DecodedStreamObject();content.set_data(b'q /Layer Do Q\n'*100)
            page[NameObject('/Contents')]=w._add_object(content);w.write(a)
            self.assertIn(4,benchmark_pages(a))

    def test_flatten_consolidate_exact_appearance_and_original_body(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            a,b,c=[Path(folder)/n for n in ['a.pdf','b.pdf','c.pdf']]
            w,page,ann=annotation_fixture();w.write(a)
            x=PdfWriter(clone_from=PdfReader(a));manifest=flatten_ink(x);x.write(b)
            manifest=json.loads(json.dumps(manifest))
            self.assertEqual(manifest['count'],1)
            with contextlib.redirect_stdout(io.StringIO()):self.assertTrue(verify_candidate(a,b,manifest)['exact'])
            consolidate_ink(x,manifest);x.write(c)
            verify_consolidated(PdfReader(c),manifest)
            with contextlib.redirect_stdout(io.StringIO()):self.assertTrue(verify_candidate(a,c,manifest)['exact'])

    def test_order_barrier_keeps_links_comments_and_later_ink(self):
        w,page,ann=annotation_fixture()
        comment=DictionaryObject({NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Text'),NameObject('/Rect'):ann['/Rect'],NameObject('/Contents'):TextStringObject('Keep comment')})
        page['/Annots'].extend([w._add_object(comment),w._add_object(DictionaryObject(ann))])
        result=flatten_ink(w)
        self.assertEqual(result['count'],1)
        self.assertEqual(len(page['/Annots']),2)
        self.assertEqual(page['/Annots'][0].get_object()['/Contents'],'Keep comment')

    def test_flags_state_opacity_and_actions_are_skipped(self):
        for change in ['flags','action','struct-zero','alpha','states','matrix','group','page-group','layer-expression','resource-layer-expression','referenced']:
            w,page,ann=annotation_fixture()
            if change=='flags':ann[NameObject('/F')]=NumberObject(20)
            if change=='action':ann[NameObject('/A')]=DictionaryObject({NameObject('/S'):NameObject('/URI'),NameObject('/URI'):TextStringObject('https://example.com')})
            if change=='struct-zero':ann[NameObject('/StructParent')]=NumberObject(0)
            if change=='alpha':ann[NameObject('/CA')]=NumberObject(0)
            if change=='states':ann['/AP'][NameObject('/R')]=ann['/AP'].raw_get('/N')
            if change=='matrix':ann['/AP']['/N'][NameObject('/Matrix')]=ArrayObject([NumberObject(x) for x in [0,0,0,0,0,0]])
            if change=='group':ann['/AP']['/N'][NameObject('/Group')]=DictionaryObject({NameObject('/S'):NameObject('/Transparency')})
            if change=='page-group':page[NameObject('/Group')]=DictionaryObject({NameObject('/S'):NameObject('/Transparency')})
            if change=='layer-expression':ann[NameObject('/OC')]=DictionaryObject({NameObject('/Type'):NameObject('/OCMD')})
            if change=='resource-layer-expression':ann['/AP']['/N']['/Resources'][NameObject('/Properties')]=DictionaryObject({NameObject('/L'):DictionaryObject({NameObject('/Type'):NameObject('/OCMD')})})
            if change=='referenced':w.root_object[NameObject('/PrivateInkReference')]=ann.indirect_reference
            self.assertEqual(flatten_ink(w)['count'],0,change)

    def test_shared_appearance_not_edited_and_layer_retained(self):
        w,page,ann=annotation_fixture()
        layer=w._add_object(DictionaryObject({NameObject('/Type'):NameObject('/OCG'),NameObject('/Name'):TextStringObject('Layer 1')}))
        ann[NameObject('/OC')]=layer
        appearance=ann['/AP']['/N'];before=appearance.get_data()
        result=flatten_ink(w)
        self.assertEqual(appearance.get_data(),before)
        self.assertNotIn('/OC',appearance)
        target=page['/Resources']['/XObject'][result['evidence'][0]['target']]
        self.assertEqual(target.raw_get('/OC'),layer)
        consolidate_ink(w,result)
        container=page['/Resources']['/XObject']['/ViewingInkLayer']
        self.assertIn(b'/OC /InkLayer0 BDC',container.get_data())

    def test_url_file_action_and_link_hit_area_changes_detected(self):
        def read(w):
            stream=io.BytesIO();w.write(stream);stream.seek(0);return PdfReader(stream)
        for kind in ['URI','GoToR']:
            w=PdfWriter();w.add_blank_page(width=100,height=100)
            node=w.add_outline_item('Go',0).get_object()
            node[NameObject('/A')]=DictionaryObject({NameObject('/S'):NameObject('/'+kind),NameObject('/URI' if kind=='URI' else '/F'):TextStringObject('original')})
            a=read(w);node['/A'][NameObject('/URI' if kind=='URI' else '/F')]=TextStringObject('changed');b=read(w)
            self.assertNotEqual(navigation_fingerprint(a),navigation_fingerprint(b))
        w=PdfWriter();p=w.add_blank_page(width=100,height=100)
        link=DictionaryObject({NameObject('/Subtype'):NameObject('/Link'),NameObject('/Rect'):ArrayObject([NumberObject(n) for n in [0,0,10,10]])})
        p[NameObject('/Annots')]=ArrayObject([w._add_object(link)])
        a=read(w);link['/Rect'][2]=NumberObject(20);b=read(w)
        self.assertNotEqual(navigation_fingerprint(a),navigation_fingerprint(b))

    def test_navigation_metadata_and_attachment_mutations_rejected(self):
        def read(w):
            stream=io.BytesIO();w.write(stream);stream.seek(0);return PdfReader(stream)
        for change in ['attachment','page-label','opening-action','private','bookmark-target','bookmark-style','named-target']:
            w=PdfWriter();w.add_blank_page(width=100,height=100);w.add_blank_page(width=100,height=100)
            bookmark=w.add_outline_item('Contents',0).get_object()
            w.add_attachment('contents.txt',b'original')
            w.add_named_destination('target',0)
            a=read(w)
            if change=='attachment':w.add_attachment('extra.txt',b'data')
            elif change=='page-label':w.set_page_label(0,1,prefix='Lecture ')
            elif change=='opening-action':w.root_object[NameObject('/OpenAction')]=ArrayObject([w.pages[1].indirect_reference,NameObject('/Fit')])
            elif change=='private':w.root_object[NameObject('/BooxPrivate')]=TextStringObject('lost navigation data')
            elif change=='bookmark-target':bookmark['/A']['/D'][0]=w.pages[1].indirect_reference
            elif change=='bookmark-style':bookmark[NameObject('/F')]=NumberObject(2)
            else:w.root_object['/Names']['/Dests']['/Names'][1]['/D'][0]=w.pages[1].indirect_reference
            self.assertNotEqual(navigation_fingerprint(a),navigation_fingerprint(read(w)),change)

    def test_edge_guard_rejects_missing_ink_and_off_edge_changes(self):
        a=Image.new('RGB',(100,100),'white');b=a.copy();b.putpixel((50,50),(200,200,200))
        with self.assertRaisesRegex(ValueError,'edge band'):visual_delta(a,b,True)
        a.putpixel((50,50),(0,0,0));b=Image.new('RGB',(100,100),'white')
        with self.assertRaisesRegex(ValueError,'guard'):visual_delta(a,b,True)

    def test_selects_speed_before_size_but_rejects_regressions(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            base,fast,small=[Path(folder)/n for n in ['base.pdf','fast.pdf','small.pdf']]
            base.write_bytes(b'0'*100);fast.write_bytes(b'0'*120);small.write_bytes(b'0'*80)
            records=[]
            for path,cpu in [(base,1),(fast,.7),(small,.95)]:
                for run in range(3):records.append({'file':str(path.absolute()),'page':1,'mode':'render','ready':{'wall':cpu,'cpu':cpu},'cold':{'wall':cpu,'cpu':cpu},'cached':{'wall':.1}})
            candidates=[{'path':str(fast),'exact':False},{'path':str(small),'exact':True}]
            self.assertEqual(choose_candidate(base,candidates,records)['path'],str(fast))
            for r in records:
                if r['file']==str(fast.absolute()):r['cached']['wall']=1
            self.assertEqual(choose_candidate(base,candidates,records)['path'],str(small))


if __name__=='__main__':unittest.main()
