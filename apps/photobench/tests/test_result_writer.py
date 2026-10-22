"""``FileResultWriter`` 的用例：主链落盘的那三件文件。

``RunResultWriter`` 是这一片唯一把评测结果写进 ``results/`` 的东西，而它的形状
**必须与 1.0 逐字节兼容**——2.0 的读侧能直接读 1.0 的 ``results/`` 目录（那 19 GB
不迁进仓库），靠的就是这四件文件的名字与内容格式没变。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from photobench.infrastructure.results.store import FileResultWriter

STATE = {
    "run_id": "run_x",
    "status": "completed",
    "items": [{"qa_id": "q1"}, {"qa_id": "q2"}],
}


def test_write_snapshot_round_trips_and_splits_items_into_jsonl(tmp_path: Path):
    """``run.json`` 是整份状态，``results.jsonl`` 是 ``items`` 的逐行投影（1.0 ``:2049``）。

    **两份都要对**：只断言 ``run.json`` 读得回来的用例，对一个把 ``results.jsonl``
    写成空文件的实现同样成立——而那个文件正是「447 MB 的 run.json 无法被逐行消费」
    时的唯一出口。
    """
    writer = FileResultWriter(tmp_path)
    writer.write_snapshot("run_x", STATE)
    assert json.loads((tmp_path / "run_x" / "run.json").read_text(encoding="utf-8")) == STATE
    lines = (tmp_path / "run_x" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"qa_id": "q1"}, {"qa_id": "q2"}]


def test_write_snapshot_replaces_instead_of_appending(tmp_path: Path):
    """一轮跑里会反复写快照（每个相位、每题一次）。追加式写会让 ``results.jsonl``
    随进度线性膨胀，而读侧按「一行一题」解析它。"""
    writer = FileResultWriter(tmp_path)
    writer.write_snapshot("run_x", STATE)
    writer.write_snapshot("run_x", {**STATE, "items": [{"qa_id": "q1"}]})
    lines = (tmp_path / "run_x" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"qa_id": "q1"}]


def test_write_snapshot_leaves_no_temporary_file_behind(tmp_path: Path):
    """原子写（临时文件 + ``replace``）：写到一半被读到的 ``run.json`` 就是
    「整个 run 损坏」。这条断言的是**临时文件被收走**——留在盘上会让人以为
    有一个损坏的 run。"""
    writer = FileResultWriter(tmp_path)
    writer.write_snapshot("run_x", STATE)
    leftovers = [path.name for path in (tmp_path / "run_x").iterdir() if path.name != "run.json"]
    assert set(leftovers) == {"results.jsonl", "snapshot-commit.json", ".snapshots"}
    assert not list((tmp_path / "run_x").rglob("*.tmp"))


def test_reset_then_append_leaves_exactly_one_sample(tmp_path: Path):
    """采样是追加式的，所以 run 开始时必须截空（1.0 ``:2105``）。

    **不截空的后果是「上一次同名 run 的曲线与本轮混在一起」**——两条曲线叠在一起
    仍然能被解析、仍然有长度，只是每一张图都错。这条用例是唯一能区分的地方。
    """
    writer = FileResultWriter(tmp_path)
    writer.reset_gpu_samples("run_x")
    writer.append_gpu_sample("run_x", {"index": 0, "temperature_c": 50.0})
    writer.reset_gpu_samples("run_x")
    writer.append_gpu_sample("run_x", {"index": 0, "temperature_c": 61.0})
    lines = (tmp_path / "run_x" / "gpu_samples.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["temperature_c"] for line in lines] == [61.0]


def test_an_append_creates_the_run_directory_when_it_is_the_first_write(tmp_path: Path):
    """采样回调可能早于第一份快照落地（run 一开就起采样线程）。"""
    writer = FileResultWriter(tmp_path)
    writer.append_gpu_sample("run_x", {"index": 0})
    assert json.loads((tmp_path / "run_x" / "gpu_samples.jsonl").read_text(encoding="utf-8")) == {"index": 0}


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "..", ""])
def test_a_run_id_that_escapes_the_results_root_is_refused(tmp_path: Path, run_id: str):
    """``run_id`` 来自请求体。读侧越界最多是读错文件，**写侧越界是覆盖别人的文件**。

    这条与 ``FileRunStore`` 的那条同源，但两处都要有：写侧少一次拦截不会让读侧的
    用例变红，反之亦然。
    """
    writer = FileResultWriter(tmp_path)
    with pytest.raises(KeyError):
        writer.write_snapshot(run_id, STATE)
    with pytest.raises(KeyError):
        writer.append_gpu_sample(run_id, {"index": 0})
