"""工艺参数查询接口：/api/process/*（只读，全量返回，客户端筛选/导出）。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.models import ProcessAlnDepRun, ProcessIcpEtchRun, ProcessMoDepRun

router = APIRouter(tags=["process"])


class FieldMeta(BaseModel):
    name: str
    label: str
    unit: str | None = None
    kind: Literal["numeric", "text"]


def _num(name: str, label: str, unit: str | None = None) -> FieldMeta:
    return FieldMeta(name=name, label=label, unit=unit, kind="numeric")


def _txt(name: str, label: str) -> FieldMeta:
    return FieldMeta(name=name, label=label, kind="text")


ICP_FIELDS: list[FieldMeta] = [
    _txt("run_no", "编号"),
    _num("block_index", "块序号"),
    _num("etch_time_sec", "刻蚀时间", "s"),
    _num("etch_rate_nm_min", "刻蚀速率", "nm/min"),
    _num("thk_a", "厚度", "Å"),
    _num("wiw_1sig_pct", "WiW 1σ", "%"),
    _num("wtw_1sig_pct", "WtW 1σ", "%"),
    _txt("comments", "备注"),
]

MO_FIELDS: list[FieldMeta] = [
    _txt("run_no", "编号"),
    _txt("group_label", "实验组"),
    _num("ar_flow_sccm", "Ar 流量", "sccm"),
    _num("dc_power_kw", "DC 功率", "kW"),
    _num("dep_time_sec", "沉积时间", "s"),
    _num("dep_rate_nm_min", "沉积速率", "nm/min"),
    _num("thk_nm", "厚度", "nm"),
    _num("wiw_1sig_pct", "WiW 1σ", "%"),
    _num("wtw_1sig_pct", "WtW 1σ", "%"),
    _num("stress_avg_mpa", "应力均值", "MPa"),
    _num("stress_wiw_range_mpa", "WiW 应力范围", "MPa"),
    _num("stress_wtw_range_mpa", "WtW 应力范围", "MPa"),
    _num("rs_ohm_sq", "方块电阻", "Ω/□"),
    _num("resistivity_uohm_cm", "电阻率", "μΩ·cm"),
    _num("rs_uni_pct", "Rs 均匀性", "%"),
    _num("roughness_ra_nm", "粗糙度 Ra", "nm"),
    _num("xrd_rc_fwhm_deg", "XRD-RC FWHM", "°"),
    _txt("comments", "备注"),
]

ALN_FIELDS: list[FieldMeta] = [
    _txt("source_sheet", "来源 Sheet"),
    _txt("run_no", "编号"),
    _txt("recipe_id", "Recipe ID"),
    _txt("group_label", "实验组"),
    _num("n2_flow_sccm", "N2 流量", "sccm"),
    _num("ar_flow_sccm", "Ar 流量", "sccm"),
    _num("dc_power_kw", "DC 功率", "kW"),
    _num("dep_time_sec", "沉积时间", "s"),
    _num("dep_rate_nm_min", "沉积速率", "nm/min"),
    _num("thk_nm", "厚度", "nm"),
    _num("wiw_1sig_pct", "WiW 1σ", "%"),
    _num("wtw_1sig_pct", "WtW 1σ", "%"),
    _num("stress_avg_mpa", "应力均值", "MPa"),
    _num("stress_wiw_range_mpa", "WiW 应力范围", "MPa"),
    _num("stress_wtw_range_mpa", "WtW 应力范围", "MPa"),
    _num("refractive_index", "折射率"),
    _num("roughness_ra_nm", "粗糙度 Ra", "nm"),
    _num("aog_per_um2", "AOG", "1/μm²"),
    _num("xrd_rc_fwhm_deg", "XRD-RC FWHM", "°"),
    _txt("comments", "备注"),
]

# key -> (ORM 模型, 中文名, 字段元数据)
STEPS: dict[str, tuple[Any, str, list[FieldMeta]]] = {
    "icp_etch": (ProcessIcpEtchRun, "ICP 刻蚀", ICP_FIELDS),
    "mo_dep": (ProcessMoDepRun, "Mo 生长", MO_FIELDS),
    "aln_dep": (ProcessAlnDepRun, "AlN 生长", ALN_FIELDS),
}


@router.get("/process/steps")
def list_process_steps(db: DbSession) -> list[dict[str, Any]]:
    result = []
    for key, (model, label, _fields) in STEPS.items():
        count = db.scalar(select(func.count()).select_from(model)) or 0
        result.append({"key": key, "label": label, "row_count": count})
    return result


@router.get("/process/{step}")
def get_process_step(step: str, db: DbSession) -> dict[str, Any]:
    if step not in STEPS:
        raise HTTPException(status_code=404, detail=f"未知工序: {step}")
    model, _label, fields = STEPS[step]
    names = [f.name for f in fields]
    stmt = select(*[getattr(model, n) for n in names]).order_by(model.id)
    rows = db.execute(stmt).mappings().all()
    return {
        "fields": [f.model_dump() for f in fields],
        "rows": [dict(r) for r in rows],
    }
