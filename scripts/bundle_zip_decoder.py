"""Bundle pinned standalone Windows ZIP decoder into every native server package."""
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / 'dist-server-pack/runtime-downloads'
SEVENZIP_VERSION = '26.04'
SEVENZIP_URL = 'https://github.com/ip7z/7zip/releases/download/26.04/7z2604-extra.7z'
SEVENZIP_SHA256 = 'dc4b11d3399db18b063630137145f5585d8f7ac847bf3639bd1185d7d1f7cee0'
EXE_SHA256 = '15d4c788c148e3677e2fc1c4a01f191fb6669eba4f6acf8a465f0f1a6f1e1260'


def run(args):
    subprocess.run(args, check=True)


def download(url):
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS / url.rsplit('/', 1)[-1]
    if not path.exists():
        run(['curl', '--fail', '--location', '--retry', '2', '-o', str(path), url])
    return path


def bundle_zip_tools(package):
    """Ship the official standalone x64 ZIP decoder; never depend on server PATH."""
    cache = DOWNLOADS / '7zip-26.04'
    if (cache / '7za.exe').is_file():
        if hashlib.sha256((cache / '7za.exe').read_bytes()).hexdigest() != EXE_SHA256:
            raise RuntimeError('Cached 7-Zip executable checksum mismatch')
        for name in ('License.txt', 'readme.txt', 'history.txt', 'provenance.json'):
            if not (cache / name).is_file():
                raise RuntimeError(f'Missing decoder distribution file: {name}')
        shutil.copytree(cache, package / 'tools/7zip', dirs_exist_ok=True)
        return json.loads((cache / 'provenance.json').read_text())
    artifact = download(SEVENZIP_URL)
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != SEVENZIP_SHA256:
        raise RuntimeError('7-Zip Extra checksum differs from the pinned official release digest')
    extractor = next((path for name in ('7zz', '7z', '7za')
                      if (path := shutil.which(name))), None)
    if extractor is None:
        raise RuntimeError('The build host needs 7zz/7z/7za to unpack 7-Zip Extra')
    destination = package / 'tools/7zip'
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aln-7zip-build-') as temporary:
        source = Path(temporary)
        run([extractor, 'x', '-y', '-o' + str(source), str(artifact)])
        shutil.copy2(source / 'x64/7za.exe', destination / '7za.exe')
        for name in ('License.txt', 'readme.txt', 'history.txt'):
            shutil.copy2(source / name, destination / name)
    provenance = {'version': SEVENZIP_VERSION, 'url': SEVENZIP_URL,
                  'archive_sha256': SEVENZIP_SHA256,
                  'exe_sha256': hashlib.sha256((destination / '7za.exe').read_bytes()).hexdigest()}
    (destination / 'provenance.json').write_text(json.dumps(provenance, indent=2))
    return provenance


if __name__ == '__main__':
    import sys
    bundle_zip_tools(Path(sys.argv[1]).resolve())
