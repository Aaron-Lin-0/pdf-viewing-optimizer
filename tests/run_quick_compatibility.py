"""Independent renderer/browser qualification outside the quick execution path."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(Path(__file__).resolve().parent)]
from quick_lossless import optimize_quick
from test_pdf_compatibility import compatibility_fixture,interactive_fixture
from viewing_copy import filehash,verify_candidate,verify_pdfjs,page_delta,detail_crop
from browser_check import check_browser


def run(stage,folder):
    folder=Path(folder).absolute()
    if stage=='fixtures':
        folder.mkdir(parents=True,exist_ok=False)
        compatibility_fixture(folder/'matrix.pdf')
        interactive_fixture(folder/'interactive.pdf')
        for label in ('matrix','interactive'):
            optimize_quick(folder/(label+'.pdf'),folder/(label+'.quick.pdf'))
        return
    for label in ('matrix','interactive'):
        source=folder/(label+'.pdf');candidate=folder/(label+'.quick.pdf')
        report=json.loads(candidate.with_suffix('.json').read_text(encoding='utf-8'))
        if (report['status']!='optimized' or filehash(source)!=report['source_sha256'] or
            filehash(candidate)!=report['output_sha256']):raise ValueError('Incomplete or changed qualification files.')
        pages=list(range(1,report['pages']+1))
        if stage=='render':
            result=verify_candidate(source,candidate,{'converted':{},'evidence':[]})
            result['pdfjs']=verify_pdfjs(source,candidate,report['pages'])
            for page in pages:
                page_delta(source,candidate,page-1,scale=4)
                page_delta(source,candidate,page-1,print_mode=True)
            page,crop=detail_crop(source,pages)
            result['detail']=page_delta(source,candidate,page-1,scale=8,crop=crop)
        else:
            result=check_browser(source,candidate,pages)
            if label=='interactive':
                values=result['results']['candidate']
                assert sum(v.get('editTest',False) for v in values['interactive'])==5
                assert any(v.get('readOnlyTest') for v in values['interactive'])
                assert any(v.get('popupTest') for v in values['interactive'])
                assert values['layers'][2]['onHash']!=values['layers'][2]['offHash']
        result.update(source_sha256=filehash(source),output_sha256=filehash(candidate))
        with (folder/(label+'.'+stage+'.json')).open('x',encoding='utf-8') as stream:
            json.dump(result,stream,indent=2)
        print(label,stage,'passed',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('fixtures','render','browser'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.stage,args.output)
