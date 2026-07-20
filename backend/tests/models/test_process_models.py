"""工艺参数模型 smoke 测试。"""

from __future__ import annotations

from sqlalchemy import select


def test_icp_etch_run_insert(db):
    from app.models import ProcessIcpEtchRun

    db.add(ProcessIcpEtchRun(block_index=1, run_no="F0425LW1", etch_time_sec=98.0, thk_a=395.3))
    db.commit()
    row = db.scalar(select(ProcessIcpEtchRun).where(ProcessIcpEtchRun.run_no == "F0425LW1"))
    assert row is not None
    assert row.block_index == 1
    assert row.wiw_1sig_pct is None
    assert row.created_at is not None


def test_mo_dep_run_insert(db):
    from app.models import ProcessMoDepRun

    db.add(
        ProcessMoDepRun(run_no="F0426B16", group_label="DOE2", ar_flow_sccm=10.0, dc_power_kw=7.0)
    )
    db.commit()
    row = db.scalar(select(ProcessMoDepRun).where(ProcessMoDepRun.run_no == "F0426B16"))
    assert row is not None
    assert row.group_label == "DOE2"
    assert row.stress_avg_mpa is None


def test_aln_dep_run_insert(db):
    from app.models import ProcessAlnDepRun

    db.add(
        ProcessAlnDepRun(
            source_sheet="AlSc",
            run_no="0623A07",
            recipe_id="DOE26-3A-180",
            n2_flow_sccm=46.9,
            stress_avg_mpa=196.7,
        )
    )
    db.commit()
    row = db.scalar(select(ProcessAlnDepRun).where(ProcessAlnDepRun.run_no == "0623A07"))
    assert row is not None
    assert row.recipe_id == "DOE26-3A-180"
    assert row.group_label is None
