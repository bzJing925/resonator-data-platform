# PINN 运行指南

> 适用场景：训练模型、本地推理测试、接入后端 API

---

## 一、环境检查

```bash
cd backend

# 检查 PyTorch + MPS（Apple Silicon GPU）
python3 -c "import torch; print(torch.__version__, torch.backends.mps.is_available())"
# 期望输出: 2.11.0 True

# 检查核心依赖
python3 -c "import numpy; import torch; import torch.nn.functional as F; print('OK')"
```

**当前环境状态**：
- ✅ PyTorch 2.11.0 + MPS
- ✅ NumPy
- ❌ scikit-rf（`skrf`）— 后端 API 解析 .s1p 需要
- ❌ SQLAlchemy / psycopg — db 模式需要
- ❌ FastAPI — 启动 REST API 需要

**安装完整依赖**（若需后端 API / db 模式）：
```bash
cd backend
# 方式 1: uv（项目配置）
# uv sync --all-extras

# 方式 2: pip（当前环境）
pip install scikit-rf sqlalchemy psycopg fastapi uvicorn
```

---

## 二、训练模型

### 2.1 合成数据模式（不依赖数据库，当前环境可直接运行）

```bash
cd backend

# 快速验证（8 epochs，约 30 秒）
python3 scripts/train_pinn_spectrum.py \
  --mode synthetic \
  --n-batches 3 \
  --n-devices-per-batch 50 \
  --epochs 8 \
  --latent-dim 8 \
  --output-dir ./pinn_test

# 正式预训练（100 epochs，约 10–20 分钟）
python3 scripts/train_pinn_spectrum.py \
  --mode synthetic \
  --n-batches 50 \
  --n-devices-per-batch 200 \
  --epochs 100 \
  --phase-a-epochs 80 \
  --phase-b-epochs 20 \
  --phase-c-epochs 0 \
  --latent-dim 8 \
  --device mps \
  --output-dir ./pinn_pretrain
```

**产物**：
```
pinn_pretrain/
├── vae.pt              # SpectralVAE 权重
├── residual_net.pt     # ResidualNet 权重
├── smart_sampler.pt    # SmartSampler 权重
├── base_latents.json   # 各 batch 基准 latent
├── params_norm.json    # 参数归一化统计
└── training_summary.txt
```

### 2.2 S1P 文件模式（真实数据，当前环境可直接运行）

```bash
cd backend

# 单 batch（240 文件全部一起训练）
python3 scripts/train_pinn_spectrum.py \
  --mode s1p-batch \
  --s1p-dir ../#3 \
  --latent-dim 8 \
  --epochs 100 \
  --phase-a-epochs 20 \
  --phase-b-epochs 60 \
  --phase-c-epochs 20 \
  --early-stop-patience 30 \
  --noise-std 0.02 \
  --device mps \
  --output-dir ./pinn_real

# 按 Area 分组（A/B/C/D/E 各一个 batch）
python3 scripts/train_pinn_spectrum.py \
  --mode s1p-batch \
  --s1p-dir ../#3 \
  --group-by-area \
  --latent-dim 8 \
  --epochs 60 \
  --device mps \
  --output-dir ./pinn_real_area
```

### 2.3 增量训练：合成预训练 → 真实微调

```bash
# Step 1: 合成预训练
python3 scripts/train_pinn_spectrum.py \
  --mode synthetic --n-batches 50 --n-devices-per-batch 200 \
  --latent-dim 8 --epochs 100 --phase-a-epochs 80 --phase-b-epochs 20 \
  --phase-c-epochs 0 --device mps --output-dir ./pinn_pretrain

# Step 2: 真实数据微调（加载预训练 VAE）
python3 scripts/train_pinn_spectrum.py \
  --mode s1p-batch --s1p-dir ../#3 \
  --pretrained-vae ./pinn_pretrain/vae.pt \
  --latent-dim 8 --epochs 80 \
  --phase-a-epochs 10 --phase-b-epochs 60 --phase-c-epochs 10 \
  --device mps --output-dir ./pinn_finetune
```

### 2.4 数据库模式（需要 PostgreSQL + SQLAlchemy）

