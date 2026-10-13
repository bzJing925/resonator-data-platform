"""Opt-in real customer result snapshot; never substitute it for raw algorithms."""

import csv
import hashlib
import io
import json
import os
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import openpyxl
import pytest
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models import Batch, Device
from app.services.source_identity import assign_batch_identity
from tests.integration.test_platform_flow import _until, live_platform  # noqa: F401

pytestmark = pytest.mark.integration
ALIASES = {
    "original_filename": "original_filename", "display_name": "display_name",
    "folder_name": "folder_name", "coord": "coord", "X": "x", "Y": "y",
    "EG": "eg", "FL": "fl", "AG": "ag", "PF": "pf",
    "fs(GHz)": "fs_ghz", "fp(GHz)": "fp_ghz", "Zs(Ω)": "zs_ohm", "Zp(Ω)": "zp_ohm",
    "Qs": "qs", "Qp": "qp", "Qs_BodeQ": "qs_bodeq", "Qp_BodeQ": "qp_bodeq",
    "dbqs": "dbqs", "dbqp": "dbqp", "BodeQ_fitted": "bodeq_fitted",
    "BodeQ_smooth": "bodeq_smooth", "BodeQ_raw": "bodeq_raw",
    "Fbode(GHz)": "fbode_ghz", "k2eff(%)": "k2eff_pct", "port_type": "s_param_port",
}


def _rows(path, sheet):
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        values = iter(workbook[sheet].iter_rows(values_only=True))
        headers = next(values)
        return [dict(zip(headers, row, strict=True)) for row in values]
    finally:
        workbook.close()


def test_customer_result_snapshot_query_and_complete_export(live_platform):  # noqa: F811
    client, engine, root, kind = live_platform
    mapping_path = os.environ.get("ALN_CUSTOMER_MAPPING_FILE")
    results_path = os.environ.get("ALN_CUSTOMER_RESULTS_FILE")
    assert mapping_path and results_path, "Original customer workbook paths required"
    mapping, results = Path(mapping_path), Path(results_path)
    assert mapping.is_file() and results.is_file(), "Original customer workbooks missing"
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (mapping, results)}
    raw = _rows(results, "Sheet1")
    subset = _rows(results, "Sheet2")
    entries = _rows(mapping, "Sheet1")
    marks = {r["Mark"]: r["Description"] for r in entries}
    assert len(entries) == len(marks) == 749
    assert len(raw) == 7553 and len(subset) == 204
    assert len({r["original_filename"] for r in raw}) == 7553
    assert {r["original_filename"] for r in subset} <= {r["original_filename"] for r in raw}
    assert sum(r["Type"] is None for r in raw) == 7349
    matched = 0
    for row in raw:
        mark = re.search(r"_([A-Za-z]\d+-\d+)_", row["original_filename"])
        if mark and mark.group(1) in marks:
            assert row["display_name"] == marks[mark.group(1)]
            matched += 1
    assert matched == 7481  # 72 site-named results remain explicitly unmapped.
    uploaded = client.post("/api/mappings", data={"name": "customer-original-mapping"},
                           files={"file": (mapping.name, mapping.read_bytes())})
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["entry_count"] == 749, uploaded.json()
    expected = []
    for row in raw:
        value = {dest: row[source] for source, dest in ALIASES.items()}
        for key in ("eg", "fl", "ag"):
            value[key] = None if value[key] in (None, "NA") else float(value[key])
        value["s_param_port"] = value["s_param_port"].upper()
        # New fixture database identity; not a claim about original customer UUIDs.
        value["source_device_key"] = str(uuid5(NAMESPACE_URL,
            f"customer-result-fixture:{hashes[str(results)]}:{row['original_filename']}"))
        expected.append(value)
    with Session(engine) as db:
        batch = Batch(batch_no="customer-result-snapshot", mapping_id=uploaded.json()["id"],
                      file_path=str(root), device_count=len(expected))
        assign_batch_identity(db, batch)
        db.add(batch)
        db.flush()
        for start in range(0, len(expected), 500):
            db.execute(insert(Device), [{"batch_id": batch.id, **r}
                                       for r in expected[start:start + 500]])
        db.commit()
    fields = list(expected[0])
    spec = {"filters": {"batch_no": "customer-result-snapshot"}, "fields": fields,
            "order_by": "id"}
    response = client.post("/api/query/devices", json={**spec, "limit": 10000, "sampling": "none"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["returned"] == body["total"] == 7553
    assert body["manifest"]["truncated"] is False
    assert body["manifest"]["sampling"]["method"] == "none"
    actual = {r["original_filename"]: r for r in body["rows"]}
    for wanted in expected:
        assert all(actual[wanted["original_filename"]][field] == value
                   for field, value in wanted.items())
    for port, count in (("S11", 3787), ("S22", 3766)):
        response = client.post("/api/query/devices", json={"filters": {"s_param_port": port},
                              "fields": ["original_filename"], "limit": 10000})
        assert response.json()["total"] == count
    job = client.post("/api/export/jobs", json={"request_key": str(uuid4()), "format": "csv",
                      "query_spec": spec, "expected_sources": body["manifest"]["source_refs"]})
    assert job.status_code == 202, job.text
    job_id = job.json()["id"]
    finished = _until(lambda: client.get(f"/api/export/jobs/{job_id}").json(),
                      lambda r: r["status"] in {"success", "failed"}, 120)
    assert finished["status"] == "success" and finished["rows"] == 7553, finished
    output = client.get(f"/api/export/jobs/{job_id}/artifacts/data.csv")
    assert output.status_code == 200
    exported = list(csv.DictReader(io.StringIO(output.text.lstrip("\ufeff"))))
    assert len(exported) == 7553
    for row in exported:
        wanted = actual[row["original_filename"]]
        for field in fields:
            value = wanted[field]
            if value is None:
                assert row[field] == ""
            elif isinstance(value, (float, int)):
                assert float(row[field]) == pytest.approx(value, rel=1e-12)
            else:
                assert row[field] == str(value)
    (root / "customer-export.csv").write_bytes(output.content)
    evidence = {"database": kind, "rows": 7553, "subset_rows": 204, "source_sha256": hashes,
                "raw_zip_algorithm": "unverified", "query_manifest": body["manifest"]}
    (root / "customer-acceptance.json").write_text(json.dumps(evidence, indent=2))
    out = Path(os.environ["ALN_CUSTOMER_SNAPSHOT_OUT"])
    out.mkdir(parents=True, exist_ok=True)
    normalized = [{**row, "qs": row["Qs"], "fs_ghz": row["fs(GHz)"],
                   "pf": row["PF"], "s_param_port": row["port_type"].upper()} for row in raw]
    (out / "customer-snapshot.json").write_text(json.dumps({"rows": normalized,
        "source_sha256": hashes, "algorithm_conditions": "unknown"}, ensure_ascii=False))
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
