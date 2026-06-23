"""单器件曲线接口：S 参数 / BodeQ。

优化：
- skrf.Network 解析结果走 LRU 缓存（文件不变则不复读）。
- BodeQ 计算结果也缓存，避免重复 FFT/拟合。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.deps import DbSession
from app.config import get_settings
from app.models import Device

log = logging.getLogger("aln")

router = APIRouter(prefix="/devices", tags=["devices"])

_PARAM_CHOICES = ("s11_db", "s11_phase", "s11_re_im", "z_mag_db", "z_phase")

# 缓存容量 ≈ 100 个文件 × 600 KB ≈ 60 MB，在容器内存预算内。
_NETWORK_CACHE_SIZE = 128
_BODEQ_CACHE_SIZE = 128


@lru_cache(maxsize=_NETWORK_CACHE_SIZE)
def _load_network(path_str: str) -> "skrf.Network":
    """缓存 skrf.Network 解析结果。

    参数用 str 而非 Path，因为 Path 不可 hash 且 lru_cache 要求可 hash 参数。
    S1P 文件一旦入库就不会修改，因此缓存不会过期是安全的。
    """
    import skrf
    return skrf.Network(path_str)


@lru_cache(maxsize=_BODEQ_CACHE_SIZE)
def _calc_bodeq_cached(s_hash: bytes, freq_hash: bytes, s_bytes: bytes, freq_bytes: bytes) -> dict[str, Any]:
    """缓存 BodeQ 计算结果。

    numpy array 不可 hash，因此序列化为 bytes + 长度元组作为缓存 key。
    实际 key 是 (s_hash, freq_hash)，s_bytes/freq_bytes 仅用于反序列化。
    """
    from app.core.extract import calc_bodeq_curve
    s = np.frombuffer(s_bytes, dtype=np.complex128)
    freq = np.frombuffer(freq_bytes, dtype=np.float64)
    return calc_bodeq_curve(s, freq)


def _resolve_sparam_path(rel_or_abs: str, batch_no: str | None = None) -> Path:
    settings = get_settings()
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    base = settings.files_dir
    if batch_no:
        base = base / batch_no
    return base / p


@router.get("/{device_id}/sparam")
def device_sparam(
    device_id: int,
    db: DbSession,
    param: Annotated[str, Query()] = "s11_db",
    fast: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    if param not in _PARAM_CHOICES:
        raise HTTPException(
            status_code=400, detail=f"param 必须是 {','.join(_PARAM_CHOICES)} 之一"
        )

    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"器件 {device_id} 不存在")

    # --- PINN 快速路径（仅 s11_db）---
    if fast and param == "s11_db":
        from app.ml.inference import predict_s11_db
        pinn_result = predict_s11_db(device)
        if pinn_result is not None:
            freq_ghz, values = pinn_result
            return {
                "device_id": device_id,
                "freq_ghz": freq_ghz,
                "values": values,
                "param": param,
                "file_path": device.s_param_path,
                "source": "pinn",
            }
        # fallback: 继续走 skrf 路径

    # --- skrf 标准路径 ---
    if not device.s_param_path:
        raise HTTPException(status_code=404, detail="该器件没有 S 参数文件")

    batch_no = device.batch.batch_no if device.batch else None
    path = _resolve_sparam_path(device.s_param_path, batch_no=batch_no)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"S 参数文件不存在: {path}")

    try:
        net = _load_network(str(path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取 S 参数失败: {exc!s}") from exc

    freq_ghz = (net.f / 1e9).tolist()
    s = net.s[:, 0, 0]

    if param == "s11_db":
        values = (20 * np.log10(np.maximum(np.abs(s), 1e-12))).tolist()
    elif param == "s11_phase":
        values = [float(v) for v in np.degrees(np.unwrap(np.angle(s)))]
    elif param == "s11_re_im":
        return {
            "device_id": device_id,
            "freq_ghz": freq_ghz,
            "values_re": np.real(s).tolist(),
            "values_im": np.imag(s).tolist(),
            "param": param,
            "file_path": device.s_param_path,
        }
    elif param == "z_mag_db":
        z0 = net.z0[0, 0]
        z = z0 * (1 + s) / (1 - s)
        values = (20 * np.log10(np.maximum(np.abs(z), 1e-12))).tolist()
    elif param == "z_phase":
        z0 = net.z0[0, 0]
        z = z0 * (1 + s) / (1 - s)
        values = [float(v) for v in np.degrees(np.unwrap(np.angle(z)))]
    else:
        raise HTTPException(status_code=400, detail="param 不支持")

    return {
        "device_id": device_id,
        "freq_ghz": freq_ghz,
        "values": values,
        "param": param,
        "file_path": device.s_param_path,
        "source": "skrf",
    }


@router.get("/{device_id}/bodeq")
def device_bodeq(device_id: int, db: DbSession) -> dict[str, Any]:
    """读 .s1p → 计算 BodeQ raw/smooth/fitted 三条曲线 → 返回 JSON。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"器件 {device_id} 不存在")
    if not device.s_param_path:
        raise HTTPException(status_code=404, detail="该器件没有 S 参数文件")

    batch_no = device.batch.batch_no if device.batch else None
    path = _resolve_sparam_path(device.s_param_path, batch_no=batch_no)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"S 参数文件不存在: {path}")

    try:
        net = _load_network(str(path))
        s = net.s[:, 0, 0]
        freq = net.f
        # BodeQ 计算缓存：key 为 numpy array 的 bytes 摘要
        s_bytes = s.tobytes()
        freq_bytes = freq.tobytes()
        result = _calc_bodeq_cached(
            hash(s_bytes), hash(freq_bytes), s_bytes, freq_bytes
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"BodeQ 计算失败: {exc!s}") from exc

    return {
        "device_id": device_id,
        **result,
        "fs_ghz": device.fs_ghz,
        "fp_ghz": device.fp_ghz,
        "fbode_ghz": device.fbode_ghz,
    }


