# PINN 光谱重建模型 — 代码改动日志

> 生成时间：2026-06-03  
> 会话主题：训练完成后自动选择迭代方向 → API 接入 → 增量训练准备  

---

## 一、本次会话新增/修改的文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/ml/inference.py` | **新增** | PINN 推理模块，提供懒加载 + 快速 S11_dB 预测 |
| `backend/app/api/devices.py` | **修改** | `GET /devices/{id}/sparam` 新增 `?fast=1` 查询参数，支持 PINN 快速路径 |
| `backend/app/ml/checkpoints/` | **新增目录** | 存放 v1_opt 训练产物（vae.pt / residual_net.pt / base_latents.json / params_norm.json） |
| `docs/api.md` | **修改** | 补充 PINN 快速路径的 API 文档 |
| `backend/scripts/train_pinn_spectrum.py` | **修改** | 新增 `--pretrained-vae` / `--pretrained-residual` 参数，支持增量训练（预训练权重加载） |

---

## 二、各功能详细说明与算法原理

### 2.1 PINN 推理模块 `app/ml/inference.py`

#### 功能
为已训练的 PINN 模型提供生产级推理接口。核心函数：

```python
predict_s11_db(device: Device) -> tuple[list[float], list[float]] | None
```

输入一个 SQLAlchemy `Device` ORM 对象，输出 `(freq_ghz, s11_db)`。若模型未加载、参数缺失或推理失败，返回 `None`（触发 fallback）。

#### 算法流程

```
Device ORM
    │
    ▼
提取 6 维参数向量: [area_um2, x, y, eg, fl, ag]
    │
    ▼
参数归一化: (params - params_mean) / params_std
    │                          └─ 从 params_norm.json 加载
    ▼
ResidualNet (MLP) → Δz (latent 偏移)
    │
    ▼
z = z_base + Δz            └─ z_base 从 base_latents.json 加载
    │
    ▼
SpectralVAE.decode(z) → 重建频谱 (1, 1, 1001)
    │
    ▼
输出: 1001 个 S11_dB 值 + 频率轴
```

#### 所用算法

| 组件 | 算法 | 维度/规模 |
|---|---|---|
| **SpectralVAE** | 1D CNN 编码器 + 全连接瓶颈 + 1D 转置 CNN 解码器 | 输入 (B,1,1001) → latent (B,8) → 输出 (B,1,1001) |
| **ResidualNet** | 3 层 MLP: Linear(6→32→16→8) + ReLU + Dropout(0.1) | 输入 6 维器件参数，输出 8 维 latent 偏移 |
| **参数归一化** | Z-score: `(x - μ) / σ` | μ=[300,0,0,0,0,0], σ=[141.42, 1e-6, 1e-6, 1e-6, 1e-6, 1e-6] |
| **推理加速** | 懒加载全局单例 + MPS/CUDA 自动设备选择 | Apple Silicon MPS 实测 ~0.62 ms / 条 |

#### 模型产物（由 `scripts/train_pinn_spectrum.py` 生成）

```
app/ml/checkpoints/
├── vae.pt              (8.4 MB)  SpectralVAE 状态字典
├── residual_net.pt     (9.4 KB)  ResidualNet 状态字典
├── smart_sampler.pt    (10 KB)   SmartSampler 状态字典（预留，API 暂未使用）
├── base_latents.json   (29 KB)   各 batch 的 z_base + base_spectrum
├── params_norm.json    (248 B)   参数归一化统计量
└── training_summary.txt          训练摘要
```

---

### 2.2 API 快速路径 `app/api/devices.py`

#### 功能
在现有 `GET /api/devices/{id}/sparam` 端点上追加 `?fast=1` 查询参数，启用 PINN 推理替代 skrf 文件读取。

#### 行为矩阵

| `param` | `fast=1` | 实际路径 | 说明 |
|---|---|---|---|
| `s11_db` | 是 | PINN → fallback skrf | 优先走 PINN，失败自动回退 |
| `s11_db` | 否 | skrf | 原行为不变 |
| `s11_phase` / `z_mag_db` / … | 是/否 | skrf | PINN 不支持，始终走 skrf |

#### 响应差异

```json
// skrf 路径
{"device_id": 123, "freq_ghz": [...12000点...], "values": [...], "param": "s11_db", "file_path": "...", "source": "skrf"}

// PINN 路径
{"device_id": 123, "freq_ghz": [...1001点...], "values": [...], "param": "s11_db", "file_path": "...", "source": "pinn"}
```

#### 性能对比

| 路径 | 单次延迟 | 吞吐量 | 依赖 |
|---|---|---|---|
| skrf | ~50–100 ms | ~10–20 spectra/s | 磁盘 I/O + scikit-rf 解析 |
| **PINN** | **~0.62 ms** | **~1,600 spectra/s** | 仅数据库参数读取 + GPU 推理 |

---

### 2.3 训练脚本增强 `scripts/train_pinn_spectrum.py`

#### 新增功能
支持从预训练权重恢复，实现**增量训练 / 迁移学习**流程。

#### 新增参数

```bash
--pretrained-vae       PATH   # 预训练 VAE 权重路径（.pt）
--pretrained-residual  PATH   # 预训练 ResidualNet 权重路径（.pt）
```

#### 使用场景

