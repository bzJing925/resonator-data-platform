"""Two actual native HTTP processes, isolated files/DBs, no Redis or PostgreSQL.

Run: uv run pytest tests/test_native_pair.py -m integration -q
Set ALN_NATIVE_DESKTOP_EXECUTABLE to test a packaged Mac backend instead of source.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Batch, Device, Mapping, MappingEntry

pytestmark = pytest.mark.integration


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def pair(tmp_path, request):
    if 'integration' not in (request.config.getoption('-m') or ''):
        pytest.skip('Run explicitly with -m integration')
    processes = []
    clients = []
    logs = []
    roots = []
    try:
        for role in ('server', 'desktop'):
            root = tmp_path / role
            root.mkdir()
            executable = (
                os.environ.get('ALN_NATIVE_DESKTOP_EXECUTABLE') if role == 'desktop' else None
            )
            desktop_home = root
            if executable:
                assert Path(executable).is_file(), executable
                root = desktop_home / '.aln-data'
                root.mkdir()
            roots.append(root)
            port = free_port()
            env = {**os.environ, 'ALN_DESKTOP_MODE': 'true',
                   'ALN_SERVER_MODE': str(role == 'server').lower(),
                   'ALN_DESKTOP_DIR': str(root), 'DATA_ROOT': str(root),
                   'DEBUG': 'false', 'WATCH_ENABLED': 'false',
                   'DOTENV_PATH': str(root / 'none.env'), 'KEEP_RAW_ZIP': 'true'}
            command = [sys.executable, '-m', 'uvicorn', 'app.main:app',
                       '--host', '127.0.0.1', '--port', str(port)]
            if executable:
                env.update(HOME=str(desktop_home), ALN_BACKEND_PORT=str(port))
                command = [executable]
            log = (root / 'test.log').open('w')
            logs.append(log)
            p = subprocess.Popen(command,
                                 env=env, stdout=log, stderr=log,
                                 cwd=Path(__file__).resolve().parents[1])
            processes.append(p)
            client = httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=20)
            clients.append(client)
            for _ in range(900 if executable else 100):
                if p.poll() is not None:
                    pytest.fail((root / 'test.log').read_text())
                try:
                    if client.get('/api/health').json()['status'] == 'ok':
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.1)
            else:
                pytest.fail('Native service did not become healthy')
        yield clients, roots
    finally:
        for c in clients:
            c.close()
        for p in processes:
            p.terminate()
        for p in processes:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        for log in logs:
            log.close()


def test_native_full_remote_plotting_and_sync(pair):
    import numpy as np

    (server, desktop), (server_dir, desktop_dir) = pair
    data_dir = server_dir / 'files/B'
    data_dir.mkdir(parents=True)
    # Smooth complex resonance with sufficient points for BodeQ fitting.
    freq = np.linspace(4, 5, 401)
    s = 0.8 - 0.6 / (1 + 1j * (freq - 4.5) / 0.01)
    content = '# GHz S RI R 50\n' + ''.join(
        f'{f} {v.real} {v.imag}\n' for f, v in zip(freq, s, strict=True))
    (data_dir / 'one.s1p').write_text(content)
    engine = create_engine(f'sqlite:///{server_dir / "aln-data.db"}')
    with Session(engine) as db:
        mapping = Mapping(name='M', file_path='m', entry_count=0)
        db.add(mapping)
        db.flush()
        batch = Batch(batch_no='B', mapping_id=mapping.id, file_path='b.zip', device_count=1)
        db.add(batch)
        db.flush()
        db.add(Device(batch_id=batch.id, original_filename='one.s1p',
                      s_param_path='one.s1p', s_param_port='S11', fs_ghz=4.5))
        db.commit()
    assert desktop.put('/api/desktop-sync/config', json={
        'server_url': str(server.base_url), 'username': 'test'}).status_code == 200
    first = desktop.post('/api/desktop-sync/synchronize')
    assert first.status_code == 200, first.text
    assert first.json()['batches_added'] == 1
    rows = desktop.get('/api/batches/B/devices').json()
    device_id = rows['items'][0]['id']
    for param in ('s11_db', 's11_phase', 's11_re_im', 'z_mag_db', 'z_phase'):
        response = desktop.get(f'/api/devices/{device_id}/sparam', params={'param': param})
        assert response.status_code == 200, response.text
        assert len(response.json()['freq_ghz']) == 401
        assert response.json()['device_id'] == device_id
    bode = desktop.get(f'/api/devices/{device_id}/bodeq')
    assert bode.status_code == 200, bode.text
    assert len(bode.json()['freq_ghz']) == 401
    files = desktop.get('/api/files', params={'batch_no': 'B'}).json()
    assert files[0]['device_id'] == device_id
    curve = desktop.get('/api/files/curve', params={
        'batch_no': 'B', 'relpath': 'one.s1p', 'param': 'z_mag_db'})
    assert curve.status_code == 200, curve.text
    tree = desktop.get('/api/files/tree', params={'batch_no': 'B'})
    assert tree.status_code == 200, tree.text
    assert all(node['id'] < 0 for node in tree.json())
    download = desktop.get(f'/api/devices/{device_id}/download-s1p')
    assert download.status_code == 200 and download.text == content
    zip_download = desktop.post('/api/files/download-zip', json={'batch_no': 'B', 'relpaths': []})
    assert zip_download.status_code == 200 and zip_download.content[:2] == b'PK'
    assert not list((desktop_dir / 'files').rglob('*.s1p'))
    assert not list((desktop_dir / 'files').rglob('*.zip'))
    with Session(engine) as db:
        db.scalar(select(Device)).fs_ghz = 4.6
        db.commit()
    updated = desktop.post('/api/desktop-sync/synchronize')
    assert updated.json()['batches_updated'] == 1, updated.text
    assert desktop.get('/api/batches/B/devices').json()['items'][0]['fs_ghz'] == 4.6


def test_native_desktop_uploads_standalone_mapping_and_raw_batch(pair):
    import numpy as np

    (server, desktop), (server_dir, desktop_dir) = pair
    engine = create_engine(f'sqlite:///{desktop_dir / "aln-data.db"}')
    with Session(engine) as db:
        mapping = Mapping(name='FromDesktop', file_path='desktop.xlsx', entry_count=1)
        db.add(mapping)
        db.flush()
        db.add(MappingEntry(mapping_id=mapping.id, mark='one', description='desktop mapping'))
        db.commit()
    assert desktop.put('/api/desktop-sync/config', json={
        'server_url': str(server.base_url), 'username': 'colleague'}).status_code == 200
    first = desktop.post('/api/desktop-sync/synchronize', timeout=120)
    assert first.status_code == 200, first.text
    assert first.json()['mappings_pushed'] == 1
    remote_mapping = next(m for m in server.get('/api/mappings').json()
                          if m['name'] == 'FromDesktop')
    entries = server.get(f'/api/mappings/{remote_mapping["id"]}/entries').json()
    assert entries['items'][0]['mark'] == 'one'

    original = desktop_dir / 'files/FromComputer/one.s1p'
    original.parent.mkdir(parents=True)
    freq = np.linspace(1e9, 3e9, 801)
    sigma = (freq[-1] - freq[0]) * 0.005
    impedance = (50 - 49 * np.exp(-((freq - 1.8e9) ** 2) / (2 * sigma**2))
                 + 950 * np.exp(-((freq - 2.2e9) ** 2) / (2 * sigma**2)))
    signal = (impedance - 50) / (impedance + 50)
    original.write_text('# Hz S RI R 50\n' + ''.join(
        f'{f:.6e} {value:.6e} 0\n'
        for f, value in zip(freq, signal, strict=True)))
    with Session(engine) as db:
        mapping = db.scalar(select(Mapping).where(Mapping.name == 'FromDesktop'))
        batch = Batch(batch_no='FromComputer', mapping_id=mapping.id,
                      file_path='original.zip', source='http', device_count=1)
        db.add(batch)
        db.flush()
        db.add(Device(batch_id=batch.id, original_filename='one.s1p',
                      s_param_path='one.s1p', s_param_port='S11'))
        db.commit()
    uploaded = desktop.post('/api/desktop-sync/synchronize', timeout=120)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()['raw_pushed'] == 1, uploaded.text
    assert uploaded.json()['errors'] == [], uploaded.text
    assert server.get('/api/batches/FromComputer').status_code == 200
    assert original.is_file()  # Original desktop file remains in place.
    assert list((server_dir / 'files/FromComputer').rglob('*.s1p*'))
    again = desktop.post('/api/desktop-sync/synchronize', timeout=120)
    assert again.status_code == 200, again.text
    assert again.json()['raw_pushed'] == 0
