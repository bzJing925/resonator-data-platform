"""批次器件列表的服务端排序（点击列头切换升降序）。"""
from uuid import uuid4

from app.models import Device
from tests.api.test_batch_trash import trash_api  # noqa: F401, F811


def _add(db, batch, name, qs, fs):
    db.add(Device(
        batch_id=batch.id, source_device_key=str(uuid4()),
        original_filename=name, s_param_path=name, s_param_port='S11',
        fs_ghz=fs, qs=qs, pf='N',
    ))


def test_devices_sort_and_default_filename_grouping(trash_api):  # noqa: F811
    client, db, batch, _device, _curve, _raw = trash_api
    _add(db, batch, 'b.s1p', 10, 1.0)
    _add(db, batch, 'a.s1p', 30, 2.0)
    _add(db, batch, 'c.s1p', 20, 3.0)
    db.commit()
    url = f"/api/batches/{batch.batch_no}/devices"

    # 默认（无 sort 参数）：按原始文件名 + id 排序，供前端按文件名合并相邻行
    names = [r['original_filename'] for r in client.get(url).json()['items']]
    assert names == sorted(names)

    resp = client.get(url + '?sort=-qs').json()['items']
    qs_desc = [r['qs'] for r in resp if r['qs'] is not None]
    assert qs_desc == sorted(qs_desc, reverse=True)

    fs_asc = [r['fs_ghz'] for r in client.get(url + '?sort=fs_ghz').json()['items']]
    assert fs_asc == sorted(fs_asc)

    assert client.get(url + '?sort=nope').status_code == 400
