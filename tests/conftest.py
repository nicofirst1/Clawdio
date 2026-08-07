import pytest


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """Tests must never read the developer's real config file: load_config()
    lets it override env vars, so a panel-written ~/.config/claudio/config.json
    (theme, theme_pack, ...) would silently change what the suite exercises.
    Tests that want a config file (test_config_api's cfg_file) re-point this."""
    monkeypatch.setenv("CLAUDIO_CONFIG", str(tmp_path / "config.json"))
