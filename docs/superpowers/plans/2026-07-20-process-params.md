# 工艺参数数据域 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将工艺参数 Excel（`副本SICM_AlN_DT.xlsx`：ICP 刻蚀 / Mo 生长 / AlN 生长）作为独立数据域纳入平台——三张类型化表、一次性导入脚本、只读 API、前端 `/process` 页面（浏览/筛选/排序/CSV 导出），含后端 pytest、前端 vitest、端到端联合测试与桌面打包。

**架构：** 纯函数解析器（`app/core/process_params.py`）→ 导入脚本（`scripts/import_process_excel.py`）→ 三张 `process_*_runs` 表（与 devices 无关联）→ 只读 API（`app/api/process.py`）→ 前端新页面（全量取数、客户端筛选/排序/导出）。

**技术栈：** FastAPI + SQLAlchemy 2.0 + Alembic + openpyxl + pytest；React 18 + TypeScript + vitest + @testing-library/react。

**规格：** `docs/superpowers/specs/2026-07-20-process-params-design.md`

**执行位置：** 除任务 0/11/12 外，全部在 worktree `/Users/jingbozuo/Projects/aln-data-backend-api`（分支 `feat/backend-api`）中进行；下文所有相对路径相对该 worktree 根目录。

---

## 文件结构

**后端（新建）：**
- `backend/app/core/process_params.py` — 纯函数 Excel 解析器（4 sheet 显式列配置）
- `backend/tests/core/test_process_params.py` — 解析器单元测试（合成 workbook）
- `backend/app/models/process.py` — 3 个 ORM 模型
- `backend/tests/models/test_process_models.py` — 模型 smoke 测试
- `backend/alembic/versions/2026_07_20_120000_add_process_tables.py` — 迁移
- `backend/scripts/import_process_excel.py` — 一次性导入 CLI
- `backend/app/api/process.py` — `/api/process/*` 端点 + 字段元数据
- `backend/tests/api/test_process.py` — API 测试

**后端（修改）：**
- `backend/app/models/__init__.py` — 导出 3 个新模型
- `backend/app/main.py` — 注册 process 路由

**前端（新建）：**
- `frontend/src/utils/processTable.ts` — 纯函数：筛选/排序/CSV/数值格式化
- `frontend/src/utils/processTable.test.ts` — 工具函数 vitest 测试
- `frontend/src/pages/Process.tsx` — 工艺数据页面
- `frontend/src/pages/Process.test.tsx` — 页面组件测试

**前端（修改）：**
- `frontend/src/types/index.ts` — ProcessFieldMeta / ProcessStep / ProcessStepData 类型
- `frontend/src/api/endpoints.ts` — listProcessSteps / getProcessStep
- `frontend/src/App.tsx` — `/process` 路由（lazy）
- `frontend/src/components/Sidebar.tsx` — 导航项"工艺数据"
- `frontend/vite.config.ts` — vitest 配置
- `frontend/package.json` — test script + vitest 相关 devDeps

---

## 任务 0：创建 worktree

在 `/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master` 执行：

- [ ] **步骤 1：创建 worktree 与分支**

```bash
cd /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master
git worktree add /Users/jingbozuo/Projects/aln-data-backend-api -b feat/backend-api
```

预期：`Preparing worktree (new branch 'feat/backend-api')`

- [ ] **步骤 2：准备后端环境**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/backend
uv sync
```

预期：依赖安装完成，`uv run pytest tests/models -q` 通过（基线绿）。

- [ ] **步骤 3：准备前端环境**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/frontend
npm install
```

预期：无错误。

---

## 任务 1：Excel 解析器（TDD）

**文件：**
- 创建：`backend/app/core/process_params.py`
- 测试：`backend/tests/core/test_process_params.py`

- [ ] **步骤 1：编写失败的测试**

创建 `backend/tests/core/test_process_params.py`：

