"""Typed configuration.

Only ``SENTRIX_DATA_DIR`` and a handful of connection settings are configurable.
Everything else is derived, because 1.0's failure mode was ~126 env flags whose
values disagreed with each other and with the code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTRIX_", extra="forbid")

    data_dir: Path = Field(default=Path("data"))

    api_port: int = 8191
    api_host: str = "0.0.0.0"

    # 2.0 reuses 1.0's already-running model services; it never manages them.
    vlm_base_url: str = "http://127.0.0.1:8100/v1"
    text_embedder_url: str = "http://127.0.0.1:8101"

    def model_post_init(self, _context: Any, /) -> None:  # noqa: D105
        self.data_dir = self.data_dir.expanduser().resolve()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_path(self) -> Path:
        return self.data_dir / "sentrix.db"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def preview_dir(self) -> Path:
        return self.data_dir / "previews"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def qdrant_dir(self) -> Path:
        """派生向量索引的目录。

        **必须由 data_dir 派生**，不允许单独配路径。1.0 的 ``data/qdrant`` 被 8091
        进程以 flock 独占（嵌入式 Qdrant 的目录只能一个进程用），2.0 若指过去，
        第二个实例起不来——而且是那种「起了、只是检索悄悄降级成全表扫」的坏法。
        派生自 data_dir 意味着换数据目录就整体换，不存在「只把索引指到 1.0」的口子。
        """
        return self.data_dir / "qdrant"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.media_dir, self.preview_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    return Settings()
