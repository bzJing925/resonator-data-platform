
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Batch,
    Device,
    Mapping,
    MappingEntry,
    ReplicaBatch,
    SyncIdentity,
    UploadTask,
)
from app.services import desktop_sync_service
from app.services import replica_service as svc
from app.services.remote_access import _translate, route
from app.services.server_client import ServerError


def session():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    svc.init_replication(engine)
    return Session(engine)


def seed(db, name='B', source='http'):
    m = Mapping(name='M', file_path='m', entry_count=0)
    db.add(m)
    db.flush()
    b = Batch(batch_no=name, mapping_id=m.id, file_path='x', source=source, device_count=1)
    db.add(b)
    db.flush()
    d = Device(batch_id=b.id, original_filename='one.s1p', s_param_path='folder/one.s1p',
               s_param_port='S11', fs_ghz=4.0)
    db.add(d)
    db.commit()
    return b, d


def test_completed_only_and_revision_bulk_update():
    with session() as db:
        b, d = seed(db)
        t = UploadTask(batch_no='B', status='running', kind='upload')
        db.add(t)
        db.commit()
        assert not svc.catalog(db)['batches'][0]['ready']
        with pytest.raises(HTTPException):
            svc.export_batch(db, b.id)
        t.status = 'success'
        db.commit()
        before = svc.export_batch(db, b.id)
        db.query(Device).filter(Device.id == d.id).update({'fs_ghz': 5.0})
        db.commit()
        after = svc.export_batch(db, b.id)
        assert after['revision'] > before['revision']
        assert after['devices'][0]['fs_ghz'] == 5


def test_update_preserves_local_device_ids_and_remote_route(tmp_path, monkeypatch):
    from app.config import Settings
    monkeypatch.setattr('app.services.remote_access.get_settings', lambda: Settings(
        ALN_DESKTOP_MODE=True, ALN_DESKTOP_DIR=tmp_path))
    with session() as server, session() as desktop:
        b, d = seed(server)
        svc.import_batch(desktop, svc.export_batch(server, b.id))
        local = desktop.scalar(select(Device))
        local_id = local.id
        for endpoint in ('sparam', 'bodeq', 'sparam-sparse', 'download-s1p'):
            target = route(f'/api/devices/{local_id}/{endpoint}', 'GET', {}, {}, desktop)
            assert target[0] == f'/api/devices/{d.id}/{endpoint}'
        assert route('/api/files', 'GET', {'batch_no': 'B'}, {}, desktop)
        assert route('/api/files/curve', 'GET', {'batch_no': 'B'}, {}, desktop)
        assert route('/api/files/download-zip', 'POST', {}, {'batch_no': 'B'}, desktop)
        d.fs_ghz = 5
        server.commit()
        svc.import_batch(desktop, svc.export_batch(server, b.id))
        desktop.expire_all()
        assert desktop.scalar(select(Device)).id == local_id
        assert desktop.scalar(select(Device)).fs_ghz == 5
        assert not list(tmp_path.rglob('*.s1p'))


def test_name_collision_never_overwrites_local_data():
    with session() as server, session() as desktop:
        b, _ = seed(server)
        seed(desktop, source='table')
        with pytest.raises(ValueError, match='冲突'):
            svc.import_batch(desktop, svc.export_batch(server, b.id))
        assert desktop.scalar(select(Batch)).source == 'table'


