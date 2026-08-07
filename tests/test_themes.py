"""Theme pack tests: src/themes.py (schema/validation/search-path) + the
config/io_modes wiring that turns a `theme_pack` config key into an
AmbientConfig override. Conventions follow test_config_api.py (sys.path
insert, cfg_file/server fixtures, _get/_post helpers)."""

import dataclasses
import json
import math
import os
import sys
import threading
import urllib.request
import urllib.error

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import config  # noqa: E402
import io_modes  # noqa: E402
import themes  # noqa: E402
from ambient import AmbientTheme  # noqa: E402
from ambient_layers import AMBIENT_CONFIG  # noqa: E402
from geiger import GeigerTheme, render_block  # noqa: E402

SR = config.SAMPLE_RATE
BLOCK = config.BLOCKSIZE


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("CLAUDIO_CONFIG", str(path))
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


@pytest.fixture
def themes_dirs(tmp_path, monkeypatch):
    """Point themes.py's repo/user search dirs at isolated tmp_path dirs so
    tests never touch the real repo's themes/ or ~/.config/claudio/themes/."""
    repo_dir = tmp_path / "repo_themes"
    user_dir = tmp_path / "user_themes"
    repo_dir.mkdir()
    user_dir.mkdir()
    monkeypatch.setattr(themes, "_REPO_THEMES_DIR", str(repo_dir))
    monkeypatch.setattr(themes, "_USER_THEMES_DIR", str(user_dir))
    return repo_dir, user_dir


def _write_pack(dirpath, name, obj):
    (dirpath / f"{name}.json").write_text(json.dumps(obj))


def make_ambient(seed=0, volume=1.0, **kw):
    return AmbientTheme(sr=SR, volume=volume, mute=False, quiet=True, seed=seed, **kw)


def render_short(seed=0, duration_s=2.0, **kw):
    state = make_ambient(seed=seed, **kw)
    n_blocks = int(math.ceil(duration_s * SR / BLOCK))
    out = np.zeros((n_blocks * BLOCK, 2), dtype=np.float32)
    state.handle_event({"hook_event_name": "SessionStart", "session_id": "s1"})
    for b in range(n_blocks):
        out[b * BLOCK:(b + 1) * BLOCK, :] = render_block(state, BLOCK)
    return out


# ---- load_pack: valid / clamp / drop / reserved / poisoning / corrupt ----

def test_load_pack_valid(themes_dirs):
    repo_dir, _ = themes_dirs
    _write_pack(repo_dir, "mypack", {"drop_timbre": "marimba", "BED_LP_BASE_HZ": 900})
    overrides = themes.load_pack("mypack")
    assert overrides == {"drop_timbre": "marimba", "BED_LP_BASE_HZ": 900.0}


def test_load_pack_clamps_out_of_range(themes_dirs, caplog):
    repo_dir, _ = themes_dirs
    _write_pack(repo_dir, "loud", {"BED_CAL_DB": 999})
    with caplog.at_level("WARNING"):
        overrides = themes.load_pack("loud")
    lo, hi = themes.PACK_SCHEMA["BED_CAL_DB"]
    assert overrides["BED_CAL_DB"] == hi
    assert any("BED_CAL_DB" in r.message for r in caplog.records)


def test_load_pack_unknown_key_dropped(themes_dirs, caplog):
    repo_dir, _ = themes_dirs
    _write_pack(repo_dir, "p", {"NOT_A_FIELD": 1, "BED_CAL_DB": 18})
    with caplog.at_level("WARNING"):
        overrides = themes.load_pack("p")
    assert overrides == {"BED_CAL_DB": 18.0}
    assert any("NOT_A_FIELD" in r.message for r in caplog.records)


def test_load_pack_reserved_keys_silently_ignored(themes_dirs, caplog):
    repo_dir, _ = themes_dirs
    _write_pack(repo_dir, "p", {
        "name": "My Pack", "author": "nico", "description": "x", "version": "1",
        "_comment": "hidden", "BED_CAL_DB": 18,
    })
    with caplog.at_level("WARNING"):
        overrides = themes.load_pack("p")
    assert overrides == {"BED_CAL_DB": 18.0}
    assert len(caplog.records) == 0  # no warning for reserved/underscore keys


def test_load_pack_rejects_infinity_and_nan(themes_dirs, caplog):
    repo_dir, _ = themes_dirs
    # json.load accepts the literal tokens Infinity/-Infinity/NaN.
    (repo_dir / "poison.json").write_text(
        '{"BED_CAL_DB": Infinity, "MIDLAYER_CAL_DB": NaN, "AIR_CAL_DB": 18}'
    )
    with caplog.at_level("WARNING"):
        overrides = themes.load_pack("poison")
    assert "BED_CAL_DB" not in overrides
    assert "MIDLAYER_CAL_DB" not in overrides
    assert overrides == {"AIR_CAL_DB": 18.0}


