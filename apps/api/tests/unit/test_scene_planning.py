"""场景切分规则：固定时长窗、中点取帧、封顶按比例放大（S8b T511）。

这些是 2.0 新定的行为（1.0 没有对应物，见 ``domain/media/scenes.py`` 的模块注释），
所以每一条都得说清它挡的是哪种退化——改一个数，抽帧数与 VLM 调用数一起变，不报错。
"""

from __future__ import annotations

from sentrix.domain.media.scenes import MAX_SCENES, SCENE_WINDOW_SEC, plan_scenes


def test_a_short_video_is_a_single_scene_and_the_frame_sits_at_its_middle():
    (scene,) = plan_scenes(12.5)

    assert (scene.index, scene.start_sec, scene.end_sec) == (0, 0.0, 12.5)
    # 中点不是「起点」也不是「终点」：取整成 0 会让每个场景的代表帧都落在黑场/转场上。
    assert scene.keyframe_timestamp_sec == 6.25


def test_an_algorithm_selected_timestamp_overrides_the_window_middle():
    from sentrix.domain.media.scenes import SceneWindow

    scene = SceneWindow(index=0, start_sec=2.0, end_sec=8.0, selected_timestamp_sec=6.7)

    assert scene.keyframe_timestamp_sec == 6.7


def test_windows_are_back_to_back_and_cover_the_whole_video():
    scenes = plan_scenes(125.0)

    assert len(scenes) == 3
    assert [(item.start_sec, item.end_sec) for item in scenes] == [
        (0.0, 60.0),
        (60.0, 120.0),
        (120.0, 125.0),
    ]
    # 中点严格落在区间内，最后一窗也不例外——``-ss`` 落到片尾之外会抽出空文件。
    for item in scenes:
        assert item.start_sec < item.keyframe_timestamp_sec < item.end_sec


def test_the_scene_count_is_capped_and_the_window_grows_instead_of_dropping_tail():
    scenes = plan_scenes(3000.0)

    assert len(scenes) == MAX_SCENES
    # 3000 / 40 = 75 秒一窗；封顶的实现是**放大窗口**，不是「只取前 40 个 60 秒窗」
    # （后者会让后 600 秒一条语义都不进库，而且看不出来）。
    assert scenes[0].end_sec == 75.0
    assert scenes[-1].end_sec == 3000.0
    for item in scenes:
        assert item.start_sec < item.keyframe_timestamp_sec < item.end_sec


def test_an_unknown_duration_yields_no_scenes():
    """时长读不出来时**不猜**：返回空，由调用方把原因记在结果里。"""
    assert plan_scenes(0.0) == ()
    assert plan_scenes(-1.0) == ()


def test_the_default_window_is_one_minute():
    assert SCENE_WINDOW_SEC == 60.0
    # 60.001 秒就要第二个窗——边界不能靠取整吞掉。
    assert len(plan_scenes(60.0)) == 1
    assert len(plan_scenes(60.001)) == 2
