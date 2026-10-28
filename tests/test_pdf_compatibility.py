"""Broad local fixtures: image codecs, vector resources, forms and publication safety."""
import contextlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from PIL import Image, ImageCms, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, ByteStringObject, DecodedStreamObject,
    DictionaryObject, EncodedStreamObject, FloatObject, NameObject, NumberObject, TextStringObject)
from geometry import page_signature
from navigation import navigation_fingerprint
from optimize_pdf import optimize
from viewing_copy import (benchmark_pages, build_candidate, filehash,
    optimize_viewing, runtime_node, verify_candidate, verify_pdfjs_page)


def nums(values):
    return ArrayObject([FloatObject(n) for n in values])


def image_object(image, codec='flate'):
    obj = EncodedStreamObject()
    colors = {'1':'/DeviceGray', 'L':'/DeviceGray', 'RGB':'/DeviceRGB', 'CMYK':'/DeviceCMYK'}
    obj.update({NameObject('/Type'):NameObject('/XObject'), NameObject('/Subtype'):NameObject('/Image'),
        NameObject('/Width'):NumberObject(image.width), NameObject('/Height'):NumberObject(image.height),
        NameObject('/BitsPerComponent'):NumberObject(1 if image.mode=='1' else 8),
        NameObject('/ColorSpace'):NameObject(colors.get(image.mode, '/DeviceRGB'))})
    if codec in {'jpeg', 'jpx'}:
        buffer=io.BytesIO();image.save(buffer,format='JPEG' if codec=='jpeg' else 'JPEG2000')
        obj._data=buffer.getvalue()
        obj[NameObject('/Filter')]=NameObject('/DCTDecode' if codec=='jpeg' else '/JPXDecode')
    elif codec=='ccitt':
        buffer=io.BytesIO();image.save(buffer,format='TIFF',compression='group4')
        raw=buffer.getvalue()
        with Image.open(io.BytesIO(raw)) as tif:
            offsets,counts=tif.tag_v2[273],tif.tag_v2[279]
            if len(offsets)!=1:raise AssertionError('Fixture requires one TIFF strip.')
            obj._data=raw[offsets[0]:offsets[0]+counts[0]]
        obj[NameObject('/Filter')]=NameObject('/CCITTFaxDecode')
        obj[NameObject('/DecodeParms')]=DictionaryObject({NameObject('/K'):NumberObject(-1),
            NameObject('/Columns'):NumberObject(image.width),NameObject('/Rows'):NumberObject(image.height),
            NameObject('/BlackIs1'):BooleanObject(False)})
    else:
        obj._data=zlib.compress(image.tobytes());obj[NameObject('/Filter')]=NameObject('/FlateDecode')
    return obj