def test_load_pack_corrupt_file_returns_empty(themes_dirs):
    repo_dir, _ = themes_dirs
    (repo_dir / "broken.json").write_text("{not json")
    assert themes.load_pack("broken") == {}


def test_load_pack_missing_returns_empty(themes_dirs):
    assert themes.load_pack("does-not-exist") == {}


def test_load_pack_non_dict_body_returns_empty(themes_dirs):
    repo_dir, _ = themes_dirs
    (repo_dir / "arr.json").write_text("[1, 2, 3]")
    assert themes.load_pack("arr") == {}


def test_user_dir_wins_on_name_clash(themes_dirs):
    repo_dir, user_dir = themes_dirs
    _write_pack(repo_dir, "dusk", {"BED_CAL_DB": 14})
    _write_pack(user_dir, "dusk", {"BED_CAL_DB": 20})
    assert themes.load_pack("dusk") == {"BED_CAL_DB": 20.0}


def test_list_packs_sorted(themes_dirs):
    repo_dir, user_dir = themes_dirs
    _write_pack(repo_dir, "zeta", {})
    _write_pack(user_dir, "alpha", {})
    assert themes.list_packs() == ["alpha", "zeta"]


# ---- schema sanity ----

def test_schema_keys_are_ambientconfig_fields():
    field_names = {f.name for f in dataclasses.fields(AMBIENT_CONFIG)}
    assert set(themes.PACK_SCHEMA) <= field_names


def test_schema_numeric_bounds_contain_defaults():
    defaults = {f.name: f.default for f in dataclasses.fields(AMBIENT_CONFIG)}
    for key, allowed in themes.PACK_SCHEMA.items():
        if isinstance(allowed, tuple) and allowed and isinstance(allowed[0], str):
            continue
        lo, hi = allowed
        assert lo <= defaults[key] <= hi, key


def test_schema_hz_fields_capped_at_8khz():
    for key, allowed in themes.PACK_SCHEMA.items():
        if key.endswith("_HZ") and not (allowed and isinstance(allowed[0], str)):
            assert allowed[1] <= 8000, key


# ---- _norm_pack ----

def test_norm_pack_rejects_traversal_and_absolute_paths():
    assert config._norm_pack("../x", "default") == "default"
    assert config._norm_pack("a/b", "default") == "default"
    assert config._norm_pack("/etc/passwd", "default") == "default"


def test_norm_pack_allows_empty_and_valid_names():
    assert config._norm_pack("", "default") == ""
    assert config._norm_pack("dusk-2", "default") == "dusk-2"


# ---- _build_theme_state wiring ----

def test_build_theme_state_applies_pack_override(themes_dirs, cfg_file):
    repo_dir, _ = themes_dirs
    _write_pack(repo_dir, "dusk", {"BED_LP_BASE_HZ": 850})
    cfg_file.write_text(json.dumps({"theme_pack": "dusk"}))
    cfg = config.load_config()
    assert cfg["theme_pack"] == "dusk"
    state = io_modes._build_theme_state(cfg, seed=7)
    assert state.cfg.BED_LP_BASE_HZ == 850.0


def test_build_theme_state_geiger_ignores_pack(themes_dirs, cfg_file):
    repo_dir, _ = themes_dirs
    _write_pack(repo_dir, "dusk", {"BED_LP_BASE_HZ": 850})
    cfg_file.write_text(json.dumps({"theme_pack": "dusk", "theme": "geiger"}))
    cfg = config.load_config()
    state = io_modes._build_theme_state(cfg, seed=7)
    assert isinstance(state, GeigerTheme)  # no cfg kwarg on this branch


# ---- GET /config includes packs ----

def test_get_config_includes_packs(server):
    _state, port, _ = server
    status, body = _get(port, "/config")
    data = json.loads(body)
    assert status == 200
    assert "packs" in data
    assert isinstance(data["packs"], list)


# ---- deterministic packed render ----

def test_packed_render_deterministic_and_differs_from_default(themes_dirs):
    repo_dir, _ = themes_dirs
    _write_pack(repo_dir, "dusk", {"BED_LP_BASE_HZ": 850, "drop_timbre": "marimba"})
    overrides = themes.load_pack("dusk")
    cfg = dataclasses.replace(AMBIENT_CONFIG, **overrides)

    out1 = render_short(seed=7, cfg=cfg)
    out2 = render_short(seed=7, cfg=cfg)
    assert np.array_equal(out1, out2)

    out_default = render_short(seed=7)
    assert not np.array_equal(out1, out_default)


def test_ambienttheme_default_cfg_matches_explicit_ambient_config():
    out1 = render_short(seed=7)
    out2 = render_short(seed=7, cfg=AMBIENT_CONFIG)
    assert np.array_equal(out1, out2)
