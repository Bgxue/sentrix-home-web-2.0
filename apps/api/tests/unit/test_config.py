from sentrix.config import Settings


def test_defaults_point_at_2_0_ports_and_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    settings = Settings()

    assert settings.api_port == 8191
    assert settings.data_dir == tmp_path
    assert settings.database_path == tmp_path / "sentrix.db"
    assert settings.media_dir == tmp_path / "media"
    assert settings.preview_dir == tmp_path / "previews"


def test_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SENTRIX_API_PORT", "9001")

    assert Settings().api_port == 9001


def test_required_dirs_are_derived_not_read_from_env(tmp_path, monkeypatch):
    """data_dir 是唯一可配的根，其余路径都由它派生，避免 1.0 那种路径散落。"""
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    settings = Settings()

    assert settings.media_dir.parent == settings.data_dir
    assert settings.preview_dir.parent == settings.data_dir