```python
"""工艺参数 Excel 解析器测试（合成 workbook，覆盖规格 §5 全部规则）。"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from app.core.process_params import parse_process_workbook


def _build_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()

    # --- SPM2 ICP Etch：2 个表头块 + mean/spec 行 + 合并单元格 + 百分比格式 ---
    ws = wb.active
    ws.title = "SPM2 ICP Etch"
    ws.append([])  # r1 空行
    ws.append([None, "Information", None, "Measrements"])  # r2
    ws.append([None, "No.", "Etch Time", "Etch Rate", "Thickness Mapping", None, None, "Comments"])  # r3
    ws.append([None, None, "sec", "nm/min", "THK/A", "WiW 1sig/%", "WtW 1sig/%"])  # r4 单位行
    ws.append([None, "F0425LW1", 98, 24.2, 395.3, 0.029, None, "5000A oxide wafer"])  # r5
    ws["F5"].number_format = "0.00%"
    ws.append([None, "F0425LW2", 98, 24.5, 399.5, 0.031])  # r6
    ws["F6"].number_format = "0.00%"
    ws.append([None, None, "mean", 24.35, 397.4, 0.03])  # r7 mean 汇总行
    ws.append([None, None, None, None, None, "spec. <5%", "spec. <2%"])  # r8 spec 行
    ws.append([])  # r9 空行
    ws.append([None, "No.", "Etch Time"])  # r10 第二表头块
    ws.append([None, None, "sec"])  # r11 单位行
    ws.append([None, "0602A17", 43, 1128.8, 809.0, 0.0351])  # r12
    ws["F12"].number_format = "0.00%"
    ws.merge_cells("G5:G6")  # WtW 组级合并单元格
    ws["G5"] = 0.011
    ws["G5"].number_format = "0.00%"

    # --- SPM3 Mo：group_label + '??' 脏单元格 ---
    ws = wb.create_sheet("SPM3 Mo")
    ws.append([None, "No.", "Ar Flow Rate", "DC Power", "Dep. Time", "Dep. Rate"])  # r1
    ws.append([None, None, "sccm", "kW", "sec", "nm/min"])  # r2 单位行
    ws.append(["DOE2", "F0507A11", 10, 7, 20, 392.3])  # r3
    ws.append([None, "F0426A6", 10, 7, 20, "??"])  # r4 脏单元格

    # --- SPM4 AlScN(30AT)：实验名 run_no ---
    ws = wb.create_sheet("SPM4 AlScN(30AT)")
    ws.append([None, "No.", "N2 Flow Rate", "Ar Flow Rate", "DC Power", "Dep. Time", "Dep. Rate"])
    ws.append([None, None, "sccm", "sccm", "kW", "sec", "nm/min"])
    ws.append(["S21", "DOE1", 40, 20, 7, 307.27, 1.13])

    # --- AlSc：recipe_id + 规格目标行（run_no 为空）+ 中间空行 ---
    ws = wb.create_sheet("AlSc")
    ws.append(["Recipe ID", "No.", "N2 Flow Rate"])
    ws.append([None, None, "sccm"])
    ws.append([None, None, None, None, None, None, None, 3500, "<0.5%"])  # 规格行：B 空、H/I 有内容
    ws.append(["DOE26-3A-180", "0623A07", 46.9])
    ws.append([])  # 空行：静默忽略，不计入 skipped
    ws.append(["DOE35", "0526A22", 46.9])

    wb.save(path)


@pytest.fixture
def workbook_path(tmp_path: Path) -> Path:
    path = tmp_path / "process.xlsx"
    _build_workbook(path)
    return path


def test_icp_rows_and_block_index(workbook_path: Path):
    data = parse_process_workbook(workbook_path)
    assert [r["run_no"] for r in data.icp_etch] == ["F0425LW1", "F0425LW2", "0602A17"]
    assert [r["block_index"] for r in data.icp_etch] == [1, 1, 2]
    assert data.icp_etch[0]["comments"] == "5000A oxide wafer"
    assert data.icp_etch[0]["etch_time_sec"] == 98.0


def test_percent_format_multiplied_by_100(workbook_path: Path):
    data = parse_process_workbook(workbook_path)
    assert data.icp_etch[0]["wiw_1sig_pct"] == pytest.approx(2.9)
    assert data.icp_etch[2]["wiw_1sig_pct"] == pytest.approx(3.51)


def test_merged_cell_kept_on_anchor_row_only(workbook_path: Path):
    data = parse_process_workbook(workbook_path)
    assert data.icp_etch[0]["wtw_1sig_pct"] == pytest.approx(1.1)
    assert data.icp_etch[1]["wtw_1sig_pct"] is None


def test_icp_summary_and_header_rows_skipped(workbook_path: Path):
    data = parse_process_workbook(workbook_path)
    rep = data.report.sheets["SPM2 ICP Etch"]
    assert rep.imported == 3
    # r2 Information、r3/r10 表头、r4/r11 单位行、r7 mean、r8 spec → 7 行
    assert rep.skipped == 7


def test_dirty_cell_becomes_none_and_reported(workbook_path: Path):
    data = parse_process_workbook(workbook_path)
    assert data.mo_dep[1]["dep_rate_nm_min"] is None
    dirty = [d for d in data.report.dirty_cells if d.sheet == "SPM3 Mo"]
    assert len(dirty) == 1
    assert dirty[0].raw == "??"
    assert dirty[0].cell == "F4"


def test_group_label_and_recipe_id(workbook_path: Path):
    data = parse_process_workbook(workbook_path)
    assert data.mo_dep[0]["group_label"] == "DOE2"
    assert data.mo_dep[1]["group_label"] is None
    assert data.aln_dep[0]["group_label"] == "S21"
    assert data.aln_dep[0]["source_sheet"] == "SPM4 AlScN(30AT)"
    alsc = [r for r in data.aln_dep if r["source_sheet"] == "AlSc"]
    assert [r["recipe_id"] for r in alsc] == ["DOE26-3A-180", "DOE35"]
    assert alsc[0]["group_label"] is None


def test_alsc_spec_row_skipped_blank_row_ignored(workbook_path: Path):
    data = parse_process_workbook(workbook_path)
    rep = data.report.sheets["AlSc"]
    assert rep.imported == 2
    # r1 表头、r2 单位行、r3 规格行 → 3 行；r5 空行不计
    assert rep.skipped == 3


def test_missing_sheet_raises(tmp_path: Path):
    wb = openpyxl.Workbook()
    wb.save(tmp_path / "empty.xlsx")
    with pytest.raises(ValueError, match="缺少 sheet"):
        parse_process_workbook(tmp_path / "empty.xlsx")
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/backend
uv run pytest tests/core/test_process_params.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'app.core.process_params'`

- [ ] **步骤 3：实现解析器**

创建 `backend/app/core/process_params.py`：