def test_large_replica_update_avoids_sqlite_variable_limit():
    import sqlite3
    from uuid import uuid4

    from app.services.source_identity import assign_batch_identity

    with session() as desktop:
        batch, first = seed(desktop)
        assign_batch_identity(desktop, batch)
        first.source_device_key = str(uuid4())
        desktop.bulk_insert_mappings(Device, [
            {'batch_id': batch.id, 'original_filename': f'{i}.s1p',
             's_param_path': f'{i}.s1p', 's_param_port': 'S11',
             'source_device_key': str(uuid4()), 'fs_ghz': 4.0}
            for i in range(1204)
        ])
        batch.device_count = 1205
        desktop.commit()
        packet = svc.export_batch(desktop, batch.id)
        packet['devices'] = packet['devices'][:1200]
        packet['batch']['device_count'] = 1200
        packet['devices'][0]['fs_ghz'] = 5.0
        packet['remote_id'] = 9999
        packet['server_id'] = str(uuid4())
        retained = {row['source_device_key']: row['id'] for row in packet['devices']}
        desktop.rollback()
        raw = desktop.connection().connection.driver_connection
        previous_limit = raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        try:
            svc.import_batch(desktop, packet, existing=batch)
            rows = desktop.scalars(select(Device).where(Device.batch_id == batch.id)).all()
            assert len(rows) == 1200
            assert {row.source_device_key: row.id for row in rows} == retained
            assert desktop.get(Device, first.id).fs_ghz == 5.0
            # Repeat after device_ids has been persisted; both deletion passes stay bounded.
            svc.import_batch(desktop, packet)
            assert len(desktop.scalars(select(Device)).all()) == 1200
            packet['devices'] = packet['devices'][:100]
            packet['batch']['device_count'] = 100
            svc.import_batch(desktop, packet)
            rows = desktop.scalars(select(Device)).all()
            assert {row.source_device_key: row.id for row in rows} == {
                row['source_device_key']: row['id'] for row in packet['devices']
            }
        finally:
            raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous_limit)


def test_table_upload_idempotent_and_linked_metrics():
    with session() as server, session() as desktop:
        b, d = seed(desktop, source='table')
        p = svc.export_batch(desktop, b.id)
        p = {k: p[k] for k in ('batch', 'mapping', 'devices')}
        one = svc.accept_table(server, 'client:1', p)
        two = svc.accept_table(server, 'client:1', p)
        assert one['remote_id'] == two['remote_id']
        assert len(server.scalars(select(Batch)).all()) == 1
        assert server.scalar(select(Device)).s_param_path is None
        p['devices'][0]['fs_ghz'] = 6
        with pytest.raises(HTTPException) as exc:
            svc.accept_table(server, 'client:1', p)
        assert exc.value.status_code == 409


def test_link_table_updates_metrics_preserving_raw_path():
    with session() as server, session() as desktop:
        raw, device = seed(server)
        table, _ = seed(desktop, name='Metrics', source='table')
        p = svc.export_batch(desktop, table.id)
        p = {k: p[k] for k in ('batch', 'mapping', 'devices')}
        p['target'] = {'batch_no': 'B', 'revision': svc.export_batch(server, raw.id)['revision']}
        p['devices'][0]['fs_ghz'] = 6
        result = svc.accept_table(server, 'desktop:table', p)
        assert result['remote_id'] == raw.id
        server.refresh(device)
        assert device.fs_ghz == 6
        assert device.s_param_path == 'folder/one.s1p'
        assert svc.accept_table(server, 'desktop:table', p)['remote_id'] == raw.id
        p['target']['revision'] = 1
        with pytest.raises(HTTPException):
            svc.accept_table(server, 'another:table', p)


class Client:
    def __init__(self, server):
        self.server = server

    def request_json(self, method, path, **kwargs):
        if path.endswith('/catalog'):
            return svc.catalog(self.server)
        if path.endswith('/tables'):
            args = kwargs['json']
            try:
                return svc.accept_table(self.server, args['key'], args['payload'])
            except HTTPException:
                # 生产环境由 get_db 的 session close 兜底回滚；stub 显式对齐，
                # 避免 open 事务跨“请求”残留造成快照假象。
                self.server.rollback()
                raise
        return svc.export_batch(self.server, int(path.rsplit('/', 1)[-1]))

    def ensure_mapping(self, name, entries):
        from app.api.sync import MappingEnsureRequest, ensure_mapping

        try:
            return ensure_mapping(MappingEnsureRequest(name=name, entries=entries), self.server)
        except HTTPException as exc:
            self.server.rollback()
            raise ServerError(f"服务器返回 {exc.status_code}: {exc.detail}") from exc


