"""ffprobe 音频元数据适配器。

**为什么单列一个端口 + ``AudioMetadata``，而不是复用 ``VideoMetadataReader``。**
形状不同：音频要的是时长 / 编码 / 采样率 / 声道 / 码率，视频要的是时长 / 帧率 / 宽高 /
旋转角；硬塞进同一个类型会让两边各背一半恒空字段（音频恒没有 width/height/fps/rotation，
视频恒没有 sample_rate/channels/bit_rate）。这与 S8a 单列 ``VideoMetadataReader``
（而不是扩展 EXIF 的 ``MetadataReader`` 三元组）是同一条理由。
ffprobe 的命令行与容器标签解析与视频共用 ``infrastructure/media/ffprobe.py``。

**1.0 没有对应物。** 1.0 的 ``probe_video_metadata`` 遇到没有视频流的文件直接抛
``ValueError("ffprobe found no video stream")``（``video/metadata.py:52``）——音频在
1.0 走的是「只转写、不读元数据」那条路，时长/编码这些从来没落过库。2.0 读它，是用户
2026-10-04 裁决「音频只做入库与播放」里「能读元数据（时长等）」那一条的直接落地。
转写另说：本切片**不做**（``s8-deviations.md`` §1.1）。
"""

from __future__ import annotations

from pathlib import Path

from sentrix.domain.media.models import AudioMetadata
from sentrix.infrastructure.media.ffprobe import (
    DEFAULT_PROBE_TIMEOUT_S,
    capture_time,
    iso6709_location,
    run_ffprobe,
)


def _int(value: object) -> int:
    """``bit_rate`` / ``sample_rate`` / ``channels`` 都是字符串形式的整数，缺了就是 0。"""
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


class FfprobeAudioMetadataReader:
    """domain ``AudioMetadataReader`` 的 ffprobe 实现。"""

    def __init__(self, *, timeout_s: int = DEFAULT_PROBE_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    def read(self, path: Path) -> AudioMetadata:
        payload = run_ffprobe(Path(path), timeout_s=self._timeout_s)
        format_info = payload.get("format") or {}
        format_tags = format_info.get("tags") or {}
        audio = next(
            (item for item in payload.get("streams") or [] if item.get("codec_type") == "audio"),
            None,
        )
        # 与视频那条对称：没有音频流就是探测失败，不是「一条空元数据」。
        if not audio:
            raise ValueError("ffprobe found no audio stream")
        stream_tags = audio.get("tags") or {}
        captured_at, creation_source = capture_time(format_tags, stream_tags)
        latitude, longitude = iso6709_location({**stream_tags, **format_tags})
        # 总时长优先取容器（format），退回流：与视频适配器同一个取法。
        duration = float(format_info.get("duration") or audio.get("duration") or 0)
        # 码率同理：容器上的是整条文件的总码率，流上的是这一条音频的。
        bit_rate = _int(audio.get("bit_rate")) or _int(format_info.get("bit_rate"))
        return AudioMetadata(
            captured_at=captured_at,
            latitude=latitude,
            longitude=longitude,
            captured_location=f"{latitude:.6f},{longitude:.6f}" if latitude is not None else None,
            duration_sec=duration,
            codec=str(audio.get("codec_name") or ""),
            sample_rate=_int(audio.get("sample_rate")),
            channels=_int(audio.get("channels")),
            bit_rate=bit_rate,
            creation_source=creation_source,
        )