def compatibility_fixture(path):
    w=PdfWriter();labels=[]
    image=Image.new('RGB',(96,96),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((5,5,40,90),fill='#b32020');draw.ellipse((35,8,90,65),fill='#116aa0')
    draw.text((10,70),'e c 8 .',fill='black')
    font=w._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    def page(label, body, resources=None):
        p=w.add_blank_page(width=180,height=180)
        stream=DecodedStreamObject();stream.set_data(body)
        p[NameObject('/Contents')]=w._add_object(stream)
        p[NameObject('/Resources')]=resources or DictionaryObject()
        w.add_outline_item(label,len(w.pages)-1);labels.append(label)
        return p
    for label,mode,codec in [
        ('JPEG RGB','RGB','jpeg'),('JPEG gray','L','jpeg'),('JPEG CMYK','CMYK','jpeg'),
        ('JPEG2000 RGB','RGB','jpx'),('Flate RGB','RGB','flate'),('Flate gray','L','flate'),
        ('Flate one bit','1','flate'),('CCITT Group 4','1','ccitt')]:
        obj=image_object(image.convert(mode),codec)
        page(label,b'q 160 0 0 160 10 10 cm /Im Do Q',
             DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/Im'):w._add_object(obj)})}))
    for label in ['Indexed palette','ICC RGB','Soft alpha mask','Color key mask','Stencil mask','Predictor RGB','Mixed raster vector OCR']:
        obj=image_object(image)
        if label=='Indexed palette':
            pal=image.quantize(colors=8);obj=image_object(pal)
            obj[NameObject('/ColorSpace')]=ArrayObject([NameObject('/Indexed'),NameObject('/DeviceRGB'),
                NumberObject(7),ByteStringObject(bytes(pal.getpalette()[:24]))])
        if label=='ICC RGB':
            icc=DecodedStreamObject();icc.set_data(ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
            icc[NameObject('/N')]=NumberObject(3);icc[NameObject('/Alternate')]=NameObject('/DeviceRGB')
            obj[NameObject('/ColorSpace')]=ArrayObject([NameObject('/ICCBased'),w._add_object(icc)])
        if label=='Soft alpha mask':
            mask=Image.new('L',image.size)
            mask.putdata([x*255//95 for y in range(96) for x in range(96)])
            obj[NameObject('/SMask')]=w._add_object(image_object(mask))
        if label=='Color key mask':obj[NameObject('/Mask')]=nums([255,255,255,255,255,255])
        if label=='Stencil mask':
            obj=image_object(image.convert('1'));obj.pop('/ColorSpace')
            obj[NameObject('/ImageMask')]=BooleanObject(True)
        if label=='Predictor RGB':
            data=image.tobytes()
            obj._data=zlib.compress(b''.join(b'\x00'+data[y*288:(y+1)*288] for y in range(96)))
            obj[NameObject('/DecodeParms')]=DictionaryObject({NameObject('/Predictor'):NumberObject(15),
                NameObject('/Colors'):NumberObject(3),NameObject('/Columns'):NumberObject(96),
                NameObject('/BitsPerComponent'):NumberObject(8)})
        body=b'q .2 .7 .2 rg 160 0 0 160 10 10 cm /Im Do Q\n'
        res=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/Im'):w._add_object(obj)})})
        if label=='Mixed raster vector OCR':
            res[NameObject('/Font')]=DictionaryObject({NameObject('/F1'):font})
            body+=b'0 0 1 RG 0.4 w 30 30 m 50 70 80 10 110 70 c S\nBT /F1 10 Tf 3 Tr 20 20 Td (searchable e and c) Tj ET\n'
        page(label,body,res)
    page('Inline bitmap',b'q 120 0 0 120 30 30 cm BI /W 2 /H 2 /BPC 8 /CS /RGB ID '+bytes([255,0,0,0,255,0,0,0,255,255,255,255])+b' EI Q')
    vector=b'q 0 0 160 160 re W n 0.2 0.3 0.8 RG 0.2 w 1 J 1 j 10 10 m 40 80 90 -20 130 100 c S 80 80 m 80 80 l S 40 40 m 41 43 44 43 45 40 c 44 37 41 37 40 40 c h S Q\n'
    p=page('Rotated crop and user unit',vector);p[NameObject('/CropBox')]=nums([10,10,170,170])
    p[NameObject('/Rotate')]=NumberObject(90);p[NameObject('/UserUnit')]=NumberObject(2)
    state=DictionaryObject({NameObject('/ca'):FloatObject(.3),NameObject('/CA'):FloatObject(.4),NameObject('/BM'):NameObject('/Multiply')})
    page('Transparent overlapping curves',b'q /G gs '+vector+b' .8 0 0 rg 30 30 60 60 re f Q',
        DictionaryObject({NameObject('/ExtGState'):DictionaryObject({NameObject('/G'):w._add_object(state)})}))
    function=DictionaryObject({NameObject('/FunctionType'):NumberObject(2),NameObject('/Domain'):nums([0,1]),
        NameObject('/C0'):nums([1,0,0]),NameObject('/C1'):nums([0,0,1]),NameObject('/N'):NumberObject(1)})
    shading=DictionaryObject({NameObject('/ShadingType'):NumberObject(2),NameObject('/ColorSpace'):NameObject('/DeviceRGB'),
        NameObject('/Coords'):nums([0,0,180,180]),NameObject('/Function'):w._add_object(function),
        NameObject('/Extend'):ArrayObject([BooleanObject(True)]*2)})
    page('Axial shading',b'/Sh sh',DictionaryObject({NameObject('/Shading'):DictionaryObject({NameObject('/Sh'):w._add_object(shading)})}))
    page('Standard font visible text',b'BT /F1 15 Tf 15 90 Td (Visible e c 8: notes) Tj ET',
        DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})}))
    page('Blank page',b'')
    pattern=DecodedStreamObject();pattern.set_data(b'1 0 0 rg 0 0 5 5 re f')
    pattern.update({NameObject('/Type'):NameObject('/Pattern'),NameObject('/PatternType'):NumberObject(1),
        NameObject('/PaintType'):NumberObject(1),NameObject('/TilingType'):NumberObject(1),
        NameObject('/BBox'):nums([0,0,10,10]),NameObject('/XStep'):NumberObject(10),NameObject('/YStep'):NumberObject(10),
        NameObject('/Resources'):DictionaryObject()})
    page('Tiling pattern',b'/Pattern cs /P scn 0 0 180 180 re f',
        DictionaryObject({NameObject('/Pattern'):DictionaryObject({NameObject('/P'):w._add_object(pattern)})}))
    form=DecodedStreamObject();form.set_data(vector)
    form.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Form'),
        NameObject('/BBox'):nums([0,0,160,160]),NameObject('/Matrix'):nums([.7,.1,.2,.8,5,5]),
        NameObject('/Resources'):DictionaryObject()})
    page('Transformed clipped Form',b'q 20 20 130 130 re W n /F Do Q',
        DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/F'):w._add_object(form)})}))
    glyph=DecodedStreamObject();glyph.set_data(b'600 0 0 0 600 700 d1 0 0 600 700 re f')
    type3=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type3'),
        NameObject('/FontBBox'):nums([0,0,600,700]),NameObject('/FontMatrix'):nums([.001,0,0,.001,0,0]),
        NameObject('/FirstChar'):NumberObject(65),NameObject('/LastChar'):NumberObject(65),NameObject('/Widths'):nums([600]),
        NameObject('/CharProcs'):DictionaryObject({NameObject('/A'):w._add_object(glyph)}),
        NameObject('/Encoding'):DictionaryObject({NameObject('/Type'):NameObject('/Encoding'),NameObject('/Differences'):ArrayObject([NumberObject(65),NameObject('/A')])}),
        NameObject('/Resources'):DictionaryObject()})
    page('Type3 vector glyph',b'BT /T3 60 Tf 30 70 Td (A) Tj ET',
        DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/T3'):w._add_object(type3)})}))
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    import reportlab
    pdfmetrics.registerFont(TTFont('AuditVera',str(Path(reportlab.__file__).parent/'fonts/Vera.ttf')))
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    for label,face,text in [('Embedded TrueType','AuditVera','Vector text: e c é 8'),
                            ('CID font and CMap','STSong-Light','笔记 test')]:
        buffer=io.BytesIO();c=canvas.Canvas(buffer,pagesize=(180,180));c.setFont(face,16);c.drawString(10,80,text);c.save()
        w.add_page(PdfReader(buffer).pages[0]);w.add_outline_item(label,len(w.pages)-1);labels.append(label)
    p=page('Shared image indirect resources',b'q 160 0 0 160 10 10 cm /Im Do Q')
    shared=DictionaryObject(w.pages[0]['/Resources']['/XObject'])
    p[NameObject('/Resources')]=w._add_object(DictionaryObject({NameObject('/XObject'):w._add_object(shared)}))
    w.add_attachment('fixture.txt',b'attachment stays')
    w.add_named_destination('Quick link',2);w.set_page_label(0,len(w.pages)-1,prefix='Case ')
    w.write(path);return labels


def interactive_fixture(path):
    from reportlab.pdfgen import canvas
    buffer=io.BytesIO();c=canvas.Canvas(buffer,pagesize=(400,400))
    c.drawString(20,370,'Bookmarks, forms and later-page layers');c.showPage()
    form=c.acroForm
    form.textfield(name='editable',x=20,y=330,width=100,height=20,value='original',maxlen=2)
    form.textfield(name='readonly',x=20,y=295,width=100,height=20,value='keep',fieldFlags='readOnly')
    form.textfield(name='hidden',x=20,y=260,width=100,height=20,value='hidden',annotationFlags='hidden')
    form.checkbox(name='check',x=20,y=225,checked=False)
    form.radio(name='radio',value='A',selected=True,x=20,y=190)
    form.radio(name='radio',value='B',selected=False,x=60,y=190)
    form.choice(name='choice',options=['alpha','beta'],value='alpha',x=20,y=140,width=100,height=20)
    c.showPage();c.drawString(20,370,'Layer toggles must include page three');c.save()
    w=PdfWriter(clone_from=PdfReader(buffer));w.add_outline_item('Forms',1);w.add_outline_item('Layer page',2)
    w.add_named_destination('forms',1);w.add_attachment('fixture.txt',b'attachment stays')
    layer=w._add_object(DictionaryObject({NameObject('/Type'):NameObject('/OCG'),NameObject('/Name'):TextStringObject('Page 3 ink')}))
    w.root_object[NameObject('/OCProperties')]=DictionaryObject({NameObject('/OCGs'):ArrayObject([layer]),
        NameObject('/D'):DictionaryObject({NameObject('/Order'):ArrayObject([layer]),NameObject('/ON'):ArrayObject([layer])})})
    p=w.pages[2];res=p['/Resources'];res[NameObject('/Properties')]=DictionaryObject({NameObject('/Later'):layer})
    data=p.get_contents().get_data()+b'\n/OC /Later BDC 1 0 0 rg 50 100 100 100 re f EMC'
    stream=DecodedStreamObject();stream.set_data(data);p[NameObject('/Contents')]=w._add_object(stream)
    w.pages[0][NameObject('/Annots')]=ArrayObject([w._add_object(DictionaryObject({
        NameObject('/Subtype'):NameObject('/Link'),NameObject('/Rect'):nums([20,320,150,350]),
        NameObject('/Border'):nums([0,0,0]),NameObject('/Dest'):ArrayObject([w.pages[1].indirect_reference,NameObject('/XYZ'),NumberObject(0),NumberObject(400),FloatObject(1.5)])}))])
    w.pages[0]['/Annots'].append(w._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Annot'),
        NameObject('/Subtype'):NameObject('/Text'),NameObject('/Rect'):nums([250,320,275,345]),
        NameObject('/Contents'):TextStringObject('Keep this comment'),NameObject('/F'):NumberObject(4)})))
    w.write(path)


