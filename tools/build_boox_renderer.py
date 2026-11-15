"""Build the pinned BOOX fork from public source; never overwrite an existing build."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'vendor' / 'boox-renderer'


def build(work, output):
    work, output = Path(work).absolute(), Path(output).absolute()
    if work.exists() or output.exists():
        raise FileExistsError('Choose new source and output paths; existing files are retained.')
    manifest = json.loads((SOURCE / 'source.json').read_text(encoding='utf-8'))
    patch = SOURCE / 'fork.patch'
    if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest['patch_sha256']:
        raise ValueError('Renderer patch checksum mismatch.')
    subprocess.run(['git', 'clone', '--no-checkout', manifest['upstream'], str(work)], check=True)
    subprocess.run(['git', '-C', str(work), 'checkout', '--detach', manifest['commit']], check=True)
    subprocess.run(['git', '-C', str(work), 'apply', '--check', str(patch)], check=True)
    subprocess.run(['git', '-C', str(work), 'apply', str(patch)], check=True)
    for name, digest in manifest['source_sha256'].items():
        if hashlib.sha256((work / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != digest:
            raise ValueError('Patched source checksum mismatch: ' + name)
    subprocess.run(['cargo', 'build', '--release', '--locked', '--manifest-path', str(work / 'Cargo.toml')], check=True)
    target = Path(os.environ.get('CARGO_TARGET_DIR', work / 'target'))
    binary = target / 'release' / ('boox-notes-renderer.exe' if os.name == 'nt' else 'boox-notes-renderer')
    output.parent.mkdir(parents=True, exist_ok=True)
    with binary.open('rb') as src, output.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    shutil.copymode(binary, output)
    print('Built renderer SHA-256: ' + hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path, help='New directory for the public source checkout.')
    parser.add_argument('--output', required=True, type=Path, help='New path for the executable.')
    args = parser.parse_args()
    build(args.work, args.output)
