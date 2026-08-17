"""代理模型交互式探索器：本地网页逐个输入结构参数 → 预测阻抗谱 + 性能指标。

叠加显示：B0 基准谱、稀疏采样点、同结构格的真实器件谱（若该格已流片）。
出格（未流片设计）时给出外推提示。

用法：
    uv run python scripts/surrogate_viewer.py --data ./surrogate_data \
        --ckpt ./checkpoints_surrogate --port 8600
    # 浏览器打开 http://localhost:8600
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

# 把 backend 目录加入路径，以便 import app（同 train_sparse_reconstructor.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.surrogate.infer import SurrogatePredictor  # noqa: E402

_PLOTLY_CANDIDATES = [
    PROJECT_ROOT.parent / "frontend/node_modules/plotly.js-dist-min/plotly.min.js",
    Path(
        "/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/frontend/node_modules/plotly.js-dist-min/plotly.min.js"
    ),
]

_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>代理模型探索器</title>
<script src="/plotly.js"></script>
<style>
 body { font-family: -apple-system, "PingFang SC", sans-serif; margin: 16px; }
 .panel { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 8px; }
 .field { display: flex; flex-direction: column; font-size: 13px; }
 .field label { font-weight: 600; margin-bottom: 2px; }
 .field input, .field select { font-size: 14px; padding: 3px 6px; width: 110px; }
 .field input[type=range] { width: 160px; }
 #warn { color: #b45309; font-size: 13px; min-height: 18px; margin: 4px 0; }
 table { border-collapse: collapse; font-size: 13px; margin-top: 4px; }
 td, th { border: 1px solid #ddd; padding: 4px 10px; text-align: right; }
 th { background: #f5f5f5; }
 .checks { display: flex; gap: 16px; align-items: center; font-size: 13px; }
</style>
</head>
<body>
<h3>AlN BAW 代理模型探索器</h3>
<div class="panel">
  <div class="field"><label>EG</label><input id="eg" type="number" step="0.05" value="1.0">
    <input id="eg_r" type="range" step="0.05"></div>
  <div class="field"><label>FL</label><input id="fl" type="number" step="0.05" value="0.5">
    <input id="fl_r" type="range" step="0.05"></div>
  <div class="field"><label>AG (μm)</label>
    <input id="ag" type="number" step="0.1" value="1.0"></div>
  <div class="field"><label>面积 (μm²)</label><select id="area"></select></div>
  <div class="field"><label>PF (massloading)</label><select id="pf">
    <option value="0">无</option><option value="1">有</option></select></div>
  <div class="checks">
    <label><input type="checkbox" id="show_base" checked> B0 基准</label>
    <label><input type="checkbox" id="show_pts" checked> 稀疏点</label>
    <label><input type="checkbox" id="show_true" checked> 同格真值</label>
  </div>
</div>
<div id="warn"></div>
<div id="chart" style="width:1000px;height:520px"></div>
<table id="params"></table>
<script>
let OPT = null;
const $ = id => document.getElementById(id);

async function boot() {
  OPT = await (await fetch('/api/options')).json();
  for (const a of OPT.areas) $('area').add(new Option(a, a));
  $('area').value = OPT.areas[Math.floor(OPT.areas.length/2)];
  for (const [nm, lo, hi] of [['eg', OPT.eg_min, OPT.eg_max], ['fl', OPT.fl_min, OPT.fl_max]]) {
    $(nm+'_r').min = lo; $(nm+'_r').max = hi; $(nm+'_r').value = $(nm).value;
    $(nm).min = lo; $(nm).max = hi;
  }
  for (const id of ['eg','fl','ag','area','pf','show_base','show_pts','show_true'])
    $(id).onchange = update;
  $('eg_r').oninput = () => { $('eg').value = $('eg_r').value; update(); };
  $('fl_r').oninput = () => { $('fl').value = $('fl_r').value; update(); };
  update();
}

let timer = null;
function update() { clearTimeout(timer); timer = setTimeout(doUpdate, 120); }

async function doUpdate() {
  const q = new URLSearchParams({eg: $('eg').value, fl: $('fl').value, ag: $('ag').value,
                                 area: $('area').value, pf: $('pf').value});
  const r = await (await fetch('/api/predict?' + q)).json();
  $('warn').textContent = r.in_grid ? '' :
    '⚠ 该结构组合不在训练网格内（或该格未流片），结果为外推/插值';
  const traces = [];
  if ($('show_true').checked && r.true_curves)
    for (const t of r.true_curves)
      traces.push({x: r.freq, y: t, mode: 'lines', name: '真值',
                   line: {color: '#16a34a', width: 1}, opacity: 0.55, showlegend: false});
  if ($('show_base').checked)
    traces.push({x: r.freq, y: r.z_base, mode: 'lines', name: 'B0 基准',
                 line: {color: '#9ca3af', dash: 'dash', width: 1.2}});
  traces.push({x: r.freq, y: r.z, mode: 'lines', name: '代理模型',
               line: {color: '#2563eb', width: 2}});
  if ($('show_pts').checked)
    traces.push({x: r.sample_freq, y: r.sample_z, mode: 'markers', name: '稀疏点(Stage 1)',
                 marker: {color: '#ea580c', size: 3}});
  Plotly.react('chart', traces, {xaxis: {title: 'Frequency (GHz)'},
               yaxis: {title: '|Z11| (dB)'}, margin: {t: 30}});
  const P = r.params, T = r.true_mean;
  const rows = [['fs (GHz)', P.fs_ghz, T?.fs_ghz], ['fp (GHz)', P.fp_ghz, T?.fp_ghz],
    ['kt² (%)', P.k2eff_pct, T?.k2eff_pct], ['Qs', P.qs, T?.qs], ['Qp', P.qp, T?.qp],
    ['Qs bodeQ', P.qs_bodeq, T?.qs_bodeq], ['Qp bodeQ', T ? P.qp_bodeq : P.qp_bodeq, T?.qp_bodeq]];
  let h = '<tr><th>指标</th><th>预测</th>' + (T ? '<th>同格真值均值</th>' : '') + '</tr>';
  for (const [n, p, t] of rows)
    h += `<tr><td>${n}</td><td>${(+p).toPrecision(5)}</td>` +
         (T ? `<td>${t == null ? '-' : (+t).toPrecision(5)}</td>` : '') + '</tr>';
  $('params').innerHTML = h;
}
boot();
</script>
</body>
</html>
"""