```python
"""工艺参数 Excel 解析器（纯函数）。

源文件结构（规格 docs/superpowers/specs/2026-07-20-process-params-design.md §3）：
  SPM2 ICP Etch    ICP 刻蚀（3 个重复表头块，块间有 mean/spec 汇总行）
  SPM3 Mo          Mo 生长（A 列为实验组标签）
  SPM4 AlScN(30AT) AlScN 生长（A 列为实验组标签）
  AlSc             AlSc 生长（A 列为 Recipe ID，含规格目标行）

数据行识别：run_no 列非空且非表头字面量（'No.' / 'Information'）。
合并单元格只返回锚点值（openpyxl 默认行为），组级统计不向下填充。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl

_HEADER_TOKENS = {"No.", "Information"}

TEXT_FIELDS = frozenset({"run_no", "group_label", "recipe_id", "comments", "source_sheet"})


@dataclass(frozen=True)
class SheetConfig:
    """一个 sheet 的显式列配置。"""

    sheet: str
    table: str  # 'icp_etch' | 'mo_dep' | 'aln_dep'
    columns: dict[str, str]  # 列字母 -> 字段名
    pct_fields: frozenset[str]  # number_format 含 % 时 ×100 的字段
    has_blocks: bool = False  # 是否有重复表头块（block_index 递增）


ICP_COLUMNS: dict[str, str] = {
    "B": "run_no",
    "C": "etch_time_sec",
    "D": "etch_rate_nm_min",
    "E": "thk_a",
    "F": "wiw_1sig_pct",
    "G": "wtw_1sig_pct",
    "H": "comments",
}

MO_COLUMNS: dict[str, str] = {
    "A": "group_label",
    "B": "run_no",
    "C": "ar_flow_sccm",
    "D": "dc_power_kw",
    "E": "dep_time_sec",
    "F": "dep_rate_nm_min",
    "G": "thk_nm",
    "H": "wiw_1sig_pct",
    "I": "wtw_1sig_pct",
    "J": "stress_avg_mpa",
    "K": "stress_wiw_range_mpa",
    "L": "stress_wtw_range_mpa",
    "M": "rs_ohm_sq",
    "N": "resistivity_uohm_cm",
    "O": "rs_uni_pct",
    "P": "roughness_ra_nm",
    "Q": "xrd_rc_fwhm_deg",
    "R": "comments",
}

# SPM4 / AlSc 共用的列（A 列除外：SPM4 是 group_label，AlSc 是 recipe_id）
ALN_COLUMNS: dict[str, str] = {
    "B": "run_no",
    "C": "n2_flow_sccm",
    "D": "ar_flow_sccm",
    "E": "dc_power_kw",
    "F": "dep_time_sec",
    "G": "dep_rate_nm_min",
    "H": "thk_nm",
    "I": "wiw_1sig_pct",
    "J": "wtw_1sig_pct",
    "K": "stress_avg_mpa",
    "L": "stress_wiw_range_mpa",
    "M": "stress_wtw_range_mpa",
    "N": "refractive_index",
    "O": "roughness_ra_nm",
    "P": "aog_per_um2",
    "Q": "xrd_rc_fwhm_deg",
    "R": "comments",
}

_PCT_FIELDS = frozenset({"wiw_1sig_pct", "wtw_1sig_pct", "rs_uni_pct"})

SHEET_CONFIGS: tuple[SheetConfig, ...] = (
    SheetConfig("SPM2 ICP Etch", "icp_etch", ICP_COLUMNS, _PCT_FIELDS, has_blocks=True),
    SheetConfig("SPM3 Mo", "mo_dep", MO_COLUMNS, _PCT_FIELDS),
    SheetConfig("SPM4 AlScN(30AT)", "aln_dep", {"A": "group_label", **ALN_COLUMNS}, _PCT_FIELDS),
    SheetConfig("AlSc", "aln_dep", {"A": "recipe_id", **ALN_COLUMNS}, _PCT_FIELDS),
)


@dataclass(frozen=True)
class DirtyCell:
    """无法转换为数值而被清洗为 NULL 的单元格。"""

    sheet: str
    cell: str  # 如 'F12'
    raw: str


@dataclass
class SheetReport:
    imported: int = 0
    skipped: int = 0


@dataclass
class ParseReport:
    sheets: dict[str, SheetReport] = field(default_factory=dict)
    dirty_cells: list[DirtyCell] = field(default_factory=list)


@dataclass
class ProcessData:
    icp_etch: list[dict[str, Any]]
    mo_dep: list[dict[str, Any]]
    aln_dep: list[dict[str, Any]]
    report: ParseReport


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def _is_percent_format(number_format: str | None) -> bool:
    return bool(number_format) and "%" in number_format


def parse_process_workbook(path: str | Path) -> ProcessData:
    """解析工艺参数 xlsx，返回三张表的行数据 + 解析报告。"""
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        missing = [c.sheet for c in SHEET_CONFIGS if c.sheet not in wb.sheetnames]
        if missing:
            raise ValueError(f"Excel 缺少 sheet: {', '.join(missing)}")

        data = ProcessData(icp_etch=[], mo_dep=[], aln_dep=[], report=ParseReport())

        for cfg in SHEET_CONFIGS:
            ws = wb[cfg.sheet]
            rep = SheetReport()
            data.report.sheets[cfg.sheet] = rep
            block_index = 0
            run_no_col = next(col for col, f in cfg.columns.items() if f == "run_no")

            for row in ws.iter_rows():
                cells = {c.column_letter: c for c in row}
                # 整行空白（配置列全无值）→ 静默忽略
                if all(cells.get(col) is None or cells[col].value is None for col in cfg.columns):
                    continue

                run_no_cell = cells.get(run_no_col)
                raw_run_no = run_no_cell.value if run_no_cell is not None else None
                if raw_run_no is None or str(raw_run_no).strip() == "":
                    # mean/spec/单位/规格目标等附属行
                    rep.skipped += 1
                    continue
                run_no_text = str(raw_run_no).strip()
                if run_no_text in _HEADER_TOKENS:
                    if run_no_text == "No." and cfg.has_blocks:
                        block_index += 1
                    rep.skipped += 1
                    continue

                record: dict[str, Any] = {}
                for col, field_name in cfg.columns.items():
                    cell = cells.get(col)
                    value = cell.value if cell is not None else None
                    if field_name in TEXT_FIELDS:
                        text = None if value is None else str(value).strip()
                        record[field_name] = text or None
                        continue
                    num = _to_float(value)
                    if num is None and value is not None and str(value).strip() != "":
                        data.report.dirty_cells.append(
                            DirtyCell(cfg.sheet, f"{col}{row[0].row}", str(value))
                        )
                    if (
                        num is not None
                        and field_name in cfg.pct_fields
                        and _is_percent_format(cell.number_format if cell is not None else None)
                    ):
                        num *= 100
                    record[field_name] = num

                record["run_no"] = run_no_text
                if cfg.table == "aln_dep":
                    record["source_sheet"] = cfg.sheet
                if cfg.has_blocks:
                    record["block_index"] = block_index
                getattr(data, cfg.table).append(record)
                rep.imported += 1

        return data
    finally:
        wb.close()
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/core/test_process_params.py -v
```

预期：8 个测试全部 PASS。

- [ ] **步骤 5：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api
git add backend/app/core/process_params.py backend/tests/core/test_process_params.py
git commit -m "feat(backend): 工艺参数 Excel 解析器（纯函数+显式列配置）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 2：ORM 模型

**文件：**
- 创建：`backend/app/models/process.py`
- 修改：`backend/app/models/__init__.py`
- 测试：`backend/tests/models/test_process_models.py`

- [ ] **步骤 1：编写失败的测试**

创建 `backend/tests/models/test_process_models.py`（复用 `tests/models/conftest.py` 的 `db` fixture）：

```python
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

    db.add(ProcessMoDepRun(run_no="F0426B16", group_label="DOE2", ar_flow_sccm=10.0, dc_power_kw=7.0))
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
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/backend
uv run pytest tests/models/test_process_models.py -v
```

预期：FAIL，`ImportError: cannot import name 'ProcessIcpEtchRun' from 'app.models'`

- [ ] **步骤 3：实现模型**

创建 `backend/app/models/process.py`：

```python
"""工艺参数表：ICP 刻蚀 / Mo 生长 / AlN 生长。

独立数据域（暂不关联 devices/batches），来源：副本SICM_AlN_DT.xlsx，
由 scripts/import_process_excel.py 一次性导入。
详见 docs/superpowers/specs/2026-07-20-process-params-design.md。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Float, Integer, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ProcessIcpEtchRun(Base, TimestampMixin):
    """ICP 刻蚀工艺记录（SPM2 ICP Etch sheet，3 个表头块合并）。"""

    __tablename__ = "process_icp_etch_runs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    block_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    run_no: Mapped[str] = mapped_column(Text, nullable=False)
    etch_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    etch_rate_nm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    thk_a: Mapped[float | None] = mapped_column(Float, nullable=True)  # 埃
    wiw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wtw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessMoDepRun(Base, TimestampMixin):
    """Mo 底电极生长工艺记录（SPM3 Mo sheet）。"""

    __tablename__ = "process_mo_dep_runs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    run_no: Mapped[str] = mapped_column(Text, nullable=False)
    group_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    ar_flow_sccm: Mapped[float | None] = mapped_column(Float, nullable=True)
    dc_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    dep_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    dep_rate_nm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    thk_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    wiw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wtw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_avg_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_wiw_range_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_wtw_range_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    rs_ohm_sq: Mapped[float | None] = mapped_column(Float, nullable=True)
    resistivity_uohm_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    rs_uni_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    roughness_ra_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    xrd_rc_fwhm_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessAlnDepRun(Base, TimestampMixin):
    """AlN/AlScN 压电层生长工艺记录（SPM4 AlScN(30AT) + AlSc 两 sheet 合并）。"""

    __tablename__ = "process_aln_dep_runs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    source_sheet: Mapped[str] = mapped_column(Text, nullable=False)
    recipe_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_no: Mapped[str] = mapped_column(Text, nullable=False)
    group_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    n2_flow_sccm: Mapped[float | None] = mapped_column(Float, nullable=True)
    ar_flow_sccm: Mapped[float | None] = mapped_column(Float, nullable=True)
    dc_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    dep_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    dep_rate_nm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    thk_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    wiw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wtw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_avg_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_wiw_range_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_wtw_range_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    refractive_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    roughness_ra_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    aog_per_um2: Mapped[float | None] = mapped_column(Float, nullable=True)
    xrd_rc_fwhm_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
```

