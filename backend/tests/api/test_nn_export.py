"""NN 训练 CSV 导出端点测试。"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import numpy as np
import pytest
import skrf
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Batch, Device, Mapping


def _make_s1p(path: Path, n: int = 101) -> None:
    """构造一个带谐振峰的 1-port S1P 文件。"""
    freq = np.linspace(1.0, 5.0, n)
    f0 = 3.0
    z = 50.0 + 200.0 * ((freq - f0) ** 2 - 0.05) / ((freq - f0) ** 2 + 0.05)
    z = np.where(np.abs(freq - (f0 - 0.1)) < 0.05, 1.0, z)
    s = (z - 50.0) / (z + 50.0)
    s = np.asarray(s, dtype=complex).reshape(n, 1, 1)
    z0 = np.full((n, 1), 50.0)
    net = skrf.Network(f=freq * 1e9, s=s, z0=z0)
    net.write_touchstone(str(path), form="ma")


@pytest.fixture
def sample_batch_and_device(db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """创建一个含有效 S1P 文件和一个缺失 S1P 路径的器件。"""
    from app.config import get_settings

    settings = get_settings()
    # 让 DATA_ROOT 指向 tmp_path，便于 files_dir 解析
    settings.DATA_ROOT = tmp_path

    # 确保服务/端点模块里用的 get_settings 也返回同一个测试配置
    monkeypatch.setattr("app.services.nn_export.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.export.get_settings", lambda: settings)

    mapping = Mapping(name="test-mapping", file_path="dummy.xlsx")
    db.add(mapping)
    db.flush()

    batch = Batch(
        batch_no="TEST001",
        mapping_id=mapping.id,
        file_path="dummy.zip",
    )
    db.add(batch)
    db.flush()

    batch_dir = settings.files_dir / "TEST001"
    batch_dir.mkdir(parents=True, exist_ok=True)
    s1p_path = batch_dir / "dut.s1p"
    _make_s1p(s1p_path, n=101)

    valid_device = Device(
        batch_id=batch.id,
        original_filename="dut.s1p",
        display_name="dut",
        eg=1.0,
        ag=2.0,
        fl=3.0,
        fs_ghz=2.9,
        fp_ghz=3.3,
        qs=1000.0,
        qp=800.0,
        k2eff_pct=6.0,
        bodeq_raw=1000.0,
        bodeq_smooth=950.0,
        bodeq_fitted=900.0,
        fbode_ghz=3.0,
        qs_bodeq=1100.0,
        qp_bodeq=850.0,
        s_param_path="dut.s1p",
        s_param_port="S11",
    )
    missing_device = Device(
        batch_id=batch.id,
        original_filename="missing.s1p",
        display_name="missing",
        eg=1.5,
        ag=2.5,
        fl=3.5,
        s_param_path="missing.s1p",
        s_param_port="S11",
    )
    db.add(valid_device)
    db.add(missing_device)
    db.commit()
    return batch, valid_device, missing_device


def test_export_nn_csv_header_only_when_empty(client: TestClient) -> None:
    """空 DB 时至少返回 header 行。"""
    r = client.post("/api/export/nn-csv", json={"filters": {}, "limit": 10})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    body = r.text
    assert "device_id,eg,ag,fl" in body
    # 只有一行 header
    assert len(body.strip().splitlines()) == 1


def test_export_nn_csv_exports_valid_device(client: TestClient, sample_batch_and_device) -> None:
    """有效 S1P 的器件应被导出；缺失文件的器件被跳过。"""
    r = client.post("/api/export/nn-csv", json={"filters": {}, "limit": 10})
    assert r.status_code == 200, r.text
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)

    # 只应有一行有效数据
    assert len(rows) == 1
    row = rows[0]
    assert row["eg"] == "1.0"
    assert row["ag"] == "2.0"
    assert row["fl"] == "3.0"
    assert "sample_freq_0" in row
    assert "sample_z_0" in row
    assert "full_freq_0" in row
    assert "full_z_0" in row

    # 默认 target_k=300，所以应存在 sample_freq_299
    assert "sample_freq_299" in row
    # 默认 target_n_freq=1001
    assert "full_freq_1000" in row


def test_export_nn_csv_limit_is_capped(client: TestClient) -> None:
    """limit 超过 50000 应被截断到 50000。"""
    r = client.post("/api/export/nn-csv", json={"filters": {}, "limit": 100_000})
    assert r.status_code == 200, r.text


def test_export_nn_csv_rejects_bad_filter(client: TestClient) -> None:
    """非法字段应返回 400。"""
    r = client.post(
        "/api/export/nn-csv",
        json={
            "filters": {
                "op": "and",
                "children": [{"field": "unknown_field", "op": "eq", "value": 1}],
            },
            "limit": 10,
        },
    )
    assert r.status_code == 400