def create_app(data_dir: str, ckpt_dir: str) -> FastAPI:
    data, ckpt = Path(data_dir), Path(ckpt_dir)
    stage2 = ckpt / "stage2_ft.pt"
    if not stage2.exists():
        stage2 = ckpt / "stage2.pt"
    predictor = SurrogatePredictor(
        stage1_path=ckpt / "stage1.pt",
        stage2_path=stage2,
        stage2_config=ckpt / "stage2_config.json",
        baseline_path=data / "baseline.npz",
        meta_path=data / "meta.json",
    )
    with np.load(data / "dataset.npz") as z:
        npz = {k: z[k] for k in z.files}
    xs, yp = npz["x_struct"], npz["y_params"]
    cells: dict[tuple, list[int]] = {}
    for i in range(len(xs)):
        key = (round(float(xs[i, 0]), 4), round(float(xs[i, 1]), 4), int(xs[i, 3]), int(xs[i, 4]))
        cells.setdefault(key, []).append(i)

    plotly = next((p for p in _PLOTLY_CANDIDATES if p.exists()), None)
    if plotly is None:
        raise SystemExit(f"找不到 plotly.min.js（尝试路径：{_PLOTLY_CANDIDATES}）")

    app = FastAPI(title="surrogate-viewer")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML

    @app.get("/plotly.js")
    def plotly_js() -> FileResponse:
        return FileResponse(plotly)

    @app.get("/api/options")
    def options() -> dict:
        return {
            "eg_min": float(xs[:, 0].min()),
            "eg_max": float(xs[:, 0].max()),
            "fl_min": float(xs[:, 1].min()),
            "fl_max": float(xs[:, 1].max()),
            "areas": sorted({int(a) for a in xs[:, 3]}),
        }

    @app.get("/api/predict")
    def predict(eg: float, fl: float, area: float, pf: int = 0, ag: float = 1.0) -> dict:
        r = predictor.predict(eg, fl, area, float(pf), ag)
        _, z_base = predictor.baseline.lookup(area, float(pf))
        key = (round(eg, 4), round(fl, 4), int(area), int(pf))
        idxs = cells.get(key, [])[:5]
        out = {
            "params": r["params"],
            "freq": np.round(r["freq_ghz"], 5).tolist(),
            "z": np.round(r["z_db"], 4).tolist(),
            "z_base": np.round(z_base, 4).tolist(),
            "sample_freq": np.round(r["sample_freq"], 5).tolist(),
            "sample_z": np.round(r["sample_z"], 4).tolist(),
            "in_grid": bool(idxs),
        }
        if idxs:
            cols = ["fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "qs_bodeq", "qp_bodeq"]
            out["true_curves"] = np.round(npz["full_z"][idxs], 4).tolist()
            out["true_mean"] = {c: float(np.mean(yp[idxs, j])) for j, c in enumerate(cols)}
        return out

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./surrogate_data")
    ap.add_argument("--ckpt", default="./checkpoints_surrogate")
    ap.add_argument("--port", type=int, default=8600)
    args = ap.parse_args()
    app = create_app(args.data, args.ckpt)
    print(f"打开浏览器: http://localhost:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