def test_synchronize_pushes_standalone_mapping_without_batch():
    with session() as server, session() as desktop, session() as colleague:
        mapping = Mapping(name='UnusedMap', file_path='local.xlsx', entry_count=1)
        desktop.add(mapping)
        desktop.flush()
        desktop.add(MappingEntry(mapping_id=mapping.id, mark='A', description='test'))
        desktop.commit()
        first = svc.synchronize(desktop, Client(server))
        assert first['mappings_pushed'] == 1
        assert not first['errors']
        assert server.scalar(select(Mapping).where(Mapping.name == 'UnusedMap')).entry_count == 1
        assert server.scalar(select(MappingEntry)).mark == 'A'
        assert svc.synchronize(desktop, Client(server))['mappings_pushed'] == 0
        received = svc.synchronize(colleague, Client(server))
        assert received['mappings_added'] == 1
        shared = colleague.scalar(select(Mapping))
        assert shared.file_path.startswith('server://')
        assert shared.entries[0].mark == 'A'
        assert svc.synchronize(colleague, Client(server))['mappings_added'] == 0
        server.delete(server.scalar(select(Mapping)))
        server.commit()
        assert svc.synchronize(colleague, Client(server))['errors'] == []
        assert colleague.scalar(select(Mapping)) is None


def test_synchronize_auto_pushes_finished_raw_and_keeps_local_file(tmp_path, monkeypatch):
    import zipfile
    from types import SimpleNamespace

    monkeypatch.setattr(desktop_sync_service, '_files_root', lambda: tmp_path / 'files')
    monkeypatch.setattr(desktop_sync_service, '_zip_staging_dir', lambda: tmp_path / 'staging')
    raw = tmp_path / 'files/B/folder/one.s1p'
    raw.parent.mkdir(parents=True)
    raw.write_text('# GHz S MA R 50\n4 0.5 170\n')

    class RawClient(Client):
        config = SimpleNamespace(username='colleague')

        def push_batch(self, zip_path, meta):
            with zipfile.ZipFile(zip_path) as archive:
                assert archive.read('B/folder/one.s1p') == raw.read_bytes()
            mapping = self.server.scalar(select(Mapping).where(Mapping.name == 'M'))
            batch = Batch(batch_no=meta['batch_no'], mapping_id=mapping.id,
                          file_path='server.zip', source='http', device_count=1)
            self.server.add(batch)
            self.server.flush()
            self.server.add(Device(batch_id=batch.id, original_filename='one.s1p',
                                   s_param_path='folder/one.s1p', s_param_port='S11'))
            self.server.commit()
            return {'status': 'accepted', 'task_id': 1}

        def task_status(self, task_id):
            return {'status': 'success'}

        def get_batch(self, batch_no):
            return {'device_count': 1}

    with session() as server, session() as desktop:
        seed(desktop)
        result = svc.synchronize(desktop, RawClient(server))
        assert result['raw_pushed'] == 1
        assert result['mappings_pushed'] == 1
        assert result['errors'] == []
        assert server.scalar(select(Batch)).batch_no == 'B'
        assert desktop.scalar(select(ReplicaBatch)) is not None
        assert raw.read_text().startswith('# GHz')
        assert svc.synchronize(desktop, RawClient(server))['raw_pushed'] == 0


def test_synchronize_updates_but_absence_never_deletes_local_replica():
    with session() as server, session() as desktop:
        b, d = seed(server)
        result = svc.synchronize(desktop, Client(server))
        assert result['batches_added'] == 1
        d.fs_ghz = 7
        server.commit()
        assert svc.synchronize(desktop, Client(server))['batches_updated'] == 1
        assert desktop.scalar(select(Device)).fs_ghz == 7
        local = Batch(batch_no='Local', mapping_id=desktop.scalar(select(Mapping.id)),
                      file_path='local', source='http')
        desktop.add(local)
        desktop.commit()
        server.delete(b)
        server.commit()
        assert svc.synchronize(desktop, Client(server))['batches_deleted'] == 0
        assert {b.batch_no for b in desktop.scalars(select(Batch))} == {'B', 'Local'}
        assert desktop.scalar(select(ReplicaBatch)) is not None


def test_reextract_same_path_new_remote_id_replaces_without_unique_conflict():
    with session() as server, session() as desktop:
        b, d = seed(server)
        svc.import_batch(desktop, svc.export_batch(server, b.id))
        server.delete(d)
        server.flush()
        server.add(Device(id=100, batch_id=b.id, original_filename='one.s1p',
                          s_param_path='folder/one.s1p', s_param_port='S11', fs_ghz=8))
        server.commit()
        svc.import_batch(desktop, svc.export_batch(server, b.id))
        desktop.expire_all()
        assert len(desktop.scalars(select(Device)).all()) == 1
        assert desktop.scalar(select(Device)).fs_ghz == 8


