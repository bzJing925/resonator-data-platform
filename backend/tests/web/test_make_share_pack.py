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


def _make_cal_s2p(path: Path, kind: str, n: int = 401) -> None:
    """校准件：OPEN → s≈+1；SHORT → s≈-1（与 DUT 同频率轴）。"""
    import skrf

    freq = np.linspace(4.0, 7.0, n)
    val = 0.999 if kind == "OPEN" else -0.999
    s = np.full((n, 2, 2), val, dtype=complex)
    net = skrf.Network(f=freq * 1e9, s=s, z0=np.full((n, 2), 50.0))
    net.write_touchstone(str(path), form="ma")


def test_make_share_pack_deembed(mini_zip, tmp_path):
    """去嵌开关：zip 内加 OPEN/SHORT 校准件，meta.deembedded=True。"""
    zip_path, map_path = mini_zip
    # 往 zip 里补校准件（重新打包）
    import skrf  # noqa: F401

    src = tmp_path / "withcal"
    src.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(src)
    _make_cal_s2p(src / "5_OPEN.s2p", "OPEN")
    _make_cal_s2p(src / "5_SHORT.s2p", "SHORT")
    zip2 = tmp_path / "data_cal.zip"
    with zipfile.ZipFile(zip2, "w") as zf:
        for f in src.iterdir():
            zf.write(f, f.name)

    out = tmp_path / "share_de.zip"
    meta = make_share_pack(zip2, "#5.1", map_path, out, deembed=True)
    assert meta["deembedded"] is True
    assert meta["deembed_method"] == "default"
    assert meta["device_count"] == 4  # 校准件不算器件
    assert meta["batch_no"] == "#5.1"


def test_make_share_pack_deembed_missing_cal(mini_zip, tmp_path):
    """去嵌开启但无校准件 → 明确报错。"""
    zip_path, map_path = mini_zip
    with pytest.raises(SystemExit, match="校准件"):
        make_share_pack(zip_path, "#5", map_path, tmp_path / "x.zip", deembed=True)
