"""分享包制作测试：合成 s2p zip → 分享包契约。"""

import csv
import gzip
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from make_share_pack import PACK_COLUMNS, make_share_pack  # noqa: E402


def _make_2port_s2p(path: Path, n: int = 401) -> None:
    """合成带谐振峰的 2-port S2P（4–7 GHz，与真实频段一致）。

    物理正阻抗：高斯峰（fp）+ 硬性凹陷（fs < fp），底噪 3Ω。
    """
    import skrf

    def z_response(fp: float, fs: float, peak: float) -> np.ndarray:
        freq = np.linspace(4.0, 7.0, n)
        z = 3.0 + peak * np.exp(-(((freq - fp) / 0.05) ** 2))
        z = np.where(np.abs(freq - fs) < 0.03, 0.8, z)  # 串联谐振凹陷
        return z

    freq = np.linspace(4.0, 7.0, n)
    z1 = z_response(5.0, 4.80, 250.0)
    z2 = z_response(5.2, 5.00, 180.0)
    s = np.zeros((n, 2, 2), dtype=complex)
    s[:, 0, 0] = (z1 - 50.0) / (z1 + 50.0)
    s[:, 1, 1] = (z2 - 50.0) / (z2 + 50.0)
    net = skrf.Network(f=freq * 1e9, s=s, z0=np.full((n, 2), 50.0))
    net.write_touchstone(str(path), form="ma")


@pytest.fixture()
def mini_zip(tmp_path):
    import pandas as pd

    # 对照表 csv（load_mapping 支持 csv）
    map_path = tmp_path / "map.csv"
    pd.DataFrame([["A1-1", "EG1.0 FL0.5 700&5500"]]).to_csv(
        map_path, header=False, index=False
    )

    src = tmp_path / "src"
    src.mkdir()
    _make_2port_s2p(src / "5_A1-1_X-1Y-1N27_Fail.s2p")
    _make_2port_s2p(src / "5_A1-1_X-1Y-2N36_Fail.s2p")
    zip_path = tmp_path / "data.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in src.iterdir():
            zf.write(f, f.name)
    return zip_path, map_path


def test_make_share_pack(mini_zip, tmp_path):
    zip_path, map_path = mini_zip
    out = tmp_path / "share.zip"
    meta = make_share_pack(zip_path, "#5", map_path, out)

    assert meta["format_version"] == 1
    assert meta["batch_no"] == "#5"
    assert meta["process_type"] == "S2P"
    assert meta["device_count"] == 4  # 2 个 s2p × 2 端口
    assert meta["failure_count"] == 0

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert {"meta.json", "mapping.json", "devices.csv.gz"} <= names
        csv_bytes = zf.read("devices.csv.gz")
        mapping = json.loads(zf.read("mapping.json"))

    import hashlib

    assert hashlib.sha256(csv_bytes).hexdigest() == meta["sha256_devices_csv_gz"]

    rows = list(csv.DictReader(io.StringIO(gzip.decompress(csv_bytes).decode("utf-8"))))
    assert len(rows) == 4
    assert set(rows[0].keys()) == set(PACK_COLUMNS)
    ports = {r["s_param_port"] for r in rows}
    assert ports == {"S11", "S22"}
    for r in rows:
        fs = float(r["fs_ghz"])
        assert 4.0 <= fs <= 7.0
        assert r["mark"] == "A1-1"
        assert r["eg"] not in ("", None)  # 对照表命中
    assert mapping["entries"][0]["mark"] == "A1-1"
