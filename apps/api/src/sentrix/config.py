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
    # W1：检索用的统一多模态嵌入侧车（WeMM-Embedding-2B，2048 维）。与 8100 的 VLM、
    # 8101 的 bge 同一条口径——2.0 不管理模型进程的生命周期，只连它。
    wemm_url: str = "http://127.0.0.1:8102"

    # **模型名不在这里。** 8100 上跑 llama.cpp 还是 vLLM、名字叫什么，由部署方决定，而且
    # 会在 2.0 运行期间被换掉——2026-09-24 实测：8100 从 llama.cpp 换成 vLLM，2.0 配的
    # ``gemma4-qat-iq3xxs`` 立刻 404，助手整条链路停摆。写一个常量在这儿，就是拿配置去追
    # 运行时的状态，正是本模块开头点名的那种坏法（「1.0 的失败模式是 ~126 env flags whose
    # values disagreed with each other and with the code」）。
    # 名字由 infrastructure/assistant/model_endpoint.py 问端点自己的 ``GET /v1/models``
    # 得到；探测失败就大声报错，不退回任何默认值。
    chat_timeout_s: float = 120.0

    # 视觉观察走同一个端点（multimodal 可用），模型名同样来自探测。
    # 一张图最多 3 次调用（首轮 / 补全 / 中文化），每次是一次带图的推理，比纯文本慢得多；
    # 整册导入是**串行**的（单卡 GPU），所以这个超时按单次调用算，不是按一张图算。
    vlm_timeout_s: float = 180.0

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def geo_dir(self) -> Path:
        """GeoNames dump 的目录（``*.txt`` / ``*.zip``）。

        **同样由 data_dir 派生**，且**不进版本控制**：官方子集 35 MB、全量 402 MB，
        放进仓库是拿源码托管当数据分发。部署方按 ``docs/runbooks/geonames-data.md``
        把文件放到这里，``GeoNamesNearestResolver`` 扫该目录。
        """
        return self.data_dir / "geo"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.media_dir, self.preview_dir, self.geo_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    return Settings()
