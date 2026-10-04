"""ffprobe 音频适配器的抽取口径（S8c T521）。

**用假 subprocess，不起真 ffprobe**：要钉的是「读哪几个字段、缺了怎么办、失败什么形态」，
它们与机器上装没装 ffmpeg 无关。真子进程的调用形态（``ffprobe -v error -print_format
json -show_format -show_streams``）由第一条断言钉住命令行。真机端到端在验收里单独做。

**1.0 没有对应物**：1.0 的 ``probe_video_metadata`` 遇到没有视频流的文件直接抛
``ValueError``，音频元数据从来没落过库（见 ``audio_metadata.py`` 的模块注释）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from sentrix.infrastructure.media.audio_metadata import FfprobeAudioMetadataReader

AAC_PAYLOAD = {
    "format": {
        "duration": "5.000000",
        "bit_rate": "129000",
        "tags": {
            "creation_time": "2026-08-02T06:55:56Z",
            "com.apple.quicktime.creationdate": "2026-08-02T14:55:56+0800",
            "com.apple.quicktime.location.ISO6709": "+22.4846+114.5438+005.469/",
        },
    },
    "streams": [
        {
            "codec_type": "audio",
            "codec_name": "aac",
            "sample_rate": "44100",
            "channels": 2,
            "bit_rate": "128000",
            # 故意与容器的 5.0 秒**不同**：钉住「总时长取容器」这条取法。
            "duration": "4.500000",
            "tags": {},
        }
    ],
}


def completed(payload: dict, *, returncode: int = 0, stderr: str = "") -> Mock:
    return Mock(returncode=returncode, stdout=json.dumps(payload), stderr=stderr)


def probe(payload: dict, *, path: str = "clip.m4a"):
    with patch(
        "sentrix.infrastructure.media.ffprobe.subprocess.run",
        return_value=completed(payload),
    ):
        return FfprobeAudioMetadataReader().read(Path(path))


def test_calls_ffprobe_with_the_same_flags_as_the_video_reader():
    """两个适配器共用 ``ffprobe.py``：命令行必须一模一样，否则「同一份标签」这个前提就没了。"""
    with patch(
        "sentrix.infrastructure.media.ffprobe.subprocess.run",
        return_value=completed(AAC_PAYLOAD),
    ) as run:
        FfprobeAudioMetadataReader().read(Path("clip.m4a"))

    command = run.call_args.args[0]
    assert command[:4] == ["ffprobe", "-v", "error", "-print_format"]
    assert "json" in command
    assert command[command.index("-show_format") + 1] == "-show_streams"
    assert command[-1] == str(Path("clip.m4a").resolve())


def test_reads_duration_codec_sample_rate_channels_and_bit_rate():
    result = probe(AAC_PAYLOAD)

    # 容器（format）的 5.0 秒赢过流的 4.5 秒——「总时长取容器」这条取法被钉住。
    assert result.duration_sec == pytest.approx(5.0)
    assert result.codec == "aac"
    assert result.sample_rate == 44100
    assert result.channels == 2
    # 流上的码率优先于容器的总码率（129000 是容器那份）。
    assert result.bit_rate == 128000


def test_creationdate_and_iso6709_gps_come_through_the_shared_helper():
    """拍摄时间与 GPS 与视频走同一条解析（``ffprobe.py``）——取错会整体偏一个时区。"""
    result = probe(AAC_PAYLOAD)

    assert result.captured_at == "2026-08-02T14:55:56+08:00"
    assert result.creation_source == "com.apple.quicktime.creationdate"
    assert result.latitude == pytest.approx(22.4846)
    assert result.longitude == pytest.approx(114.5438)
    assert result.captured_location == "22.484600,114.543800"
    assert result.captured_datetime() is not None
    assert result.gps() is not None


def test_bit_rate_falls_back_to_the_container_value():
    """流上没有 ``bit_rate`` 时取容器的——两个都没有才是 0，不炸。"""
    payload = {
        "format": {"duration": "1", "bit_rate": "96000", "tags": {}},
        "streams": [{"codec_type": "audio", "codec_name": "mp3", "sample_rate": "48000", "channels": 1}],
    }
    assert probe(payload).bit_rate == 96000

    payload["format"].pop("bit_rate")
    assert probe(payload).bit_rate == 0


def test_no_audio_stream_raises_value_error():
    """没有音频流就是探测失败，不是「一条空元数据」——与视频适配器对称。"""
    payload = {
        "format": {"duration": "3", "tags": {}},
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1, "height": 1}],
    }

    with pytest.raises(ValueError, match="no audio stream"):
        probe(payload)


def test_ffprobe_failure_raises_value_error():
    """端口契约：读不出来抛 ``ValueError``，由用例接住记进 ``audio_probe_error``。"""
    with (
        patch(
            "sentrix.infrastructure.media.ffprobe.subprocess.run",
            return_value=completed({}, returncode=1, stderr="No such file or directory"),
        ),
        pytest.raises(ValueError, match="ffprobe failed"),
    ):
        FfprobeAudioMetadataReader().read(Path("gone.m4a"))


def test_half_a_coordinate_is_not_a_gps_point():
    payload = {
        "format": {"duration": "1", "tags": {"location": "+22.4846/"}},
        "streams": [{"codec_type": "audio", "codec_name": "aac", "tags": {}}],
    }
    metadata = probe(payload)

    assert metadata.gps() is None
    assert metadata.captured_datetime() is None
