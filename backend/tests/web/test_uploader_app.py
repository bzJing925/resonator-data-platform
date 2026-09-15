"""简易上传页面服务测试：TestClient 驱动打包全流程（不碰网络/原生弹窗）。"""

import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from tests.web.test_make_share_pack import _make_2port_s2p  # noqa: E402

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def mini_inputs(tmp_path):
    import pandas as pd

    map_path = tmp_path / "map.csv"
    pd.DataFrame([["A1-1", "EG1 FL0.5 700&5500"]]).to_csv(map_path, header=False, index=False)
    src = tmp_path / "src"
    src.mkdir()
    _make_2port_s2p(src / "9_A1-1_X-1Y-1N27_Fail.s2p")
    _make_2port_s2p(src / "9_A1-1_X-1Y-2N36_Fail.s2p")
    zip_path = tmp_path / "data.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in src.iterdir():
            zf.write(f, f.name)
    return zip_path, map_path


def test_pack_flow(mini_inputs, tmp_path):
    from app.webui.uploader.server import create_app

    app = create_app()
    client = TestClient(app)

    r = client.get("/")
    assert r.status_code == 200 and "数据文件" in r.text

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    zip_path, map_path = mini_inputs
    r = client.post("/api/pack", json={
        "inputs": [str(zip_path)],
        "batch_no": "#9",
        "mapping_mode": "local",
        "mapping_path": str(map_path),
        "out_dir": str(out_dir),
    })
    assert r.status_code == 200 and r.json()["started"]

    for _ in range(120):
        s = client.get("/api/progress").json()
        if s["done"]:
            break
        time.sleep(0.25)
    assert s["done"] and s["error"] is None, s
    pack = Path(s["pack_path"])
    assert pack.exists() and pack.parent == out_dir
    meta = s["meta"]
    assert meta["batch_no"] == "#9" and meta["device_count"] == 4

    import csv
    import gzip
    import io

    with zipfile.ZipFile(pack) as zf:
        text = gzip.decompress(zf.read("devices.csv.gz")).decode()
        rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 4 and {r["s_param_port"] for r in rows} == {"S11", "S22"}


def test_double_pack_rejected(mini_inputs, tmp_path):
    from app.webui.uploader import server

    client = TestClient(server.create_app())
    server._set_state(running=True, done=False)
    r = client.post("/api/pack", json={
        "inputs": [str(mini_inputs[0])], "batch_no": "#9",
        "mapping_mode": "local", "mapping_path": str(mini_inputs[1]),
        "out_dir": str(tmp_path),
    })
    assert r.status_code == 409
    server._set_state(running=False)


def test_pack_flow_xlsx(tmp_path):
    """xlsx 模式：input_type=xlsx + 已去嵌勾选 → 分享包带 report。"""
    from app.webui.uploader.server import create_app
    from tests.web.test_make_share_pack import _make_params_xlsx

    xlsx = tmp_path / "3_de_results.xlsx"
    _make_params_xlsx(xlsx, n_marks=2, both_ports=True)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    client = TestClient(create_app())
    r = client.post("/api/pack", json={
        "inputs": [str(xlsx)],
        "batch_no": "#T9",
        "input_type": "xlsx",
        "xlsx_deembedded": True,
        "mapping_mode": "auto",
        "out_dir": str(out_dir),
    })
    assert r.status_code == 200 and r.json()["started"]

    for _ in range(120):
        s = client.get("/api/progress").json()
        if s["done"]:
            break
        time.sleep(0.25)
    assert s["done"] and s["error"] is None, s
    meta = s["meta"]
    assert meta["batch_no"] == "#T9"
    assert meta["device_count"] == 4
    assert meta["deembedded"] is True
    assert meta["source"] == "xlsx"
    assert meta["mapping_name"] == "auto_T9"
    rep = meta["report"]
    assert set(rep["ignored_mbvd"]) == {
        "C0(pF)", "Cm(pF)", "Lm(nH)", "Rm(Ω)", "R0(Ω)", "Rs(Ω)"}
    assert rep["unknown"] == ["神秘列"]
    assert rep["n_marks"] == 2


def test_pack_rechecks_website_batch_before_writing(mini_inputs, tmp_path, monkeypatch):
    """页面打开后网站有新批次时，后台也必须拒绝重名上传。"""
    from app.webui.uploader import server

    monkeypatch.setattr(
        server,
        "_fetch_site",
        lambda: {"online": True, "batches": ["#9"], "mappings": []},
    )
    server._set_state(running=False, done=False, error=None)
    client = TestClient(server.create_app())
    zip_path, map_path = mini_inputs
    response = client.post("/api/pack", json={
        "inputs": [str(zip_path)],
        "batch_no": "#9",
        "mapping_mode": "local",
        "mapping_path": str(map_path),
        "out_dir": str(tmp_path),
    })
    assert response.status_code == 200

    for _ in range(40):
        state = client.get("/api/progress").json()
        if state["done"]:
            break
        time.sleep(0.05)
    assert state["done"] and "已存在于网站" in state["error"]
