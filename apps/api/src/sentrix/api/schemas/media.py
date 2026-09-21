from __future__ import annotations

from pydantic import BaseModel


class ImportResponse(BaseModel):
    asset_id: str
    observation_id: str | None
    media_type: str
    status: str
    deduplicated: bool


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