def test_synchronize_pushes_table_then_other_desktop_receives_it():
    with session() as server, session() as first, session() as second:
        seed(first, name='UploadedTable', source='table')
        result = svc.synchronize(first, Client(server))
        assert result['tables_pushed'] == 1
        assert not result['errors']
        assert first.scalar(select(Batch)).source == 'server'
        assert svc.synchronize(first, Client(server))['tables_pushed'] == 0
        assert svc.synchronize(second, Client(server))['batches_added'] == 1
        assert second.scalar(select(Device)).fs_ghz == 4
        assert second.scalar(select(Device)).s_param_path is None


def test_processing_batch_is_not_frozen_in_desktop_cache():
    with session() as server, session() as desktop:
        b, _ = seed(server)
        task = UploadTask(batch_no='B', status='running', kind='upload')
        server.add(task)
        server.commit()
        assert svc.synchronize(desktop, Client(server))['batches_added'] == 0
        assert desktop.scalar(select(Batch)) is None
        task.status = 'success'
        server.commit()
        assert svc.synchronize(desktop, Client(server))['batches_added'] == 1


def test_v1_pushed_batch_is_adopted_as_replica_not_conflict():
    """v1 回传（source=http + synced_at + count 一致）的同名批次被收养为副本。"""
    from datetime import UTC, datetime

    with session() as server, session() as desktop:
        b, d = seed(server)
        local, local_dev = seed(desktop, source='http')
        local.synced_at = datetime.now(UTC)
        desktop.commit()
        result = svc.synchronize(desktop, Client(server))
        assert result['errors'] == []
        assert result['batches_updated'] == 1 and result['batches_added'] == 0
        batches = desktop.scalars(select(Batch)).all()
        assert len(batches) == 1  # 不重复导入
        assert batches[0].source == 'server'
        assert desktop.scalar(select(ReplicaBatch)).remote_id == b.id
        # 再次同步：revision 已对齐，幂等跳过
        again = svc.synchronize(desktop, Client(server))
        assert again['errors'] == [] and again['batches_updated'] == 0


def test_v1_pushed_batch_count_mismatch_still_conflicts():
    """同名但器件数不一致 = 同名不同数据，必须报错且不覆盖本地批次。"""
    from datetime import UTC, datetime

    with session() as server, session() as desktop:
        b, _ = seed(server)
        b.device_count = 2
        server.commit()
        local, _ = seed(desktop, source='http')
        local.synced_at = datetime.now(UTC)
        desktop.commit()
        result = svc.synchronize(desktop, Client(server))
        assert result['errors'] and '冲突' in result['errors'][0]
        assert desktop.scalar(select(Batch)).source == 'http'
        assert desktop.scalar(select(ReplicaBatch)) is None


def test_table_target_revision_stays_original_after_conflict():
    """关联 revision 过期后保留原base，多次同步不覆盖服务器新指标。"""
    with session() as server, session() as desktop:
        raw, dev = seed(server)
        assert svc.synchronize(desktop, Client(server))['batches_added'] == 1
        table, _ = seed(desktop, name='Metrics', source='table')
        replica = desktop.scalar(select(ReplicaBatch))
        desktop.add(SyncIdentity(
            key=f'table-target:{table.id}',
            value=f'{{"batch_no": "B", "revision": {replica.revision}}}'))
        desktop.commit()
        dev.fs_ghz = 9  # 他处更新使 revision 前进
        server.commit()
        first = svc.synchronize(desktop, Client(server))
        assert first['errors'] and '409' in first['errors'][0]
        assert first['tables_pushed'] == 0
        original_base = desktop.get(SyncIdentity, f'table-target:{table.id}').value
        second = svc.synchronize(desktop, Client(server))
        assert second['errors'] and '409' in second['errors'][0]
        assert second['tables_pushed'] == 0
        assert desktop.get(SyncIdentity, f'table-target:{table.id}').value == original_base
        assert desktop.scalar(select(Batch).where(Batch.batch_no == 'Metrics')) is not None
        server.refresh(dev)
        assert dev.fs_ghz == 9


