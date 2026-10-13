"""Mode/DB is independent of the native executor and source handshake."""

from types import SimpleNamespace

import pytest

from app.services import capabilities as svc
from tests.fixtures.platform_db import platform_database


@pytest.mark.parametrize(
    "database,desktop,server,expected",
    [
        ("sqlite", False, False, "server"),
        ("sqlite", True, True, "server"),
        ("sqlite", True, False, "desktop"),
        pytest.param("postgresql", False, False, "server", marks=pytest.mark.integration),
    ],
)
def test_modes(database, desktop, server, expected, tmp_path, monkeypatch):
    settings = SimpleNamespace(
        is_desktop=desktop,
        ALN_SERVER_MODE=server,
        EXPORT_MAX_ROWS=5000000,
        EXPORT_MAX_BYTES=2**31,
        COMPARISON_MAX_ROWS=200000,
    )
    monkeypatch.setattr(svc, "get_settings", lambda: settings)
    monkeypatch.setattr(svc, "executor_ready", lambda _: True)
    monkeypatch.setattr(svc, "upstream_delete_ready", lambda: False)
    with platform_database(database, tmp_path) as (db, _):
        caps = svc.describe(db)
        assert caps.mode == expected and caps.database == database
        assert caps.features.full_export_xlsx
        assert caps.features.remote_delete == (expected == "server")
        assert caps.features.credential_storage is False
        monkeypatch.setattr(svc, "executor_ready", lambda _: False)
        assert not svc.describe(db).features.upload_precheck


def test_desktop_requires_handshake(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        is_desktop=True,
        ALN_SERVER_MODE=False,
        EXPORT_MAX_ROWS=5000000,
        EXPORT_MAX_BYTES=2**31,
        COMPARISON_MAX_ROWS=200000,
    )
    monkeypatch.setattr(svc, "get_settings", lambda: settings)
    monkeypatch.setattr(svc, "executor_ready", lambda _: True)
    with platform_database("sqlite", tmp_path) as (db, _):
        monkeypatch.setattr(svc, "upstream_delete_ready", lambda: False)
        assert not svc.describe(db).features.remote_delete
        monkeypatch.setattr(svc, "upstream_delete_ready", lambda: True)
        assert svc.describe(db).features.remote_delete
