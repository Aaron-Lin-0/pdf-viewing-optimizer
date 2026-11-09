"""Reproduce generated-fixture checks without private documents."""
import argparse
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(REPO),str(Path(__file__).resolve().parent)]
from test_pdf_compatibility import compatibility_fixture,interactive_fixture,mixed_transparency_fixture
from viewing_copy import build_candidate,filehash,verify_candidate,verify_pdfjs,page_delta,detail_crop
from browser_check import check_browser,probe_browser
from pypdf import PdfReader


def run(stage,root):
    root=Path(root).absolute()
    root.mkdir(parents=True,exist_ok=True)
    def save(name,data):
        with (root/(name+'.json')).open('x',encoding='utf-8') as stream:
            json.dump(data,stream,indent=2)
    if stage=='fixtures':
        if any(root.iterdir()):raise FileExistsError('Choose an empty output folder for generated fixtures.')
        source,out=root/'matrix.pdf',root/'matrix.dedup.pdf'
        labels=compatibility_fixture(source)
        manifest=build_candidate(source,out,'dedup')
        checks=verify_candidate(source,out,manifest)
        checks.update(pdfjs=verify_pdfjs(source,out,len(labels)),labels=labels,
                      source_sha256=filehash(source),output_sha256=filehash(out))
        save('matrix',checks)
        source,out=root/'interactive.pdf',root/'interactive.dedup.pdf'
        interactive_fixture(source)
        save('interactive-structural',verify_candidate(source,out,build_candidate(source,out,'dedup')))
        source,out=root/'mixed-transparency.pdf',root/'mixed-transparency.batched.pdf'
        mixed_transparency_fixture(source)
        manifest=build_candidate(source,out,'batched')
        checks=verify_candidate(source,out,manifest,allow_edges=True)
        checks['pdfjs']=verify_pdfjs(source,out,4,allow_edges=True)
        for page in range(4):
            page_delta(source,out,page,allow_edges=True,scale=4)
            page_delta(source,out,page,allow_edges=True,print_mode=True)
        page,crop=detail_crop(source,[1,2,3,4])
        checks['detail']=page_delta(source,out,page-1,allow_edges=True,scale=8,crop=crop)
        checks.update(manifest=manifest,source_sha256=filehash(source),output_sha256=filehash(out))
        save('mixed-transparency',checks)
    else:
        result=check_browser(root/'interactive.pdf',root/'interactive.dedup.pdf',[1,2,3])
        values=result['results']['candidate']
        assert any(v.get('readOnlyTest') for v in values['interactive'])
        assert sum(v.get('editTest',False) for v in values['interactive'])==5
        assert any(v.get('hidden') for v in values['interactive'])
        assert any(v.get('popupTest') for v in values['interactive'])
        assert len(values['layers'])==3
        assert values['layers'][2]['onHash']!=values['layers'][2]['offHash']
        save('interactive-browser',result)
        with PdfReader(root/'matrix.pdf') as reader:pages=list(range(1,len(reader.pages)+1))
        result=check_browser(root/'matrix.pdf',root/'matrix.dedup.pdf',pages)
        traces=result['results']['source']['bookmarkTrace']
        assert all(row['target'] in row['viewport']['visible'] and row['viewport']['state']==3 for row in traces)
        assert [row['title'] for row in traces[-3:]]==[
            'Shared image indirect resources','Repeated Fit one','Repeated Fit two']
        assert all(row['viewport']==traces[-1]['viewport'] for row in traces[-3:])
        assert traces[-1]['target']==len(pages), 'Fixture must exercise the final-page bookmark'
        save('matrix-browser',result)
        source,out=root/'mixed-transparency.pdf',root/'mixed-transparency.batched.pdf'
        probe=probe_browser(source,[1,2,3,4])
        save('mixed-transparency-browser',check_browser(source,out,[1,2,3,4],source_result=probe))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['fixtures','browser'])
    parser.add_argument('--output',type=Path,default=REPO/'audit-output')
    args=parser.parse_args()
    run(args.stage,args.output)