class CompatibilityTests(unittest.TestCase):
    def test_viewing_batching_keeps_paint_operations_when_transparency_is_present(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
            w=PdfWriter();p=w.add_blank_page(width=60,height=60)
            p[NameObject('/Resources')]=DictionaryObject({NameObject('/ExtGState'):DictionaryObject({
                NameObject('/G'):DictionaryObject({NameObject('/CA'):FloatObject(.5)})})})
            w.write(source)
            with patch('handwriting.rewrite_handwriting') as rewrite,patch('handwriting.inline_handwriting'):
                with self.assertRaisesRegex(ValueError,'transparency'):build_candidate(source,out,'batched')
                rewrite.assert_not_called()
            self.assertFalse(out.exists())

    def test_page_metadata_and_retained_appearance_references_protect_ink(self):
        from test_viewing_copy import annotation_fixture
        from annotation_ink import flatten_ink
        for where in ['page','retained','orphan-parent']:
            w,p,ann=annotation_fixture()
            if where=='page':p[NameObject('/PrivateInkReference')]=ann.indirect_reference
            else:
                extra=DictionaryObject(ann)
                if where=='retained':
                    extra[NameObject('/F')]=NumberObject(20)
                    extra[NameObject('/PrivateInkReference')]=ann.indirect_reference
                else:
                    extra[NameObject('/Subtype')]=NameObject('/Widget')
                    extra[NameObject('/Parent')]=w._add_object(DictionaryObject({NameObject('/FT'):NameObject('/Tx'),
                        NameObject('/PrivateInkReference'):ann.indirect_reference}))
                p['/Annots'].append(w._add_object(extra))
            with self.subTest(where=where):self.assertEqual(flatten_ink(w)['count'],0)

    def test_build_releases_first_writer_before_rebuilding(self):
        import weakref
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
            w=PdfWriter();w.add_blank_page(width=60,height=60);w.write(source)
            writers=[]
            def tracked(*args,**kwargs):
                self.assertTrue(all(ref() is None for ref in writers),'Prior writer retained during rebuild')
                writer=PdfWriter(*args,**kwargs);writers.append(weakref.ref(writer));return writer
            with patch('viewing_copy.PdfWriter',side_effect=tracked):build_candidate(source,out,'dedup')
            self.assertEqual(len(writers),2)

    def test_source_change_during_preflight_is_detected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
            w=PdfWriter();w.add_blank_page(width=60,height=60);w.write(source)
            def changed(*args,**kwargs):
                w.add_metadata({'/Subject':'changed during preflight'});w.write(source)
                return {}
            with patch('viewing_copy.benchmark_worker',side_effect=changed),\
                 patch('viewing_copy.build_candidate',side_effect=ValueError('unsupported')),\
                 patch('pymupdf.open',side_effect=ValueError('unsupported')):
                with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'Original changed'):
                    optimize_viewing(source,out)
            self.assertEqual(list(Path(folder).iterdir()),[source])

    def test_indirect_page_boxes_and_appearance_matrices(self):
        from test_viewing_copy import annotation_fixture
        from annotation_ink import flatten_ink,consolidate_ink
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'a.pdf',Path(folder)/'b.pdf'
            w,p,ann=annotation_fixture();form=ann['/AP']['/N']
            p[NameObject('/MediaBox')]=w._add_object(nums([0,0,100,100]))
            ann[NameObject('/Rect')]=w._add_object(nums([0,0,60,60]))
            form[NameObject('/BBox')]=w._add_object(nums([0,0,60,60]))
            form[NameObject('/Matrix')]=w._add_object(nums([1,0,0,1,0,0]))
            w.write(source);manifest=flatten_ink(w);consolidate_ink(w,manifest);w.write(out)
            with contextlib.redirect_stdout(io.StringIO()):self.assertTrue(verify_candidate(source,out,manifest)['exact'])

    def test_consolidated_container_transform_and_clip_are_protected(self):
        from test_viewing_copy import annotation_fixture
        from annotation_ink import flatten_ink,consolidate_ink,verify_consolidated
        for key,value in [('/BBox',[0,0,10,10]),('/Matrix',[2,0,0,2,0,0])]:
            w,p,ann=annotation_fixture();manifest=flatten_ink(w);consolidate_ink(w,manifest)
            p['/Resources']['/XObject']['/ViewingInkLayer'][NameObject(key)]=nums(value)
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'[Cc]ontainer'):
                verify_consolidated(w,manifest)

    def test_unfamiliar_appearance_metadata_keeps_original_form(self):
        from test_viewing_copy import annotation_fixture
        from annotation_ink import flatten_ink,consolidate_ink
        w,p,ann=annotation_fixture()
        ann['/AP']['/N'][NameObject('/PieceInfo')]=DictionaryObject({NameObject('/Private'):TextStringObject('keep')})
        manifest=flatten_ink(w);consolidate_ink(w,manifest)
        self.assertEqual(len(manifest['consolidated'][0]['ordinary_calls']),1)

    def test_free_xref_entry_is_not_a_resolved_optional_layer(self):
        from navigation import Graph
        from pypdf.generic import IndirectObject
        w=PdfWriter();w.add_blank_page(width=20,height=20);buffer=io.BytesIO();w.write(buffer)
        reader=PdfReader(buffer);reader.xref[0][999]=0;reader.xref_free_entry.setdefault(0,{})[999]=True
        with self.assertRaisesRegex(ValueError,'Unresolved'):
            Graph(reader).value(IndirectObject(999,0,reader),optional=True)

    def test_geometry_guard_preserves_child_matrix_layer_and_container_resources(self):
        from test_inline_handwriting import fixture, IDENTITY
        for change in ['matrix','layer','font']:
            w,parents,leaf=fixture();parents[0].set_data(IDENTITY);before=page_signature(w,0)
            if change=='matrix':leaf[NameObject('/Matrix')]=nums([2,0,0,2,0,0])
            elif change=='layer':leaf[NameObject('/OC')]=w._add_object(DictionaryObject({
                NameObject('/Type'):NameObject('/OCG'),NameObject('/Name'):TextStringObject('Ink')}))
            else:parents[0]['/Resources'][NameObject('/Font')]=DictionaryObject({
                NameObject('/F'):DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/BaseFont'):NameObject('/Helvetica')})})
            with self.subTest(change=change):self.assertNotEqual(before,page_signature(w,0))

    def test_known_bad_layer_id_is_not_accepted_outside_verified_boox_ink(self):
        from pypdf.generic import IndirectObject
        w=PdfWriter();p=w.add_blank_page(width=20,height=20)
        form=DecodedStreamObject();form.set_data(b'0 0 10 10 re f')
        form[NameObject('/Subtype')]=NameObject('/Form')
        form[NameObject('/OC')]=IndirectObject(1738407840,0,w)
        p[NameObject('/Resources')]=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/F'):w._add_object(form)})})
        with self.assertRaisesRegex(ValueError,'Unresolved'):page_signature(w,0)

    def test_geometry_guard_does_not_normalize_unproven_duplicate_draws(self):
        from test_handwriting import make_boox
        from handwriting import DUPLICATE
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'alpha.pdf';make_boox(source,alpha=0)
            w=PdfWriter(clone_from=PdfReader(source));p=w.pages[0]
            before=page_signature(w,0,normalize_duplicate=True)
            s=DecodedStreamObject();s.set_data(DUPLICATE.sub(lambda m:m[1],p.get_contents().get_data()))
            p[NameObject('/Contents')]=w._add_object(s)
            self.assertNotEqual(before,page_signature(w,0,normalize_duplicate=True))

    def test_interrupted_pdf_publication_removes_partial_copy(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'source.pdf',Path(folder)/'out.pdf'
            w=PdfWriter();w.add_blank_page(width=60,height=60);w.write(source);original=source.read_bytes()
            def choose(source,candidates,results):
                if not candidates:return None
                candidates[0]['cpu_reduction']=.2
                return candidates[0]
            def interrupted(inp,output):
                output.write(b'partial');raise KeyboardInterrupt()
            with patch('viewing_copy.benchmark_worker',return_value={}),\
                 patch('viewing_copy.benchmarks',return_value=[]),\
                 patch('viewing_copy.choose_candidate',side_effect=choose),\
                 patch('viewing_copy.verify_pdfjs',return_value=[]),\
                 patch('browser_check.check_browser',return_value={}),\
                 patch('viewing_copy.shutil.copyfileobj',side_effect=interrupted):
                with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(KeyboardInterrupt):
                    optimize_viewing(source,out)
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()),['out.checkpoint','source.pdf'])
            self.assertEqual(source.read_bytes(),original)

    def test_rejected_candidate_does_not_leave_success_checks_in_final_report(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'source.pdf',Path(folder)/'out.pdf'
            w=PdfWriter();w.add_blank_page(width=60,height=60);w.write(source)
            def choose(source,candidates,results):
                if not candidates:return None
                candidates[0]['cpu_reduction']=.2
                return candidates[0]
            with patch('viewing_copy.benchmark_worker',return_value={}),\
                 patch('viewing_copy.benchmarks',return_value=[]),\
                 patch('viewing_copy.choose_candidate',side_effect=choose),\
                 patch('viewing_copy.verify_pdfjs',return_value=[{'exact':True}]),\
                 patch('browser_check.check_browser',side_effect=ValueError('browser failed')):
                with contextlib.redirect_stdout(io.StringIO()):optimize_viewing(source,out)
            report=json.loads(out.with_suffix('.json').read_text())
            self.assertEqual(report['status'],'no improvement')
            self.assertNotIn('pdfjs_all_pages',report);self.assertNotIn('high_dpi_and_print_pages',report)
            self.assertFalse(out.exists())

    def test_pdfjs_decodes_jpeg2000_instead_of_comparing_blank_images(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,png=Path(folder)/'jpx.pdf',Path(folder)/'render.png'
            w=PdfWriter();p=w.add_blank_page(width=60,height=60)
            obj=image_object(Image.new('RGB',(20,20),(180,20,20)),'jpx')
            p[NameObject('/Resources')]=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/I'):w._add_object(obj)})})
            s=DecodedStreamObject();s.set_data(b'q 60 0 0 60 0 0 cm /I Do Q');p[NameObject('/Contents')]=w._add_object(s);w.write(source)
            child=subprocess.run([runtime_node(),str(Path(__file__).resolve().parents[1]/'pdfjs_benchmark.mjs'),str(source),'1','render','120',str(png)],capture_output=True,text=True)
            self.assertEqual(child.returncode,0,child.stderr)
            with Image.open(png) as image:
                self.assertLess(image.convert('RGB').getpixel((60,60))[1],50,'JPEG2000 was silently omitted.')

    def test_unresolved_graphics_state_cannot_prove_duplicate_opacity(self):
        from handwriting import opaque_drawing
        form=DecodedStreamObject()
        form[NameObject('/Subtype')]=NameObject('/Form')
        form.set_data(b'/Missing gs 0 0 10 10 re f')
        self.assertFalse(opaque_drawing(form,PdfWriter()))

    def test_image_decode_parameters_are_part_of_geometry_guard(self):
        w=PdfWriter();p=w.add_blank_page(width=20,height=20)
        obj=image_object(Image.new('RGB',(2,2),'red'),'jpeg')
        obj[NameObject('/DecodeParms')]=DictionaryObject({NameObject('/ColorTransform'):NumberObject(0)})
        p[NameObject('/Resources')]=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/I'):w._add_object(obj)})})
        before=page_signature(w,0)
        obj['/DecodeParms'][NameObject('/ColorTransform')]=NumberObject(1)
        self.assertNotEqual(before,page_signature(w,0))

    def test_legacy_mode_rejects_orphan_signature(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'sig.pdf',Path(folder)/'out.pdf'
            w=PdfWriter();p=w.add_blank_page(width=100,height=100)
            parent=w._add_object(DictionaryObject({NameObject('/FT'):NameObject('/Sig')}))
            p[NameObject('/Annots')]=ArrayObject([w._add_object(DictionaryObject({
                NameObject('/Subtype'):NameObject('/Widget'),NameObject('/Parent'):parent}))])
            w.write(source)
            with self.assertRaisesRegex(ValueError,'[Ss]ignature'):optimize(source,out)
            self.assertFalse(out.exists())

    def test_densest_bitmap_page_is_sampled(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'scan.pdf';w=PdfWriter()
            sizes=[2,3,4,200,5,100,150,50]
            for i in range(8):
                p=w.add_blank_page(width=100,height=100)
                obj=image_object(Image.new('L',(sizes[i],sizes[i]),0))
                p[NameObject('/Resources')]=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/I'):w._add_object(obj)})})
                s=DecodedStreamObject();s.set_data(b'q 100 0 0 100 0 0 cm /I Do Q');p[NameObject('/Contents')]=w._add_object(s)
            w.write(source);self.assertEqual(benchmark_pages(source),[1,4,6,7])

    def test_empty_document_rejected_clearly(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'empty.pdf',Path(folder)/'out.pdf';PdfWriter().write(source)
            with self.assertRaisesRegex(ValueError,'[Nn]o pages|[Ee]mpty PDF'):optimize_viewing(source,out)
            self.assertFalse(out.exists())

    def test_xfa_forms_retain_original_when_behavior_cannot_be_checked(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'xfa.pdf',Path(folder)/'out.pdf'
            w=PdfWriter();w.add_blank_page(width=60,height=60)
            xml=DecodedStreamObject();xml.set_data(b'<template/>')
            w.root_object[NameObject('/AcroForm')]=DictionaryObject({NameObject('/Fields'):ArrayObject(),
                NameObject('/XFA'):w._add_object(xml)})
            w.write(source);original=source.read_bytes()
            with self.assertRaisesRegex(ValueError,'XFA'):optimize_viewing(source,out)
            self.assertFalse(out.exists());self.assertEqual(source.read_bytes(),original)

    def test_matrix_dedup_preserves_both_renderers_and_navigation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source,out=Path(folder)/'matrix.pdf',Path(folder)/'out.pdf'
            labels=compatibility_fixture(source);original=filehash(source)
            with contextlib.redirect_stdout(io.StringIO()):
                manifest=build_candidate(source,out,'dedup')
                self.assertTrue(verify_candidate(source,out,manifest)['exact'])
            for page,label in enumerate(labels,1):
                with self.subTest(label=label):self.assertTrue(verify_pdfjs_page(source,out,page)['exact'])
            self.assertEqual(filehash(source),original)
            self.assertEqual(navigation_fingerprint(PdfReader(source)),navigation_fingerprint(PdfReader(out)))
            self.assertIn('searchable e and c',PdfReader(out).pages[14].extract_text())


if __name__=='__main__':unittest.main()
