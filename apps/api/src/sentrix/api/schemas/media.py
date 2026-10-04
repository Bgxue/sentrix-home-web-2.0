from __future__ import annotations

from pydantic import BaseModel


class ImportResponse(BaseModel):
    asset_id: str
    observation_id: str | None
    media_type: str
    status: str
    deduplicated: bool
    # 有没有真的写成视觉向量。1.0 的「导入成功但没有向量」在接口上完全不可见，
    # 只能在事后手工清点库里少了多少条才发现。
    indexed: bool
    # 有没有真的解析出地点（落 asset_places 一行）。与 indexed 同一条理由：
    # 「导入成功但地点没落」在接口上必须是可见的。
    placed: bool = False
    # 折进的 / 新建的事件 id。事件归属没接上时为 None。
    event_id: str | None = None
    # 视频有没有真的走到 ffprobe（S8a T505）。与 indexed / placed 同一个可观测面：
    # 原先「视频收下了但什么都没发生」在响应里完全看不出来。图片恒为 False。
    video_probed: bool = False
    # 这次导入之后建起来的视频场景数（S8b T512）。与 ``video_probed`` 同一条理由：
    # 「视频导入成功但一个场景都没有」也要看得见。图片恒为 0。
    video_scenes: int = 0
    # 音频有没有真的走到 ffprobe（S8c T521）。与 ``video_probed`` 同一个可观测面：
    # 「音频收下了但什么都没发生」原先在响应里完全看不出来。音频以外恒为 False。
    audio_probed: bool = False


class AssetSummary(BaseModel):
    id: str
    file_name: str
    media_type: str
    mime_type: str | None
    size_bytes: int
    status: str
    captured_at: str | None
    gps: dict[str, float] | None


class AssetListResponse(BaseModel):
    items: list[AssetSummary]
