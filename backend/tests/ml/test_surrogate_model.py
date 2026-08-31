"""Stage 1 模型单测。"""

import torch

from app.ml.surrogate.model import BaselineResidualMLP


class TestBaselineResidualMLP:
    def test_forward_shapes(self):
        model = BaselineResidualMLP(in_dim=5, hidden=64, n_res_blocks=2, n_params=7, k=32)
        out = model(torch.randn(8, 5), torch.randn(8, 7), torch.randn(8, 32))
        assert out["params"].shape == (8, 7)
        assert out["sparse"].shape == (8, 32)

    def test_zero_residual_identity(self):
        """最后一层零初始化 → 输出恒等于基准。"""
        model = BaselineResidualMLP(in_dim=5, hidden=64, n_res_blocks=2, n_params=7, k=32)
        p_base = torch.randn(4, 7)
        s_base = torch.randn(4, 32)
        out = model(torch.randn(4, 5), p_base, s_base)
        torch.testing.assert_close(out["params"], p_base)
        torch.testing.assert_close(out["sparse"], s_base)

    def test_residual_changes_output(self):
        """非零输入扰动后输出应偏离基准（证明残差通路存在）。"""
        model = BaselineResidualMLP(in_dim=5, hidden=64, n_res_blocks=2, n_params=7, k=32)
        with torch.no_grad():
            for p in model.head_params[-1].parameters():
                p.normal_(0, 0.1)
        p_base = torch.zeros(2, 7)
        s_base = torch.zeros(2, 32)
        out = model(torch.ones(2, 5), p_base, s_base)
        assert out["params"].abs().sum() > 0
