"""trace 导出：把一轮 run 的模型调用轨迹导成可下载的 JSON。

1.0 有两条（``benchmark_orchestrator.py`` 的 ``export_traces`` ``:6724`` 与
``export_sft`` ``:6447``），2.0 逐行移植。两者导的东西不一样：

- ``export_traces``：**每题只导两项**——planner 的完整输入/输出，加上 final 那一次的
  完整轨迹。1.0 的 docstring 写明这个取舍，照搬。
- ``export_sft``：**完整思考轨迹**——每题全部 ``debug_trace`` 步 + ``agent2_trace`` +
  评测元数据，外加一份 prompt→response 的 SFT 样本。

**前端只用 ``export-traces``**（``App.vue:2572`` 的 ``window.open``），``export-sft``
在 1.0 前端零命中。仍然一并实现：它是 1.0 的真实能力（SFT 训练数据导出），
少了它，「同一个界面换后端」时那条能力会静默消失。已登记在 ``s10-deviations.md``。

**两处刻意的差异，别当成笔误：**

1. ``export_traces`` 的过滤是**两个独立的 ``if``**（``scores`` 与 ``min_score`` 同时生效），
   而 ``export_sft`` 是 ``if/elif``（给了 ``scores`` 就忽略 ``min_score``）。1.0 就是这样。
2. ``export_sft`` 里的 ``_parse`` 用 ``ast.literal_eval`` 把字符串形状的
   ``arguments`` / ``observation`` 还原成结构。那是 1.0 的兼容处理——旧 run.json 里
   这两个字段是 repr 字符串而不是 JSON。
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from typing import Any

#: 1.0 ``:6530`` 的 ``retrieval`` 子对象取的字段清单，顺序照抄。
_RETRIEVAL_FIELDS = (
    "retrieval_recall",
    "retrieval_precision",
    "retrieval_f1",
    "media_retrieval_recall",
    "media_retrieval_precision",
    "media_retrieval_f1",
    "image_retrieval_recall",
    "image_retrieval_precision",
    "image_retrieval_f1",
    "video_retrieval_recall",
    "video_retrieval_precision",
    "video_retrieval_f1",
    "retrieval_media_refs",
    "answer_evidence_media_refs",
    "gt_media",
    "predicted_media",
    "retrieved_candidate_media",
    "evidence_source_media",
    "gt_images",
    "predicted_images",
    "predicted_file_names",
    "matched_file_names",
)


def parse_filter(raw: str) -> list[int]:
    """1.0 ``:7795`` / ``:7818``：``?scores=`` 查询串 → 分数集合。

    **只认 ``0`` / ``1`` / ``2``**，认不出的项直接丢掉（不是报错）。空串得到空表，
    调用方把空表当「没给过滤」。
    """
    return [int(x) for x in raw.split(",") if x.strip() in {"0", "1", "2"}]


def parse_min_score(raw: str) -> int | None:
    """1.0 同两处：``?min_score=`` 只认 ``1`` / ``2``，其余当没给。"""
    return int(raw) if raw in {"1", "2"} else None


def export_traces(
    state: Mapping[str, Any],
    *,
    scores: Sequence[int] | None = None,
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    """1.0 ``:6724-6770``。返回一个 list，不是带信封的对象（1.0 原样如此）。"""
    include = set(scores) if scores else None
    items_out: list[dict[str, Any]] = []
    for item in state.get("items") or []:
        item_score = (item.get("judge") or {}).get("score")
        if include is not None and item_score not in include:
            continue
        if min_score is not None and (item_score is None or item_score < min_score):
            continue
        planner = None
        model_steps: list[Mapping[str, Any]] = []
        for turn in item.get("runtime_turns") or []:
            for step in turn.get("debug_trace") or []:
                if not isinstance(step, Mapping):
                    continue
                if step.get("type") == "planner":
                    planner = {
                        "type": "planner",
                        "prompt": step.get("prompt"),
                        "raw_full": step.get("raw_full") or step.get("raw"),
                    }
                elif step.get("type") == "model":
                    model_steps.append(step)
        final_trace = None
        if model_steps:
            last = model_steps[-1]
            final_trace = {
                "step_id": last.get("step_id"),
                "prompt": last.get("prompt"),
                "raw_full": last.get("raw_full") or last.get("raw"),
            }
        items_out.append(
            {
                "qa_id": item.get("qa_id"),
                "question": item.get("question"),
                "planner": planner,
                "final_trace": final_trace,
            }
        )
    return items_out


def _parse(value: Any) -> Any:
    """1.0 ``:6456`` 的 ``_parse``。已经是 dict 的、不是字符串的、空串的一律原样返回。"""
    if isinstance(value, Mapping) or not isinstance(value, str) or not value.strip():
        return value
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def export_sft(
    state: Mapping[str, Any],
    *,
    scores: Sequence[int] | None = None,
    min_score: int | None = None,
) -> dict[str, Any]:
    """1.0 ``:6447-6551``。

    ``scores`` 非空时只导出评分落在集合内的题；``min_score`` 是**兼容旧参数**，
    只在前者没给时才起作用（1.0 的 ``if/elif``）。
    """
    include = set(scores) if scores else None
    items_out: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for item in state.get("items") or []:
        if include is not None:
            if (item.get("judge") or {}).get("score") not in include:
                continue
        elif min_score is not None:
            item_score = (item.get("judge") or {}).get("score")
            if item_score is None or item_score < min_score:
                continue
        qa_id = item.get("qa_id")
        turns_out: list[dict[str, Any]] = []
        for turn in item.get("runtime_turns") or []:
            steps_out: list[dict[str, Any]] = []
            for step in turn.get("debug_trace") or []:
                if not isinstance(step, Mapping):
                    continue
                copy = dict(step)
                if isinstance(copy.get("arguments"), str):
                    copy["arguments"] = _parse(copy["arguments"])
                if isinstance(copy.get("observation"), str):
                    copy["observation"] = _parse(copy["observation"])
                steps_out.append(copy)
            turns_out.append(
                {
                    "turn": turn.get("index"),
                    "message": turn.get("message"),
                    "expected_action": turn.get("expected_action"),
                    "answer": turn.get("answer"),
                    "agent_status": turn.get("agent_status"),
                    "turn_outcome": turn.get("turn_outcome"),
                    "steps": steps_out,
                }
            )
            # SFT prompt→response 样本（1.0 的「兼容旧格式」，完整轨迹见 items）。
            for step in turn.get("debug_trace") or []:
                if not isinstance(step, Mapping) or step.get("type") != "model":
                    continue
                prompt = step.get("prompt")
                response = step.get("raw_full") or step.get("raw")
                if not prompt or not isinstance(response, str) or not response.strip():
                    continue
                messages = list(prompt) if isinstance(prompt, list) else []
                messages.append({"role": "assistant", "content": response})
                samples.append(
                    {
                        "qa_id": qa_id,
                        "turn": turn.get("index"),
                        "step": step.get("step_id") or step.get("status", "complete"),
                        "messages": messages,
                    }
                )
        items_out.append(
            {
                "qa_id": qa_id,
                "question": item.get("question"),
                "expected_action": item.get("expected_action"),
                "answer": item.get("answer"),
                "judge": item.get("judge"),
                "task_judge": item.get("task_judge"),
                "retrieval": {key: item.get(key) for key in _RETRIEVAL_FIELDS},
                "agent_status": item.get("agent_status"),
                "termination_reason": item.get("termination_reason"),
                "turn_outcome": item.get("turn_outcome"),
                "timing_breakdown": item.get("timing_breakdown"),
                "agent_stability": item.get("agent_stability"),
                "answer_grounding": item.get("answer_grounding"),
                "tool_trace": item.get("tool_trace"),
                "agent2_trace": item.get("agent2_trace"),
                "turns": turns_out,
            }
        )
    return {
        "run_id": state.get("run_id"),
        "count": len(samples),
        "item_count": len(items_out),
        "items": items_out,
        "samples": samples,
        "scores": sorted(include) if include is not None else None,
        "min_score": min_score,
        "filtered": (include is not None or min_score is not None),
    }
