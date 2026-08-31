# 静态网页版平台 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把数据平台做成纯静态网站（GitHub Pages）：数据随 `build_web.py` 一条命令从桌面 sqlite/训练产物快照同步；代理模型在浏览器端用 ONNX 推理。

**架构：** 构建时导出 Parquet/JSON 快照 + torch→ONNX；运行时零后端：DuckDB-WASM 查 Parquet、onnxruntime-web 跑模型，两者懒加载。规格：`docs/superpowers/specs/2026-08-18-static-web-platform-design.md`。

**技术栈：** Python（pyarrow/onnx/onnxruntime）、React+TS、@duckdb/duckdb-wasm、onnxruntime-web、vitest。

**工作区：** 新 worktree `/Users/jingbozuo/Projects/aln-data-static-web`（分支 `feat/static-web`，从 main 切）。所有相对路径基于该 worktree。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/scripts/export_web_snapshot.py` | sqlite + surrogate_data → `frontend/public/webdata/` 快照 |
| `backend/scripts/export_surrogate_onnx.py` | torch → ONNX 导出 + 数值对齐校验 |
| `backend/scripts/gen_web_golden.py` | 生成 TS 移植的黄金值夹具 JSON |
| `backend/tests/web/test_web_snapshot.py` | 快照导出测试 |
| `backend/tests/web/test_onnx_export.py` | ONNX 导出/对齐测试 |
| `frontend/src/ml/sampling.ts` | 确定性采样位置 TS 移植 |
| `frontend/src/ml/baseline.ts` | 基准表查找 TS 移植 |
| `frontend/src/ml/spline.ts` | not-a-knot 三次样条 TS 移植 |
| `frontend/src/ml/surrogateOnnx.ts` | 浏览器端两阶段推理编排 |
| `frontend/src/ml/__tests__/*.test.ts` | 黄金值对拍测试（vitest） |
| `frontend/src/api/static/duckdb.ts` | DuckDB-WASM 懒单例 + Parquet 注册 |
| `frontend/src/api/static/index.ts` | 与 endpoints.ts 同签名的静态实现 |
| `frontend/src/api/endpoints.ts` | 改为 IS_STATIC 分发（现有实现改名 http*） |
| `frontend/src/config.ts` | `export const IS_STATIC = import.meta.env.VITE_STATIC === '1'` |
| `frontend/src/components/Sidebar.tsx` `App.tsx` `pages/BatchDetail.tsx` `pages/Surrogate.tsx` | 静态模式 UI 裁剪/接线 |
| `build_web.py`（仓库根） | 编排：快照 → ONNX → 前端构建 → gh-pages 部署 |

---

## 任务 0：worktree 与依赖

- [ ] **步骤 1：建 worktree**

```bash
cd /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master
git worktree add /Users/jingbozuo/Projects/aln-data-static-web -b feat/static-web main
```

- [ ] **步骤 2：后端依赖（复用 master venv，避开 zipfile-deflate64 源码编译坑）**

```bash
cd /Users/jingbozuo/Projects/aln-data-static-web/backend
UV_PROJECT_ENVIRONMENT=/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/backend/.venv \
  uv add --group dev pyarrow onnx onnxruntime
```

注意：这会把包装进 master 的 venv（无害：PyInstaller 只按导入图打包，onnx 不会被桌面版带进去）；分支合并后 dev 组依赖随 pyproject 进 main。

- [ ] **步骤 3：前端依赖（worktree 独立 node_modules）**

```bash
cd /Users/jingbozuo/Projects/aln-data-static-web/frontend
npm install   # 先装基线依赖（网络慢，可后台）
npm install @duckdb/duckdb-wasm onnxruntime-web
```

- [ ] **步骤 4：Commit**

```bash
git add backend/pyproject.toml backend/uv.lock frontend/package.json frontend/package-lock.json
git commit -m "chore: static-web worktree 依赖（pyarrow/onnx + duckdb-wasm/onnxruntime-web）"
```

---

## 任务 1：快照导出器 `export_web_snapshot.py`

**文件：**
- 创建：`backend/scripts/export_web_snapshot.py`
- 测试：`backend/tests/web/test_web_snapshot.py`（先 `mkdir backend/tests/web` 并放空 `__init__.py`）

导出内容（到 `frontend/public/webdata/`）：`devices.parquet`、`batches.json`、`mappings.json`（含 entries）、`process.json`、`stats.json`、`fields.json`、`manifest.json`。

- [ ] **步骤 1：编写失败的测试**

```python
# backend/tests/web/test_web_snapshot.py
"""快照导出测试：小 sqlite → parquet/json 契约。"""

import json
import sqlite3
from pathlib import Path

import pyarrow.parquet as pq
import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from export_web_snapshot import export_all  # noqa: E402

DEVICE_COLS = (
    "id,batch_id,original_filename,display_name,mark,wafer,folder_name,coord,x,y,"
    "eg,fl,ag,pf,area_n,area_um2,fs_ghz,fp_ghz,zs_ohm,zp_ohm,qs,qp,qs_bodeq,qp_bodeq,"
    "dbqs,dbqp,bodeq_fitted,bodeq_smooth,bodeq_raw,fbode_ghz,k2eff_pct,fp2_ghz,fs2_ghz,"
    "zp2_ohm,zs2_ohm,deembedded,s_param_path,s_param_port"
)


@pytest.fixture()
def mini_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE batches (id INTEGER PRIMARY KEY, batch_no TEXT UNIQUE NOT NULL,"
                 " mapping_id INTEGER, f_start_ghz REAL, f_end_ghz REAL, deembedded INTEGER DEFAULT 0,"
                 " uploaded_at TEXT DEFAULT '', device_count INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE mappings (id INTEGER PRIMARY KEY, name TEXT, uploaded_at TEXT DEFAULT '')")
    conn.execute("CREATE TABLE mapping_entries (id INTEGER PRIMARY KEY, mapping_id INTEGER,"
                 " mark TEXT, description TEXT, eg REAL, fl REAL, ag REAL,"
                 " area_s11 INTEGER, area_s22 INTEGER, has_pf INTEGER DEFAULT 0)")
    conn.execute(f"CREATE TABLE devices ({', '.join(c + ' TEXT' for c in DEVICE_COLS.split(','))})")
    conn.execute("INSERT INTO batches VALUES (1, '#T', 1, NULL, NULL, 0, '2026-01-01', 2)")
    conn.execute("INSERT INTO mappings VALUES (1, 'M1', '')")
    conn.execute("INSERT INTO mapping_entries VALUES (1, 1, 'A1', 'EG1', 1.0, 0.5, NULL, 700, 5500, 0)")
    for i in (1, 2):
        vals = {c: None for c in DEVICE_COLS.split(",")}
        vals.update({"id": i, "batch_id": 1, "original_filename": f"f{i}.s1p",
                     "mark": "A1", "eg": 1.0, "fl": 0.5, "area_um2": 700, "pf": "N",
                     "fs_ghz": 4.8 + i * 0.001, "fp_ghz": 5.1, "k2eff_pct": 7.0, "qs": 500})
        conn.execute(
            f"INSERT INTO devices VALUES ({', '.join('?' for _ in DEVICE_COLS.split(','))})",
            [vals[c] for c in DEVICE_COLS.split(",")],
        )
    conn.commit()
    conn.close()
    return db


def test_export_all(mini_db, tmp_path):
    out = tmp_path / "webdata"
    export_all(mini_db, surrogate_dir=None, out_dir=out)
    t = pq.read_table(out / "devices.parquet")
    assert t.num_rows == 2
    assert "batch_no" in t.column_names and "s_param_path" not in t.column_names
    assert t.column("batch_no").to_pylist() == ["#T", "#T"]
    batches = json.loads((out / "batches.json").read_text())
    assert batches[0]["batch_no"] == "#T"
    maps = json.loads((out / "mappings.json").read_text())
    assert maps[0]["entries"][0]["mark"] == "A1"
    stats = json.loads((out / "stats.json").read_text())
    assert stats["total_devices"] == 2
    fields = json.loads((out / "fields.json").read_text())
    assert any(f["name"] == "batch_no" for f in fields["categorical"])
    manifest = json.loads((out / "manifest.json").read_text())
    assert "devices.parquet" in manifest["files"]
```

- [ ] **步骤 2：运行验证失败**

```bash
cd backend && uv run pytest tests/web/test_web_snapshot.py -q
```

预期：FAIL `ModuleNotFoundError`

- [ ] **步骤 3：实现**

```python
# backend/scripts/export_web_snapshot.py
"""静态网页版数据快照导出（规格 §3）。

从桌面 sqlite（~/.aln-data/aln-data.db）导出 Parquet/JSON 到 frontend/public/webdata/。
只读源库；可选并入代理模型数据（任务 2）。

用法：
    uv run python scripts/export_web_snapshot.py \
        --db ~/.aln-data/aln-data.db --out ../frontend/public/webdata
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# devices 排除列：原文件路径（静态版无原文件查看）
DEVICE_EXCLUDE = {"s_param_path"}

DEVICE_COLS = [
    "id", "batch_id", "original_filename", "display_name", "mark", "wafer", "folder_name",
    "coord", "x", "y", "eg", "fl", "ag", "pf", "area_n", "area_um2",
    "fs_ghz", "fp_ghz", "zs_ohm", "zp_ohm", "qs", "qp", "qs_bodeq", "qp_bodeq",
    "dbqs", "dbqp", "bodeq_fitted", "bodeq_smooth", "bodeq_raw", "fbode_ghz",
    "k2eff_pct", "fp2_ghz", "fs2_ghz", "zp2_ohm", "zs2_ohm", "deembedded", "s_param_port",
]

BATCH_COLS = ["id", "batch_no", "mapping_id", "f_start_ghz", "f_end_ghz",
              "deembedded", "uploaded_at", "device_count"]
ENTRY_COLS = ["id", "mapping_id", "mark", "description", "eg", "fl", "ag",
              "area_s11", "area_s22", "has_pf"]


def _rows(conn: sqlite3.Connection, table: str, cols: list[str]) -> list[dict]:
    cur = conn.execute(f"SELECT {', '.join(cols)} FROM {table}")
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def export_all(db_path: str | Path, surrogate_dir: str | Path | None,
               out_dir: str | Path) -> dict:
    import pandas as pd  # 延迟导入：脚本级依赖

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    written: list[str] = []

    # devices.parquet：JOIN batches 拍平 batch_no
    cols = [c for c in DEVICE_COLS if c not in DEVICE_EXCLUDE]
    select_cols = ", ".join(f"d.{c}" for c in cols)
    df = pd.read_sql_query(
        f"SELECT {select_cols}, b.batch_no FROM devices d"
        " JOIN batches b ON d.batch_id = b.id ORDER BY d.id",
        conn,
    )
    df.to_parquet(out / "devices.parquet", compression="zstd", index=False)
    written.append("devices.parquet")

    # batches.json
    batches = _rows(conn, "batches", BATCH_COLS)
    (out / "batches.json").write_text(
        json.dumps(batches, ensure_ascii=False), encoding="utf-8")
    written.append("batches.json")

    # mappings.json（含 entries）
    mappings = _rows(conn, "mappings", ["id", "name", "uploaded_at"])
    entries = _rows(conn, "mapping_entries", ENTRY_COLS)
    by_mid: dict[int, list[dict]] = {}
    for e in entries:
        by_mid.setdefault(e["mapping_id"], []).append(e)
    for m in mappings:
        m["entries"] = by_mid.get(m["id"], [])
        m["entry_count"] = len(m["entries"])
    (out / "mappings.json").write_text(
        json.dumps(mappings, ensure_ascii=False), encoding="utf-8")
    written.append("mappings.json")

    # process.json（复用 api.process 的 STEPS 元数据；表不存在则跳过）
    from app.api.process import STEPS

    process: dict[str, dict] = {}
    for key, (model, label, fields) in STEPS.items():
        table = model.__tablename__
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        names = [f.name for f in fields]
        rows = _rows(conn, table, ["id", *names]) if "id" not in names else _rows(conn, table, names)
        process[key] = {
            "label": label,
            "fields": [f.model_dump() for f in fields],
            "rows": rows,
        }
    (out / "process.json").write_text(
        json.dumps(process, ensure_ascii=False, default=str), encoding="utf-8")
    written.append("process.json")

    # stats.json（对齐 /api/stats 的核心字段）
    n_dev = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    n_pass = conn.execute("SELECT COUNT(*) FROM devices WHERE pf='Y'").fetchone()[0]
    stats = {
        "total_batches": len(batches),
        "total_devices": n_dev,
        "pass_devices": n_pass,
        "pass_rate": (n_pass / n_dev) if n_dev else 0.0,
    }
    (out / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    written.append("stats.json")

    # fields.json（/api/query/fields 的静态等价）
    from app.api.deps import ALLOWED_QUERY_FIELDS  # noqa: F401 — 校验列存在性用

    fields = {
        "categorical": [
            {"name": "batch_no", "label": "批次号", "values": sorted({b["batch_no"] for b in batches})},
            {"name": "wafer", "label": "Wafer"},
            {"name": "pf", "label": "Pass/Fail", "values": ["Y", "N"]},
            {"name": "folder_name", "label": "端口", "values": ["S11", "S22"]},
        ],
        "geometric": [
            {"name": "x", "label": "X 坐标"}, {"name": "y", "label": "Y 坐标"},
        ],
        "numeric": [
            {"name": n, "label": n} for n in cols
            if n not in ("id", "batch_id", "original_filename", "display_name", "mark",
                         "coord", "folder_name", "pf", "deembedded", "s_param_port")
        ],
        "process": [
            {"name": "eg", "label": "EG"}, {"name": "fl", "label": "FL"},
            {"name": "ag", "label": "AG"},
        ],
    }
    (out / "fields.json").write_text(
        json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    written.append("fields.json")

    missing: list[str] = []
    if surrogate_dir is not None:
        from export_web_surrogate_data import export_surrogate_webdata

        w, m = export_surrogate_webdata(Path(surrogate_dir), out)
        written += w
        missing += m

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": written,
        "missing": missing,
        "counts": {"devices": int(n_dev), "batches": len(batches)},
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".aln-data/aln-data.db"))
    ap.add_argument("--surrogate-dir", default=str(PROJECT_ROOT / "surrogate_data"))
    ap.add_argument("--out", default=str(PROJECT_ROOT.parent / "frontend/public/webdata"))
    args = ap.parse_args()
    m = export_all(args.db, args.surrogate_dir, args.out)
    print(json.dumps(m["counts"], ensure_ascii=False), "→", args.out)


if __name__ == "__main__":
    main()
```

- [ ] **步骤 4：运行验证通过**

```bash
uv run pytest tests/web/test_web_snapshot.py -q   # 预期 1 passed（任务 2 完成后需解除 surrogate 依赖注释）
```

注意：步骤 3 引用的 `export_web_surrogate_data` 在任务 2 创建；本任务测试传 `surrogate_dir=None` 不触发。

- [ ] **步骤 5：Commit**

```bash
git add backend/scripts/export_web_snapshot.py backend/tests/web/
git commit -m "feat(web): 静态快照导出器（devices.parquet + 各 JSON + manifest）"
```

---

## 任务 2：代理模型 webdata 导出 `export_web_surrogate_data.py`

**文件：**
- 创建：`backend/scripts/export_web_surrogate_data.py`
- 测试：追加到 `backend/tests/web/test_web_snapshot.py`

从 `backend/surrogate_data/` 导出：`baseline.json`（pfs/areas/params_base/z_base_full 嵌套数组）、`surr_meta.json`（freq_ghz/k/归一化统计）、`cell_means.parquet`（长表：eg,fl,area,pf,idx,z；按格对 npz full_z 求均值）。

- [ ] **步骤 1：编写失败的测试（追加到 test_web_snapshot.py）**

```python
def test_export_surrogate_webdata(tmp_path):
    import numpy as np
    from export_web_surrogate_data import export_surrogate_webdata  # 见步骤 3 的 sys.path 处理

    sd = tmp_path / "surr"
    sd.mkdir()
    n_freq = 11
    np.savez_compressed(
        sd / "baseline.npz",
        pfs=np.array([0.0, 1.0]), areas=np.array([700.0, 1500.0]),
        params_base=np.tile(np.arange(7, dtype=np.float64), (2, 2, 1)),
        z_base_full=np.full((2, 2, n_freq), 40.0),
    )
    (sd / "meta.json").write_text(json.dumps({
        "freq_ghz": list(np.linspace(4.0, 7.0, n_freq)), "k": 4,
        "x_mean": [0.5] * 5, "x_std": [1.0] * 5,
        "params_mean": [0.0] * 7, "params_std": [1.0] * 7,
        "z_mean": [42.0], "z_std": [3.0]}))
    np.savez_compressed(
        sd / "dataset.npz",
        x_struct=np.array([[1.0, 0.5, 1.0, 700, 0], [1.0, 0.5, 1.0, 700, 0]], dtype=np.float32),
        full_z=np.stack([np.zeros(n_freq), np.full(n_freq, 2.0)]).astype(np.float32),
    )
    out = tmp_path / "webdata"
    out.mkdir()
    written, missing = export_surrogate_webdata(sd, out)
    assert missing == []
    assert set(written) == {"baseline.json", "surr_meta.json", "cell_means.parquet"}
    import pyarrow.parquet as pq
    t = pq.read_table(out / "cell_means.parquet")
    assert t.num_rows == n_freq  # 1 格 × 11 点
    assert t.column("z").to_pylist()[0] == 1.0  # mean(0, 2)
    bl = json.loads((out / "baseline.json").read_text())
    assert bl["areas"] == [700.0, 1500.0] and len(bl["z_base_full"]) == 2
```

（文件顶部加 `import json`、`import numpy as np` 与 sys.path 引导——同任务 1 测试。）

- [ ] **步骤 2：运行验证失败** — `uv run pytest tests/web -q`，预期 ModuleNotFoundError

- [ ] **步骤 3：实现**

```python
# backend/scripts/export_web_surrogate_data.py
"""代理模型静态网页数据导出：baseline.json / surr_meta.json / cell_means.parquet（规格 §3）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def export_surrogate_webdata(surrogate_dir: Path, out_dir: Path) -> tuple[list[str], list[str]]:
    """返回 (written, missing)。任一源文件缺失 → 全部跳过并记入 missing。"""
    need = [surrogate_dir / "baseline.npz", surrogate_dir / "meta.json",
            surrogate_dir / "dataset.npz"]
    if not all(p.exists() for p in need):
        return [], ["baseline.json", "surr_meta.json", "cell_means.parquet"]

    with np.load(surrogate_dir / "baseline.npz") as b:
        baseline = {k: b[k].tolist() for k in ("pfs", "areas", "params_base", "z_base_full")}
    (out_dir / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")

    meta = json.loads((surrogate_dir / "meta.json").read_text(encoding="utf-8"))
    surr_meta = {k: meta[k] for k in
                 ("freq_ghz", "k", "x_mean", "x_std", "params_mean", "params_std",
                  "z_mean", "z_std")}
    (out_dir / "surr_meta.json").write_text(json.dumps(surr_meta), encoding="utf-8")

    import pandas as pd

    with np.load(surrogate_dir / "dataset.npz") as z:
        xs, full_z = z["x_struct"], z["full_z"]
    n_freq = full_z.shape[1]
    # 按 (eg,fl,area,pf) 格求均值谱 → 长表
    keys = {}
    for i in range(len(xs)):
        key = (float(xs[i, 0]), float(xs[i, 1]), int(xs[i, 3]), int(xs[i, 4]))
        keys.setdefault(key, []).append(i)
    recs = []
    for (eg, fl, area, pf), idxs in sorted(keys.items()):
        mean_z = full_z[idxs].mean(axis=0)
        for j in range(n_freq):
            recs.append((eg, fl, area, pf, j, float(mean_z[j])))
    df = pd.DataFrame(recs, columns=["eg", "fl", "area", "pf", "idx", "z"])
    df.to_parquet(out_dir / "cell_means.parquet", compression="zstd", index=False)

    return ["baseline.json", "surr_meta.json", "cell_means.parquet"], []
```

- [ ] **步骤 4：运行验证通过** — `uv run pytest tests/web -q`，预期 2 passed

- [ ] **步骤 5：Commit** — `git add backend/scripts/export_web_surrogate_data.py backend/tests/web/ && git commit -m "feat(web): 代理模型 webdata 导出（baseline/surr_meta/cell_means）"`

---

## 任务 3：ONNX 导出 `export_surrogate_onnx.py`

**文件：**
- 创建：`backend/scripts/export_surrogate_onnx.py`
- 测试：`backend/tests/web/test_onnx_export.py`

Stage 1 需包一层（ONNX 不支持 dict 输出）；Stage 2 静态形状 K=256/N=1001。导出后立刻用 onnxruntime 在真实数据上对齐校验（|diff|<1e-3，归一化空间），不通过则退出非零。

- [ ] **步骤 1：编写失败的测试**

```python
# backend/tests/web/test_onnx_export.py
"""ONNX 导出契约：导出文件存在、输出形状/数值与 torch 一致。"""

import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from export_surrogate_onnx import export_stage1, export_stage2  # noqa: E402

from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402
from app.ml.surrogate.model import BaselineResidualMLP  # noqa: E402


def test_stage1_export(tmp_path):
    m = BaselineResidualMLP(in_dim=5, hidden=32, n_res_blocks=1, n_params=7, k=32).eval()
    out = export_stage1(m, tmp_path / "s1.onnx", k=32)
    import onnxruntime as ort
    sess = ort.InferenceSession(str(out))
    x = np.random.default_rng(0).normal(size=(3, 5)).astype(np.float32)
    pb = np.zeros((3, 7), dtype=np.float32)
    sb = np.zeros((3, 32), dtype=np.float32)
    p_o, s_o = sess.run(None, {"x": x, "params_base": pb, "sparse_base": sb})
    with torch.no_grad():
        ref = m(torch.from_numpy(x), torch.from_numpy(pb), torch.from_numpy(sb))
    assert p_o.shape == (3, 7) and s_o.shape == (3, 32)
    np.testing.assert_allclose(p_o, ref["params"].numpy(), atol=1e-5)


def test_stage2_export(tmp_path):
    m = SparseReconstructor(d_model=32, n_encoder_layers=1, n_heads=2, dropout=0.0).eval()
    out = export_stage2(m, tmp_path / "s2.onnx", k=32, n_freq=101)
    import onnxruntime as ort
    sess = ort.InferenceSession(str(out))
    rng = np.random.default_rng(1)
    cond = rng.normal(size=(2, 5)).astype(np.float32)
    samples = rng.normal(size=(2, 32, 2)).astype(np.float32)
    ft = np.tile(np.linspace(4.0, 7.0, 101, dtype=np.float32), (2, 1))
    zb = rng.normal(size=(2, 101)).astype(np.float32) + 40
    (o,) = sess.run(None, {"cond": cond, "samples": samples,
                           "freq_target": ft, "z_baseline": zb})
    with torch.no_grad():
        ref = m(torch.from_numpy(cond), torch.from_numpy(samples),
                torch.from_numpy(ft), z_baseline=torch.from_numpy(zb))
    assert o.shape == (2, 101)
    np.testing.assert_allclose(o, ref.numpy(), atol=1e-4)
```

- [ ] **步骤 2：运行验证失败** — 预期 ModuleNotFoundError

- [ ] **步骤 3：实现**

```python
# backend/scripts/export_surrogate_onnx.py
"""代理模型 torch → ONNX 导出 + 对齐校验（规格 §5.1）。

用法：
    uv run python scripts/export_surrogate_onnx.py \
        --ckpt ./checkpoints_surrogate --data ./surrogate_data \
        --out ../frontend/public/webdata
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402
from app.ml.surrogate.model import BaselineResidualMLP  # noqa: E402


class _Stage1Wrapped(nn.Module):
    """dict 输出 → 元组（ONNX 导出约束）。"""

    def __init__(self, m: BaselineResidualMLP) -> None:
        super().__init__()
        self.m = m

    def forward(self, x, params_base, sparse_base):
        out = self.m(x, params_base, sparse_base)
        return out["params"], out["sparse"]


def export_stage1(model: BaselineResidualMLP, out_path: str | Path, k: int) -> Path:
    w = _Stage1Wrapped(model).eval()
    args = (torch.zeros(1, 5), torch.zeros(1, 7), torch.zeros(1, k))
    torch.onnx.export(
        w, args, str(out_path), opset_version=17,
        input_names=["x", "params_base", "sparse_base"],
        output_names=["params", "sparse"],
        dynamic_axes={"x": {0: "b"}, "params_base": {0: "b"}, "sparse_base": {0: "b"},
                      "params": {0: "b"}, "sparse": {0: "b"}},
    )
    return Path(out_path)


def export_stage2(model: SparseReconstructor, out_path: str | Path, k: int,
                  n_freq: int) -> Path:
    model = model.eval()

    class _W(nn.Module):
        def forward(self, cond, samples, freq_target, z_baseline):
            return model(cond, samples, freq_target, z_baseline=z_baseline)

    args = (torch.zeros(1, 5), torch.zeros(1, k, 2),
            torch.zeros(1, n_freq), torch.zeros(1, n_freq))
    torch.onnx.export(
        _W(), args, str(out_path), opset_version=17,
        input_names=["cond", "samples", "freq_target", "z_baseline"],
        output_names=["z"],
        dynamic_axes={"cond": {0: "b"}, "samples": {0: "b"}, "freq_target": {0: "b"},
                      "z_baseline": {0: "b"}, "z": {0: "b"}},
    )
    return Path(out_path)


def _align_check(ckpt_dir: Path, data_dir: Path, s1_onnx: Path, s2_onnx: Path,
                 n_cases: int = 100, tol: float = 1e-3) -> None:
    """真实数据对齐：torch vs onnxruntime（归一化空间，1e-3 容差）。"""
    import onnxruntime as ort

    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    with np.load(data_dir / "dataset.npz") as z:
        x_struct = z["x_struct"][:n_cases]
        y_sparse = z["y_sparse"][:n_cases]
        params_base = z["params_base"][:n_cases]
        sparse_base = z["sparse_base"][:n_cases]
        sample_pos = z["sample_pos"][:n_cases]
        full_z = z["full_z"][:n_cases]
        y_params = z["y_params"][:n_cases]
    freq = np.asarray(meta["freq_ghz"], dtype=np.float32)
    x_mean = np.asarray(meta["x_mean"], dtype=np.float32)
    x_std = np.asarray(meta["x_std"], dtype=np.float32)
    p_mean = np.asarray(meta["params_mean"], dtype=np.float32)
    p_std = np.asarray(meta["params_std"], dtype=np.float32)
    zm, zs = float(meta["z_mean"][0]), float(meta["z_std"][0])

    x_n = ((x_struct - x_mean) / x_std).astype(np.float32)
    pb_n = ((params_base - p_mean) / p_std).astype(np.float32)
    sb_n = ((sparse_base - zm) / zs).astype(np.float32)

    s1 = ort.InferenceSession(str(s1_onnx))
    p_o, s_o = s1.run(None, {"x": x_n, "params_base": pb_n, "sparse_base": sb_n})
    c1 = torch.load(ckpt_dir / "stage1.pt", map_location="cpu", weights_only=True)
    m1 = BaselineResidualMLP(in_dim=c1["in_dim"], hidden=c1["hidden"],
                             n_res_blocks=c1["n_res_blocks"], n_params=c1["n_params"],
                             k=c1["k"]).eval()
    m1.load_state_dict(c1["model_state"])
    with torch.no_grad():
        ref = m1(torch.from_numpy(x_n), torch.from_numpy(pb_n), torch.from_numpy(sb_n))
    np.testing.assert_allclose(p_o, ref["params"].numpy(), atol=tol)
    np.testing.assert_allclose(s_o, ref["sparse"].numpy(), atol=tol)

    # Stage 2：真值稀疏点 + 样条基线（归一化空间，与 Step A/B 训练一致）
    from app.core.sparse_sampler import baseline_interpolate

    cond_idx = [0, 1, 3, 4, 2]
    cond = ((y_params[:, cond_idx] - p_mean[cond_idx]) / p_std[cond_idx]).astype(np.float32)
    sfreq = freq[sample_pos]
    sz_n = ((y_sparse - zm) / zs).astype(np.float32)
    samples = np.stack([sfreq, sz_n], axis=-1).astype(np.float32)
    zb = np.stack([baseline_interpolate(sfreq[i], sz_n[i], freq)
                   for i in range(len(sfreq))]).astype(np.float32)
    ft = np.tile(freq, (len(samples), 1))
    s2 = ort.InferenceSession(str(s2_onnx))
    (z_o,) = s2.run(None, {"cond": cond, "samples": samples,
                           "freq_target": ft, "z_baseline": zb})
    cfg = json.loads((ckpt_dir / "stage2_config.json").read_text(encoding="utf-8"))
    m2 = SparseReconstructor(**cfg).eval()
    m2.load_state_dict(torch.load(ckpt_dir / "stage2.pt", map_location="cpu",
                                  weights_only=True))
    with torch.no_grad():
        ref2 = m2(torch.from_numpy(cond), torch.from_numpy(samples),
                  torch.from_numpy(ft), z_baseline=torch.from_numpy(zb))
    np.testing.assert_allclose(z_o, ref2.numpy(), atol=tol)
    print(f"对齐校验通过（{len(x_n)} 例，容差 {tol}）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="./checkpoints_surrogate")
    ap.add_argument("--data", default="./surrogate_data")
    ap.add_argument("--out", default=str(PROJECT_ROOT.parent / "frontend/public/webdata"))
    args = ap.parse_args()
    ckpt, data, out = Path(args.ckpt), Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    c1 = torch.load(ckpt / "stage1.pt", map_location="cpu", weights_only=True)
    m1 = BaselineResidualMLP(in_dim=c1["in_dim"], hidden=c1["hidden"],
                             n_res_blocks=c1["n_res_blocks"], n_params=c1["n_params"],
                             k=c1["k"]).eval()
    m1.load_state_dict(c1["model_state"])
    cfg = json.loads((ckpt / "stage2_config.json").read_text(encoding="utf-8"))
    m2 = SparseReconstructor(**cfg).eval()
    stage2_pt = ckpt / "stage2_ft.pt"
    if not stage2_pt.exists():
        stage2_pt = ckpt / "stage2.pt"
    m2.load_state_dict(torch.load(stage2_pt, map_location="cpu", weights_only=True))
    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))

    s1 = export_stage1(m1, out / "stage1.onnx", k=c1["k"])
    s2 = export_stage2(m2, out / "stage2.onnx", k=meta["k"], n_freq=meta["n_freq"])
    _align_check(ckpt, data, s1, s2)
    print(f"ONNX 已导出并校验: {s1} , {s2}")


if __name__ == "__main__":
    main()
```

注意：`_align_check` 的 Stage 2 校验加载 `stage2.pt`——因为 main() 里 `stage2_ft.pt` 优先。校验与导出用同一权重文件即可；实现时把 `stage2_pt` 路径传给 `_align_check` 作为参数（修正：给 `_align_check` 加形参 `stage2_path: Path`，调用处传 `stage2_pt`，函数内 `torch.load(stage2_path, ...)`）。

- [ ] **步骤 4：运行验证通过** — `uv run pytest tests/web/test_onnx_export.py -q`，预期 2 passed

- [ ] **步骤 5：Commit** — `git add backend/scripts/export_surrogate_onnx.py backend/tests/web/test_onnx_export.py && git commit -m "feat(web): torch→ONNX 导出 + 真实数据对齐校验"`

---

## 任务 4：黄金值夹具 `gen_web_golden.py`

**文件：**
- 创建：`backend/scripts/gen_web_golden.py`
- 产出（入库，供 vitest 对拍）：`frontend/src/ml/__fixtures__/{sampling,baseline,spline,surrogate}.json`

- [ ] **步骤 1：实现（无需先行测试——它本身生成测试数据）**

```python
# backend/scripts/gen_web_golden.py
"""生成 TS 移植逻辑的黄金值夹具（规格 §8）。

用法：uv run python scripts/gen_web_golden.py   # 在 backend/ 下跑，输出到 ../frontend/src/ml/__fixtures__/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import AlgorithmConfig  # noqa: E402
from app.core.sparse_sampler import baseline_interpolate, deterministic_sample_positions  # noqa: E402
from app.ml.surrogate.baseline import BaselineTable  # noqa: E402

OUT = PROJECT_ROOT.parent / "frontend/src/ml/__fixtures__"
FREQ = np.linspace(4.0, 7.0, 1001)  # 与真实数据同轴


def gen_sampling() -> dict:
    cases = []
    for fs, fp in [(4.8, 5.1), (4.05, 4.2), (6.7, 6.95), (5.0, 5.0), (-1.0, 5.0),
                   (4.0, 7.0), (5.5, 5.62)]:
        pos = deterministic_sample_positions(FREQ, fs, fp)
        cases.append({"fs": fs, "fp": fp, "k": int(AlgorithmConfig.surr_k),
                      "positions": pos.tolist()})
    return {"freq_ghz": FREQ.tolist(), "cases": cases}


def gen_baseline() -> dict:
    rng = np.random.default_rng(7)
    pfs = np.array([0.0, 1.0])
    areas = np.array([700.0, 1200.0, 2000.0])
    params = rng.normal(0, 1, (2, 3, 7))
    z = rng.normal(40, 5, (2, 3, 21))  # 小 N 即可
    bt = BaselineTable(pfs, areas, params, z)
    cases = []
    for area, pf in [(700.0, 0.0), (950.0, 1.0), (2000.0, 1.0), (3000.0, 0.0),
                     (500.0, 0.0), (1600.0, 0.7)]:
        p, zz = bt.lookup(area, pf)
        cases.append({"area": area, "pf": pf, "params": p.tolist(), "z": zz.tolist()})
    return {"pfs": pfs.tolist(), "areas": areas.tolist(),
            "params_base": params.tolist(), "z_base_full": z.tolist(), "cases": cases}


def gen_spline() -> dict:
    rng = np.random.default_rng(11)
    cases = []
    for n_pts in (8, 32, 256):
        xs = np.sort(rng.uniform(4.0, 7.0, n_pts))
        ys = np.sin(xs * 3) * 10 + rng.normal(0, 0.5, n_pts) + 40
        out = baseline_interpolate(xs, ys, FREQ)
        cases.append({"xs": xs.tolist(), "ys": ys.tolist(), "out": out.tolist()})
    return {"freq_ghz": FREQ.tolist(), "cases": cases}


def gen_surrogate() -> dict:
    """端到端黄金：5 组格内输入的完整 Python 推理输出（供 TS 编排对拍）。"""
    from app.ml.surrogate.infer import SurrogatePredictor

    data, ckpt = PROJECT_ROOT / "surrogate_data", PROJECT_ROOT / "checkpoints_surrogate"
    if not (data / "meta.json").exists():
        return {"cases": [], "note": "surrogate_data 缺失，跳过（e2e 兜底）"}
    stage2 = ckpt / "stage2_ft.pt"
    if not stage2.exists():
        stage2 = ckpt / "stage2.pt"
    pred = SurrogatePredictor(ckpt / "stage1.pt", stage2, ckpt / "stage2_config.json",
                              data / "baseline.npz", data / "meta.json")
    cases = []
    for eg, fl, area, pf in [(0.0, 0.0, 700.0, 0.0), (1.0, 0.5, 1500.0, 0.0),
                             (2.0, 2.0, 5500.0, 1.0), (0.75, 1.25, 3000.0, 0.0),
                             (1.5, 0.25, 4500.0, 1.0)]:
        r = pred.predict(eg, fl, area, pf)
        cases.append({"eg": eg, "fl": fl, "area": area, "pf": pf,
                      "params": r["params"], "sample_freq": r["sample_freq"].tolist(),
                      "sample_z": r["sample_z"].tolist(), "z_db": r["z_db"].tolist()})
    return {"cases": cases}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, gen in [("sampling", gen_sampling), ("baseline", gen_baseline),
                      ("spline", gen_spline), ("surrogate", gen_surrogate)]:
        (OUT / f"{name}.json").write_text(json.dumps(gen()), encoding="utf-8")
        print(f"written {name}.json")


if __name__ == "__main__":
    main()
```

- [ ] **步骤 2：运行生成 + 检查 BaselineTable 构造签名**

注意：`BaselineTable(pfs, areas, params, z)` 的位置参数顺序以 `app/ml/surrogate/baseline.py` 的 `__init__`/`dataclass` 定义为准（实现时核对，若签名是关键字或含更多字段按实调整）。运行：

```bash
cd backend && uv run python scripts/gen_web_golden.py
ls ../frontend/src/ml/__fixtures__/
```

预期：4 个 JSON 生成（surrogate.json 在 master 副本的 surrogate_data 存在时有 5 个 case；注意本 worktree 需先 `cp -R` 产物或指向 master 路径——`PROJECT_ROOT/"surrogate_data"` 在 worktree 里不存在时自动跳过，e2e 前务必拷贝）。

- [ ] **步骤 3：Commit**

```bash
git add backend/scripts/gen_web_golden.py frontend/src/ml/__fixtures__/
git commit -m "feat(web): TS 移植黄金值夹具（sampling/baseline/spline/surrogate）"
```

---

## 任务 5：`sampling.ts` 确定性采样位置移植

**文件：**
- 创建：`frontend/src/ml/sampling.ts`
- 测试：`frontend/src/ml/__tests__/sampling.test.ts`

注意 numpy 是 round-half-even，TS `Math.round` 是 half-up，必须实现 `npRound`。

- [ ] **步骤 1：编写失败的测试**

```typescript
// frontend/src/ml/__tests__/sampling.test.ts
import { describe, it, expect } from 'vitest';
import { deterministicSamplePositions } from '../sampling';
import fixture from '../__fixtures__/sampling.json';

describe('deterministicSamplePositions 黄金对拍', () => {
  const freq = fixture.freq_ghz;
  for (const c of fixture.cases) {
    it(`fs=${c.fs} fp=${c.fp}`, () => {
      const pos = deterministicSamplePositions(freq, c.fs, c.fp, c.k);
      expect(pos).toEqual(c.positions);
    });
  }
});
```

- [ ] **步骤 2：运行验证失败** — `npm test -- sampling`，预期模块不存在

- [ ] **步骤 3：实现**

```typescript
// frontend/src/ml/sampling.ts
/** 确定性稀疏采样位置（移植 app/core/sparse_sampler.py，行为逐点一致）。 */

