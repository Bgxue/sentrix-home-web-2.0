"""浏览器预览转码：判据、命令行、产物位置。移植 1.0 ``processor.py:58-69``。

同样用假 subprocess：要钉的是「H.264 不转」「转的时候参数是什么」「产物落在哪」，
这些与机器上有没有 ffmpeg 无关。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from sentrix.infrastructure.media.video_transcode import (
    FfmpegVideoTranscoder,
    is_browser_playable,
    transcode_to_h264,
)

PATCH_TARGET = "sentrix.infrastructure.media.video_transcode.subprocess.run"


def test_h264_and_avc1_are_considered_playable():
    """后缀不是判据，``codec_name`` 才是——``avc1`` 是同一个编码在 MP4 里的另一种写法。"""
    assert is_browser_playable("h264") is True
    assert is_browser_playable("AVC1") is True
    assert is_browser_playable("hevc") is False
    assert is_browser_playable("") is False


def test_transcode_command_is_libx264_crf23_with_faststart(tmp_path: Path):
    """``+faststart`` 不是可选项：moov 不前置，浏览器要下完整段才能起播。"""
    source = tmp_path / "in.mov"
    source.write_bytes(b"not really a video")
    target = tmp_path / "out" / "preview.mp4"

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"mp4")
        return Mock(returncode=0, stdout="", stderr="")

    with patch(PATCH_TARGET, side_effect=fake_run) as run:
        result = transcode_to_h264(source, target)

    command = run.call_args.args[0]
    assert command[0] == "ffmpeg"
    assert "-y" in command
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-crf") + 1] == "23"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-c:a") + 1] == "aac"
    assert "+faststart" in command
    # 可选音轨映射：没有声音的视频不该让整次转码失败。
    assert "0:a:0?" in command
    assert result == target
    assert target.is_file()


def test_transcode_failure_raises_with_the_ffmpeg_stderr(tmp_path: Path):
    source = tmp_path / "in.mov"
    source.write_bytes(b"x")

    with (
        patch(PATCH_TARGET, return_value=Mock(returncode=1, stdout="", stderr="boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        transcode_to_h264(source, tmp_path / "preview.mp4")


def test_missing_output_file_is_a_failure_even_with_zero_exit(tmp_path: Path):
    """ffmpeg 退出 0 但产物不在，同样是失败——只看退出码会交出一个不存在的文件路径。"""
    source = tmp_path / "in.mov"
    source.write_bytes(b"x")

    with (
        patch(PATCH_TARGET, return_value=Mock(returncode=0, stdout="", stderr="")),
        pytest.raises(RuntimeError),
    ):
        transcode_to_h264(source, tmp_path / "preview.mp4")


def test_h264_skips_ffmpeg_entirely(tmp_path: Path):
    with patch(PATCH_TARGET) as run:
        result = FfmpegVideoTranscoder(tmp_path / "media").browser_preview(
            "asset_1", tmp_path / "clip.mp4", "h264"
        )

    assert result is None
    run.assert_not_called()


def test_preview_lands_under_the_derived_video_directory(tmp_path: Path):
    source = tmp_path / "clip.mov"
    source.write_bytes(b"x")

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"mp4")
        return Mock(returncode=0, stdout="", stderr="")

    with patch(PATCH_TARGET, side_effect=fake_run):
        result = FfmpegVideoTranscoder(tmp_path / "media").browser_preview("asset_1", source, "hevc")

    assert result == tmp_path / "media" / "derived" / "video" / "asset_1" / "preview.mp4"
    assert result.is_file()
