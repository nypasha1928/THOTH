"""Build a source deployment bundle from explicit allowlisted project paths.

Never include .git, .env, virtual environments, databases or private secrets.
The generated manifest records all included file hashes; bundle checksums are
written alongside the archive. Existing releases are never overwritten.
"""
import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES=('README.md','Dockerfile','compose.yaml','compose.testnet.yaml','compose.execution.yaml','compose.live.yaml','compose.live-unverified.yaml','requirements.txt','requirements-dev.txt','.dockerignore','.gitignore','.gitattributes')
DIRECTORIES=('thoth','web','tools','reference','deploy','tests')
EXTENSIONS={'.py','.js','.html','.json','.webmanifest','.pine','.svg','.png','.md','.yaml','.sh','.txt'}


def package(destination,root=ROOT):
    destination=Path(destination);root=Path(root)
    files=[root/p for p in ROOT_FILES if (root/p).is_file()]
    for directory in DIRECTORIES:
        files.extend(p for p in (root/directory).rglob('*') if p.is_file() and p.suffix in EXTENSIONS and '__pycache__' not in p.parts)
    if any(p.is_symlink() or not p.resolve().is_relative_to(root.resolve()) for p in files):raise ValueError('Refusing symlink or escaping release input')
    manifest={'format':1,'files':[]}
    for path in sorted(files):
        content=path.read_bytes();manifest['files'].append({'file':str(path.relative_to(root)),'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content)})
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as stream,tarfile.open(fileobj=stream,mode='w:gz') as archive:
        for item in manifest['files']:
            path=root/item['file'];info=tarfile.TarInfo('THOTH/'+item['file']);info.size=item['bytes'];info.mode=0o755 if path.suffix=='.sh' else 0o644
            archive.addfile(info,io.BytesIO(path.read_bytes()))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('THOTH/release-manifest.json');info.size=len(data);info.mode=0o644;archive.addfile(info,io.BytesIO(data))
    checksum=hashlib.sha256(destination.read_bytes()).hexdigest()
    with destination.with_suffix(destination.suffix+'.sha256').open('x') as output:output.write(checksum+'  '+destination.name+'\n')
    return {'files':len(manifest['files']),'sha256':checksum,'bytes':destination.stat().st_size}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination',type=Path);args=parser.parse_args()
    print(json.dumps(package(args.destination),indent=2))
