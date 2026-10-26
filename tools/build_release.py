"""Package the maintained program and Codex skill; verify every shipped byte."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PROGRAM=['optimize_pdf.py','handwriting.py','invisible_text.py','navigation.py','geometry.py',
         'annotation_ink.py','viewing_copy.py','batch_workflow.py','viewing_copy_gui.py','browser_check.py',
         'pdfjs_benchmark.mjs','browser_harness.html','verification_worker.py','tools/evidence_cache.py']


def build(destination):
    destination=Path(destination).absolute()
    if destination.exists():raise FileExistsError('Release folder already exists; choose a new --output folder.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    files=PROGRAM+['README.md','README.txt','requirements.txt','package.json','package-lock.json']
    files += [str(p.relative_to(ROOT)).replace('\\','/') for p in sorted(ROOT.glob('*.cmd'))]
    files += [str(p.relative_to(ROOT)).replace('\\','/') for p in sorted((ROOT/'docs').glob('*')) if p.is_file()]
    if len(files)!=len(set(files)):raise ValueError('Duplicate release member.')
    with tempfile.TemporaryDirectory(prefix='pdf-release-',dir=destination.parent) as temporary:
        staged=Path(temporary)/'release';staged.mkdir()
        archive_path=staged/'PDF Optimizer.zip'
        with zipfile.ZipFile(archive_path,'x',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for name in files:archive.write(ROOT/name,name)
        skill=staged/'optimize-pdfs'
        shutil.copytree(ROOT/'skill',skill)
        (skill/'scripts').mkdir()
        for name in PROGRAM:
            target=skill/'scripts'/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,target)
        with zipfile.ZipFile(archive_path) as archive:
            if set(archive.namelist())!=set(files):raise ValueError('Release file list mismatch.')
            for name in files:
                if archive.read(name)!=(ROOT/name).read_bytes():raise ValueError(f'ZIP mismatch: {name}')
        for name in PROGRAM:
            if (skill/'scripts'/name).read_bytes()!=(ROOT/name).read_bytes():raise ValueError(f'Skill mismatch: {name}')
        (staged/'file-hashes.json').write_text(json.dumps({name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                                       for name in files},indent=2)+'\n',encoding='utf-8')
        if destination.exists():raise FileExistsError('Release folder appeared during packaging.')
        staged.rename(destination)
    print(f'Verified {len(files)} ZIP members and {len(PROGRAM)} skill program files: {destination}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    build(parser.parse_args().output)