/** numpy round-half-even（与 np.round 一致；Math.round 是 half-up，不能直接换）。 */
function npRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1; // 恰 .5 → 偶数
}

function linspaceInts(lo: number, hi: number, m: number): number[] {
  if (m <= 0) return [];
  if (m === 1) return [npRound(lo)];
  const step = (hi - lo) / (m - 1);
  return Array.from({ length: m }, (_, i) => npRound(lo + i * step));
}

function take(idx: number[], m: number): number[] {
  if (m <= 0 || idx.length === 0) return [];
  const sel = linspaceInts(0, idx.length - 1, Math.min(m, idx.length));
  return sel.map((i) => idx[i]);
}

export function deterministicSamplePositions(
  freqGhz: number[],
  fs: number,
  fp: number,
  k = 256,
  denseRatio = 0.60,
  transitionRatio = 0.25,
  denseHalfBw = 0.5,
  transitionHalfBw = 1.5,
): number[] {
  const n = freqGhz.length;
  k = Math.min(k, n);
  if (fs <= 0 || fp <= fs) {
    return linspaceInts(0, n - 1, k);
  }
  const delta = fp - fs;
  const fmin = freqGhz[0];
  const fmax = freqGhz[n - 1];
  const denseLo = Math.max(fs - denseHalfBw * delta, fmin);
  const denseHi = Math.min(fp + denseHalfBw * delta, fmax);
  const transLo = Math.max(fs - transitionHalfBw * delta, fmin);
  const transHi = Math.min(fp + transitionHalfBw * delta, fmax);

  const inRange = (lo: number, hi: number) =>
    freqGhz.map((f, i) => (f >= lo && f <= hi ? i : -1)).filter((i) => i >= 0);

  const denseIdx = inRange(denseLo, denseHi);
  const transIdx = [
    ...inRange(transLo, denseLo - 1e-12),
    ...inRange(denseHi + 1e-12, transHi),
  ];
  const occupied = new Set([...denseIdx, ...transIdx]);
  const farIdx = freqGhz.map((_, i) => i).filter((i) => !occupied.has(i));

  let qDense = npRound(k * denseRatio);
  let qTrans = npRound(k * transitionRatio);
  const qFar = k - qDense - qTrans;

  let takeDense = Math.min(qDense, denseIdx.length);
  let takeTrans = Math.min(qTrans, transIdx.length);
  let takeFar = Math.min(qFar, farIdx.length);

  let leftover = k - (takeDense + takeTrans + takeFar);
  const caps: [string, number][] = [
    ['dense', denseIdx.length - takeDense],
    ['trans', transIdx.length - takeTrans],
    ['far', farIdx.length - takeFar],
  ];
  while (leftover > 0 && caps.some(([, c]) => c > 0)) {
    for (let i = 0; i < caps.length; i++) {
      if (leftover <= 0) break;
      const [name, cap] = caps[i];
      if (cap <= 0) continue;
      if (name === 'dense') takeDense++;
      else if (name === 'trans') takeTrans++;
      else takeFar++;
      caps[i] = [name, cap - 1];
      leftover--;
    }
  }
  const result = [
    ...take(denseIdx, takeDense),
    ...take(transIdx, takeTrans),
    ...take(farIdx, takeFar),
  ];
  return result.sort((a, b) => a - b);
}
```

- [ ] **步骤 4：运行验证通过** — `npm test -- sampling`，预期 7 个 case 全过（含非法 fs/fp 回退、贴边截断）

- [ ] **步骤 5：Commit** — `git add frontend/src/ml/sampling.ts frontend/src/ml/__tests__/ && git commit -m "feat(web): 确定性采样位置 TS 移植（黄金对拍通过）"`

---

## 任务 6：`baseline.ts` + `spline.ts`

**文件：**
- 创建：`frontend/src/ml/baseline.ts`、`frontend/src/ml/spline.ts`
- 测试：`frontend/src/ml/__tests__/baseline.test.ts`、`spline.test.ts`

spline 用 not-a-knot 边界（scipy CubicSpline 默认）；稠密高斯消元（257 阶系统 <10ms，交互够用）。

- [ ] **步骤 1：编写失败的测试**

```typescript
// frontend/src/ml/__tests__/baseline.test.ts
import { describe, it, expect } from 'vitest';
import { baselineLookup } from '../baseline';
import fixture from '../__fixtures__/baseline.json';