```bash
# 确保数据库运行: docker compose up postgres

python3 scripts/train_pinn_spectrum.py \
  --mode db \
  --db-url "postgresql+psycopg://aln:123456@localhost:5432/aln" \
  --data-root "/Users/jingbozuo/Downloads/aln-data-master/data" \
  --latent-dim 8 \
  --epochs 60 \
  --device mps \
  --output-dir ./pinn_db
```

---

## 三、推理测试（不依赖数据库）

### 3.1 直接调用推理模块

```bash
cd backend

python3 -c "
import app.ml.inference as inf
from unittest.mock import MagicMock

# 加载模型（自动识别 checkpoints/ 目录）
inf.load_models()

# 构造 mock Device（模拟 ORM 对象）
dev = MagicMock()
dev.id = 1
dev.area_um2 = 300
dev.x = 0
dev.y = 0
dev.eg = 0.0
dev.fl = 0.0
dev.ag = 0.0
dev.batch = MagicMock()
dev.batch.f_start_ghz = 4.0
dev.batch.f_end_ghz = 7.0

# 推理
freq, s11 = inf.predict_s11_db(dev)
print(f'频点数: {len(freq)}')
print(f'频率范围: [{freq[0]:.2f}, {freq[-1]:.2f}] GHz')
print(f'S11 范围: [{min(s11):.4f}, {max(s11):.4f}] dB')
"
```

### 3.2 性能 Benchmark

```bash
python3 scripts/test_pinn_performance.py \
  --s1p-dir ../#3 \
  --model-dir ./app/ml/checkpoints \
  --device mps
```

输出示例：
```
数据处理: 240 文件, 2.4s
单条推理: 0.62 ms
批量推理(batch=128): 0.55 ms
RMSE 均值: 0.254 dB
RMSE 中位数: 0.184 dB
RMSE 最大: 1.260 dB (E1-3)
```

---

## 四、启动后端 API（需要完整依赖）

### 4.1 启动服务

```bash
cd backend

# 方式 1: uvicorn 直接启动
uvicorn app.main:app --reload --port 8000

# 方式 2: Docker Compose 全栈启动
# cd ../deploy && docker compose up
```

### 4.2 调用 PINN 快速路径

```bash
# 标准路径（skrf 读取 .s1p，~50–100ms）
curl "http://localhost:8000/api/devices/123/sparam?param=s11_db"

# PINN 快速路径（~0.6ms）
curl "http://localhost:8000/api/devices/123/sparam?param=s11_db&fast=1"
```

响应对比：
```json
// fast=1, source=pinn
{
  "device_id": 123,
  "freq_ghz": [4.0, 4.003, ..., 7.0],
  "values": [-0.32, -0.33, ..., -1.85],
  "param": "s11_db",
  "file_path": "...",
  "source": "pinn"
}

// fast=0 或 fallback, source=skrf
{
  "device_id": 123,
  "freq_ghz": [4.00025, 4.00050, ..., 7.0],
  "values": [-0.26, -0.27, ..., -12.5],
  "param": "s11_db",
  "file_path": "...",
  "source": "skrf"
}
```

---

## 五、模型产物部署清单

将训练好的模型接入 API，只需复制以下文件到 `backend/app/ml/checkpoints/`：

```bash
cp pinn_outputs/vae.pt              backend/app/ml/checkpoints/
cp pinn_outputs/residual_net.pt     backend/app/ml/checkpoints/
cp pinn_outputs/base_latents.json   backend/app/ml/checkpoints/
cp pinn_outputs/params_norm.json    backend/app/ml/checkpoints/
# smart_sampler.pt 可选（API 当前未使用）
```

---

## 六、常见问题

**Q: 为什么训练脚本在 60 秒后被 kill？**  
A: 当前 Shell 环境有 60s 超时限制。正式训练请在本地终端运行，或将 epoch 数拆小分段执行。

**Q: 没有 GPU 能跑吗？**  
A: 能。去掉 `--device mps`，脚本会自动 fallback 到 CPU。速度约慢 5–10 倍。

**Q: `skrf` 安装失败？**  
A: `pip install scikit-rf`（注意包名是 scikit-rf，不是 skrf）。

**Q: 只有 240 个 S1P 文件，能训练出好模型吗？**  
A: 当前模型在典型器件（A–D 区域）上表现良好（RMSE ~0.18 dB），E 区域（大面积浅谷）误差较高。要突破瓶颈需要增量数据或合成预训练。
