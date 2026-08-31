"""SurrogateDataset 单测。"""

from app.ml.surrogate.dataset import SurrogateDataset


class TestSurrogateDataset:
    def test_split_filtering(self, tiny_npz):
        train = SurrogateDataset(tiny_npz, split="train")
        val = SurrogateDataset(tiny_npz, split="val")
        test = SurrogateDataset(tiny_npz, split="test")
        assert len(train) == 16 and len(val) == 4 and len(test) == 4

    def test_item_shapes_and_norm(self, tiny_npz):
        ds = SurrogateDataset(tiny_npz, split="train")
        item = ds[0]
        assert item["x"].shape == (5,)
        assert item["y_params"].shape == (7,)
        assert item["y_sparse"].shape == (32,)
        assert item["params_base"].shape == (7,)
        assert item["sparse_base"].shape == (32,)
        # 归一化：item 0 来自 group 0（eg=0），fs 原始 4.8 → (4.8-4.85)/0.02 = -2.5
        assert abs(item["y_params"][0].item() - (-2.5)) < 1e-4
        # 归一化基准同样被归一化
        expected_base0 = (4.8 - 4.85) / 0.02
        assert abs(item["params_base"][0].item() - expected_base0) < 1e-4

    def test_exposes_meta(self, tiny_npz):
        ds = SurrogateDataset(tiny_npz, split="train")
        assert ds.freq_ghz.shape == (101,)
        assert ds.k == 32