**场景 A：合成数据预训练 → 真实数据微调**
```bash
# Step 1: 大量合成数据预训练 VAE
python scripts/train_pinn_spectrum.py \
  --mode synthetic --n-batches 50 --n-devices-per-batch 200 \
  --phase-a-epochs 80 --phase-b-epochs 20 --phase-c-epochs 0 \
  --output-dir ./pinn_pretrain

# Step 2: 真实数据微调（加载预训练 VAE）
python scripts/train_pinn_spectrum.py \
  --mode s1p-batch --s1p-dir /path/to/s1p \
  --pretrained-vae ./pinn_pretrain/vae.pt \
  --phase-a-epochs 10 --phase-b-epochs 60 \
  --output-dir ./pinn_finetune
```

**场景 B：断点续训**
```bash
python scripts/train_pinn_spectrum.py \
  --mode s1p-batch --s1p-dir /path/to/s1p \
  --pretrained-vae ./previous_run/vae.pt \
  --pretrained-residual ./previous_run/residual_net.pt \
  --epochs 100 --output-dir ./pinn_resume
```

---

## 三、PINN 核心模型架构（完整回顾）

### 3.1 SpectralVAE（频谱变分自编码器）

```python
# 编码器: (B, 1, 1001) → (B, 16)
Conv1d(1→16, k=7, s=2) → BN → ReLU
Conv1d(16→32, k=5, s=2) → BN → ReLU
Conv1d(32→64, k=3, s=2) → BN → ReLU
Flatten → Linear(64×125, 256) → ReLU → Linear(256, latent_dim×2)

# 重参数化: μ, logvar → z = μ + ε·exp(0.5·logvar)

# 解码器: (B, 8) → (B, 1, 1001)
Linear(8, 256) → ReLU → Linear(256, 64×125) → ReLU
Reshape → ConvTranspose1d(64→32, k=4, s=2) → BN → ReLU
ConvTranspose1d(32→16, k=4, s=2) → BN → ReLU
ConvTranspose1d(16→1, k=4, s=2)
```

### 3.2 ResidualNet（器件参数 → Latent 偏移）

```python
Sequential(
    Linear(6, 32), ReLU, Dropout(0.1),
    Linear(32, 16), ReLU, Dropout(0.1),
    Linear(16, latent_dim)
)
# 输入: [area_um2, x, y, eg, fl, ag]（归一化后）
# 输出: Δz，与 z_base 相加得到最终 latent
```

### 3.3 PINNSpectralLoss（6 项加权物理约束损失）

```
L_total = L_recon + λ_kl·L_kl + λ_coherence·L_coherence
          + λ_smoothness·L_smoothness + λ_order·L_order + λ_far·L_far

L_recon      = MSE(recon, target)
L_kl         = -0.5 · Σ(1 + logvar - μ² - exp(logvar))
L_coherence  = MSE(z, z_base)          # 同 batch 压电层厚度一致
L_smoothness = mean((recon[i-1] - 2·recon[i] + recon[i+1])²)  # 二阶导数惩罚
L_order      = ReLU(fs_pred - fp_pred)  # 物理约束: fs < fp
L_far        = MSE(recon[far_mask], target[far_mask])  # 远带回归基准
```

权重: `λ_coherence=0.05, λ_smoothness=0.01, λ_order=5.0, λ_far=0.05, λ_kl=0.001`

### 3.4 SmartSampler（梯度感知降采样头）

```python
# 输入: 频谱 S 的一阶/二阶数值梯度 → 3 通道
# 输出: 每频点保留概率 p_i ∈ [0,1]
Conv1d(3→16, k=7) → ReLU
Conv1d(16→8, k=5) → ReLU
Conv1d(8→1, k=3) → Sigmoid
```

**注意**：当前 API 快速路径**未启用** SmartSampler，因为生产场景优先保证全频点重建质量，降采样用于边缘设备部署或带宽受限场景。

---

## 四、训练策略（三阶段 + 调度）

```
Phase A: VAE only (epochs 1–20)
  └─ 冻结 ResidualNet，训练 VAE 重建能力
  └─ 学习率: lr × 1.0

Phase B: VAE + ResidualNet (epochs 21–120)
  └─ 联合训练，ResidualNet 学习器件参数 → latent 偏移
  └─ 学习率: lr × 0.5

Phase C: + SmartSampler (epochs 121–200)
  └─ 加入降采样头，优化保留点重建质量
  └─ 学习率: lr × 0.3

全局调度: CosineAnnealingLR（每个 phase 独立）
早停: val_mse 连续 --early-stop-patience 个 epoch 不下降则恢复最佳模型
梯度裁剪: max_norm=1.0
```

---

## 五、当前性能基准（v1_opt 训练结果）

| 指标 | 数值 |
|---|---|
| 最佳 val_mse | 0.094024 dB²（epoch 85，早停） |
| 全量 RMSE 均值 | 0.254 dB |
| 全量 RMSE 中位数 | 0.184 dB |
| 最差样本 | E1-3（RMSE = 1.260 dB，浅谷器件过度重建） |
| 推理速度 | 0.62 ms / 条（MPS）|
| 批量推理 | 0.55 ms / batch-128 |

---

## 六、已知限制与后续方向

1. **数据量瓶颈**：当前仅 240 条真实 S1P 数据，ResidualNet 的输入中 x/y/eg/fl/ag 几乎全为 0，模型实际只学到 `area_um2` 的 5 级映射。
2. **E 区域误差高**：area_um2=500 的器件谷深浅，ResidualNet 将其拉向 batch 基准深谷形状。
3. **频点分辨率**：PINN 输出 1001 点，skrf 输出 ~12000 点；对精细谐振特征分析需 fallback。
4. **增量训练已准备**：脚本已支持 `--pretrained-vae`，但受限于 60s Shell 超时，长训练需分段或本地手动执行。