describe('baselineLookup 黄金对拍', () => {
  for (const c of fixture.cases) {
    it(`area=${c.area} pf=${c.pf}`, () => {
      const [p, z] = baselineLookup(fixture.pfs, fixture.areas,
        fixture.params_base, fixture.z_base_full, c.area, c.pf);
      p.forEach((v, i) => expect(v).toBeCloseTo(c.params[i], 10));
      z.forEach((v, i) => expect(v).toBeCloseTo(c.z[i], 10));
    });
  }
});
```

```typescript
// frontend/src/ml/__tests__/spline.test.ts
import { describe, it, expect } from 'vitest';
import { cubicSplineInterpolate } from '../spline';
import fixture from '../__fixtures__/spline.json';

describe('cubicSplineInterpolate（not-a-knot，对齐 scipy）', () => {
  const freq = fixture.freq_ghz;
  for (const [ci, c] of fixture.cases.entries()) {
    it(`case ${ci}（${c.xs.length} 点）`, () => {
      const out = cubicSplineInterpolate(c.xs, c.ys, freq);
      out.forEach((v, i) => expect(Math.abs(v - c.out[i])).toBeLessThan(1e-6));
    });
  }
});
```

- [ ] **步骤 2：运行验证失败** — 预期模块不存在

- [ ] **步骤 3：实现**

```typescript
// frontend/src/ml/baseline.ts
/** 基准表查找（移植 BaselineTable.lookup）：pf 取最近档，面积维线性插值、越界端点。 */
export function baselineLookup(
  pfs: number[],
  areas: number[],
  paramsBase: number[][][],   // (P, A, 7)
  zBaseFull: number[][][],    // (P, A, N)
  area: number,
  pf: number,
): [number[], number[]] {
  let pi = 0;
  for (let i = 1; i < pfs.length; i++) {
    if (Math.abs(pfs[i] - pf) < Math.abs(pfs[pi] - pf)) pi = i;
  }
  const a = area;
  if (a <= areas[0]) return [[...paramsBase[pi][0]], [...zBaseFull[pi][0]]];
  if (a >= areas[areas.length - 1]) {
    const last = areas.length - 1;
    return [[...paramsBase[pi][last]], [...zBaseFull[pi][last]]];
  }
  let hi = 0;
  while (areas[hi] < a) hi++; // searchsorted 等价
  const lo = hi - 1;
  const w = (a - areas[lo]) / (areas[hi] - areas[lo]);
  const params = paramsBase[pi][lo].map((v, j) => (1 - w) * v + w * paramsBase[pi][hi][j]);
  const z = zBaseFull[pi][lo].map((v, j) => (1 - w) * v + w * zBaseFull[pi][hi][j]);
  return [params, z];
}
```

```typescript
// frontend/src/ml/spline.ts
/** not-a-knot 三次样条（对齐 scipy.interpolate.CubicSpline 默认 bc_type）。
 *  点数 <4 退化为线性插值（与 Python baseline_interpolate 一致）。 */

