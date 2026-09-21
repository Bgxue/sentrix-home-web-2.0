from sentrix.bootstrap.container import Container
from sentrix.config import Settings


def test_container_builds_all_adapters(tmp_path, monkeypatch):
    # conftest.py 要到 Task 5 才建，这里就地构造 Settings，不依赖 settings fixture
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    settings = Settings()
    container = Container.build(settings)

    assert container.settings is settings
    assert container.database.path == settings.database_path
    assert container.database.migrations_dir.name == "migrations"


def test_database_healthy_reports_false_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path / "gone"))
    settings = Settings()
    container = Container.build(settings)

    assert container.database_healthy() is False