修改 `backend/app/models/__init__.py`：在 import 区加

```python
from app.models.process import ProcessAlnDepRun, ProcessIcpEtchRun, ProcessMoDepRun
```

并在 `__all__` 中追加 `"ProcessAlnDepRun"`, `"ProcessIcpEtchRun"`, `"ProcessMoDepRun"`。

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/models/test_process_models.py tests/models -v
```

预期：新 3 个测试 PASS，既有模型测试不回归。

- [ ] **步骤 5：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api
git add backend/app/models/process.py backend/app/models/__init__.py backend/tests/models/test_process_models.py
git commit -m "feat(backend): 工艺参数三个 ORM 模型

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 3：Alembic 迁移

**文件：**
- 创建：`backend/alembic/versions/2026_07_20_120000_add_process_tables.py`

- [ ] **步骤 1：编写迁移**

```python
"""add process parameter tables (icp_etch / mo_dep / aln_dep)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "2026_07_20_120000"
down_revision = "2026_07_06_160000"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "process_icp_etch_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("block_index", sa.SmallInteger(), nullable=False),
        sa.Column("run_no", sa.Text(), nullable=False),
        sa.Column("etch_time_sec", sa.Float(), nullable=True),
        sa.Column("etch_rate_nm_min", sa.Float(), nullable=True),
        sa.Column("thk_a", sa.Float(), nullable=True),
        sa.Column("wiw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("wtw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "process_mo_dep_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_no", sa.Text(), nullable=False),
        sa.Column("group_label", sa.Text(), nullable=True),
        sa.Column("ar_flow_sccm", sa.Float(), nullable=True),
        sa.Column("dc_power_kw", sa.Float(), nullable=True),
        sa.Column("dep_time_sec", sa.Float(), nullable=True),
        sa.Column("dep_rate_nm_min", sa.Float(), nullable=True),
        sa.Column("thk_nm", sa.Float(), nullable=True),
        sa.Column("wiw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("wtw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("stress_avg_mpa", sa.Float(), nullable=True),
        sa.Column("stress_wiw_range_mpa", sa.Float(), nullable=True),
        sa.Column("stress_wtw_range_mpa", sa.Float(), nullable=True),
        sa.Column("rs_ohm_sq", sa.Float(), nullable=True),
        sa.Column("resistivity_uohm_cm", sa.Float(), nullable=True),
        sa.Column("rs_uni_pct", sa.Float(), nullable=True),
        sa.Column("roughness_ra_nm", sa.Float(), nullable=True),
        sa.Column("xrd_rc_fwhm_deg", sa.Float(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "process_aln_dep_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_sheet", sa.Text(), nullable=False),
        sa.Column("recipe_id", sa.Text(), nullable=True),
        sa.Column("run_no", sa.Text(), nullable=False),
        sa.Column("group_label", sa.Text(), nullable=True),
        sa.Column("n2_flow_sccm", sa.Float(), nullable=True),
        sa.Column("ar_flow_sccm", sa.Float(), nullable=True),
        sa.Column("dc_power_kw", sa.Float(), nullable=True),
        sa.Column("dep_time_sec", sa.Float(), nullable=True),
        sa.Column("dep_rate_nm_min", sa.Float(), nullable=True),
        sa.Column("thk_nm", sa.Float(), nullable=True),
        sa.Column("wiw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("wtw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("stress_avg_mpa", sa.Float(), nullable=True),
        sa.Column("stress_wiw_range_mpa", sa.Float(), nullable=True),
        sa.Column("stress_wtw_range_mpa", sa.Float(), nullable=True),
        sa.Column("refractive_index", sa.Float(), nullable=True),
        sa.Column("roughness_ra_nm", sa.Float(), nullable=True),
        sa.Column("aog_per_um2", sa.Float(), nullable=True),
        sa.Column("xrd_rc_fwhm_deg", sa.Float(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("process_aln_dep_runs")
    op.drop_table("process_mo_dep_runs")
    op.drop_table("process_icp_etch_runs")
```

- [ ] **步骤 2：用 sqlite 文件库验证迁移可升级/降级**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/backend
rm -f /tmp/aln_mig_test.db
DATABASE_URL="sqlite:////tmp/aln_mig_test.db" uv run alembic upgrade head
DATABASE_URL="sqlite:////tmp/aln_mig_test.db" uv run python -c "
from sqlalchemy import create_engine, inspect
insp = inspect(create_engine('sqlite:////tmp/aln_mig_test.db'))
tables = insp.get_table_names()
for t in ('process_icp_etch_runs', 'process_mo_dep_runs', 'process_aln_dep_runs'):
    assert t in tables, t
    print(t, len(insp.get_columns(t)), 'columns')
"
DATABASE_URL="sqlite:////tmp/aln_mig_test.db" uv run alembic downgrade -1
DATABASE_URL="sqlite:////tmp/aln_mig_test.db" uv run alembic upgrade head
```

预期：三表创建（分别 10 / 20 / 22 列），downgrade 后再 upgrade 成功。

- [ ] **步骤 3：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api
git add backend/alembic/versions/2026_07_20_120000_add_process_tables.py
git commit -m "feat(backend): 工艺参数三表 Alembic 迁移

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 4：导入脚本

**文件：**
- 创建：`backend/scripts/import_process_excel.py`

- [ ] **步骤 1：实现脚本**

```python
"""一次性导入工艺参数 Excel 到 process_*_runs 三表。

用法（从 backend 目录）：
    uv run python scripts/import_process_excel.py <xlsx路径> [--replace]

目标表非空时默认报错退出；--replace 先清空三表再导入。
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete, func, select

from app.core.process_params import parse_process_workbook
from app.db import SessionLocal
from app.models import ProcessAlnDepRun, ProcessIcpEtchRun, ProcessMoDepRun