function solveDense(a: number[][], b: number[]): number[] {
  // 部分主元高斯消元
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(m[r][col]) > Math.abs(m[piv][col])) piv = r;
    }
    [m[col], m[piv]] = [m[piv], m[col]];
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = m[r][col] / m[col][col];
      for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c];
    }
  }
  return m.map((row, i) => row[n] / m[i][i] === 0 ? row[n] : row[n] / m[i][i]);
}

export function cubicSplineInterpolate(
  xs: number[], ys: number[], target: number[],
): number[] {
  const n = xs.length;
  if (n < 4) {
    return target.map((t) => {
      if (t <= xs[0]) return ys[0] + (ys[1] - ys[0]) * (t - xs[0]) / (xs[1] - xs[0]);
      if (t >= xs[n - 1]) {
        return ys[n - 1] + (ys[n - 1] - ys[n - 2]) * (t - xs[n - 1]) / (xs[n - 1] - xs[n - 2]);
      }
      let i = 0;
      while (xs[i + 1] < t) i++;
      const w = (t - xs[i]) / (xs[i + 1] - xs[i]);
      return ys[i] * (1 - w) + ys[i + 1] * w;
    });
  }
  // Burden 形式：S_i(x) = a_i + b_i·dx + c_i·dx² + d_i·dx³，解 c（=S''/2）
  const h = Array.from({ length: n - 1 }, (_, i) => xs[i + 1] - xs[i]);
  const size = n;
  const A: number[][] = Array.from({ length: size }, () => new Array(size).fill(0));
  const rhs = new Array(size).fill(0);
  // not-a-knot 首行：h1·c0 − (h0+h1)·c1 + h0·c2 = 0
  A[0][0] = h[1]; A[0][1] = -(h[0] + h[1]); A[0][2] = h[0];
  // 内部行
  for (let i = 1; i < n - 1; i++) {
    A[i][i - 1] = h[i - 1];
    A[i][i] = 2 * (h[i - 1] + h[i]);
    A[i][i + 1] = h[i];
    rhs[i] = 3 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1]);
  }
  // not-a-knot 末行：h_{n-2}·c_{n-3}... 由 d_{n-3}=d_{n-2}：
  // (c_{n-2}−c_{n-3})/h_{n-3} = (c_{n-1}−c_{n-2})/h_{n-2}
  A[n - 1][n - 3] = h[n - 2]; A[n - 1][n - 2] = -(h[n - 3] + h[n - 2]); A[n - 1][n - 1] = h[n - 3];
  const c = solveDense(A, rhs);
  const b = new Array(n - 1);
  const d = new Array(n - 1);
  for (let i = 0; i < n - 1; i++) {
    b[i] = (ys[i + 1] - ys[i]) / h[i] - h[i] * (2 * c[i] + c[i + 1]) / 3;
    d[i] = (c[i + 1] - c[i]) / (3 * h[i]);
  }
  return target.map((t) => {
    let i = 0;
    if (t <= xs[0]) i = 0;
    else if (t >= xs[n - 1]) i = n - 2;
    else { while (xs[i + 1] < t) i++; }
    const dx = t - xs[i];
    return ys[i] + b[i] * dx + c[i] * dx * dx + d[i] * dx * dx * dx;
  });
}
```

注意：`solveDense` 返回行写法 `row[n] / m[i][i] === 0 ? ... : ...` 是错误的残留表述，实现时直接 `m[i][n] / m[i][i]`（消元后对角元非零；若出现 0 主元说明样条矩阵奇异，直接抛错）。正确实现：

```typescript
  return m.map((row, i) => {
    if (m[i][i] === 0) throw new Error('样条矩阵奇异');
    return row[n] / m[i][i];
  });
