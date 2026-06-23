#!/bin/bash
# 稀疏采样训练自动循环（绕过 60s 后台限制）
# 用法: bash scripts/train_loop.sh

set -e

cd "$(dirname "$0")/.."

ARGS="--s1p-dir ../#3 --piezo-thickness 308 --target-k 300 \
  --d-model 64 --n-encoder-layers 4 --n-heads 4 \
  --epochs 50 --phase1-epochs 15 --batch-size 8 \
  --lr 1e-3 --early-stop-patience 15 --device mps \
  --output-dir ./sparse_recon_v2 --steps-per-run 8"

# 第一轮（无 resume）
if [ ! -f sparse_recon_v2/train_state.json ]; then
    echo "=== 第一轮训练（初始化）==="
    python3 scripts/train_sparse_reconstructor.py $ARGS
fi

# 自动续跑直到完成
while true; do
    if [ -f sparse_recon_v2/train_state.json ]; then
        EPOCH=$(python3 -c "import json; print(json.load(open('sparse_recon_v2/train_state.json'))['epoch'])")
        echo "=== 当前进度: epoch $EPOCH / 50 ==="
        if [ "$EPOCH" -ge 50 ]; then
            echo "训练完成！"
            break
        fi
    fi
    echo "=== 续跑下一轮 ==="
    python3 scripts/train_sparse_reconstructor.py $ARGS --resume
done

echo "=== 验证最佳模型 ==="
python3 -c "
from app.ml.sparse.inference import predict_z11_sparse
import numpy as np
for fname in ['S22_3_A1-1_X0Y0N20_Fail_de.s1p', 'S22_3_C3-1_X0Y0N20_Fail_de.s1p', 'S22_3_E1-1_X0Y0N20_Fail_de.s1p']:
    r = predict_z11_sparse(f'../#3/{fname}', piezo='308', target_k=300)
    if r:
        rmse = np.sqrt(np.mean((np.array(r['z_pred']) - np.array(r['z_true']))**2))
        print(f'{fname}: RMSE={rmse:.3f} dB, 采样点={len(r[\"sample_points\"])}')
"
