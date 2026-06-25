"""Tests for app.config settings."""


def test_pipeline_settings_defaults():
    from app.config import get_settings

    settings = get_settings()
    assert settings.PIPELINE_ENABLED is True
    assert settings.PIPELINE_WORKERS == 0
    assert settings.PIPELINE_SCAN_INTERVAL == 1.0
    assert settings.PIPELINE_COMPRESS_RAW is True
    assert settings.PIPELINE_KEEP_DEEMBED_TEMP is False