```

- [ ] **步骤 4：运行验证通过** — `npm test -- baseline spline`，预期全过（spline 容差 1e-6）

- [ ] **步骤 5：Commit** — `git add frontend/src/ml/baseline.ts frontend/src/ml/spline.ts frontend/src/ml/__tests__/ && git commit -m "feat(web): 基准表查找 + not-a-knot 样条 TS 移植"`

---

## 任务 7：`surrogateOnnx.ts` 浏览器推理编排

**文件：**
- 创建：`frontend/src/ml/surrogateOnnx.ts`
- 测试：`frontend/src/ml/__tests__/surrogateOnnx.test.ts`

- [ ] **步骤 1：编写失败的测试**

```typescript
// frontend/src/ml/__tests__/surrogateOnnx.test.ts
import { describe, it, expect } from 'vitest';
import { surrogatePredictOnnx } from '../surrogateOnnx';
import fixture from '../__fixtures__/surrogate.json';

// webdata 需在 public/webdata/（任务 1-3 的真实导出）；vitest 里从 public 读文件
describe('ONNX 端到端对拍（vs Python SurrogatePredictor）', () => {
  it.skipIf(fixture.cases.length === 0)('5 组格内输入', async () => {
    for (const c of fixture.cases) {
      const r = await surrogatePredictOnnx(c.eg, c.fl, c.area, c.pf);
      for (const [k, v] of Object.entries(c.params)) {
        expect(Math.abs((r.params as any)[k] - v)).toBeLessThan(
          k.includes('ghz') ? 0.001 : Math.abs(v) * 0.001 + 1e-3);
      }
      r.z_db.forEach((v, i) => expect(Math.abs(v - c.z_db[i])).toBeLessThan(0.05));
      expect(r.sample_freq).toEqual(c.sample_freq);
    }
  }, 120_000);
});
```

若 onnxruntime-web 在 vitest(node) 环境加载 wasm 失败，本测试改为在 e2e（任务 11）用 Playwright 断言同一容差，本文件保留采样点等纯逻辑断言。

- [ ] **步骤 2：运行验证失败**

- [ ] **步骤 3：实现**

```typescript
// frontend/src/ml/surrogateOnnx.ts
/** 浏览器端两阶段代理模型推理（规格 §5.2）。
 *  数据文件在 /webdata/（public 目录随构建进 dist）。懒加载 onnxruntime-web。 */

