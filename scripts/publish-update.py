"""Publish a completed fiscal package to a folder channel, atomically."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
import uuid
import zipfile


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def publish(package, destination):
    package, destination = Path(package).resolve(), Path(destination).resolve()
    with zipfile.ZipFile(package) as archive:
        manifests = [name for name in archive.namelist() if name.endswith('/local-manifest.json') or name == 'local-manifest.json']
        if len(manifests) != 1:
            raise ValueError('O ZIP deve conter um único manifesto fiscal.')
        manifest_name = manifests[0]
        manifest = json.loads(archive.read(manifest_name))
        version = manifest.get('version', '')
        if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', version):
            raise ValueError('Versão inválida no manifesto.')
        prefix = manifest_name.removesuffix('local-manifest.json')
        for item in manifest['files']:
            with archive.open(prefix + item['path']) as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
                if digest.hexdigest() != item['sha256']:
                    raise ValueError('Arquivo divergente do manifesto: ' + item['path'])
        if prefix + 'scripts/install-local.ps1' not in archive.namelist():
            raise ValueError('Este não é um pacote do fiscal local sem Docker.')
    digest = file_hash(package)
    destination.mkdir(parents=True, exist_ok=True)
    filename = 'DocPronto-Fiscal-' + version + '-' + digest[:12] + '.zip'
    target = destination / filename
    temporary = destination / (uuid.uuid4().hex + '.partial')
    try:
        shutil.copyfile(package, temporary)
        if file_hash(temporary) != digest:
            raise ValueError('A cópia do pacote falhou; a versão publicada foi preservada.')
        temporary.replace(target)
        feed = {'version': version, 'package': filename, 'sha256': digest, 'published_at': time.time()}
        temporary.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(destination / 'latest.json')
        return feed
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(publish(args.package, args.destination), ensure_ascii=False, indent=2))
