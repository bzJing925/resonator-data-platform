"""代理模型接口：结构参数 → 性能参数 + Z11(dB) 阻抗谱。

速度要点：
- 推理器与 npz 首次请求时加载一次（懒单例 + 锁），之后常驻内存；
- 预测按舍入键 LRU 缓存（滑块拖动大量重复值）；
- 推理持锁串行（torch 非线程安全），单次 ~30–80ms，同步 def 走线程池不阻塞事件循环。

训练产物缺失（surrogate_data/ 或 checkpoints_surrogate/ 未部署）时：
status 返回 available=false，前端展示降级提示，平台其余功能不受影响。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException

from app.config import get_settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["surrogate"])

_BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/app/api/ → backend/
_lock = threading.Lock()
_bundle: dict | None = None
_load_error: str | None = None


def _resolve(p: Path) -> Path:
    return p if p.is_absolute() else _BACKEND_DIR / p


def _load_bundle() -> dict | None:
    """首次请求时加载推理器与数据集；失败记录原因并返回 None（不反复重试）。"""
    global _bundle, _load_error
    if os.environ.get("ALN_DISABLE_TORCH") == "1":
        _load_error = "当前系统的 PyTorch 运行检查未通过，代理模型已停用"
        return None
    if _bundle is not None or _load_error is not None:
        return _bundle
    with _lock:
        if _bundle is not None or _load_error is not None:
            return _bundle
        try:
            settings = get_settings()
            data = _resolve(settings.SURROGATE_DATA_DIR)
            ckpt = _resolve(settings.SURROGATE_CKPT_DIR)
            stage2 = ckpt / "stage2_ft.pt"
            if not stage2.exists():
                stage2 = ckpt / "stage2.pt"
            required = [
                ckpt / "stage1.pt",
                stage2,
                ckpt / "stage2_config.json",
                data / "baseline.npz",
                data / "meta.json",
            ]
            missing = [str(p) for p in required if not p.exists()]
            if missing:
                raise FileNotFoundError(f"代理模型产物缺失: {missing}")

            from app.ml.surrogate.infer import SurrogatePredictor

            predictor = SurrogatePredictor(
                stage1_path=ckpt / "stage1.pt",
                stage2_path=stage2,
                stage2_config=ckpt / "stage2_config.json",
                baseline_path=data / "baseline.npz",
                meta_path=data / "meta.json",
            )

            # 同格真值叠加（可选：npz 缺失时仅不叠加，不影响预测）
            cells: dict[tuple, list[int]] = {}
            npz: dict[str, np.ndarray] = {}
            npz_path = data / "dataset.npz"
            if npz_path.exists():
                with np.load(npz_path) as z:
                    npz = {k: z[k] for k in z.files}
                xs = npz["x_struct"]
                for i in range(len(xs)):
                    key = (
                        round(float(xs[i, 0]), 4),
                        round(float(xs[i, 1]), 4),
                        int(xs[i, 3]),
                        int(xs[i, 4]),
                    )
                    cells.setdefault(key, []).append(i)

            meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
            # 仿真外推面积档（可选：sim_extension.npz 存在时，这些面积档仅由校准仿真
            # 伪标签支撑、无实测验证；前端据此显示"仿真外推"注释）
            sim_areas: list[int] = []
            ext_path = data / "sim_extension.npz"
            if ext_path.exists():
                with np.load(ext_path) as z:
                    sim_areas = sorted({int(a) for a in z["cells"][:, 2]})
            _bundle = {
                "predictor": predictor,
                "cells": cells,
                "npz": npz,
                "meta": meta,
                "sim_areas": sim_areas,
            }
            log.info("代理模型加载完成（stage2=%s，同格索引 %d 格）", stage2.name, len(cells))
        except Exception as exc:  # noqa: BLE001 — 降级而非拖垮平台
            _load_error = str(exc)
            log.warning("代理模型不可用: %s", exc)
    return _bundle


def _options(bundle: dict) -> dict:
    sim_areas = bundle.get("sim_areas") or []
    xs = bundle["npz"].get("x_struct") if bundle["npz"] else None
    if xs is not None and len(xs):
        return {
            "eg_min": float(xs[:, 0].min()),
            "eg_max": float(xs[:, 0].max()),
            "fl_min": float(xs[:, 1].min()),
            "fl_max": float(xs[:, 1].max()),
            "areas": sorted({int(a) for a in xs[:, 3]} | set(sim_areas)),
            "sim_only_areas": sim_areas,
        }
    # 无 npz 时用基准表网格
    bl = bundle["predictor"].baseline
    return {
        "eg_min": 0.0,
        "eg_max": 2.0,
        "fl_min": 0.0,
        "fl_max": 2.0,
        "areas": sorted({int(a) for a in bl.areas} | set(sim_areas)),
        "sim_only_areas": sim_areas,
    }


def _metadata():
    from app.schemas.model_metadata import ModelMetadata
    from app.services.model_metadata import describe
    if os.environ.get("ALN_DISABLE_TORCH") == "1":
        return ModelMetadata()
    settings = get_settings()
    return describe(_resolve(settings.SURROGATE_DATA_DIR), _resolve(settings.SURROGATE_CKPT_DIR),
                    _resolve(settings.SURROGATE_EVAL_DIR))


@router.get("/surrogate/status")
def status() -> dict:
    bundle = _load_bundle()
    if bundle is None:
        return {"available": False, "reason": _load_error,
                "metadata": _metadata().model_dump(mode="json")}
    return {"available": True, **_options(bundle), "metadata": _metadata().model_dump(mode="json")}


def prewarm() -> None:
    """后台预热入口：服务就绪后由 lifespan 调度，提前完成懒加载。

    与 /surrogate/status 共用 _load_bundle（幂等 + 锁 + 异常降级为 _load_error），
    避免用户首次打开代理模型页时等待 torch/检查点加载（实测约 29s）。
    """
    _load_bundle()


@lru_cache(maxsize=512)
def _predict_cached(eg: float, fl: float, ag: float, area: float, pf: float) -> dict:
    """按舍入后的键缓存（float 入参已在端点舍入，滑块拖动命中率高）。"""
    bundle = _load_bundle()
    if bundle is None:
        raise RuntimeError(_load_error or "代理模型不可用")
    with _lock:
        r = bundle["predictor"].predict(eg, fl, area, pf, ag)
        _, z_base = bundle["predictor"].baseline.lookup(area, pf)

    out = {
        "params": r["params"],
        "freq": np.round(r["freq_ghz"], 5).tolist(),
        "z": np.round(r["z_db"], 4).tolist(),
        "z_base": np.round(z_base, 4).tolist(),
        "sample_freq": np.round(r["sample_freq"], 5).tolist(),
        "sample_z": np.round(r["sample_z"], 4).tolist(),
    }
    key = (round(eg, 4), round(fl, 4), int(area), int(pf))
    idxs = bundle["cells"].get(key, [])[:5]
    out["in_grid"] = bool(idxs)
    if idxs:
        npz, yp = bundle["npz"], bundle["npz"]["y_params"]
        cols = ["fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "qs_bodeq", "qp_bodeq"]
        out["true_curves"] = np.round(npz["full_z"][idxs], 4).tolist()
        out["true_mean"] = {c: float(np.mean(yp[idxs, j])) for j, c in enumerate(cols)}
    return out


@router.get("/surrogate/predict")
def predict(eg: float, fl: float, area: float, pf: int = 0, ag: float = 1.0) -> dict:
    try:
        from app.services.model_metadata import with_applicability
        answer = _predict_cached(round(eg, 3), round(fl, 3), round(ag, 3),
                                 round(area, 1), float(pf))
        metadata = with_applicability(_metadata(), dict(eg=eg, fl=fl, ag=ag, area=area, pf=pf))
        return {**answer, "metadata": metadata.model_dump(mode="json")}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