def test_accept_table_concurrent_same_revision_push_gets_409(tmp_path):
    """BEGIN IMMEDIATE 使并发同 revision 推送的后者阻塞至前者提交，随后 409。"""
    import threading
    import time

    engine = create_engine(f'sqlite:///{tmp_path}/s.db',
                           connect_args={'check_same_thread': False, 'timeout': 5})
    Base.metadata.create_all(engine)
    svc.init_replication(engine)
    with Session(engine) as server:
        raw, _device = seed(server)
        revision = svc.export_batch(server, raw.id)['revision']
    # 手工构造客户端表格 payload（避免同一 DB 上再造一个客户端批次）
    p = {
        'batch': {'batch_no': 'Metrics', 'deembedded': False},
        'mapping': {'id': 1, 'name': 'M', 'entries': []},
        'devices': [{'original_filename': 'one.s1p', 's_param_port': 'S11', 'fs_ghz': 6}],
        'target': {'batch_no': 'B', 'revision': revision},
    }

    # 竞争者：持写锁并推进 revision（模拟另一客户端先完成的关联推送）
    locker = Session(engine)
    locker.execute(text('BEGIN IMMEDIATE'))
    locker.execute(text('UPDATE sync_revisions SET revision=revision+1 WHERE batch_id=:b'),
                   {'b': raw.id})
    outcome = {}

    def push():
        with Session(engine) as db:
            try:
                svc.accept_table(db, 'concurrent:1', p)
                outcome['result'] = 'applied'
            except HTTPException as exc:
                outcome['result'] = exc.status_code
            finally:
                db.rollback()

    t = threading.Thread(target=push)
    t.start()
    time.sleep(0.5)  # 让后者阻塞在 BEGIN IMMEDIATE 上；慢机器上退化为串行，结论不变
    locker.commit()
    locker.close()
    t.join(timeout=10)
    assert outcome['result'] == 409
    with Session(engine) as db:
        assert db.scalar(select(Device).where(Device.batch_id == raw.id)).fs_ghz == 4.0


def test_translate_unmapped_device_id_becomes_none():
    out = _translate([{'device_id': 50, 'v': 1}, {'device_id': 99}], {'50': 7}, '/api/files')
    assert out[0]['device_id'] == 7
    assert out[1]['device_id'] is None  # 不透传远程 ID 到本地正数空间


def test_translate_negates_string_task_id_and_rewrites_stream_url():
    out = _translate({'task_id': '5', 'stream_url': '/api/tasks/5/stream'}, {},
                     '/api/batches/B/recompute')
    assert out['task_id'] == -5
    assert out['stream_url'] == '/api/tasks/-5/stream'


def test_negative_task_route_uses_upstream_identity():
    with session() as desktop:
        desktop.add(SyncIdentity(key='upstream', value='srv-1'))
        desktop.commit()
        assert route('/api/tasks/-12/stream', 'GET', {}, {}, desktop) == (
            '/api/tasks/12/stream', {}, None, 'srv-1')


def test_adopted_batch_prefers_local_gz_file(tmp_path, monkeypatch):
    """收养后本地保留的原始文件（含 gzip 形态）优先于远程代理。"""
    from datetime import UTC, datetime

    from app.config import Settings
    monkeypatch.setattr('app.services.remote_access.get_settings', lambda: Settings(
        ALN_DESKTOP_MODE=True, ALN_DESKTOP_DIR=tmp_path))
    with session() as server, session() as desktop:
        seed(server)
        local, _ = seed(desktop, source='http')
        local.synced_at = datetime.now(UTC)
        desktop.commit()
        assert svc.synchronize(desktop, Client(server))['errors'] == []
        dev = desktop.scalar(select(Device).where(Device.batch_id == local.id))
        assert route(f'/api/devices/{dev.id}/sparam', 'GET', {}, {}, desktop) is not None
        gz = tmp_path / 'files' / 'B' / 'folder' / 'one.s1p.gz'
        gz.parent.mkdir(parents=True)
        gz.write_bytes(b'')
        assert route(f'/api/devices/{dev.id}/sparam', 'GET', {}, {}, desktop) is None


def test_files_route_tolerates_null_node_ids():
    with session() as desktop:
        assert route('/api/files/tree/rename', 'POST', {},
                     {'node_id': -3, 'node_ids': None}, desktop) is None