@router.get("/{device_id}/sparam-sparse")
def device_sparam_sparse(
    device_id: int,
    db: DbSession,
    param: Annotated[str, Query()] = "z_mag_db",
    piezo: Annotated[str, Query()] = "308",
    n_points: Annotated[int, Query()] = 300,
) -> dict[str, Any]:
    """稀疏采样神经网络重建 Z11 频谱。

    按压电层厚度路由到对应模型，支持 308nm / 325nm。
    """
    if param != "z_mag_db":
        raise HTTPException(status_code=400, detail="稀疏重建当前仅支持 z_mag_db")

    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"器件 {device_id} 不存在")
    if not device.s_param_path:
        raise HTTPException(status_code=404, detail="该器件没有 S 参数文件")

    batch_no = device.batch.batch_no if device.batch else None
    path = _resolve_sparam_path(device.s_param_path, batch_no=batch_no)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"S 参数文件不存在: {path}")

    # 优先使用数据库中预计算的参数
    cond = None
    if all(v is not None for v in [device.fs_ghz, device.fp_ghz, device.qs, device.qp, device.k2eff_pct]):
        cond = {
            "fs": float(device.fs_ghz),
            "fp": float(device.fp_ghz),
            "Qs": float(device.qs),
            "Qp": float(device.qp),
            "kt2": float(device.k2eff_pct),
        }

    from app.ml.sparse.inference import predict_z11_sparse
    result = predict_z11_sparse(
        str(path),
        cond=cond,
        piezo=piezo,
        target_k=n_points,
        use_learned_sampler=False,  # Phase 1 用固定规则采样
    )

    if result is None:
        raise HTTPException(
            status_code=503,
            detail=f"稀疏重建模型未加载（piezo={piezo}nm）。请先训练模型。",
        )

    import numpy as np
    z_pred = np.array(result["z_pred"])
    z_true = np.array(result["z_true"])
    rmse = float(np.sqrt(np.mean((z_pred - z_true) ** 2)))

    return {
        "device_id": device_id,
        "freq_ghz": result["freq_ghz"],
        "values": result["z_pred"],
        "values_true": result["z_true"],
        "sample_points": result["sample_points"],
        "regions": result["regions"],
        "params": result["cond"],
        "rmse": rmse,
        "param": param,
        "piezo_thickness": f"{piezo}nm",
        "source": "sparse-recon",
    }


@router.get("/{device_id}/download-s1p")
def download_device_s1p(
    device_id: int,
    db: DbSession,
) -> FileResponse:
    """下载该器件对应的原始 S1P/S2P 文件。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"器件 {device_id} 不存在")
    if not device.s_param_path:
        raise HTTPException(status_code=404, detail="该器件没有 S 参数文件路径")

    batch_no = device.batch.batch_no if device.batch else None
    path = _resolve_sparam_path(device.s_param_path, batch_no=batch_no)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"S 参数文件不存在: {path}")

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
    )
