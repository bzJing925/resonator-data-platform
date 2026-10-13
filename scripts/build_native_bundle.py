#!/usr/bin/env python3
"""Build the complete Windows x64 server + desktop offline distribution.

Run with backend's uv environment. Uses existing downloads/wheels, downloading
missing artifacts from official upstream URLs and PyPI as needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from email.parser import BytesParser
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'dist-server-pack'
DOWNLOADS = OUT / 'runtime-downloads'
WHEELS = OUT / 'aln-server-lite/wheelhouse'
PYTHON_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
ELECTRON_VERSION = '42.4.0'
ELECTRON_URL = (f'https://github.com/electron/electron/releases/download/v{ELECTRON_VERSION}/'
                f'electron-v{ELECTRON_VERSION}-win32-x64.zip')


def run(args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True)


def download(url):
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS / url.rsplit('/', 1)[-1]
    if not path.exists():
        run(['curl', '--fail', '--location', '--retry', '2', '-o', str(path), url])
    return path


def windows_requirements():
    exported = subprocess.check_output([
        'uv', 'export', '--frozen', '--no-dev', '--no-hashes', '--no-emit-project'],
        cwd=ROOT / 'backend', text=True)
    env = default_environment()
    env.update(sys_platform='win32', os_name='nt', platform_machine='AMD64',
               platform_system='Windows', python_version='3.12', python_full_version='3.12.10')
    requirements = []
    for line in exported.splitlines():
        if not line or line.lstrip().startswith('#'):
            continue
        req = Requirement(line)
        if req.name in {'zipfile-deflate64', 'uvloop'}:
            continue
        if req.marker is None or req.marker.evaluate(env):
            requirements.append(req.name + str(req.specifier))
    requirements.append('pip==25.0.1')
    return requirements, env


def verify_wheels(paths, env):
    found = {canonicalize_name(parse_wheel_filename(p.name)[0]):
             parse_wheel_filename(p.name)[1] for p in paths}
    errors = []
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            meta = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
            message = BytesParser().parsebytes(archive.read(meta))
        for line in message.get_all('Requires-Dist', []):
            req = Requirement(line)
            if req.marker and not req.marker.evaluate({**env, 'extra': ''}):
                continue
            installed = found.get(canonicalize_name(req.name))
            if installed is None or installed not in req.specifier:
                errors.append(f'{path.name}: {req} is not satisfied by {installed}')
    if errors:
        raise RuntimeError('\n'.join(errors))


def copytree(source, dest):
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))


def main():
    global OUT, DOWNLOADS, WHEELS
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-build', action='store_true')
    parser.add_argument('--out-dir', type=Path, default=OUT,
                        help='Build output directory (defaults to dist-server-pack).')
    parser.add_argument('--version',
                        help='Software version (defaults to frontend/package.json).')
    parser.add_argument('--frontend-dist', type=Path, default=ROOT / 'frontend/dist')
    parser.add_argument('--electron-dist', type=Path, default=ROOT / 'frontend/electron-dist')
    args = parser.parse_args()
    OUT = args.out_dir.resolve()
    DOWNLOADS = OUT / 'runtime-downloads'
    WHEELS = OUT / 'aln-server-lite/wheelhouse'
    OUT.mkdir(parents=True, exist_ok=True)
    version = str(Version(args.version or json.loads(
        (ROOT / 'frontend/package.json').read_text())['version']))
    stamp = datetime.now().strftime('%Y%m%d')
    name = f'aln-platform-win64-{stamp}'
    package = OUT / name
    output = OUT / (name + '.zip')
    checksum = output.with_suffix('.zip.sha256')
    if package.exists() or output.exists() or checksum.exists():
        raise FileExistsError(f'Refusing to overwrite existing package output for {name} in {OUT}')
    if not args.skip_build:
        run(['npm', 'run', 'build'], ROOT / 'frontend')
    requirements, env = windows_requirements()
    req_file = OUT / 'requirements-windows.txt'
    req_file.write_text('\n'.join(requirements) + '\n')
    WHEELS.mkdir(parents=True, exist_ok=True)
    missing = []
    for line in requirements:
        req = Requirement(line)
        if not any(canonicalize_name(parse_wheel_filename(p.name)[0]) ==
                   canonicalize_name(req.name)
                   and parse_wheel_filename(p.name)[1] in req.specifier
                   for p in WHEELS.glob('*.whl')):
            missing.append(line)
    if missing:
        missing_file = OUT / 'requirements-windows-missing.txt'
        missing_file.write_text('\n'.join(missing) + '\n')
        run([sys.executable, '-m', 'pip', 'download', '-r', str(missing_file),
             '--platform', 'win_amd64', '--python-version', '3.12', '--only-binary=:all:',
             '--no-deps', '-d', str(WHEELS), '--quiet'])
    selected = []
    for line in requirements:
        req = Requirement(line)
        candidates = [p for p in WHEELS.glob('*.whl')
                      if canonicalize_name(parse_wheel_filename(p.name)[0]) ==
                      canonicalize_name(req.name)
                      and parse_wheel_filename(p.name)[1] in req.specifier]
        if len(candidates) != 1:
            raise RuntimeError(f'Expected one Windows wheel for {req}: {candidates}')
        selected.extend(candidates)
    verify_wheels(selected, env)
    python_zip, electron_zip = download(PYTHON_URL), download(ELECTRON_URL)
    redist = download('https://aka.ms/vs/17/release/vc_redist.x64.exe')
    # Verify Electron's official release checksum; TLS fetches the signed upstream release.
    sums = download(f'https://github.com/electron/electron/releases/download/v{ELECTRON_VERSION}/SHASUMS256.txt')
    expected = next(line.split()[0] for line in sums.read_text().splitlines()
                    if line.split()[-1].lstrip('*') == electron_zip.name)
    assert hashlib.sha256(electron_zip.read_bytes()).hexdigest() == expected
    copytree(ROOT / 'deploy/native-v2', package)
    for relative in ('README.txt', 'DEPLOYMENT.md'):
        document = package / relative
        if document.exists():
            content = document.read_text(encoding='utf-8-sig')
            content = content.replace('v0.3.0', f'v{version}')
            content = content.replace('aln-platform-win64-20261008', name)
            document.write_text(content, encoding='utf-8')
    # Windows 脚本编码守卫：
    # - .ps1：PS 5.1 对无 BOM 的脚本按系统代码页（GBK）解析，含非 ASCII 必须带 UTF-8 BOM
    # - .cmd/.bat：cmd.exe 无 BOM 标准，按 OEM 代码页 DBCS 逐字节扫描——非 ASCII 字节会把
    #   注释行与命令行粘连成乱码"命令"（已在 cp936 实机复现），必须纯 ASCII
    for script in package.rglob('*'):
        suffix = script.suffix.lower()
        if suffix not in ('.ps1', '.cmd', '.bat') or not script.is_file():
            continue
        raw = script.read_bytes()
        ascii_only = all(b < 0x80 or b in (0x09, 0x0A, 0x0D) for b in raw)
        if suffix == '.ps1':
            if not ascii_only and not raw.startswith(b'\xef\xbb\xbf'):
                raise RuntimeError(f'{script.name} 含非 ASCII 字符但缺 UTF-8 BOM（PS5.1 会按 GBK 误读）')
        elif not ascii_only:
            raise RuntimeError(f'{script.name} 含非 ASCII 字符（.cmd/.bat 必须纯 ASCII，cmd.exe 无 BOM 标准）')
    (package / 'prerequisites').mkdir()
    shutil.copy2(redist, package / 'prerequisites/vc_redist.x64.exe')
    copytree(ROOT / 'backend/app', package / 'backend/app')
    # Include trained model resources, never raw SNP, databases or customer uploads.
    copytree(ROOT / 'backend/checkpoints_surrogate', package / 'backend/checkpoints_surrogate')
    (package / 'backend/surrogate_data').mkdir()
    for name_ in ('baseline.npz', 'meta.json', 'dataset.npz', 'sim_extension.npz'):
        source = ROOT / 'backend/surrogate_data' / name_
        if source.exists():
            shutil.copy2(source, package / 'backend/surrogate_data' / name_)
    copytree(args.frontend_dist.resolve(), package / 'frontend/dist')
    (package / 'wheelhouse').mkdir()
    for path in selected:
        shutil.copy2(path, package / 'wheelhouse' / path.name)
    shutil.copy2(req_file, package / 'requirements-server.txt')
    with zipfile.ZipFile(python_zip) as archive:
        archive.extractall(package / 'runtime')
    (package / 'runtime/python312._pth').write_text(
        'python312.zip\n.\nLib/site-packages\n../backend\nimport site\n')
    with zipfile.ZipFile(electron_zip) as archive:
        archive.extractall(package / 'desktop')
    (package / 'desktop/electron.exe').rename(package / 'desktop/ALN-Desktop.exe')
    app = package / 'desktop/resources/app'
    app.mkdir(parents=True)
    (app / 'package.json').write_text(json.dumps({
        'name': 'aln-native-desktop', 'productName': 'ALN Data Platform',
        'version': version, 'type': 'module', 'main': 'electron-dist/electron/main.js'}))
    copytree(args.electron_dist.resolve(), app / 'electron-dist')
    copytree(ROOT / 'frontend/dist', app / 'dist')
    (package / 'native-bundle.json').write_text(json.dumps({
        'version': version, 'protocol': 2, 'built_at': datetime.now().isoformat(),
        'python': '3.12.10', 'electron': ELECTRON_VERSION,
        'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'includes_working_tree_changes': True,
        'upstream': [PYTHON_URL, ELECTRON_URL], 'wheel_count': len(selected),
    }, indent=2))
    hashes = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(package.rglob('*')) if p.is_file()}
    (package / 'SHA256SUMS.json').write_text(json.dumps(hashes, indent=2))
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p in sorted(package.rglob('*')):
            if p.is_file():
                archive.write(p, arcname=p.relative_to(OUT))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum.write_text(f'{digest}  {output.name}\n')
    print(json.dumps({'zip': str(output), 'bytes': output.stat().st_size,
                      'sha256': digest, 'files': len(hashes) + 1}, indent=2))


if __name__ == '__main__':
    main()
