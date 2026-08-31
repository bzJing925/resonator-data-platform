"""代理模型后台预热：服务就绪后自动完成懒加载，避免首次访问代理模型页卡 ~30s。"""

import asyncio

import app.api.surrogate as surrogate
import app.main as main_module


def test_prewarm_delegates_to_shared_loader(monkeypatch):
    """prewarm 必须走与 /surrogate/status 相同的 _load_bundle 入口（幂等 + 锁 + 异常降级）。"""
    calls = []
    monkeypatch.setattr(surrogate, "_load_bundle", lambda: calls.append(1))
    surrogate.prewarm()
    assert calls == [1]


def test_prewarm_coroutine_invokes_prewarm_after_delay(monkeypatch):
    """lifespan 调度的预热协程：延迟让开首批请求后，在线程中调用 surrogate.prewarm。"""
    called = []
    monkeypatch.setattr(main_module, "_PREWARM_DELAY_S", 0)
    monkeypatch.setattr(surrogate, "prewarm", lambda: called.append(1))
    asyncio.run(main_module._prewarm_surrogate())
    assert called == [1]


def test_prewarm_coroutine_swallows_errors(monkeypatch):
    """预热失败（如产物缺失外的意外）不得影响服务存活。"""

    def _boom():
        raise RuntimeError("ckpt corrupted")

    monkeypatch.setattr(main_module, "_PREWARM_DELAY_S", 0)
    monkeypatch.setattr(surrogate, "prewarm", _boom)
    asyncio.run(main_module._prewarm_surrogate())  # 不应抛异常
