from app.workers.reprocess_batch import _validate_metrics


def test_validate_metrics():
    assert _validate_metrics(["qs", "kt2"]) == ["qs", "kt2"]


def test_validate_metrics_rejects_unknown():
    import pytest

    with pytest.raises(ValueError):
        _validate_metrics(["qs", "foo"])