_TABLES = {
    "icp_etch": ProcessIcpEtchRun,
    "mo_dep": ProcessMoDepRun,
    "aln_dep": ProcessAlnDepRun,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="导入工艺参数 Excel")
    parser.add_argument("xlsx", help="工艺参数 xlsx 文件路径")
    parser.add_argument("--replace", action="store_true", help="先清空三张工艺表再导入")
    args = parser.parse_args()

    try:
        data = parse_process_workbook(args.xlsx)
    except (ValueError, FileNotFoundError) as exc:
        print(f"解析失败: {exc}", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        counts = {
            key: db.scalar(select(func.count()).select_from(model)) or 0
            for key, model in _TABLES.items()
        }
        if any(counts.values()) and not args.replace:
            print(f"错误：工艺表非空 {counts}，如需重新导入请加 --replace", file=sys.stderr)
            return 1
        if args.replace:
            for model in _TABLES.values():
                db.execute(delete(model))

        for key, model in _TABLES.items():
            db.add_all(model(**row) for row in getattr(data, key))
        db.commit()
    finally:
        db.close()

    print("导入完成：")
    for sheet, rep in data.report.sheets.items():
        print(f"  {sheet}: 导入 {rep.imported} 行, 跳过 {rep.skipped} 行")
    if data.report.dirty_cells:
        print(f"清洗脏单元格 {len(data.report.dirty_cells)} 个（置 NULL）：")
        for d in data.report.dirty_cells:
            print(f"  {d.sheet}!{d.cell} = {d.raw!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **步骤 2：sqlite 端到端冒烟（真实 Excel）**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/backend
rm -f /tmp/aln_proc_e2e.db
DATABASE_URL="sqlite:////tmp/aln_proc_e2e.db" uv run alembic upgrade head
DATABASE_URL="sqlite:////tmp/aln_proc_e2e.db" uv run python scripts/import_process_excel.py "/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/副本SICM_AlN_DT.xlsx"
```

预期：退出码 0，打印 4 个 sheet 的导入/跳过行数（icp 表 42 行左右；mo 表 70+ 行；aln 表 160+ 行——以真实输出为准），脏单元格清单（`??` 等）。
再运行一次不带 `--replace` 的相同命令，预期：退出码 1，报错"工艺表非空"。
加 `--replace` 再跑一次，预期：退出码 0。

- [ ] **步骤 3：抽样核对数值（对照 Excel 原文）**

```bash
DATABASE_URL="sqlite:////tmp/aln_proc_e2e.db" uv run python -c "
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import ProcessIcpEtchRun, ProcessMoDepRun, ProcessAlnDepRun
from sqlalchemy import select
db = Session(create_engine('sqlite:////tmp/aln_proc_e2e.db'))
r = db.scalar(select(ProcessIcpEtchRun).where(ProcessIcpEtchRun.run_no=='F0425LW1'))
print('ICP F0425LW1:', r.etch_time_sec, r.etch_rate_nm_min, r.thk_a, r.wiw_1sig_pct)
m = db.scalar(select(ProcessMoDepRun).where(ProcessMoDepRun.run_no=='F0426B16'))
print('Mo F0426B16:', m.ar_flow_sccm, m.dc_power_kw, m.thk_nm, m.rs_ohm_sq)
a = db.scalar(select(ProcessAlnDepRun).where(ProcessAlnDepRun.run_no=='0623A07'))
print('AlSc 0623A07:', a.recipe_id, a.thk_nm, a.stress_avg_mpa)
"
```

预期（对照 Excel 原值，wiw 已 ×100）：
- `ICP F0425LW1: 98.0 24.199... 395.257... 2.9128...`
- `Mo F0426B16: 10.0 7.0 130.75... 0.8659...`
- `AlSc 0623A07: DOE26-3A-180 3472.0 196.7`

- [ ] **步骤 4：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api
git add backend/scripts/import_process_excel.py
git commit -m "feat(backend): 工艺参数 Excel 一次性导入脚本

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 5：API 端点

**文件：**
- 创建：`backend/app/api/process.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/api/test_process.py`

- [ ] **步骤 1：编写失败的测试**

创建 `backend/tests/api/test_process.py`（复用 `tests/api/conftest.py` 的 `client` / `db` fixture）：

```python
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

    db.add(ProcessMoDepRun(run_no="F0426B16", group_label="DOE2", ar_flow_sccm=10.0, dc_power_kw=7.0))
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
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/backend
uv run pytest tests/api/test_process.py -v
```

预期：FAIL，404（路由未注册）。

- [ ] **步骤 3：实现 API**

创建 `backend/app/api/process.py`：

```python
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
```

修改 `backend/app/main.py`：
- 第 30 行 import 列表改为：`from app.api import batches, devices, export, files, mappings, process, query, system, tasks, upload`
- 在 `app.include_router(export.router, prefix="/api")` 之后加一行：`app.include_router(process.router, prefix="/api")`

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/api/test_process.py -v
```

预期：4 个测试全部 PASS。

- [ ] **步骤 5：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api
git add backend/app/api/process.py backend/app/main.py backend/tests/api/test_process.py
git commit -m "feat(backend): /api/process 只读查询端点

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 6：前端类型 + API wrapper + 表格工具函数（TDD）

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/api/endpoints.ts`
- 创建：`frontend/src/utils/processTable.ts`
- 测试：`frontend/src/utils/processTable.test.ts`

- [ ] **步骤 1：配置 vitest**

`frontend/vite.config.ts` 第一行改为从 vitest 导入 defineConfig 并加 test 配置：

```typescript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: './',
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
  },
  server: {
    // ……保持原有 server / build 配置不变……
  },
});
```

（即：仅替换 import 来源并在 plugins 后插入 test 块，其余配置原样保留。）

安装依赖：

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/frontend
npm install -D vitest jsdom @testing-library/react
```

`frontend/package.json` scripts 增加：`"test": "vitest run"`。

- [ ] **步骤 2：编写失败的测试**

创建 `frontend/src/utils/processTable.test.ts`：

