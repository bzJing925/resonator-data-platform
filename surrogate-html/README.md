# surrogate-html —— 离线单文件「代理模型」页

把平台的「代理模型」页面（`frontend/src/pages/Surrogate.tsx`）完整移植成**一个 HTML 文件**：
几何参数（EG/FL 滑杆、AG 输入、面积下拉、PF 开关）→ 两阶段 ONNX 神经网络 →
7 项性能参数（fs/fp/kt²/Qs/Qp/Qs-bodeQ/Qp-bodeQ）+ 全频段 |Z11| (dB) 阻抗谱曲线。

ONNX 模型、基准数据、onnxruntime wasm 运行时、Plotly、应用 JS/CSS **全部内联**，
双击即可用浏览器打开（`file://`），不依赖任何网络与服务器。

## 重新生成

```bash
backend/.venv/bin/python surrogate-html/build.py
# 产物：~/Desktop/AlN代理模型.html（约 27 MB）
```

选项：

| 参数 | 说明 |
|---|---|
| `--with-truth` | 额外内嵌格均值真值叠加数据（cell_means.json），页面多出「格均值真值」曲线；默认关闭 |
| `--out PATH` | 自定义输出路径 |
| `--skip-export` | 跳过 torch/数据导出，复用 `assets/`（只改了模板时用，秒级完成） |
| `--no-check` | 导出 ONNX 时跳过 torch↔onnxruntime 对齐校验（首次构建建议保留校验） |

首次构建要加载 torch 并做 100 例对齐校验，需几分钟。

## 目录

```
surrogate-html/
├── build.py          # 一键生成器（导数据 → 拷 vendor → 装配内联）
├── export_onnx.py    # torch→ONNX 导出（改编自 backend/scripts/export_surrogate_onnx.py）
├── export_data.py    # 基准/元数据/网格/真值导出（改编自 backend/scripts/export_web_surrogate_data.py）
├── template/
│   ├── index.html    # 页面骨架 + 内联 CSS + %%TOKEN%% 占位符
│   └── app.js        # 推理链 + UI 的原生 JS 移植（无框架、无 import）
├── vendor/           # 【gitignored】build.py 从 frontend/node_modules 拷贝
└── assets/           # 【gitignored】导出的 stage1.onnx/stage2.onnx/*.json
```

改页面只动 `template/`，再跑 `build.py --skip-export` 即可。

## 使用

- 双击生成的 HTML（推荐 Chrome/Edge）。首次打开要解压约 20MB 内嵌资源，需数秒。
- EG/FL 拖滑杆或改数字，150ms 去抖后自动重算；过期请求自动丢弃。
- 「B0 基准 / 稀疏点」复选框控制曲线叠加；「格均值真值」仅在 `--with-truth` 构建时出现。
- 输入组合不在训练网格内时显示「网格外」提示；面积档为仿真外推档（600 μm²）时显示对应提示。

## 与平台版的差异（数值注意）

- 推理链与平台浏览器版 `frontend/src/ml/surrogateOnnx.ts` 逐函数一致（x/cond 归一化、
  确定性 256 点稀疏采样、not-a-knot 样条基线、两阶段 ONNX）。
- **不含服务器端 zp_corrector 顶点修正**（与静态网页版一致）——服务器 HTTP 接口的
  预测会多做一步修正，本文件输出与之在谐振峰附近可能有零点几 dB 的差异，属预期。
- 真值叠加为「格均值谱」（同静态网页版），非服务器版的「同格各器件谱」。
