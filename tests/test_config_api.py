"""Config-file layer + /config HTTP API tests. No audio device required --
the server is exercised against a GeigerTheme state that is never rendered."""

import json
import math
import os
import sys
import threading
import urllib.request
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import config  # noqa: E402
import io_modes  # noqa: E402
from geiger import GeigerTheme  # noqa: E402


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("SONIFIER_CONFIG", str(path))
    return path


@pytest.fixture
def server(cfg_file):
    state = GeigerTheme(volume=0.5)
    restart_event = threading.Event()
    httpd = io_modes._make_http_server(
        state, 0, boot_cfg=config.load_config(), restart_event=restart_event
    )
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state, port, restart_event
    httpd.shutdown()
    httpd.server_close()


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, r.read()


def _post(port, path, obj):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            return r.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---- config file layer ----

def test_file_overrides_env(cfg_file, monkeypatch):
    monkeypatch.setenv("SONIFIER_VOLUME", "0.9")
    cfg_file.write_text(json.dumps({"volume": 0.2, "theme": "geiger"}))
    cfg = config.load_config()
    assert cfg["volume"] == 0.2
    assert cfg["theme"] == "geiger"


def test_file_values_normalized(cfg_file):
    cfg_file.write_text(json.dumps({"volume": 7, "theme": "banana", "port": -3}))
    cfg = config.load_config()
    assert cfg["volume"] == 1.0  # clamped
    assert cfg["theme"] == "ambient"  # unknown theme -> default kept
    assert cfg["port"] == 9753  # invalid port -> default kept


def test_save_config_merges_and_ignores_unknown(cfg_file):
    config.save_config({"volume": 0.3})
    config.save_config({"mute": True, "nonsense": 1})
    data = json.loads(cfg_file.read_text())
    assert data == {"volume": 0.3, "mute": True}


def test_missing_or_corrupt_file_is_ignored(cfg_file):
    assert config.load_config()["volume"] == 0.5
    cfg_file.write_text("{not json")
    assert config.load_config()["volume"] == 0.5


# ---- HTTP API ----

def test_get_config_reports_keys(server):
    _state, port, _ = server
    status, body = _get(port, "/config")
    data = json.loads(body)
    assert status == 200
    assert data["config"]["volume"] == 0.5
    assert "volume" in data["live_keys"]
    assert "theme" in data["restart_keys"]
    assert data["pending_restart"] is False
    assert data["version"] == config.VERSION


def test_post_config_applies_live_and_persists(server, cfg_file):
    state, port, _ = server
    status, data = _post(port, "/config", {"volume": 0.8, "mute": True})
    assert status == 200 and data["ok"]
    assert state.volume == 0.8
    assert state.mute is True
    assert data["pending_restart"] is False
    assert json.loads(cfg_file.read_text())["volume"] == 0.8


def test_post_config_restart_key_flags_pending(server):
    _state, port, _ = server
    status, data = _post(port, "/config", {"theme": "geiger"})
    assert status == 200
    assert data["pending_restart"] is True


def test_post_config_rejects_unknown_keys(server):
    _state, port, _ = server
    status, data = _post(port, "/config", {"volume": 0.1, "evil": 1})
    assert status == 400
    assert "evil" in data["error"]


def test_post_restart_sets_event(server):
    _state, port, restart_event = server
    status, data = _post(port, "/restart", {})
    assert status == 200 and data["restarting"]
    assert restart_event.is_set()


def test_static_ui_served(server):
    _state, port, _ = server
    status, body = _get(port, "/")
    assert status == 200
    assert b"Claudio" in body
    assert _get(port, "/app.js")[0] == 200
    assert _get(port, "/style.css")[0] == 200


def test_live_apply_reaches_ambient_rain_layer(cfg_file):
    # Regression: RainLayer copies rain_enabled at construction and the render
    # path reads the layer's copy, so live-apply must reach into the layer.
    from ambient import AmbientTheme
    state = AmbientTheme(clicks_enabled=True, chimes_enabled=True)
    io_modes._apply_live(state, {"clicks": False, "chimes": False, "volume": 0.7})
    assert state.rain_enabled is False
    assert state.rain.rain_enabled is False
    assert state.gestures_enabled is False
    assert state.volume == 0.7


def test_rain_layer_tracks_theme_flag_without_snapshot():
    # RainLayer.rain_enabled must read through to the theme's live flag, not
    # cache a snapshot at construction -- distinguishes "reads live" (this
    # test passes) from "cached a copy at __init__ time" (this test fails,
    # since the cached copy would still read the constructor's original
    # value). Bypasses _apply_live entirely: sets the theme-level flag
    # directly to prove there is only one source of truth, not two kept in
    # sync by a write-both-copies path.
    from ambient import AmbientTheme
    state = AmbientTheme(clicks_enabled=True)
    assert state.rain.rain_enabled is True
    state.rain_enabled = False
    assert state.rain.rain_enabled is False


def test_event_endpoint_still_works(server):
    _state, port, _ = server
    status, _data = _post(port, "/event", {"hook_event_name": "PreToolUse", "tool_name": "Read"})
    assert status == 200


def test_post_config_infinite_idle_normalizes_to_default(server, cfg_file):
    # Regression: {"idle_exit_min": 1e999} used to persist literal Infinity
    # into config.json, permanently breaking the web panel's JSON.parse.
    _state, port, _ = server
    status, data = _post(port, "/config", {"idle_exit_min": float("1e999")})
    assert status == 200 and data["ok"]
    assert math.isfinite(data["config"]["idle_exit_min"])
    status, body = _get(port, "/config")
    data = json.loads(body)
    assert math.isfinite(data["config"]["idle_exit_min"])
    # config.json on disk must stay valid, boring JSON -- no Infinity token.
    raw = cfg_file.read_text()
    json.loads(raw)
    assert "Infinity" not in raw


def test_post_config_nan_idle_does_not_zero_it(server):
    # Regression: {"idle_exit_min": NaN} used to become max(0.0, nan) = 0.0,
    # which made the daemon idle-exit within 1s.
    _state, port, _ = server
    status, data = _post(port, "/config", {"idle_exit_min": float("nan")})
    assert status == 200 and data["ok"]
    assert data["config"]["idle_exit_min"] != 0.0
    assert math.isfinite(data["config"]["idle_exit_min"])