import { deterministicSamplePositions } from './sampling';
import { baselineLookup } from './baseline';
import { cubicSplineInterpolate } from './spline';

const COND_IDX = [0, 1, 3, 4, 2]; // [fs, fp, Qs, Qp, kt2]
const PARAM_COLS = ['fs_ghz', 'fp_ghz', 'k2eff_pct', 'qs', 'qp', 'qs_bodeq', 'qp_bodeq'];

interface WebData {
  freq: number[];
  k: number;
  xMean: number[]; xStd: number[];
  pMean: number[]; pStd: number[];
  zMean: number; zStd: number;
  pfs: number[]; areas: number[];
  paramsBase: number[][][];
  zBaseFull: number[][][];
  s1: any; s2: any; // ort.InferenceSession
}

let _data: Promise<WebData> | null = null;

async function load(): Promise<WebData> {
  const [meta, baseline, ort] = await Promise.all([
    fetch('webdata/surr_meta.json').then((r) => r.json()),
    fetch('webdata/baseline.json').then((r) => r.json()),
    import('onnxruntime-web'),
  ]);
  const [s1, s2] = await Promise.all([
    ort.InferenceSession.create('webdata/stage1.onnx'),
    ort.InferenceSession.create('webdata/stage2.onnx'),
  ]);
  return {
    freq: meta.freq_ghz, k: meta.k,
    xMean: meta.x_mean, xStd: meta.x_std,
    pMean: meta.params_mean, pStd: meta.params_std,
    zMean: meta.z_mean[0], zStd: meta.z_std[0],
    pfs: baseline.pfs, areas: baseline.areas,
    paramsBase: baseline.params_base, zBaseFull: baseline.z_base_full,
    s1, s2,
  };
}

function getData(): Promise<WebData> {
  if (!_data) _data = load();
  return _data;
}

export interface OnnxPredictResult {
  params: Record<string, number>;
  freq_ghz: number[];
  z_db: number[];
  sample_freq: number[];
  sample_z: number[];
}

export async function surrogatePredictOnnx(
  eg: number, fl: number, area: number, pf = 0, ag = 1.0,
): Promise<OnnxPredictResult> {
  const d = await getData();
  const ort = await import('onnxruntime-web');
  const x = [eg, fl, ag, area, pf].map((v, i) => (v - d.xMean[i]) / d.xStd[i]);
  const xT = new ort.Tensor('float32', Float32Array.from(x), [1, 5]);

  const [paramsBase, zBaseFull] = baselineLookup(
    d.pfs, d.areas, d.paramsBase, d.zBaseFull, area, pf);
  const pbN = paramsBase.map((v, i) => (v - d.pMean[i]) / d.pStd[i]);
  const pbT = new ort.Tensor('float32', Float32Array.from(pbN), [1, 7]);

  // 第一遍：性能参数（sparse_base 置零）
  const zeroSb = new ort.Tensor('float32', new Float32Array(d.k), [1, d.k]);
  const out1 = await d.s1.run({ x: xT, params_base: pbT, sparse_base: zeroSb });
  const paramsArr = Array.from(out1.params.data as Float32Array)
    .map((v, i) => v * d.pStd[i] + d.pMean[i]);
  const fs = paramsArr[0];
  const fp = paramsArr[1];

  // 预测 fs/fp → 采样位置 → sparse_base（dB → 归一化）
  const pos = deterministicSamplePositions(d.freq, fs, fp, d.k);
  const sbDb = pos.map((i) => zBaseFull[i]); // webdata 频率轴与基准表同网格（导出保证）
  const sbT = new ort.Tensor('float32',
    Float32Array.from(sbDb.map((v) => (v - d.zMean) / d.zStd)), [1, d.k]);

  // 第二遍：稀疏点
  const out2 = await d.s1.run({ x: xT, params_base: pbT, sparse_base: sbT });
  const sparseZ = Array.from(out2.sparse.data as Float32Array)
    .map((v) => v * d.zStd + d.zMean);

  // Stage 2：cond 归一化 + 样条基线（归一化空间）
  const cond = COND_IDX.map((i) => (paramsArr[i] - d.pMean[i]) / d.pStd[i]);
  const sfreq = pos.map((i) => d.freq[i]);
  const szN = sparseZ.map((v) => (v - d.zMean) / d.zStd);
  const samplesFlat = new Float32Array(d.k * 2);
  for (let i = 0; i < d.k; i++) { samplesFlat[i * 2] = sfreq[i]; samplesFlat[i * 2 + 1] = szN[i]; }
  const zbN = cubicSplineInterpolate(sfreq, szN, d.freq);
  const feeds = {
    cond: new ort.Tensor('float32', Float32Array.from(cond), [1, 5]),
    samples: new ort.Tensor('float32', samplesFlat, [1, d.k, 2]),
    freq_target: new ort.Tensor('float32', Float32Array.from(d.freq), [1, d.freq.length]),
    z_baseline: new ort.Tensor('float32', Float32Array.from(zbN), [1, d.freq.length]),
  };
  const out3 = await d.s2.run(feeds);
  const zDb = Array.from(out3.z.data as Float32Array).map((v) => v * d.zStd + d.zMean);

  const params: Record<string, number> = {};
  PARAM_COLS.forEach((c, i) => { params[c] = paramsArr[i]; });
  return { params, freq_ghz: d.freq, z_db: zDb, sample_freq: sfreq, sample_z: sparseZ };
}
```

注意 webdata 的 `baseline.json` 的 `z_base_full` 网格必须等于 `surr_meta.freq_ghz` 网格（任务 2 的导出同源保证）；上面对 sparse_base 的直接索引依赖这一点——**断言**：加载时校验 `zBaseFull[0].length === freq.length`，不等则 throw 并提示重新导出。

- [ ] **步骤 4：运行验证通过** — `npm test -- surrogateOnnx`（或降级到任务 11 的 e2e 断言）

- [ ] **步骤 5：Commit** — `git add frontend/src/ml/surrogateOnnx.ts frontend/src/ml/__tests__/ && git commit -m "feat(web): 浏览器端两阶段 ONNX 推理编排"`

---

## 任务 8：静态 API 适配层

**文件：**
- 创建：`frontend/src/config.ts`、`frontend/src/api/static/duckdb.ts`、`frontend/src/api/static/index.ts`
- 修改：`frontend/src/api/endpoints.ts`（现有函数改 http* 私有，导出改 IS_STATIC 分发）
- 测试：`frontend/src/api/static/__tests__/sql.test.ts`（filter→SQL 构造单测）

- [ ] **步骤 1：config.ts + duckdb 单例**

```typescript
// frontend/src/config.ts
export const IS_STATIC = import.meta.env.VITE_STATIC === '1';
```

```typescript
// frontend/src/api/static/duckdb.ts
/** DuckDB-WASM 懒单例：加载 wasm + 注册 devices.parquet / cell_means.parquet。 */
import * as duckdb from '@duckdb/duckdb-wasm';
import duckdb_wasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url';
import mvp_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url';
import duckdb_wasm_eh from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url';
import eh_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url';

let _conn: Promise<duckdb.AsyncDuckDBConnection> | null = null;

