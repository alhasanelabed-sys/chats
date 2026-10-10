"""Neither initial startup nor a restart may initialize a half-restored DB."""
from types import SimpleNamespace
import threading

import pytest

import run
from hader import backup as B


def test_incomplete_recovery_stops_before_reload(monkeypatch):
    calls = []
    runtime = SimpleNamespace(reload_settings=lambda: calls.append("reload"))
    monkeypatch.setattr(B, "apply_pending_restore", lambda: {
        "ok": False, "recovery_required": True, "error": "locked original database",
    })
    with pytest.raises(RuntimeError, match="startup stopped"):
        run._restore_offline(runtime)
    assert calls == []


def test_completed_restore_reloads_settings(monkeypatch):
    calls = []
    runtime = SimpleNamespace(reload_settings=lambda: calls.append("reload"))
    monkeypatch.setattr(B, "apply_pending_restore", lambda: {"ok": True})
    run._restore_offline(runtime)
    assert calls == ["reload"]


def test_server_restart_calls_restore_without_name_error(monkeypatch):
    calls = []
    runtime = SimpleNamespace(RESTART=threading.Event(), STOP=threading.Event(),
        applied=lambda: None, firewall=lambda: None, reload_settings=lambda: None)
    settings = SimpleNamespace(host="127.0.0.1", web_port=8090, portal_port=0, adms_ports=[])

    async def serve(_app, _settings, _socket, **_kwargs):
        calls.append("serve")
        if calls.count("serve") == 1:
            runtime.RESTART.set()
        else:
            runtime.STOP.set()

    def restore():
        calls.append("restore")

    monkeypatch.setattr(run, "serve", serve)
    monkeypatch.setattr(run, "_bind", lambda *_args: object())
    monkeypatch.setattr(B, "apply_pending_restore", restore)
    run._run(SimpleNamespace(open=False), settings, runtime, object())
    assert calls == ["serve", "restore", "serve"]