```typescript
import { describe, expect, it } from 'vitest';
import { applyFilters, fmtValue, sortRows, toCsv, type Filters } from './processTable';
import type { ProcessFieldMeta } from '../types';

const fields: ProcessFieldMeta[] = [
  { name: 'run_no', label: '编号', unit: null, kind: 'text' },
  { name: 'thk_nm', label: '厚度', unit: 'nm', kind: 'numeric' },
];

const rows = [
  { run_no: 'F0425LW1', thk_nm: 395.3 },
  { run_no: 'F0425LW2', thk_nm: null },
  { run_no: 'F0518HW1', thk_nm: 130.8 },
];

describe('applyFilters', () => {
  it('无筛选条件时返回全部行', () => {
    expect(applyFilters(rows, fields, {})).toHaveLength(3);
  });

  it('数值 min/max 过滤，null 值被排除', () => {
    const filters: Filters = { thk_nm: { min: '200' } };
    const result = applyFilters(rows, fields, filters);
    expect(result.map((r) => r.run_no)).toEqual(['F0425LW1']);
    const ranged = applyFilters(rows, fields, { thk_nm: { min: '100', max: '400' } });
    expect(ranged).toHaveLength(2);
  });

  it('空字符串条件不生效', () => {
    expect(applyFilters(rows, fields, { thk_nm: { min: '', max: '' } })).toHaveLength(3);
  });

  it('文本包含匹配（大小写不敏感）', () => {
    const result = applyFilters(rows, fields, { run_no: { text: 'hw' } });
    expect(result.map((r) => r.run_no)).toEqual(['F0518HW1']);
  });
});

describe('sortRows', () => {
  it('数值升序，null 恒排最后', () => {
    const sorted = sortRows(rows, 'thk_nm', 'asc');
    expect(sorted.map((r) => r.run_no)).toEqual(['F0518HW1', 'F0425LW1', 'F0425LW2']);
  });

  it('数值降序，null 恒排最后', () => {
    const sorted = sortRows(rows, 'thk_nm', 'desc');
    expect(sorted.map((r) => r.run_no)).toEqual(['F0425LW1', 'F0518HW1', 'F0425LW2']);
  });

  it('文本排序', () => {
    const sorted = sortRows(rows, 'run_no', 'asc');
    expect(sorted.map((r) => r.run_no)).toEqual(['F0425LW1', 'F0425LW2', 'F0518HW1']);
  });

  it('sortKey 为 null 时原样返回', () => {
    expect(sortRows(rows, null, 'asc')).toBe(rows);
  });
});

describe('toCsv', () => {
  it('生成带 BOM、带单位表头的 CSV', () => {
    const csv = toCsv(rows.slice(0, 1), fields);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    expect(csv).toContain('编号,厚度(nm)');
    expect(csv).toContain('F0425LW1,395.3');
  });

  it('转义逗号/引号/换行，null 输出空串', () => {
    const csv = toCsv([{ run_no: 'a,"b"\nc', thk_nm: null }], fields);
    expect(csv).toContain('"a,""b""\nc",');
    expect(csv.trimEnd().endsWith(',')).toBe(true);
  });
});

describe('fmtValue', () => {
  it('整数原样，小数保留 4 位，空值输出占位符', () => {
    expect(fmtValue(98)).toBe('98');
    expect(fmtValue(24.199416909)).toBe('24.1994');
    expect(fmtValue(null)).toBe('—');
    expect(fmtValue('F0425LW1')).toBe('F0425LW1');
  });
});
```

- [ ] **步骤 3：运行测试验证失败**

```bash
npx vitest run src/utils/processTable.test.ts
```

预期：FAIL，`Cannot find module './processTable'`（同时确认 vitest 本身可运行）。

- [ ] **步骤 4：实现类型 + 工具函数 + endpoints**

`frontend/src/types/index.ts` 末尾追加：

```typescript
export interface ProcessFieldMeta {
  name: string;
  label: string;
  unit?: string | null;
  kind: 'numeric' | 'text';
}

export interface ProcessStep {
  key: string;
  label: string;
  row_count: number;
}

export interface ProcessStepData {
  fields: ProcessFieldMeta[];
  rows: Record<string, string | number | null>[];
}
```

创建 `frontend/src/utils/processTable.ts`：

```typescript
import type { ProcessFieldMeta } from '../types';

export type Row = Record<string, string | number | null>;
export interface FilterCond {
  min?: string;
  max?: string;
  text?: string;
}
export interface Filters {
  [field: string]: FilterCond;
}
export type SortDir = 'asc' | 'desc';

/** 客户端筛选：数值列 min/max，文本列包含匹配；null 值在任何生效条件下都被排除。 */
export function applyFilters(rows: Row[], fields: ProcessFieldMeta[], filters: Filters): Row[] {
  return rows.filter((row) =>
    fields.every((f) => {
      const cond = filters[f.name];
      if (!cond) return true;
      const v = row[f.name];
      if (f.kind === 'numeric') {
        const hasMin = cond.min !== undefined && cond.min !== '';
        const hasMax = cond.max !== undefined && cond.max !== '';
        if (!hasMin && !hasMax) return true;
        if (v === null || v === undefined || v === '') return false;
        const num = Number(v);
        if (hasMin && num < Number(cond.min)) return false;
        if (hasMax && num > Number(cond.max)) return false;
        return true;
      }
      const t = (cond.text ?? '').trim().toLowerCase();
      if (!t) return true;
      return String(v ?? '').toLowerCase().includes(t);
    }),
  );
}

/** 客户端排序：null 恒排最后（与方向无关）。 */
export function sortRows(rows: Row[], key: string | null, dir: SortDir): Row[] {
  if (!key) return rows;
  const mul = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = a[key];
    const vb = b[key];
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * mul;
    return String(va).localeCompare(String(vb), 'zh') * mul;
  });
}

/** 生成 CSV（含 BOM，Excel 可直接打开；表头带单位）。 */
export function toCsv(rows: Row[], fields: ProcessFieldMeta[]): string {
  const esc = (v: string | number | null | undefined): string => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = fields.map((f) => esc(f.unit ? `${f.label}(${f.unit})` : f.label)).join(',');
  const lines = rows.map((r) => fields.map((f) => esc(r[f.name])).join(','));
  return '﻿' + [header, ...lines].join('\n');
}

/** 表格显示格式化：整数原样，小数最多 4 位，空值占位符。 */
export function fmtValue(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(+v.toFixed(4));
  return v;
}
```

注意：`toCsv` 中 `'﻿'` 是 BOM 字符（U+FEFF），写文件时确保它是 `﻿`——更稳妥的写法是 `return '﻿' + [header, ...lines].join('\n');`。

`frontend/src/api/endpoints.ts` 追加（import 行补上 `ProcessStep, ProcessStepData` 类型）：

```typescript
export const listProcessSteps = () =>
  api.get('/process/steps').then((r: AxiosResponse<ProcessStep[]>) => r.data);
export const getProcessStep = (step: string) =>
  api.get(`/process/${step}`).then((r: AxiosResponse<ProcessStepData>) => r.data);
```

- [ ] **步骤 5：运行测试验证通过**

```bash
npx vitest run src/utils/processTable.test.ts
```

预期：全部 PASS。

- [ ] **步骤 6：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api
git add frontend/src/types/index.ts frontend/src/api/endpoints.ts frontend/src/utils/processTable.ts frontend/src/utils/processTable.test.ts frontend/vite.config.ts frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): 工艺数据类型/API wrapper/表格工具函数 + vitest 引入

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 7：前端 Process 页面 + 路由 + 导航

**文件：**
- 创建：`frontend/src/pages/Process.tsx`
- 测试：`frontend/src/pages/Process.test.tsx`
- 修改：`frontend/src/App.tsx`
- 修改：`frontend/src/components/Sidebar.tsx`

- [ ] **步骤 1：编写失败的测试**

创建 `frontend/src/pages/Process.test.tsx`：

```tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Process from './Process';

vi.mock('../api/endpoints', () => ({
  listProcessSteps: vi.fn().mockResolvedValue([
    { key: 'icp_etch', label: 'ICP 刻蚀', row_count: 2 },
    { key: 'mo_dep', label: 'Mo 生长', row_count: 1 },
  ]),
  getProcessStep: vi.fn().mockResolvedValue({
    fields: [
      { name: 'run_no', label: '编号', unit: null, kind: 'text' },
      { name: 'etch_time_sec', label: '刻蚀时间', unit: 's', kind: 'numeric' },
    ],
    rows: [
      { run_no: 'F0425LW1', etch_time_sec: 98 },
      { run_no: 'F0518HW3', etch_time_sec: 43 },
    ],
  }),
}));

describe('Process 页面', () => {
  it('渲染工序 tab 并加载首个工序数据', async () => {
    render(<Process />);
    expect(await screen.findByText('F0425LW1')).toBeTruthy();
    expect(screen.getByText('Mo 生长 (1)')).toBeTruthy();
    expect(screen.getByText('共 2 行')).toBeTruthy();
  });

  it('文本筛选过滤行', async () => {
    render(<Process />);
    await screen.findByText('F0425LW1');
    fireEvent.click(screen.getByTitle('筛选'));
    fireEvent.change(screen.getByPlaceholderText('编号 包含'), { target: { value: 'HW' } });
    await waitFor(() => {
      expect(screen.queryByText('F0425LW1')).toBeNull();
      expect(screen.getByText('F0518HW3')).toBeTruthy();
    });
  });

  it('点击表头切换排序', async () => {
    render(<Process />);
    await screen.findByText('F0425LW1');
    const cells = () => screen.getAllByRole('row').slice(1).map((r) => r.textContent);
    expect(cells()[0]).toContain('F0425LW1');
    fireEvent.click(screen.getByText(/刻蚀时间/));
    fireEvent.click(screen.getByText(/刻蚀时间/)); // 第二次：降序
    await waitFor(() => {
      expect(cells()[0]).toContain('F0425LW1'); // 98 降序在前
    });
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

```bash
npx vitest run src/pages/Process.test.tsx
```

预期：FAIL，`Cannot find module './Process'`。

- [ ] **步骤 3：实现页面**

创建 `frontend/src/pages/Process.tsx`：

```tsx
import React, { useEffect, useMemo, useState } from 'react';
import I from '../components/Icons';
import { getProcessStep, listProcessSteps } from '../api/endpoints';
import type { ProcessFieldMeta, ProcessStep, ProcessStepData } from '../types';
import {
  applyFilters,
  fmtValue,
  sortRows,
  toCsv,
  type Filters,
  type Row,
  type SortDir,
} from '../utils/processTable';

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Process() {
  const [steps, setSteps] = useState<ProcessStep[]>([]);
  const [active, setActive] = useState<string>('');
  const [data, setData] = useState<ProcessStepData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [showFilters, setShowFilters] = useState(false);

  useEffect(() => {
    listProcessSteps()
      .then((s) => {
        setSteps(s);
        if (s.length > 0) setActive((cur) => cur || s[0].key);
      })
      .catch((e) => setError(`加载工序列表失败: ${e?.message ?? e}`));
  }, []);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    setError(null);
    setFilters({});
    setSortKey(null);
    setSortDir('asc');
    getProcessStep(active)
      .then(setData)
      .catch((e) => setError(`加载工艺数据失败: ${e?.response?.data?.detail ?? e?.message ?? e}`))
      .finally(() => setLoading(false));
  }, [active]);

  const fields: ProcessFieldMeta[] = data?.fields ?? [];
  const rows: Row[] = useMemo(() => data?.rows ?? [], [data]);
  const viewRows = useMemo(
    () => sortRows(applyFilters(rows, fields, filters), sortKey, sortDir),
    [rows, fields, filters, sortKey, sortDir],
  );

  const toggleSort = (name: string) => {
    if (sortKey !== name) {
      setSortKey(name);
      setSortDir('asc');
    } else if (sortDir === 'asc') {
      setSortDir('desc');
    } else {
      setSortKey(null);
    }
  };

  const setFilter = (name: string, patch: { min?: string; max?: string; text?: string }) =>
    setFilters((f) => ({ ...f, [name]: { ...f[name], ...patch } }));

  return (
    <div className="page" style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="page-head" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px' }}>
        <span className="page-title" style={{ fontSize: 15, fontWeight: 600 }}>工艺数据</span>
        <div style={{ display: 'flex', gap: 4, marginLeft: 16 }}>
          {steps.map((s) => (
            <button
              key={s.key}
              className={`btn ghost sm${active === s.key ? ' primary' : ''}`}
              onClick={() => setActive(s.key)}
            >
              {s.label} ({s.row_count})
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn ghost sm" title="筛选" onClick={() => setShowFilters((v) => !v)}>
          <I.filter size={14} /> 筛选
        </button>
        <button
          className="btn ghost sm"
          title="导出当前筛选结果为 CSV"
          onClick={() => downloadText(`process_${active}.csv`, toCsv(viewRows, fields))}
          disabled={viewRows.length === 0}
        >
          <I.download size={14} /> 导出 CSV
        </button>
      </div>

      {error && <div style={{ padding: '8px 16px', color: 'var(--danger, #e5534b)' }}>{error}</div>}

      {showFilters && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
            gap: 8,
            padding: '8px 16px',
            borderTop: '1px solid var(--line, #2a2f3a)',
          }}
        >
          {fields.map((f) =>
            f.kind === 'numeric' ? (
              <div key={f.name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 12, color: 'var(--fg-3)', minWidth: 56 }}>{f.label}</span>
                <input
                  style={{ width: 52 }}
                  placeholder="最小"
                  value={filters[f.name]?.min ?? ''}
                  onChange={(e) => setFilter(f.name, { min: e.target.value })}
                />
                <span style={{ color: 'var(--fg-3)' }}>~</span>
                <input
                  style={{ width: 52 }}
                  placeholder="最大"
                  value={filters[f.name]?.max ?? ''}
                  onChange={(e) => setFilter(f.name, { max: e.target.value })}
                />
              </div>
            ) : (
              <div key={f.name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 12, color: 'var(--fg-3)', minWidth: 56 }}>{f.label}</span>
                <input
                  style={{ flex: 1 }}
                  placeholder={`${f.label} 包含`}
                  value={filters[f.name]?.text ?? ''}
                  onChange={(e) => setFilter(f.name, { text: e.target.value })}
                />
              </div>
            ),
          )}
        </div>
      )}

      <div style={{ padding: '4px 16px', fontSize: 12, color: 'var(--fg-3)' }}>
        共 {rows.length} 行{viewRows.length !== rows.length ? `，筛选后 ${viewRows.length} 行` : ''}
        {loading ? '，加载中…' : ''}
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '0 16px 16px' }}>
        <table className="dtable">
          <thead>
            <tr>
              {fields.map((f) => (
                <th
                  key={f.name}
                  onClick={() => toggleSort(f.name)}
                  style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}
                  title="点击排序"
                >
                  {f.label}
                  {f.unit ? ` (${f.unit})` : ''}
                  {sortKey === f.name ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {viewRows.map((row, i) => (
              <tr key={i}>
                {fields.map((f) => (
                  <td key={f.name} className={f.kind === 'numeric' ? 'num' : undefined}>
                    {fmtValue(row[f.name])}
                  </td>
                ))}
              </tr>
            ))}
            {!loading && viewRows.length === 0 && (
              <tr>
                <td colSpan={fields.length} style={{ textAlign: 'center', color: 'var(--fg-3)' }}>
                  暂无数据
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

修改 `frontend/src/App.tsx`：
- lazy import 区（`const Impedance` 之后）加：`const Process      = React.lazy(() => import('./pages/Process'));`
- Routes 中 `<Route path="/impedance" .../>` 之后加：`<Route path="/process" element={<Process />} />`

修改 `frontend/src/components/Sidebar.tsx` NAV 数组，在 `对照表` 项之后插入：

```typescript
  { to: '/process', icon: I.wafer, label: '工艺数据' },
```

- [ ] **步骤 4：运行测试验证通过**

```bash
npx vitest run src/pages/Process.test.tsx src/utils/processTable.test.ts
npm run typecheck
```

预期：vitest 全部 PASS；`tsc --noEmit` 无错误。

- [ ] **步骤 5：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api
git add frontend/src/pages/Process.tsx frontend/src/pages/Process.test.tsx frontend/src/App.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat(frontend): 工艺数据页面（tab 浏览+筛选+排序+CSV 导出）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 8：后端全量回归

- [ ] **步骤 1：ruff + 全量 pytest**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/backend
uv run ruff check .
uv run pytest
```

预期：ruff 无输出（通过）；pytest 全绿（integration 标记默认跳过）。

- [ ] **步骤 2：若有失败，修复后重新运行（保持 TDD 纪律：先定位原因，不压制测试）**

---

## 任务 9：联合测试（真实 Excel → DB → API → 浏览器）

- [ ] **步骤 1：真实 Excel 导入 sqlite（如任务 4 步骤 2 未做则执行；已做则复用 /tmp/aln_proc_e2e.db）**

- [ ] **步骤 2：启动 API 并验证端点**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/backend
DATABASE_URL="sqlite:////tmp/aln_proc_e2e.db" WATCH_ENABLED=false uv run uvicorn app.main:app --port 8765 &
sleep 3
curl -s http://127.0.0.1:8765/api/process/steps
curl -s http://127.0.0.1:8765/api/process/icp_etch | uv run python -c "import json,sys; d=json.load(sys.stdin); print(len(d['fields']), 'fields,', len(d['rows']), 'rows'); print(d['rows'][0])"
curl -s http://127.0.0.1:8765/api/process/nope
```

预期：
- steps 返回三个工序及真实行数（与导入摘要一致）
- icp_etch 首行 `run_no == 'F0425LW1'`，`wiw_1sig_pct ≈ 2.9128`
- 未知 step 返回 `{"detail": "未知工序: nope"}`

- [ ] **步骤 3：前端 dev server + 浏览器验证（webapp-testing 技能 / Playwright）**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/frontend
# vite.config.ts 代理指向 8000；此处改用 8765 需临时指定代理目标：
VITE_API_TARGET=http://localhost:8765 npx vite --port 5199
```

若 vite.config 不支持 VITE_API_TARGET 环境变量，则直接改 `server.proxy['/api'].target` 为 `http://localhost:8765`（验证后改回，或保留环境变量化改动并 commit）。

用 Playwright 打开 `http://localhost:5199/process`：
1. 页面显示"工艺数据"标题与三个 tab（ICP 刻蚀 / Mo 生长 / AlN 生长，带行数）
2. 默认 tab 表格显示 F0425LW1 等行
3. 切到 Mo 生长 tab，显示 F0426B16
4. 打开筛选，编号输入 `F0518`，行数减少
5. 点击"厚度"表头，顺序变化
6. 点击"导出 CSV"，下载文件，用文本编辑器确认 BOM + 表头 + 数据行
7. 截图存档 `/tmp/process-page.png`

- [ ] **步骤 4：前端生产构建**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api/frontend
npm run build
```

预期：`dist/` 构建成功（含 electron main 编译）。

- [ ] **步骤 5：停掉验证用进程（uvicorn 8765、vite 5199）**

---

## 任务 10：文档更新

**文件：**
- 修改：`docs/database-schema.md`
- 修改：`docs/api.md`

- [ ] **步骤 1：`docs/database-schema.md`** 在表清单与建表顺序附近追加一节"工艺参数表（process_*_runs）"，说明三张表用途、列清单（可引用规格文档 §4）、"独立数据域，暂不关联 devices"的决策，并注明来源与导入脚本。

- [ ] **步骤 2：`docs/api.md`** 在端点清单追加：

```markdown
### GET `/api/process/steps`

三个工序（icp_etch / mo_dep / aln_dep）的 key、中文名、行数。

### GET `/api/process/{step}`

某工序全部行的字段元数据（中文标签/单位/类型）与数据。未知 step → 404。
```

- [ ] **步骤 3：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-api
git add docs/database-schema.md docs/api.md
git commit -m "docs: 工艺参数表结构与 /api/process 端点

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 11：合并回 main

在 master（`/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master`）执行：

- [ ] **步骤 1：合并**

```bash
cd /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master
git merge feat/backend-api --no-ff -m "merge: 工艺参数数据域（三表+导入+API+前端页面）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **步骤 2：合并后全量验证（master 环境）**

```bash
cd /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/backend
uv sync && uv run ruff check . && uv run pytest
cd ../frontend && npm install && npm run typecheck && npx vitest run && npm run build
```

预期：全部通过。

- [ ] **步骤 3：真实 Excel 导入桌面版 sqlite（可选但推荐）**

```bash
cd /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/backend
ALN_DESKTOP_MODE=true uv run python scripts/import_process_excel.py "../副本SICM_AlN_DT.xlsx" --replace
```

预期：桌面库 `~/.aln-data/aln-data.db` 三表导入成功（桌面模式自动 create_all 新表）。

---

## 任务 12：桌面打包

在 master 执行：

- [ ] **步骤 1：完整构建**

```bash
cd /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master
python build.py --target mac
```

预期：前端构建 → PyInstaller 后端 → electron-builder 输出安装包到 `frontend/release/`。若 electron-builder 因签名/网络失败，回退 `python build.py --target mac --skip-backend` 验证前端+Electron 打包链路，并记录失败原因。

- [ ] **步骤 2：验证产物存在并汇报路径与体积**

```bash
ls -lh frontend/release/
```

---

## 自检结果（编写时已核对）

- **规格覆盖**：§4 三表 → 任务 2/3；§5 解析规则 → 任务 1（每条规则有对应测试）；§6 导入脚本 → 任务 4；§7 API → 任务 5；§8 前端 → 任务 6/7；§9 错误处理 → 各任务测试与步骤；§10 测试与验证 → 任务 1/2/5/6/7/8/9/11；§11 范围外 → 未包含；§12 worktree/文档 → 任务 0/10/11。用户追加要求（后端+前端+联合测试、打包）→ 任务 6/7/9/12。
- **类型一致性**：解析器输出 dict 键与模型列名一致（`block_index`/`source_sheet` 仅对应表有）；API fields 的 name 与模型属性一致；前端 Row 键与 API fields.name 一致。
- **占位符**：无。
