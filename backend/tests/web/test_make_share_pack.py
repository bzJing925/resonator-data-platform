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


# ---------------------------------------------------------------------------
# 参数表格（xlsx）直传
# ---------------------------------------------------------------------------


def _make_params_xlsx(path: Path, n_marks: int = 2, both_ports: bool = True,
                      extra_unknown: bool = True) -> None:
    """合成参数表格：表头对齐客户示例（含 mBVD 6 列与未知列）。"""
    import pandas as pd

    recs = []
    for mi in range(n_marks):
        mark = f"A1-{mi + 1}"
        ports = ("S11", "S22") if both_ports else ("S11",)
        for pi, port in enumerate(ports):
            recs.append({
                "original_filename": f"3_{mark}_X-1Y-{mi + 1}N27_Fail_{port}.s1p",
                "display_name": f"EG1.0 FL0.5 700&5500 #{mi}",
                "folder_name": port,
                "coord": f"X-1Y-{mi + 1}",
                "X": -1,
                "Y": -(mi + 1),
                "EG": 1.0, "FL": 0.5, "AG": "700&5500",
                "PF": "Y" if mi == 0 else "N",
                "Area": mi + 1,
                "Area(um2)": 12000.0 + mi,
                "fs(GHz)": 4.80 + 0.1 * mi, "fp(GHz)": 5.00 + 0.1 * mi,
                "Zs(Ω)": 1.5, "Zp(Ω)": 250.0 + 10 * pi,
                "Qs": 800.0, "Qp": 900.0,
                "Qs_BodeQ": 810.0, "Qp_BodeQ": 910.0,
                "dbqs": None, "dbqp": None,
                "BodeQ_fitted": 820.0, "BodeQ_smooth": 830.0,
                "Fbode(GHz)": 4.95,
                "k2eff(%)": 6.5,
                # mBVD 6 列：识别但平台不存储
                "C0(pF)": 1.1, "Cm(pF)": 0.2, "Lm(nH)": 90.0,
                "Rm(Ω)": 2.0, "R0(Ω)": 3.0, "Rs(Ω)": 0.5,
            })
            if extra_unknown:
                recs[-1]["神秘列"] = "foo"
    pd.DataFrame(recs).to_excel(path, index=False)


def test_make_share_pack_xlsx(tmp_path):
    from app.share_pack import make_share_pack_xlsx

    xlsx = tmp_path / "3_results.xlsx"
    _make_params_xlsx(xlsx)
    out = tmp_path / "share_xlsx.zip"
    meta, report = make_share_pack_xlsx(xlsx, "#T1", out, deembedded=False)

    assert meta["batch_no"] == "#T1"
    assert meta["source"] == "xlsx"
    assert meta["deembedded"] is False
    assert meta["process_type"] == "BOTH"
    assert meta["device_count"] == 4  # 2 marks × 2 ports
    assert meta["mapping_name"] == "auto_T1"

    # 列识别报告：mBVD 忽略、未知列列出
    assert set(report["ignored_mbvd"]) == {
        "C0(pF)", "Cm(pF)", "Lm(nH)", "Rm(Ω)", "R0(Ω)", "Rs(Ω)"}
    assert report["unknown"] == ["神秘列"]
    assert report["n_marks"] == 2
    assert "fs_ghz" in report["mapped"] and "k2eff_pct" in report["mapped"]

    with zipfile.ZipFile(out) as zf:
        csv_bytes = zf.read("devices.csv.gz")
        mapping = json.loads(zf.read("mapping.json"))

    import hashlib

    assert hashlib.sha256(csv_bytes).hexdigest() == meta["sha256_devices_csv_gz"]
    rows = list(csv.DictReader(io.StringIO(gzip.decompress(csv_bytes).decode("utf-8"))))
    assert len(rows) == 4
    assert set(rows[0].keys()) == set(PACK_COLUMNS)
    by_port = {(r["mark"], r["s_param_port"]): r for r in rows}
    assert set(by_port) == {("A1-1", "S11"), ("A1-1", "S22"),
                            ("A1-2", "S11"), ("A1-2", "S22")}
    r = by_port[("A1-1", "S11")]
    assert r["coord"] == "X-1Y-1" and r["x"] == "-1" and r["y"] == "-1"
    assert abs(float(r["fs_ghz"]) - 4.8) < 1e-9
    assert float(r["fs_ghz"]) < float(r["fp_ghz"])
    assert r["k2eff_pct"] == "6.5"
    assert r["pf"] == "Y"
    assert r["deembedded"] == "False"
    # 派生对照表：S11/S22 面积分列、has_pf 取并集
    e1 = next(e for e in mapping["entries"] if e["mark"] == "A1-1")
    assert e1["area_s11"] == 12000.0 and e1["area_s22"] == 12000.0
    assert e1["has_pf"] is True
    e2 = next(e for e in mapping["entries"] if e["mark"] == "A1-2")
    assert e2["has_pf"] is False


def test_make_share_pack_xlsx_de(tmp_path):
    """勾选已去嵌：meta 与每行 deembedded=True。"""
    from app.share_pack import make_share_pack_xlsx

    xlsx = tmp_path / "3_de_results.xlsx"
    _make_params_xlsx(xlsx, n_marks=1, both_ports=False)
    meta, _ = make_share_pack_xlsx(xlsx, "#T2", tmp_path / "s.zip", deembedded=True)
    assert meta["deembedded"] is True
    assert meta["process_type"] == "S1P"
    with zipfile.ZipFile(tmp_path / "s.zip") as zf:
        rows = list(csv.DictReader(io.StringIO(
            gzip.decompress(zf.read("devices.csv.gz")).decode("utf-8"))))
    assert all(r["deembedded"] == "True" for r in rows)


def test_make_share_pack_xlsx_missing_required(tmp_path):
    """缺必填列 → 报错并附表头提示。"""
    import pandas as pd

    from app.share_pack import XLSX_HINT, make_share_pack_xlsx

    xlsx = tmp_path / "bad.xlsx"
    pd.DataFrame([{"original_filename": "3_A1-1_X-1Y-1N27_Fail_S11.s1p",
                   "display_name": "x", "EG": 1.0}]).to_excel(xlsx, index=False)
    with pytest.raises(SystemExit) as exc:
        make_share_pack_xlsx(xlsx, "#T3", tmp_path / "s.zip", deembedded=False)
    assert "缺少必填列" in str(exc.value)
    assert XLSX_HINT in str(exc.value)


def test_make_share_pack_xlsx_xy_without_coord(tmp_path):
    """无 coord 列但有 X/Y 两列 → 通过校验，coord 从文件名派生。"""
    import pandas as pd

    from app.share_pack import rows_from_xlsx

    xlsx = tmp_path / "xy.xlsx"
    _make_params_xlsx(xlsx, n_marks=1, both_ports=False)
    df = pd.read_excel(xlsx).drop(columns=["coord"])
    df.to_excel(xlsx, index=False)
    rows, _, _, _, _ = rows_from_xlsx(xlsx, deembedded=False)
    assert len(rows) == 1
    assert rows[0]["coord"] == "X-1Y-1"  # 来自 original_filename
    assert rows[0]["x"] == -1 and rows[0]["y"] == -1