async function init(): Promise<duckdb.AsyncDuckDBConnection> {
  const bundle = await duckdb.selectBundle({
    mvp: { mainModule: duckdb_wasm, mainWorker: mvp_worker },
    eh: { mainModule: duckdb_wasm_eh, mainWorker: eh_worker },
  });
  const worker = new Worker(bundle.mainWorker!);
  const db = new duckdb.AsyncDuckDB(
    new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  const conn = await db.connect();
  for (const name of ['devices', 'cell_means']) {
    const buf = await fetch(`webdata/${name}.parquet`).then((r) => r.arrayBuffer());
    await db.registerFileBuffer(`${name}.parquet`, new Uint8Array(buf));
    await conn.query(
      `CREATE VIEW ${name} AS SELECT * FROM parquet_scan('${name}.parquet')`);
  }
  return conn;
}

export function getConn(): Promise<duckdb.AsyncDuckDBConnection> {
  if (!_conn) _conn = init();
  return _conn;
}

/** SQL 字符串字面量转义（值来自 UI 输入）。 */
export function q(v: string | number): string {
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : 'NULL';
  return `'${v.replaceAll("'", "''")}'`;
}
```

- [ ] **步骤 2：filter→SQL 单测（先行）**

```typescript
// frontend/src/api/static/__tests__/sql.test.ts
import { describe, it, expect } from 'vitest';
import { filtersToWhere } from '../index';

describe('filtersToWhere', () => {
  it('数组 → IN', () => {
    expect(filtersToWhere({ batch_no: ['#2', '01'] })).toBe(
      "(batch_no IN ('#2', '01'))");
  });
  it('范围对象', () => {
    expect(filtersToWhere({ fs_ghz: { gte: 4.5, lt: 5 } })).toBe(
      '(fs_ghz >= 4.5 AND fs_ghz < 5)');
  });
  it('组合 + in 列表', () => {
    expect(filtersToWhere({ eg: { in: [0, 0.75] }, pf: ['Y'] })).toBe(
      "(eg IN (0, 0.75)) AND (pf IN ('Y'))");
  });
  it('空 → 恒真', () => {
    expect(filtersToWhere({})).toBe('1=1');
  });
});
```

- [ ] **步骤 3：实现 `static/index.ts`**（核心函数全码）

```typescript
// frontend/src/api/static/index.ts
/** 静态模式 API 实现：DuckDB-WASM（parquet）+ fetch JSON（规格 §4）。
 *  与 endpoints.ts 的 HTTP 版同签名。 */

import { getConn, q } from './duckdb';

async function fetchJson(path: string) {
  const r = await fetch(`webdata/${path}`);
  if (!r.ok) throw new Error(`快照缺失: ${path}`);
  return r.json();
}

// ---- filter 语义子集（对齐后端 /api/query：数组=IN；对象支持 in/gte/gt/lte/lt）----
export function filtersToWhere(filters: Record<string, any>): string {
  const parts: string[] = [];
  for (const [field, cond] of Object.entries(filters ?? {})) {
    if (cond == null) continue;
    if (Array.isArray(cond)) {
      if (cond.length) parts.push(`(${field} IN (${cond.map(q).join(', ')}))`);
      continue;
    }
    if (typeof cond === 'object') {
      const subs: string[] = [];
      if (cond.in?.length) subs.push(`${field} IN (${cond.in.map(q).join(', ')})`);
      for (const [op, sym] of [['gte', '>='], ['gt', '>'], ['lte', '<='], ['lt', '<']] as const) {
        if (cond[op] != null) subs.push(`${field} ${sym} ${q(cond[op])}`);
      }
      if (subs.length) parts.push(`(${subs.join(' AND ')})`);
      continue;
    }
    parts.push(`(${field} = ${q(cond)})`);
  }
  return parts.length ? parts.join(' AND ') : '1=1';
}

async function runQuery(sql: string): Promise<any[]> {
  const conn = await getConn();
  const t = await conn.query(sql);
  return t.toArray().map((r) => r.toJSON());
}

export const getHealth = async () => ({ status: 'ok', db: 'static', redis: 'static' });

export const getStats = () => fetchJson('stats.json');

export const listBatches = async () => {
  const items = await fetchJson('batches.json');
  return { items, total: items.length };
};
export const getBatch = async (batchNo: string) => {
  const items = await fetchJson('batches.json');
  const b = items.find((x: any) => x.batch_no === batchNo);
  if (!b) throw new Error(`批次不存在: ${batchNo}`);
  return b;
};

export const listMappings = async () => {
  const ms = await fetchJson('mappings.json');
  return ms.map((m: any) => ({ ...m, entries: undefined }));
};
export const listMappingEntries = async (id: number | string) => {
  const ms = await fetchJson('mappings.json');
  const m = ms.find((x: any) => String(x.id) === String(id));
  return { items: m?.entries ?? [], total: m?.entries?.length ?? 0 };
};

export const getProcessSteps = async () => {
  const p = await fetchJson('process.json');
  return Object.entries(p).map(([key, v]: [string, any]) => ({
    key, label: v.label, row_count: v.rows.length,
  }));
};
export const getProcessStep = async (step: string) => {
  const p = await fetchJson('process.json');
  const s = p[step];
  if (!s) throw new Error(`未知工序: ${step}`);
  return { fields: s.fields, rows: s.rows };
};

export const queryFields = () => fetchJson('fields.json');

export interface QueryBody {
  filters?: Record<string, any>;
  fields?: string[];
  limit?: number;
  order_by?: string;
  page?: number;
  size?: number;
}

export async function queryDevices(body: QueryBody) {
  const where = filtersToWhere(body.filters ?? {});
  const cols = body.fields?.length ? body.fields.join(', ') : '*';
  const limit = Math.min(body.limit ?? 10000, 200000);
  const order = body.order_by
    ? ` ORDER BY ${body.order_by.replace(/^-/, '')} ${body.order_by.startsWith('-') ? 'DESC' : 'ASC'}`
    : '';
  const rows = await runQuery(`SELECT ${cols} FROM devices WHERE ${where}${order} LIMIT ${limit}`);
  const cnt = await runQuery(`SELECT COUNT(*) AS c FROM devices WHERE ${where}`);
  return { total: Number(cnt[0].c), returned: rows.length,
           truncated: rows.length >= limit, rows };
}

export async function queryAggregate(body: {
  filters?: Record<string, any>; group_by: string[];
  metrics: { field: string; agg: string[] }[];
}) {
  const where = filtersToWhere(body.filters ?? {});
  const gb = body.group_by.join(', ');
  const aggs = body.metrics.flatMap((m) =>
    m.agg.map((a) => {
      const f = m.field;
      if (a === 'count') return `COUNT(*) AS "${f}__count"`;
      if (a === 'min') return `MIN(${f}) AS "${f}__min"`;
      if (a === 'max') return `MAX(${f}) AS "${f}__max"`;
      const p = { p25: 0.25, p50: 0.5, p75: 0.75 }[a as string];
      return `quantile_cont(${f}, ${p}) AS "${f}__${a}"`;
    }));
  const rows = await runQuery(
    `SELECT ${gb}, ${aggs.join(', ')} FROM devices WHERE ${where} GROUP BY ${gb}`);
  // 整形为后端响应结构：{groups: [{<group keys>, <field>: {agg: val}}]}
  const groups = rows.map((r) => {
    const g: Record<string, any> = {};
    for (const k of body.group_by) g[k] = r[k];
    for (const m of body.metrics) {
      g[m.field] = {};
      for (const a of m.agg) g[m.field][a] = r[`${m.field}__${a}`];
    }
    return g;
  });
  return { groups };
}

// 批次内器件（BatchDetail 用）：分页
export async function listBatchDevices(batchNo: string, params: { page?: number; size?: number; [k: string]: any } = {}) {
  const page = params.page ?? 1;
  const size = params.size ?? 100;
  const extra = { ...params };
  delete extra.page; delete extra.size;
  const where = `(batch_no = ${q(batchNo)}) AND ${filtersToWhere(extra)}`;
  const cnt = await runQuery(`SELECT COUNT(*) AS c FROM devices WHERE ${where}`);
  const rows = await runQuery(
    `SELECT * FROM devices WHERE ${where} ORDER BY id LIMIT ${size} OFFSET ${(page - 1) * size}`);
  return { items: rows, total: Number(cnt[0].c) };
}

// CSV 导出（静态版：客户端生成下载）
export async function exportCsv(body: QueryBody) {
  const res = await queryDevices({ ...body, limit: 200000 });
  const rows = res.rows;
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const csv = [cols.join(','),
    ...rows.map((r) => cols.map((c) => JSON.stringify(r[c] ?? '')).join(','))].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv' }));
  a.download = 'devices.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}
```

- [ ] **步骤 4：endpoints.ts 分发改造**

模式（对每个保留页面用到的函数）：

```typescript
import { IS_STATIC } from '../config';
import * as staticApi from './static';

const httpGetStats = () => api.get('/stats').then((r: AxiosResponse<StatsResponse>) => r.data);
export const getStats = IS_STATIC ? staticApi.getStats : httpGetStats;
// ……对其余函数同样处理：getHealth/listBatches/getBatch/listBatchDevices/listMappings/
// listMappingEntries/queryFields(query 的 fields 端点)/queryDevices/queryAggregate/
// exportCsv/getProcessSteps/getProcessStep/getSurrogateStatus/predictSurrogate
```

`getSurrogateStatus`/`predictSurrogate` 静态实现（加进 `static/index.ts`）：

```typescript
export const getSurrogateStatus = async () => {
  try {
    const [m, meta] = await Promise.all([fetchJson('manifest.json'), fetchJson('surr_meta.json')]);
    if (m.missing?.includes('surr_meta.json')) return { available: false, reason: '模型数据未同步' };
    const cm = await getConn().then((c) =>
      c.query('SELECT MIN(eg) e0, MAX(eg) e1, MIN(fl) f0, MAX(fl) f1 FROM cell_means')
        .then((t) => t.toArray()[0].toJSON()));
    const areas = await getConn().then((c) =>
      c.query('SELECT DISTINCT area FROM cell_means ORDER BY area')
        .then((t) => t.toArray().map((r: any) => Number(r.toJSON().area))));
    return { available: true, eg_min: cm.e0, eg_max: cm.e1,
             fl_min: cm.f0, fl_max: cm.f1, areas };
  } catch (e) {
    return { available: false, reason: String(e) };
  }
};

export const predictSurrogate = async (params: { eg: number; fl: number; area: number; pf: number; ag: number }) => {
  const { surrogatePredictOnnx } = await import('../../ml/surrogateOnnx');
  const r = await surrogatePredictOnnx(params.eg, params.fl, params.area, params.pf, params.ag);
  // 同格真值（格均值谱，长表按格查询）
  const conn = await getConn();
  const rows = await conn.query(
    `SELECT idx, z FROM cell_means WHERE eg=${q(params.eg)} AND fl=${q(params.fl)}`
    + ` AND area=${q(params.area)} AND pf=${q(params.pf)} ORDER BY idx`)
    .then((t) => t.toArray().map((x: any) => x.toJSON()));
  const trueCurve = rows.length ? rows.map((r: any) => r.z) : undefined;
  return { ...r, freq: r.freq_ghz, z: r.z_db, z_base: r.z_base,
           sample_freq: r.sample_freq, sample_z: r.sample_z,
           in_grid: !!trueCurve, true_curves: trueCurve ? [trueCurve] : undefined,
           true_mean: undefined };
};
```

surrogateOnnx 返回需补 `z_base` 字段（任务 7 的返回加 `z_base: zBaseFull`）；`true_mean` 静态版省略（格均值谱已叠加，参数均值从 cell 的 devices 可查——YAGNI，页面隐藏该列即可，见任务 9）。

- [ ] **步骤 5：验证 + Commit**

```bash
npm test -- sql && npm run typecheck
git add frontend/src/config.ts frontend/src/api/ && git commit -m "feat(web): 静态 API 适配层（DuckDB-WASM + JSON）+ endpoints 分发"
```

---

## 任务 9：静态模式 UI 接线

**文件：**
- 修改：`frontend/src/components/Sidebar.tsx`、`frontend/src/App.tsx`、`frontend/src/pages/BatchDetail.tsx`、`frontend/src/pages/Surrogate.tsx`、`frontend/src/pages/Batches.tsx`、`frontend/src/pages/Mappings.tsx`

- [ ] **步骤 1：Sidebar/App 裁剪**

```typescript
// Sidebar.tsx 顶部
import { IS_STATIC } from '../config';
// items 定义后：
const visible = IS_STATIC
  ? items.filter((n) => ['/', '/explore', '/surrogate', '/batches', '/mappings', '/process']
      .includes(n.to))
  : items;
// 渲染用 visible；App.tsx 路由保持不变（隐藏入口即可，直接输 URL 时页面在静态模式下数据显示空态可接受——但 Upload/Tasks/Impedance 路由静态模式下重定向到 '/'）
```

App.tsx 在 IS_STATIC 时把 `/upload`、`/tasks`、`/impedance` 路由 element 换成 `<Navigate to="/" replace />`（import { Navigate } from 'react-router-dom'），Titlebar 的接口/数据库/缓存 pills 在 IS_STATIC 时整体隐藏。

- [ ] **步骤 2：写操作按钮隐藏**

Batches 的删除/重命名、BatchDetail 的重处理/文件列表区、Mappings 的上传/删除：用 `import { IS_STATIC } from '../config'` 包裹 `{!IS_STATIC && (...)}`。BatchDetail 的文件列表（listBatchFiles 调用与文件树 UI）在 IS_STATIC 时不渲染、不发请求。

- [ ] **步骤 3：Surrogate 页静态适配**

- `true_mean` 为 undefined 时隐藏"同格真值均值"行（现有渲染已是条件渲染，确认即可）
- 真值叠加图例文案静态版为"格均值真值"（`data.true_curves` 仅 1 条时改名字即可）
- 其余零改动（predictSurrogate/getSurrogateStatus 已在适配层分流）

- [ ] **步骤 4：验证 + Commit**

```bash
VITE_STATIC=1 npm run build && npm run typecheck
git add frontend/src/ && git commit -m "feat(web): 静态模式 UI 裁剪（导航/写操作/文件区/真值叠加）"
```

---

## 任务 10：`build_web.py` 编排与 gh-pages 部署

**文件：** 创建仓库根 `build_web.py`

- [ ] **步骤 1：实现**

```python
#!/usr/bin/env python3
"""静态网页版一键构建 + 部署（规格 §6）。

步骤：快照 → ONNX 导出校验 → VITE_STATIC=1 前端构建 → gh-pages 推送。
用法：
    backend/.venv/bin/python build_web.py              # 构建 + 部署
    backend/.venv/bin/python build_web.py --no-deploy  # 只构建
环境变量：
    ALN_WEB_REPO   部署仓库 URL（默认 https://github.com/bzJing925/aln-data-web.git）
    GITHUB_TOKEN   推送 token（只读环境变量，不落盘）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DEPLOY_DIR = ROOT / ".web-deploy"

DEFAULT_REPO = "https://github.com/bzJing925/aln-data-web.git"


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    print(f"\n>> {' '.join(str(c) for c in cmd)}")
    subprocess.check_call([str(c) for c in cmd], cwd=cwd, env=env)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true")
    args = ap.parse_args()
    py = BACKEND / ".venv" / "bin" / "python"
    python = str(py) if py.exists() else sys.executable

    print("=" * 60 + "\nStep 1: 数据快照\n" + "=" * 60)
    run([python, "scripts/export_web_snapshot.py"], cwd=BACKEND)

    print("=" * 60 + "\nStep 2: ONNX 导出 + 对齐校验\n" + "=" * 60)
    run([python, "scripts/export_surrogate_onnx.py"], cwd=BACKEND)

    print("=" * 60 + "\nStep 3: 前端静态构建\n" + "=" * 60)
    env = {**os.environ, "VITE_STATIC": "1"}
    run(["npm", "run", "build"], cwd=FRONTEND, env=env)

    if args.no_deploy:
        print("\n构建完成（未部署）。本地预览：cd frontend/dist && python3 -m http.server 8642")
        return

    print("=" * 60 + "\nStep 4: 部署 gh-pages\n" + "=" * 60)
    repo = os.environ.get("ALN_WEB_REPO", DEFAULT_REPO)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("缺少 GITHUB_TOKEN 环境变量（只读使用，不会写入任何配置）")
    auth_repo = repo.replace("https://", f"https://x-access-token:{token}@")

    if not (DEPLOY_DIR / ".git").exists():
        if DEPLOY_DIR.exists():
            shutil.rmtree(DEPLOY_DIR)
        run(["git", "clone", "--branch", "gh-pages", "--single-branch", auth_repo,
             str(DEPLOY_DIR)], cwd=ROOT)
    # 清旧文件（保留 .git），拷新 dist
    for p in DEPLOY_DIR.iterdir():
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    shutil.copytree(FRONTEND / "dist", DEPLOY_DIR, dirs_exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    run(["git", "add", "-A"], cwd=DEPLOY_DIR)
    run(["git", "commit", "-m", f"deploy: 数据快照 {stamp}"], cwd=DEPLOY_DIR)
    run(["git", "push", auth_repo, "gh-pages:gh-pages"], cwd=DEPLOY_DIR)
    print("\n已部署。Pages 设置：仓库 Settings → Pages → Source 选 gh-pages 分支根目录")


if __name__ == "__main__":
    main()
```

注意：`git clone` 失败（gh-pages 分支尚不存在，新仓库）时改用 orphan 流程兜底——实现时处理：`clone` 失败则 `git init DEPLOY_DIR && cd DEPLOY_DIR && git checkout --orphan gh-pages && git rm -rf .`。commit 时空提交保护：`git diff --cached --quiet && echo 无变化跳过 || git commit ...`。

- [ ] **步骤 2：--no-deploy 冒烟**

```bash
backend/.venv/bin/python build_web.py --no-deploy
```

预期：快照打印 counts、ONNX 对齐校验通过、dist 产出含 webdata/（parquet+json+onnx）。

- [ ] **步骤 3：Commit** — `git add build_web.py && git commit -m "feat(web): 一键构建部署脚本（快照→ONNX→构建→gh-pages）"`

---

## 任务 11：端到端验证 + 文档

- [ ] **步骤 1：本地静态服务 + Playwright 全链路**

```bash
cd frontend/dist && python3 -m http.server 8642 &
```

Playwright 断言（系统 python3 有 playwright）：
1. `/` 仪表盘统计显示非零器件数
2. `/explore` 选一个批次筛选，结果行数 > 0
3. `/surrogate` 拖 EG=1.0/FL=0.5/area=1500/pf=0：谱图渲染、参数表有值；且与黄金夹具 `surrogate.json` 同输入的 z_db 逐点 |diff| < 0.05 dB（在页面 evaluate 里读 trace 数据对比）
4. `/batches` 列表含 `#2`，点进详情器件表有行

- [ ] **步骤 2：更新使用说明.md**

在 §1 后新增「§1.4 静态网页版」：构建命令、部署仓库/环境变量、本地预览、数据更新频率说明。

- [ ] **步骤 3：Commit**

```bash
git add 使用说明.md && git commit -m "docs: 使用说明补静态网页版构建部署"
```

---

## 自检结果（计划编写后核对）

- 规格 §3 快照各文件 → 任务 1/2 ✓；§4 适配层/裁剪 → 任务 8/9 ✓；§5 ONNX+TS 移植 → 任务 3/5/6/7 ✓；§6 构建部署 → 任务 10 ✓；§7 错误处理 → 任务 8（fetchJson 抛错 + 页面降级沿用现有 ErrorBoundary）+ 任务 3（构建中止）✓；§8 测试 → 各任务 TDD + 任务 11 e2e ✓；§9 验收 → 任务 11 ✓
- 已知风险备注：onnxruntime-web 在 vitest node 环境的 wasm 加载可能失败 → 任务 7 给了 e2e 降级路径；Transformer ONNX 导出失败时降级为预计算全网格谱（规格 §10），届时在任务 3 失败后回到规格讨论
- 类型一致性：surrogatePredictOnnx 返回 OnnxPredictResult（params/freq_ghz/z_db/sample_freq/sample_z + z_base），predictSurrogate 适配层映射为 API 形状（z/z_base/freq）——任务 7 步骤 3 需包含 z_base 字段（已在任务 8 注明）
