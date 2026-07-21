"""工艺参数 API 测试。"""

from __future__ import annotations


def test_steps_empty(client):
    r = client.get("/api/process/steps")
    assert r.status_code == 200
    assert r.json() == [
        {"key": "icp_etch", "label": "ICP 刻蚀", "row_count": 0},
        {"key": "mo_dep", "label": "Mo 生长", "row_count": 0},
        {"key": "aln_dep", "label": "AlN 生长", "row_count": 0},
    ]


def test_step_data_fields_and_rows(client, db):
    from app.models import ProcessMoDepRun

    db.add(
        ProcessMoDepRun(run_no="F0426B16", group_label="DOE2", ar_flow_sccm=10.0, dc_power_kw=7.0)
    )
    db.add(ProcessMoDepRun(run_no="F0426A6", ar_flow_sccm=10.0))
    db.commit()

    r = client.get("/api/process/mo_dep")
    assert r.status_code == 200
    payload = r.json()
    names = [f["name"] for f in payload["fields"]]
    assert names[0] == "run_no"
    assert "ar_flow_sccm" in names
    unit_by_name = {f["name"]: f["unit"] for f in payload["fields"]}
    assert unit_by_name["ar_flow_sccm"] == "sccm"
    assert len(payload["rows"]) == 2
    assert payload["rows"][0]["run_no"] == "F0426B16"
    assert payload["rows"][0]["dc_power_kw"] == 7.0
    assert payload["rows"][1]["group_label"] is None


def test_steps_reflect_counts(client, db):
    from app.models import ProcessIcpEtchRun

    db.add(ProcessIcpEtchRun(block_index=1, run_no="F0425LW1"))
    db.commit()
    r = client.get("/api/process/steps")
    counts = {s["key"]: s["row_count"] for s in r.json()}
    assert counts == {"icp_etch": 1, "mo_dep": 0, "aln_dep": 0}


def test_unknown_step_404(client):
    r = client.get("/api/process/nope")
    assert r.status_code == 404
    assert r.json()["detail"] == "未知工序: nope"
