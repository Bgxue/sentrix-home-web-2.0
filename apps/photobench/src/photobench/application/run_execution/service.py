"""run-execution 主链：``BenchmarkRun.execute``（1.0 ``:2341``）在 2.0 的重建。

**交付点**（S9b-2）：能对一个跑着的 2.0 跑完一轮「建空间 → 种身份 → 导入 → 处理 →
问答」，把每题的问答与轨迹落进 run 目录。

**范围调整（2026-10-04，用户定的，理由是不做两遍）**：计划书原本把「对 2.0 的端点适配」
放在 S9c，但主链本来就要调主 API，先照 1.0 的端点写一遍再在 S9c 改一遍是白费。
所以这个文件**直接按 2.0 的接口写**，S9c 相应收窄为「两个死端点 + 换模型重启 + 收口」。

**与 1.0 的相位对照**（``execute`` 的 ``all_phases`` ``:2356``）：

========================  ==================================================================
相位                      2.0 的落地
========================  ==================================================================
``model_deploy``          保留。**这是 ``LifecycleProvider.start`` / ``.stop`` 的唯一生产
                          调用者**。管理器没配时记 ``not_applicable``——1.0 用
                          ``use_cloud_model`` 表达同一件事，2.0 没有云端模型这个概念，
                          判据换成「有没有 manager_url」。
``scope_setup``           **没有 HTTP 调用**：2.0 的空间由导入时内部 ``ensure``
                          （``application/media/import_assets.py:249``）。这里只决定
                          scope_id 与名字。
``scope_attach``          删。它是 reuse 模式的入口，而 reuse 依赖写侧路由（S9b-3）。
``identity_seed``         逐个 ``POST /people/seed``（1.0 的 ``seed-batch`` 在 2.0 不存在）
                          + ``POST /relationships/batch``。
``photo_import``          按媒体目录逐个 ``POST /api/v1/imports``。**2.0 收的是服务端
                          目录**，不是上传的字节——这是本切片最大的一条形状变化。
``pipeline_processing``   轮询 ``GET /api/v1/assets``。2.0 的导入是**同步**的，所以这里
                          通常一两次就收敛；保留轮询是因为它是 1.0 的契约，
                          而且它是「导入真的落库了」的独立证据。
``qa_eval``               每题一个会话：``POST /assistant/conversations`` 建会话，
                          逐条消息 ``POST .../turns`` 再轮询到终态。**Agent 池与 Judge
                          池是两个池**（1.0 ``:3103``）：Agent 一完成就立刻排进 Judge 池。
``gpu_metrics``           ``GpuSampler.aggregate()`` 的曲线统计（1.0 ``:4568``）。
``aggregate``             汇总（1.0 ``:4580``），写入 ``state["summary"]``。
========================  ==================================================================

**judge（S9b-3 接上）**：``_judge_item``（``:3588``）在
``application.run_execution.judge.JudgeRunner`` 里；这里只负责把它排进第二个池。
没有 judge 配置时（用例里的默认）item 的三个 judge 字段保持
``pending_judge`` 占位（1.0 的同一位置也这么写，见 ``:3555-3557``）——读侧的
``domain.runs.analysis.effective_summary`` 要这三个键存在才能算出分布。
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from photobench.application.deploy.service import SentrixModelSwitch
from photobench.application.run_execution.gpu_sampler import PHASE_LABELS, GpuSampler
from photobench.application.run_execution.judge import InlineImages, JudgeRunner
from photobench.application.run_execution.state import RunState
from photobench.domain.datasets.ports import AlbumSource
from photobench.domain.http.ports import JsonHttpClient
from photobench.domain.results.ports import RunResultWriter
from photobench.domain.runs.analysis import (
    apply_live_sample,
    derive_attribution,
    effective_summary,
    now_iso,
)
from photobench.domain.runs.execution import (
    RunCancelledError,
    album_media_entries,
    import_directories,
    index_assets_by_name,
    modality_metrics,
    normalize_media_refs,
    resolve_gt_media,
    resolve_predicted_media,
    safe_slug,
    selected_asset_ids,
    summarize_progress,
)
from photobench.domain.runtime.ports import (
    InferenceProvider,
    LifecycleProvider,
    TelemetryProvider,
)
from photobench.domain.sentrix.ports import SentrixClient

#: 2.0 的 ``AssetStatus``（``domain/media/models.py:46``）只有四态，1.0 那张表里
#: 的 ``video-queued`` / ``semantic_enriching`` 等 2.0 一个都没有。**不照抄 1.0 的表**：
#: 一个不存在的状态留在「待处理」集合里，只会让轮询永远不收敛。
PIPELINE_PENDING_STATUSES = frozenset({"queued", "processing"})
PIPELINE_FAILED_STATUSES = frozenset({"failed"})

#: 2.0 的 ``TurnStatus``（``domain/assistant/models.py:40``）里非终态的两种。
TURN_PENDING_STATUSES = frozenset({"pending", "running"})

#: 模型装载的参数。1.0 是写死的局部量（``:2557`` / ``:2568``），抽成模块常量只为
#: 让用例能 monkeypatch——**在用例里真睡 5 秒 + 30 秒退避是不可接受的**，而那两段
#: 睡眠与「start 被调了几次」这条断言毫无关系。
MODEL_COOLDOWN_SECONDS = 5.0
MODEL_START_BACKOFF_SECONDS = (10, 30, 60, 180)
#: 等模型就绪的上限（1.0 ``_wait_model_ready`` 的 ``ready_timeout=600`` 默认值）。
#: 同样抽成常量：那是一个**真实等待**的循环，用例里跑满它要十分钟。
MODEL_READY_TIMEOUT_SECONDS = 600

#: 管道轮询的间隔与停滞阈值（1.0 ``:2661`` 的 ``PHOTOBENCH_PIPELINE_STALL_TIMEOUT_SECONDS``）。
PIPELINE_POLL_SECONDS = 3.0
DEFAULT_STALL_TIMEOUT_SECONDS = 1200

#: 1.0 ``:213`` 的 ``LIVE_TELEMETRY_TAIL_POINTS``：run.json 里的实时曲线只留尾部
#: 若干个点。**权威来源是 gpu_samples.jsonl**（读侧从它重建完整曲线），这里是
#: 防「run.json 被一次次追加撑大」的那条闸门。
LIVE_TELEMETRY_TAIL_POINTS = max(0, int(os.environ.get("PHOTOBENCH_LIVE_TELEMETRY_TAIL_POINTS", "240")))
#: 1.0 ``:2222``：滚动聚合的发布节流。每来一条样本就重算一次聚合，在一个 0.5s 采样、
#: 跑一小时的 run 上是 7200 次全表扫描；GPU 卡片并不需要那个刷新率。
GPU_METRICS_PUBLISH_SECONDS = 2.0

#: 真正的 ``time.sleep``，**在导入时绑死**。``_cancel_wait`` 靠 ``self.sleep is`` 它
#: 来判断「调用方有没有注入假等待」。不能写成 ``self.sleep is time.sleep``：用例会把
#: 整个 ``service.time`` 换成假钟（``_TickingTime``），那个假钟根本没有 ``sleep``
#: 属性，于是这个判断本身会抛 ``AttributeError``——一次「为了可测试性」的比较把
#: 被测代码弄崩，而且崩在一个与断言无关的地方。
_DEFAULT_SLEEP = time.sleep

#: 顺带搬过来的 item 字段（1.0 ``:3379``）。这些是题库侧的标签，主链只负责带过去。
PASSTHROUGH_FIELDS = (
    "task_type",
    "question_type",
    "tags",
    "angle",
    "difficulty",
    "answerability",
    "scope",
    "scope_anchor",
    "required_evidence_sources",
    "query_anchors",
    "expected_action",
    "answer_claims",
)


@dataclass(frozen=True)
class RunRequest:
    """一次 run 的输入。对应 1.0 ``POST /api/runs`` 的请求体。

    S9b-3 加了 ``runtime_framework``（``detect_runtime_framework`` 的结果，决定
    ``select_runtime_providers`` 选哪一路遥测）与 judge 的四项覆盖（1.0 的
    ``start_suite`` 把它们从请求体透传给 ``BenchmarkRun``）。覆盖为空时用
    ``RunExecution.judge_settings``（配置里的默认值）。
    """

    album_id: str
    qa_set: str
    model_profile: str
    sentrix_url: str
    manager_url: str = ""
    model_base_url: str = ""
    run_id: str = ""
    scope_id: str = ""
    import_timeout: int = 7200
    turn_timeout: int = 900
    qa_concurrency: int = 0
    runtime_framework: str = ""
    judge_url: str = ""
    judge_model: str = ""
    judge_api_key: str = ""
    judge_prompts: Mapping[str, str | None] = field(default_factory=dict)
    evidence_judge_enabled: bool = False


@dataclass(frozen=True)
class JudgeSettings:
    """judge 的**默认**配置。来源是 ``ConfigService``（提示词 / provider / 密钥）。

    ``RunRequest`` 上的同名项是**一次请求的覆盖**（1.0 的 ``start_suite`` 允许请求体
    带 ``judge_url`` / ``judge_model`` / ``judge_api_key`` / 三个提示词）。
    """

    judge_url: str = ""
    judge_model: str = ""
    judge_api_key: str = ""
    answer_quality_prompt: str | None = None
    task_judge_prompt: str | None = None
    evidence_judge_prompt: str | None = None
    evidence_enabled: bool = False


@dataclass
class RunExecution:
    """跑完一条主链。构造一次跑一轮——状态不跨 run 复用（1.0 是一 run 一个对象）。

    ``http`` 与 ``judge_settings`` 都可空：空 ``http`` 意味着**这一轮不打分**
    （用例里的默认），此时 item 保留 ``pending_judge`` 占位。
    """

    client: SentrixClient
    albums: AlbumSource
    writer: RunResultWriter
    lifecycle: LifecycleProvider
    inference: InferenceProvider
    telemetry: TelemetryProvider
    sleep: Callable[[float], None] = field(default=time.sleep)
    http: JsonHttpClient | None = None
    judge_settings: JudgeSettings | None = None
    #: 选中的遥测来源（``select_runtime_providers`` 的 ``source``）。写进 run.json
    #: 供读侧与运维判断这轮的数字是什么口径（1.0 ``:1977`` 的 ``telemetry_source``）。
    telemetry_source: str = ""
    #: 召回图 → data URL 的实现（要用 PIL）。由装配点注入，application 不许 import PIL。
    inline_images: InlineImages | None = None
    #: 换模型 = 重启 2.0（S9c · T923）：把 2.0 的 ``vlm_base_url`` 对准本轮的端点。
    #: ``None`` 时 ``model_deploy`` 如实报「未装配」——用例里的默认，不假装同步过。
    sentrix_switch: SentrixModelSwitch | None = None
    _state: RunState | None = field(default=None, repr=False)
    _judge: JudgeRunner | None = field(default=None, repr=False)
    _sampler: GpuSampler | None = field(default=None, repr=False)
    #: 这一轮的输入。**它不进 ``state.data``**：那份 dict 是落盘契约，会被
    #: ``json.dumps`` 写进 run.json，而 ``RunRequest`` 不是可序列化的值——
    #: 塞进去会让每一次快照写都抛 ``TypeError``，而错误被后台写线程记成
    #: ``_persist_error`` 之后只会表现为「run.json 一直没有出现」。
    _request: RunRequest | None = field(default=None, repr=False)

    @property
    def state(self) -> RunState | None:
        """当前/最近一轮的状态。取消与写侧路由要用它。"""
        return self._state

    def cancel(self, source: str = "api") -> None:
        """1.0 ``BenchmarkRun.cancel``（``:2232``）的 2.0 版。

        **拆成三件事的顺序照搬**：先置取消信号（让正在等的那一段立刻醒来）、再尽力
        回收这一轮起过的模型（否则取消在 model_deploy 中间会把一个没人要的模型留在
        GPU 上）、最后 ``persist(wait=True)`` 把 ``cancelling`` 落盘。

        **1.0 的 ``_cancel_remote_batch`` 没有对应**：它打的是
        ``/api/ingest-batches/{id}/cancel`` 那个 2.0/1.0 都不存在的死端点（S9c 的
        T922 要处理它）。这里不假装调过——那会让 run.json 里出现一个假的
        ``batch_cancel_requested``。
        """
        state = self._state
        if state is None:
            return
        state.request_cancel(source)
        self._reclaim_model_after_cancel(state)
        state.persist(wait=True)

    def _reclaim_model_after_cancel(self, state: RunState) -> None:
        """1.0 ``_reclaim_vllm_after_cancel``（``:2245``）：这一轮起过的模型就停掉。

        判据用「Manager 报的 profile 与本轮要的一致」——不一致说明那个模型不是这一轮
        起的，停了会踩到别人。1.0 另外还看 ``use_current_model`` / ``use_cloud_model``
        两个开关，2.0 没有它们（外部端点那一支本来就没有 Manager 可停）。
        """
        request = self._request
        if request is None or not request.manager_url:
            return
        try:
            manager_state = self.lifecycle.state() or {}
        except Exception:  # noqa: BLE001 —— 回收是尽力而为，探不到就什么都不做
            return
        if manager_state.get("profile") and manager_state.get("profile") == request.model_profile:
            try:
                self.lifecycle.stop({"timeout": 60}, timeout=90)
                state.set("cancel_vllm_stopped", now_iso())
            except Exception as exc:  # noqa: BLE001
                state.set("cancel_vllm_stop_error", str(exc)[:300])

    def execute(self, request: RunRequest) -> dict[str, Any]:
        run_id = request.run_id or f"run_{uuid.uuid4().hex[:12]}"
        album_id = request.album_id
        manifest = self.albums.manifest(album_id)
        scope_id = request.scope_id or self._default_scope_id(album_id, request.model_profile)
        state = RunState(
            self._initial_state(run_id, request, manifest, scope_id, album_id),
            run_id=run_id,
            writer=self.writer,
        )
        # 取消回收模型时要读它（``_reclaim_model_after_cancel``）。存在 self 上而不是
        # state 里：state 要被 json.dumps 落盘，见 ``_request`` 的注释。
        self._request = request
        self._state = state
        self._judge = self._build_judge(request, state)
        state.start_persist_writer()
        state.persist()
        # 采样回调要在 ``on_sample`` 里拿到采样器本身（发布滚动聚合），而采样器的构造
        # 又要先有回调——用一个一格容器解开这个环，等价于 1.0 里 ``self._gpu_sampler``
        # 这个属性（它的回调是方法，能随时读 self）。
        sampler_holder: list[GpuSampler] = []
        sampler = GpuSampler(self.telemetry, on_sample=self._sample_writer(state, sampler_holder))
        sampler_holder.append(sampler)
        self._sampler = sampler
        sampling = self._sampling_enabled() and self.telemetry_source != "unavailable"
        phases: list[tuple[str, Callable[[RunState, RunRequest], None]]] = [
            ("model_deploy", self._phase_model_deploy),
            ("scope_setup", self._phase_scope_setup),
            ("identity_seed", self._phase_identity_seed),
            ("photo_import", self._phase_photo_import),
            ("pipeline_processing", self._phase_processing),
            ("qa_eval", self._phase_qa_eval),
            ("gpu_metrics", self._phase_gpu_metrics),
            ("aggregate", self._phase_aggregate),
        ]
        state.set("status", "running")
        state.set("started_at", now_iso())
        state.set("hardware_snapshots", {"start": self._hardware_snapshot(), "end": None})
        if sampling:
            self.writer.reset_gpu_samples(run_id)
            sampler.start()
        current: str | None = None
        try:
            for name, phase in phases:
                if state.cancelled:
                    raise RunCancelledError(f"cancelled before phase {name}")
                current = name
                state.set("current_phase", name)
                state.phase_start(name)
                phase(state, request)
                state.persist()
            partial = any(
                bucket.get("status") == "partial" for bucket in (state.get("phases") or {}).values()
            )
            state.set("status", "completed_with_errors" if partial else "completed")
        except RunCancelledError as exc:
            # **取消与失败必须分开**：一次主动取消落到 ``failed`` 会让读侧把它当成
            # 「这轮评测炸了」，而事实是有人叫停了它（1.0 ``:2417`` 的同一条区分）。
            if current:
                state.record_phase(current, "status", "cancelled")
                state.record_phase(current, "error", str(exc))
            state.set("status", "cancelled")
            state.set("cancelled_phase", current)
        except Exception as exc:  # noqa: BLE001 —— 1.0 ``:2404``：任何相位异常都收成 failed
            if current:
                state.phase_failed(current, str(exc))
            state.set("status", "failed")
            state.set("failed_phase", current)
            state.set("fatal_error", str(exc))
        finally:
            if sampling:
                sampler.stop()
            state.set("current_phase", None)
            state.set("finished_at", now_iso())
            snapshots = dict(state.get("hardware_snapshots") or {})
            snapshots["end"] = self._hardware_snapshot()
            state.set("hardware_snapshots", snapshots)
            state.persist()
            state.stop_persist_writer()
        return state.data

    # ---- 状态与落盘 ---------------------------------------------------------

    def _default_scope_id(self, album_id: str, model_profile: str) -> str:
        """1.0 ``:2646``：``PhotoBench-{时间戳}-{album}-{模型}``，截到 100 字符。

        2.0 里这个名字同时是 **scope id**（``_ensure_space(scope_id, scope_id)``
        ``import_assets.py:496``）——1.0 的 ``POST /api/memory-spaces`` 收 ``{name}``
        并由服务端造 id。所以这里生成的就是 id 本身，读侧看到的 ``scope_id`` 与
        ``scope_name`` 会是同一个串（1.0 是两个不同的值）。**这条差异登记在行为基线里**：
        靠 scope 名字做正则解析的下游（1.0 的 ``_build_reuse_bases`` ``:1073``）在 2.0
        拿到的形状不同。
        """
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return f"PhotoBench-{stamp}-{safe_slug(album_id)}-{safe_slug(model_profile)[-40:]}"[:100]

    def _initial_state(
        self,
        run_id: str,
        request: RunRequest,
        manifest: dict[str, Any],
        scope_id: str,
        album_id: str,
    ) -> dict[str, Any]:
        rows = self.albums.qa_rows(album_id, request.qa_set)
        return {
            "run_id": run_id,
            "mode": "full",
            "scope_source": "created_by_import",
            "album_id": album_id,
            "album_name": manifest.get("album_name"),
            "model_profile": request.model_profile,
            "model_name": request.model_profile,
            "qa_set": request.qa_set,
            "qa_count": len(rows),
            "scope_id": scope_id,
            "scope_name": scope_id,
            "sentrix_url": request.sentrix_url,
            "manager_url": request.manager_url or None,
            "model_base_url": request.model_base_url or None,
            # 1.0 ``:1977``：这两个是读侧与运维判断「这轮的数字是什么口径」的入口
            # （``run_list_entry`` 会取它们）。
            "telemetry_source": self.telemetry_source or None,
            "runtime_framework": request.runtime_framework or "generic",
            "judge_url": request.judge_url
            or (self.judge_settings.judge_url if self.judge_settings else "")
            or None,
            "judge_model": request.judge_model
            or (self.judge_settings.judge_model if self.judge_settings else "")
            or None,
            "rejudge": None,
            "memory_profile": None,
            "input_integrity": self.albums.integrity(album_id, manifest, request.qa_set),
            "hardware_snapshots": {"start": None, "end": None},
            "status": "pending",
            "created_at": now_iso(),
            "started_at": None,
            "finished_at": None,
            "phases": {},
            "items": [],
            "summary": {},
            "run_valid": False,
            "fatal_error": None,
        }

    def _persist(self, run_id: str, state: RunState) -> None:
        """请求一份快照。``run_id`` 保留只为让调用点不必改——写线程知道自己的 run。"""
        del run_id
        state.persist()

    def _build_judge(self, request: RunRequest, state: RunState) -> JudgeRunner | None:
        """按「请求覆盖 > 配置默认」造一个 ``JudgeRunner``；两者都没有就是不判分。

        **不判分是一条合法路径**，不是错误：用例与「只想跑链路不要分数」的部署都走它。
        ``cancel`` 传的是这一轮的取消信号，所以 judge 的退避与限流等待可取消。
        """
        defaults = self.judge_settings
        if self.http is None:
            return None
        judge_url = request.judge_url or (defaults.judge_url if defaults else "")
        judge_model = request.judge_model or (defaults.judge_model if defaults else "")
        if not judge_url or not judge_model:
            return None
        prompts = request.judge_prompts or {}
        return JudgeRunner(
            http=self.http,
            judge_url=judge_url,
            judge_model=judge_model,
            judge_api_key=request.judge_api_key or (defaults.judge_api_key if defaults else ""),
            answer_quality_prompt=prompts.get("answer_quality")
            or (defaults.answer_quality_prompt if defaults else None),
            task_judge_prompt=prompts.get("task_decision")
            or (defaults.task_judge_prompt if defaults else None),
            evidence_judge_prompt=prompts.get("evidence")
            or (defaults.evidence_judge_prompt if defaults else None),
            evidence_enabled=bool(
                request.evidence_judge_enabled or (defaults.evidence_enabled if defaults else False)
            ),
            sentrix_url=request.sentrix_url,
            cancel=state.cancel_event,
            inline_images=self.inline_images,
        )

    def _sample_writer(
        self, state: RunState, sampler_holder: list[GpuSampler] | None = None
    ) -> Callable[[dict[str, Any]], None]:
        """采样回调：打相位标签、追加 JSONL、并维护 run.json 的实时视图。

        1.0 ``_persist_gpu_sample``（``:2110``）的三段在这里逐段对应：

        1. **追加 JSONL**（立即落盘，取消/崩溃时才不丢）——``gpu_samples.jsonl`` 是
           完整曲线的权威来源。
        2. **更新 ``telemetry_live``**（``domain.runs.analysis.apply_live_sample``）：
           latest/peak/history/phase_snapshots 的滚动视图，给实时 GPU 卡片看。
        3. **发布一次滚动聚合**到 ``phases.gpu_metrics``（节流
           ``GPU_METRICS_PUBLISH_SECONDS``，1.0 ``_refresh_gpu_metrics_phase``
           ``:2209``）——卡片不必等到整轮结束才有数字。

        ``sampler_holder`` 是那格「装着采样器自己的容器」：回调要在 ``on_sample`` 里
        读 ``sampler.samples`` 来算滚动聚合，而采样器要先用回调构造。没有它（用例里
        直接调这个闭包）时第 3 段跳过。
        """
        sampler = sampler_holder[0] if sampler_holder else None
        last_publish = [0.0]

        def on_sample(sample: dict[str, Any]) -> None:
            phase = str(state.get("current_phase") or "unassigned")
            label = PHASE_LABELS.get(phase, phase)
            persisted = dict(sample)
            persisted.setdefault("phase", phase)
            persisted.setdefault("phase_label", label)
            self.writer.append_gpu_sample(state.run_id, persisted)
            live = state.mutate("telemetry_live")
            with state.lock:
                apply_live_sample(live, sample, phase, label, tail=LIVE_TELEMETRY_TAIL_POINTS)
                if sample.get("llama_server_args") and not state.data.get("llama_server_args"):
                    state.data["llama_server_args"] = sample.get("llama_server_args")
            if sampler is not None:
                now = time.monotonic()
                if now - last_publish[0] >= GPU_METRICS_PUBLISH_SECONDS:
                    last_publish[0] = now
                    self._publish_partial_gpu_metrics(state, sampler)
            state.persist()

        return on_sample

    def _publish_partial_gpu_metrics(self, state: RunState, sampler: GpuSampler) -> None:
        """1.0 ``_refresh_gpu_metrics_phase``（``:2209``）：让 GPU 卡片是活的。

        **已经 ``done`` / ``skipped`` 的相位不再被覆盖**——那说明聚合已经由
        ``_phase_gpu_metrics`` 写过，滚动值把它盖回去会让最终数字随机停在某一秒。
        """
        existing = (state.get("phases") or {}).get("gpu_metrics") or {}
        if existing.get("status") in {"done", "skipped"}:
            return
        if not sampler.samples:
            return
        partial = sampler.aggregate()
        if not partial.get("samples_count"):
            return
        partial.update(
            {
                "status": "running",
                "partial": True,
                "started_at": existing.get("started_at") or now_iso(),
                "updated_at": now_iso(),
                "llama_server_args": state.get("llama_server_args"),
            }
        )
        with state.lock:
            state.data.setdefault("phases", {})["gpu_metrics"] = partial

    def _sampling_enabled(self) -> bool:
        return os.environ.get("PHOTOBENCH_DISABLE_GPU_SAMPLING", "0") != "1"

    def _hardware_snapshot(self) -> dict[str, Any]:
        """1.0 ``:2303``：run 开始/结束时各采一次硬件与模型状态。

        与 1.0 的差别：1.0 在 ``use_cloud_model`` / ``telemetry_source == "unavailable"``
        时**整段跳过**。2.0 没有那两个标志（``select_runtime_providers`` 还没搬），
        所以改成「探不到就如实记 status」——1.0 对遥测的既定口径本来就是
        「探不到报 unavailable，绝不用 0 顶替」（``domain/runtime/ports.py`` 的注释）。
        """
        snapshot: dict[str, Any] = {"captured_at": now_iso()}
        try:
            snapshot["manager"] = self.lifecycle.state()
        except Exception as exc:  # noqa: BLE001
            snapshot["manager_error"] = str(exc)
        try:
            gpu = self.telemetry.gpu_stats()
            snapshot["gpu"] = (gpu.get("data") or {}).get("gpus") or []
            if gpu.get("status") != "available":
                snapshot["gpu_status"] = gpu
        except Exception as exc:  # noqa: BLE001
            snapshot["gpu_error"] = str(exc)
        try:
            memory = self.telemetry.process_memory()
            snapshot["process_memory"] = memory.get("data") if memory.get("status") == "available" else None
            if memory.get("status") != "available":
                snapshot["process_memory_status"] = memory
        except Exception as exc:  # noqa: BLE001
            snapshot["process_memory_error"] = str(exc)
        return snapshot

    # ---- model_deploy -------------------------------------------------------

    def _phase_model_deploy(self, state: RunState, request: RunRequest) -> None:
        """1.0 ``:2526``。

        **``LifecycleProvider.start`` / ``.stop`` 的唯一生产调用者。** 管理器没配时
        径直记 ``not_applicable``：1.0 用的是 ``use_cloud_model`` 那个开关，2.0 没有
        云端模型，判据换成「有没有 manager_url」——没有管理器就没有可起停的东西，
        与 1.0 的云端分支在语义上是同一件事。

        **1.0 的第 4 步（``bind-runtime`` 把 Sentrix 指向新模型）在 2.0 变成「重启 2.0」**
        （S9c · T923 / 裁决③）：2.0 的 ``vlm_base_url`` 是启动时从 Settings 读死的
        （``apps/api/src/sentrix/bootstrap/container.py`` 的
        ``ServedModel(settings.vlm_base_url)``），端点换不了，只能换一个环境变量再起一个
        进程。这一段的 ``gamma_sync`` 现在是真的（``_sync_sentrix_endpoint``），不再是
        占位——**两个分支都要做**：没有 Manager 的「外部端点」正是 153 上的形态，
        而 2.0 的助手用哪个模型只由它自己的 ``vlm_base_url`` 决定，与有没有 Manager 无关。
        """
        if not request.manager_url:
            state.phase_done(
                "model_deploy",
                {
                    "source": "external_endpoint",
                    "deployment_mode": "external_model_manager_not_configured",
                    "manager_status": "not_applicable",
                    "model_base_url": request.model_base_url or None,
                    "gamma_sync": self._sync_sentrix_endpoint(request),
                },
            )
            return
        started = time.perf_counter()
        stop_seconds = 0.0
        # 1.0 ``:2547``：卸载失败不阻断装载——没在跑的模型本来就停不掉。
        with contextlib.suppress(Exception):
            stop_seconds = self._timed(lambda: self.lifecycle.stop({"timeout": 60}, timeout=90))
        self._cancel_wait(MODEL_COOLDOWN_SECONDS)
        load_started = time.perf_counter()
        start_error: Exception | None = None
        for attempt, backoff in enumerate(MODEL_START_BACKOFF_SECONDS):
            try:
                self.lifecycle.start({"profile": request.model_profile, "wait_ready": False}, timeout=120)
                start_error = self._wait_model_ready(request)
                if start_error is None:
                    break
            except RunCancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                start_error = exc
            if attempt < len(MODEL_START_BACKOFF_SECONDS) - 1:
                self._cancel_wait(backoff)
        if start_error is not None:
            raise RuntimeError(
                f"model manager /start failed after {len(MODEL_START_BACKOFF_SECONDS)} attempts: "
                f"{start_error}"
            ) from start_error
        load_seconds = time.perf_counter() - load_started
        manager_state = self.lifecycle.state()
        health_error = self._probe_model_endpoint(manager_state, request)
        if health_error is not None:
            raise health_error
        state.phase_done(
            "model_deploy",
            {
                "source": "managed",
                "deployment_mode": "model_manager",
                "manager_status": "ready",
                "unload_seconds": round(stop_seconds, 1),
                "load_seconds": round(load_seconds, 1),
                "model_probe": self._model_probe(manager_state, request),
                "gamma_sync": self._sync_sentrix_endpoint(request),
                "total_seconds": round(time.perf_counter() - started, 1),
                "model_state": {
                    key: manager_state.get(key)
                    for key in (
                        "profile",
                        "served_model_name",
                        "port",
                        "dtype",
                        "quantization",
                        "max_model_len",
                        "max_num_seqs",
                    )
                },
            },
        )

    def _sync_sentrix_endpoint(self, request: RunRequest) -> dict[str, Any]:
        """1.0 第 4 步（``bind-runtime``，``:2611``）的 2.0 落地：把 2.0 指到本轮端点。

        **失败要中断整轮，这一条与 1.0 刻意不同。** 1.0 的 ``bind-runtime`` 只是同步一个
        **缓存**——Sentrix 的答题用的是它自己的直连端点，同步失败最多让 grep 慢一点，
        所以 1.0 把异常收进 ``gamma_sync.error`` 就继续跑（``:2613``）。2.0 不是：
        **助手的答案由 2.0 用自己的 ``vlm_base_url`` 产出**，重启失败意味着这一轮
        评测的每一个答案都来自另一个模型——继续跑只会产出一份**标着 A 模型、其实是
        B 模型**的结果。那正是本项目最不想要的那种错。所以：真重启过（``restarted``）
        却没起来 → 抛，让 ``model_deploy`` 记 ``failed``。

        「本来就不用换」（``restarted=False``）不是失败，照常记结果——没有 Manager、
        端点又正好对得上，是 153 上最常见的形态。
        """
        if self.sentrix_switch is None:
            return {
                "ok": False,
                "error": "sentrix process controller is not wired",
                "restarted": False,
            }
        outcome = self.sentrix_switch.ensure_endpoint(request.model_base_url)
        if outcome.restarted and not outcome.ok:
            raise RuntimeError(f"could not point Sentrix at {outcome.endpoint or '(none)'}: {outcome.error}")
        return outcome.to_state()

    def _cancel_wait(self, seconds: float) -> None:
        """等一段时间；被取消就抛 ``RunCancelledError``。1.0 ``self._cancel.wait(...)``。

        S9b-2 如实记过「``_wait_model_ready`` 与 ``_await_turn`` 的等待不是取消感知的」
        ——这里还清：等待走 ``Event.wait``，取消立刻打断，而不是「下一次循环才看到」。

        **``sleep`` 被注入过时走注入的那个**。用例注入 ``lambda _: None`` 是为了不真等
        （否则 5 秒冷却 + 3 秒轮询 × 每条用例会把秒级的用例拖成分钟级），而
        ``Event.wait`` 不吃这个替身。两种模式下**取消语义是同一份**：真等待时由
        ``Event.wait`` 的返回值给，假等待时由等待后的 ``is_set()`` 给——不会出现
        「用例里能取消、生产里不能」。
        """
        state = self._state
        event = state.cancel_event if state is not None else None
        if event is not None and self.sleep is _DEFAULT_SLEEP:
            if event.wait(seconds):
                raise RunCancelledError("cancelled while waiting")
            return
        self.sleep(seconds)
        if event is not None and event.is_set():
            raise RunCancelledError("cancelled while waiting")

    def _wait_model_ready(self, request: RunRequest) -> Exception | None:
        """1.0 ``:2471``：轮询 Manager 状态，再用一次探活确认端点真的在答。

        返回 ``None`` 表示就绪，返回异常表示这一轮没等到。1.0 用 ``self._cancel``
        的 ``wait`` 做取消感知——S9b-3 接上取消路由后这里是真的可取消的。
        """
        deadline = time.monotonic() + MODEL_READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                manager_state = self.lifecycle.state() or {}
            except Exception as exc:  # noqa: BLE001
                manager_state = {}
                if time.monotonic() >= deadline:
                    return exc
            served = {manager_state.get("profile"), manager_state.get("served_model_name")}
            if request.model_profile in served:
                probe = self._probe_model_endpoint(manager_state, request, timeout=20)
                if probe is None:
                    return None
            self._cancel_wait(2.0)
        return TimeoutError(f"model not ready within {MODEL_READY_TIMEOUT_SECONDS}s")

    def _probe_model_endpoint(
        self, manager_state: dict[str, Any], request: RunRequest, timeout: int = 180
    ) -> Exception | None:
        """1.0 ``:2496``：打一次最小对话确认端点活着。

        1.0 用 ``request_json`` 手拼 ``{root}/v1/chat/completions``；2.0 走
        ``InferenceProvider.chat``——**这正是那个端口存在的理由**（避免每个调用点
        自己拼 OpenAI 路径）。返回 ``None`` 表示通，返回异常表示不通。
        """
        deadline = time.monotonic() + timeout
        model = str(manager_state.get("served_model_name") or request.model_profile)
        while True:
            try:
                self.inference.chat(
                    {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                    timeout=30,
                )
                return None
            except Exception as exc:  # noqa: BLE001
                if time.monotonic() >= deadline:
                    return RuntimeError(f"model endpoint did not become ready within {timeout}s: {exc}")

    def _model_probe(self, manager_state: dict[str, Any], request: RunRequest) -> dict[str, Any]:
        """1.0 ``model_probe``（``:809``）：流式测 TTFT 与吞吐。

        1.0 自己拼 ``/v1/chat/completions`` 并逐行解析 SSE；2.0 用
        ``InferenceProvider.chat_stream``——**这是那个端口的第一个生产调用者**
        （``domain/runtime/ports.py`` 的注释说它的返回类型是「逐行的迭代器」，
        正是为这段代码准备的）。探测失败不抛：1.0 ``:2635`` 把它收进 ``{"error": ...}``
        然后照常跑完整轮。
        """
        model = str(manager_state.get("served_model_name") or request.model_profile)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hello, how are you? Please reply briefly."}],
            "max_tokens": 128,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            started = time.perf_counter()
            ttft: float | None = None
            content_tokens = 0
            usage: dict[str, Any] = {}
            for line in self.inference.chat_stream(payload, timeout=60):
                chunk = _parse_sse_line(line)
                if chunk is None:
                    continue
                choices = chunk.get("choices") or []
                delta = choices[0].get("delta", {}) if choices else {}
                if delta.get("content"):
                    if ttft is None:
                        ttft = (time.perf_counter() - started) * 1000
                    content_tokens += 1
                if chunk.get("usage"):
                    usage = chunk["usage"]
            total_ms = (time.perf_counter() - started) * 1000
            completion_tokens = usage.get("completion_tokens", content_tokens)
            return {
                "ttft_ms": round(ttft, 1) if ttft else None,
                "total_ms": round(total_ms, 1),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": completion_tokens,
                "tokens_per_second": round(completion_tokens / total_ms * 1000, 1) if total_ms > 0 else 0,
            }
        except Exception as exc:  # noqa: BLE001 —— 1.0 ``:2635``
            return {"error": str(exc)}

    @staticmethod
    def _timed(action: Callable[[], Any]) -> float:
        started = time.perf_counter()
        action()
        return time.perf_counter() - started

    # ---- scope_setup --------------------------------------------------------

    def _phase_scope_setup(self, state: RunState, request: RunRequest) -> None:
        """**没有 HTTP 调用。** 1.0 ``:2643`` 调 ``POST /api/memory-spaces`` 建空间；
        2.0 没有这个端点——空间由导入路径内部的 ``_ensure_space`` 建
        （``application/media/import_assets.py:249`` / ``:495``，``ensure(id, id)``，
        幂等 upsert），``POST /people/seed`` 那条路也会先 ``spaces.ensure``
        （``api/routes/identity.py:963``）。

        所以这个相位只确认 id 已经有名字。真正确认空间存在的证据在
        ``identity_seed`` 与 ``photo_import`` 两次调用成功——**那两次调用的前提
        就是空间能被 ensure 出来**。
        """
        state.phase_done(
            "scope_setup",
            {
                "scope_id": state.get("scope_id"),
                "scope_name": state.get("scope_name"),
                "created_by": "import_ensure_space",
                "note": "2.0 的空间由导入/种身份的内部 ensure 创建，本相位不发请求",
            },
        )

    # ---- identity_seed ------------------------------------------------------

    def _phase_identity_seed(self, state: RunState, request: RunRequest) -> None:
        """1.0 ``:2684``。

        **批量 → 逐个。** 1.0 的 ``POST /api/people/seed-batch`` 一次带 N 个人 + N 张
        参考图；2.0 只有 ``POST /people/seed``。**失败不中断整批**照搬 1.0 的语义
        （1.0 从 ``results`` 里过滤掉带 ``error`` 的条目，整段不抛），所以这里逐个人
        ``try/except`` 并把错误记进 ``details``——一个人种不上不该让整轮评测没有结果，
        但那条错误必须留在 run.json 里，否则「模型答不出明明」与「明明根本没入库」
        看起来一模一样。
        """
        started = time.perf_counter()
        scope_id = str(state.get("scope_id"))
        manifest = self.albums.manifest(str(state.get("album_id")))
        entity_by_name: dict[str, str] = {}
        details: list[dict[str, Any]] = []
        for face in manifest.get("faces") or []:
            name = str(
                face.get("canonical_name") or face.get("family_role") or (face.get("aliases") or ["未知"])[0]
            )
            relative = str(face.get("ref_image") or "")
            if not relative:
                details.append({"name": name, "error": "manifest 里这条 face 没有 ref_image"})
                continue
            try:
                content = self.albums.read_file(str(state.get("album_id")), relative)
                result = self.client.seed_person(
                    scope_id=scope_id,
                    name=name,
                    family_role=str(face.get("family_role") or ""),
                    aliases=[str(alias) for alias in face.get("aliases") or []],
                    file_name=relative.rsplit("/", 1)[-1],
                    content=content,
                )
            except Exception as exc:  # noqa: BLE001 —— 单人失败不中断整批，见 docstring
                details.append({"name": name, "ref_image": relative, "error": str(exc)})
                continue
            entity_id = str(result.get("entity_id") or "")
            if entity_id:
                entity_by_name[name] = entity_id
            details.append(
                {
                    "name": name,
                    "entity_id": entity_id or None,
                    "cluster_id": result.get("cluster_id"),
                    "face_count": result.get("face_count"),
                    "photos_without_faces": result.get("photos_without_faces"),
                }
            )
        relationship_import: dict[str, Any] = {"status": "not_configured", "requested": 0}
        relationships = manifest.get("family_relationships") or []
        if relationships:
            try:
                relationship_import = dict(
                    self.client.import_relationships(
                        scope_id=scope_id,
                        relationships=list(relationships),
                        entity_by_name=entity_by_name,
                    )
                )
            except Exception as exc:  # noqa: BLE001 —— 与种身份同一个形状
                relationship_import = {"status": "failed", "error": str(exc)}
        state.phase_done(
            "identity_seed",
            {
                "upload_seconds": round(time.perf_counter() - started, 3),
                "seeded_count": len(entity_by_name),
                "failed_count": sum(1 for item in details if item.get("error")),
                "details": details,
                "entity_by_name": entity_by_name,
                "family_relationship_import": relationship_import,
            },
        )

    # ---- photo_import -------------------------------------------------------

    def _phase_photo_import(self, state: RunState, request: RunRequest) -> None:
        """1.0 ``:2733`` 的 2.0 版：**按目录整册导入**。

        1.0 把 manifest 列的每个文件按 8 个一块上传到 ``POST /api/import``，再调
        ``/api/ingest-batches/{id}/complete`` 收尾。2.0 两者都没有：``POST /api/v1/imports``
        收一个**服务端目录**、**同步**跑完整条链，收尾是请求的一部分。

        于是「哪些文件被导了」的权威从 manifest 变成了目录内容——这是 2.0 端点形状
        决定的（``ImportAlbum.execute`` 用 ``root.iterdir()`` 非递归扫后缀
        ``import_album.py:100``）。manifest 仍然决定**导哪几个目录**
        （``import_directories``），而 manifest 里缺失的文件由
        ``input_integrity.missing_files`` 单独记录。
        """
        started = time.perf_counter()
        state.set("current_phase", "photo_import")
        scope_id = str(state.get("scope_id"))
        album_id = str(state.get("album_id"))
        manifest = self.albums.manifest(album_id)
        directories = import_directories(manifest)
        accepted = 0
        deduplicated = 0
        events = 0
        summarized = 0
        failures: list[dict[str, Any]] = []
        per_directory: list[dict[str, Any]] = []
        for relative in directories:
            # 逐个目录检查取消（1.0 ``:2843``）：一个 7200 秒超时的导入正在跑时，
            # 取消至少要能在**下一个目录之前**生效，而不是等整段结束。
            if state.cancelled:
                raise RunCancelledError("cancelled during photo_import")
            absolute, entry_count = self.albums.import_target(album_id, relative)
            limit = min(1000, max(1, entry_count))
            result = self.client.import_album(
                scope_id=scope_id,
                directory=absolute,
                limit=limit,
                timeout=request.import_timeout,
            )
            accepted += int(result.get("imported") or 0)
            deduplicated += int(result.get("deduplicated") or 0)
            events += int(result.get("events") or 0)
            summarized += int(result.get("summarized") or 0)
            for failure in result.get("failures") or []:
                if isinstance(failure, dict):
                    failures.append(
                        {
                            "sample_id": failure.get("file_name") or "unknown",
                            "status": "failed",
                            "error_type": "import_error",
                            "reason": str(failure.get("reason") or "Sentrix 拒绝了这张媒体"),
                        }
                    )
            per_directory.append(
                {
                    "directory": absolute,
                    "relative": relative,
                    "limit": limit,
                    "imported": result.get("imported"),
                    "deduplicated": result.get("deduplicated"),
                    "events": result.get("events"),
                    "lexical_terms": result.get("lexical_terms"),
                    "summarized": result.get("summarized"),
                }
            )
            state.record_phase("photo_import", "accepted_count", accepted)
            state.record_phase(
                "photo_import",
                "progress",
                {"directories_done": len(per_directory), "directories_total": len(directories)},
            )
            self._persist(str(state.get("run_id")), state)
        total_media = len(album_media_entries(manifest))
        state.set("import_accepted_count", accepted)
        phase_result = {
            "total_seconds": round(time.perf_counter() - started, 1),
            "total_photos": len(manifest.get("photos") or []),
            "total_videos": len(manifest.get("videos") or []),
            "total_media": total_media,
            "accepted_count": accepted,
            "deduplicated_count": deduplicated,
            "event_count": events,
            "summarized_count": summarized,
            "failed_count": len(failures),
            "error": f"{len(failures)} 个媒体文件导入失败" if failures else None,
            "error_details": failures,
            "directories": per_directory,
        }
        if failures:
            state.phase_partial("photo_import", phase_result)
        else:
            state.phase_done("photo_import", phase_result)

    # ---- pipeline_processing ------------------------------------------------

    def _phase_processing(self, state: RunState, request: RunRequest) -> None:
        """1.0 ``:2894`` 的 2.0 版：轮询资产状态直到没有待处理项。

        1.0 同时看两处：``GET /api/assets`` 的状态计数与 ``GET /api/ingest-batches/{id}``
        的批次状态。**2.0 没有批次**——导入是同步的，请求返回时流水线已经走完
        （``api/routes/timeline.py`` 的 docstring 明说）。所以这里只剩资产那一半，
        判据也只剩一条：连续两次采样都是零待处理。

        「连续两次」不是多余的：1.0 ``:3010`` 的注释解释了它——资产状态行可能晚于
        批次翻转落地（上传与流水线重叠）。2.0 里这个窗口更小，但**判据去掉它就会
        在状态行恰好还没写的瞬间收工**，而那正是这段代码唯一要防的事。
        """
        started = time.perf_counter()
        scope_id = str(state.get("scope_id"))
        stall_timeout = self._stall_timeout()
        poll_count = 0
        stable_polls = 0
        assets: list[dict[str, Any]] = []
        terminal_error: str | None = None
        last_progress_at = time.monotonic()
        last_progress_wall = now_iso()
        progress_signature: tuple[Any, ...] | None = None
        while True:
            poll_count += 1
            assets = self.client.list_assets(scope_id=scope_id, limit=1000)
            pending = [item for item in assets if item.get("status") in PIPELINE_PENDING_STATUSES]
            failed = [item for item in assets if item.get("status") in PIPELINE_FAILED_STATUSES]
            processed = [item for item in assets if item.get("status") == "processed"]
            status_counts: dict[str, int] = {}
            for asset in assets:
                status = str(asset.get("status") or "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
            # 停滞判据用**稳定签名**：聚合计数 + 已经离开待处理集合的
            # （id, 终态）对。**待处理集合内部的状态抖动不算进展**——1.0
            # ``:2988-2996`` 的注释解释了为什么：视频在重试环里会在
            # ``video-keyframe-extracting`` 与 ``video-queued`` 之间来回，
            # 按「状态字符串变了」判进展的话，停滞检测会被这个抖动永久击败，
            # 管道挂死而没人发现。2.0 的状态少，但判据不能因此简化。
            non_pending_signature = tuple(
                sorted(
                    (
                        str(asset.get("id") or asset.get("file_name") or index),
                        str(asset.get("status") or "unknown"),
                    )
                    for index, asset in enumerate(assets)
                    if asset.get("status") not in PIPELINE_PENDING_STATUSES
                )
            )
            signature = (len(pending), len(processed), len(failed), non_pending_signature)
            now = time.monotonic()
            if signature != progress_signature:
                progress_signature = signature
                last_progress_at = now
                last_progress_wall = now_iso()
            no_progress_seconds = max(0.0, now - last_progress_at)
            state.record_phase(
                "pipeline_processing",
                "progress",
                {
                    "total": len(assets),
                    "processed": len(processed),
                    "pending": len(pending),
                    "failed": len(failed),
                    "poll_count": poll_count,
                    "status_counts": status_counts,
                    "last_progress_at": last_progress_wall,
                    "no_progress_seconds": round(no_progress_seconds, 1),
                },
            )
            self._persist(str(state.get("run_id")), state)
            if not pending:
                stable_polls += 1
                if stable_polls >= 2:
                    break
            else:
                stable_polls = 0
            if stall_timeout > 0 and no_progress_seconds >= stall_timeout:
                terminal_error = (
                    f"pipeline processing stalled: {no_progress_seconds:.0f}s 内没有资产状态变化"
                    f"（阈值 {stall_timeout}s）；仍有 {len(pending)} 个待处理"
                )
                state.record_phase("pipeline_processing", "status", "stalled")
                state.record_phase("pipeline_processing", "stalled_at", now_iso())
                break
            self._cancel_wait(PIPELINE_POLL_SECONDS)
        failed_assets = [item for item in assets if item.get("status") in PIPELINE_FAILED_STATUSES]
        skipped = [
            item for item in assets if terminal_error and item.get("status") in PIPELINE_PENDING_STATUSES
        ]

        def detail(asset: dict[str, Any], reason: str) -> dict[str, Any]:
            return {
                "sample_id": asset.get("file_name") or asset.get("id") or "unknown",
                "asset_id": asset.get("id"),
                "status": asset.get("status") or "failed",
                "error_type": "pipeline_error",
                "reason": reason,
            }

        error_details = [detail(asset, "资产处理失败") for asset in failed_assets] + [
            detail(asset, terminal_error or "批次处理中止") for asset in skipped
        ]
        phase_result = {
            "total_seconds": round(time.perf_counter() - started, 1),
            "poll_iterations": poll_count,
            "processed_photo_count": len([a for a in assets if a.get("status") == "processed"]),
            "failed_asset_count": len(failed_assets),
            "skipped_asset_count": len(skipped),
            "error": terminal_error or (f"{len(failed_assets)} 个资产处理失败" if failed_assets else None),
            "error_details": error_details,
            "progress": {
                "total": len(assets),
                "processed": len([a for a in assets if a.get("status") == "processed"]),
                "pending": 0
                if terminal_error
                else len([a for a in assets if a.get("status") in PIPELINE_PENDING_STATUSES]),
                "failed": len(failed_assets),
                "skipped": len(skipped),
            },
        }
        # **空 scope 不是「处理成功」。** 1.0 在 ``import_accepted_count == 0`` 时就
        # 早退成 partial（``:2913``），判据是「这一轮一张都没收进来」。2.0 的导入会报
        # ``deduplicated``，所以「一张都没收」有两种成因（导入全覆盖去重 / 什么都没导），
        # 但那两种对**接下来要做的事**是同一件：在空相册上把题全问一遍，产出的是一份
        # 全错的 run.json。所以判据换成「这个 scope 里到底有没有资产」——
        # 比 1.0 的那条更准，且不会把「重复导入同一个相册」误判成失败。
        if not assets:
            phase_result["error"] = "这个 scope 里没有任何资产可处理——导入没有落库"
            phase_result["error_details"] = [
                {
                    "sample_id": "media_batch",
                    "status": "skipped",
                    "error_type": "empty_import",
                    "reason": "导入之后 scope 里没有资产",
                }
            ]
        if error_details or terminal_error or not assets:
            state.phase_partial("pipeline_processing", phase_result)
        else:
            state.phase_done("pipeline_processing", phase_result)

    @staticmethod
    def _stall_timeout() -> int:
        """1.0 ``:2933`` 的 ``PHOTOBENCH_PIPELINE_STALL_TIMEOUT_SECONDS``，含它那条
        「非数字退回默认值」的处理——环境变量被写坏时不该让整轮评测起不来。"""
        try:
            return max(
                0,
                int(
                    os.environ.get(
                        "PHOTOBENCH_PIPELINE_STALL_TIMEOUT_SECONDS", str(DEFAULT_STALL_TIMEOUT_SECONDS)
                    )
                ),
            )
        except ValueError:
            return DEFAULT_STALL_TIMEOUT_SECONDS

    # ---- gpu_metrics / aggregate -------------------------------------------

    def _phase_gpu_metrics(self, state: RunState, request: RunRequest) -> None:
        """1.0 ``:4568``：把采样收成曲线统计。

        1.0 在这里有两条早退（``use_cloud_model`` / ``telemetry_source ==
        "unavailable"``）。2.0 没有云端模型这个概念，只保留第二条：**没有遥测来源时
        报 ``skipped`` 并写明原因**，绝不产出一份 ``samples_count: 0`` 的假聚合——
        那会让读侧把「这台机器没遥测」与「跑了但一个点都没采到」看成一回事。
        """
        if self.telemetry_source == "unavailable" or self._sampler is None:
            state.phase_done(
                "gpu_metrics",
                {
                    "status": "skipped",
                    "source": self.telemetry_source or "unavailable",
                    "reason": "no_local_telemetry_source",
                },
            )
            return
        agg = self._sampler.aggregate()
        agg["partial"] = False
        state.phase_done("gpu_metrics", agg)

    def _phase_aggregate(self, state: RunState, request: RunRequest) -> None:
        """1.0 ``:4580``：算总分分布、召回均值、能力汇总，写进 ``state["summary"]``。

        **2.0 复用读侧的 ``effective_summary``**（``domain.runs.analysis``），而不是
        把 1.0 那段 60 行再抄一遍：两者算的是同一批字段，而读侧那份还要在**老 run**
        上重算一次（``get_run`` 就是这么做的）。两份实现迟早会漂，一处就够了。
        ``summary`` 落盘之后读侧仍然会重算，所以即使这里的版本落后于代码，展示也不会
        错——它只是让 run.json 里有一份「当时算的」快照。
        """
        state.set("summary", {})
        summary = effective_summary(state.data)
        state.set("summary", summary)
        state.phase_done("aggregate", {"summary": summary})

    # ---- qa_eval ------------------------------------------------------------

    def _resolve_qa_concurrency(self, request: RunRequest) -> int:
        """1.0 ``:3330``：默认跟随被服务模型的 ``max_num_seqs``。

        2.0 的这条来源没有了（没有 model_deploy 的模型快照可读，也没有
        ``current_model_snapshot``），所以默认值是 **1**：串行。这不是保守，
        是 2.0 侧唯一有依据的值——1.0 的默认值本身就是从被测模型那儿读来的，
        读不到时 ``except`` 也退回 1（``:3338``）。环境变量 ``PHOTOBENCH_QA_CONCURRENCY``
        的覆盖照搬。
        """
        if request.qa_concurrency:
            return max(1, request.qa_concurrency)
        raw = str(os.environ.get("PHOTOBENCH_QA_CONCURRENCY") or "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                pass
        return 1

    @staticmethod
    def _resolve_judge_concurrency(qa_concurrency: int) -> int:
        """1.0 ``:3347``：judge 是一个独立的池，绝不占 Agent 的槽位。"""
        raw = str(os.environ.get("PHOTOBENCH_JUDGE_CONCURRENCY") or "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                pass
        return min(8, max(1, int(qa_concurrency or 1)))

    def _phase_qa_eval(self, state: RunState, request: RunRequest) -> None:
        """1.0 ``:3103``：Agent 池 + 独立的 Judge 池。**Agent 一完成就立刻排进 Judge**。

        两个池分开是 1.0 的形态（``:3200``），理由写在 ``_resolve_judge_concurrency``
        上：judge 打的是另一个端点（云 API 或本机另一个模型），把它塞进 Agent 的
        并发预算会让「评测吞吐」与「打分吞吐」互相拖住。
        """
        started = time.perf_counter()
        scope_id = str(state.get("scope_id"))
        album_id = str(state.get("album_id"))
        assets = self.client.list_assets(scope_id=scope_id, limit=1000)
        assets_by_name = index_assets_by_name(assets)
        rows = self.albums.qa_rows(album_id, request.qa_set)
        concurrency = self._resolve_qa_concurrency(request)
        judge = self._judge
        judge_concurrency = self._resolve_judge_concurrency(concurrency)
        state.set("qa_concurrency", concurrency)
        state.set("qa_assets", len(assets))
        state.set("judge_concurrency", judge_concurrency if judge is not None else 0)
        state.record_phase("qa_eval", "agent_total", len(rows))
        state.record_phase("qa_eval", "agent_phase_started_at", now_iso())
        if judge is not None:
            state.record_phase("qa_eval", "judge_total", len(rows))

        def record_progress(done: int) -> None:
            state.record_phase(
                "qa_eval",
                "progress",
                {
                    "total": len(rows),
                    "completed": done,
                    "agent_completed": done,
                    "agent_total": len(rows),
                    "qa_concurrency": concurrency,
                },
            )

        record_progress(0)
        items: dict[int, dict[str, Any]] = {}
        judge_futures: dict[concurrent.futures.Future[dict[str, Any]], int] = {}
        judge_done = 0
        judge_pool = (
            concurrent.futures.ThreadPoolExecutor(
                max_workers=judge_concurrency, thread_name_prefix="qa-judge"
            )
            if judge is not None
            else None
        )
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=concurrency, thread_name_prefix="qa-agent"
            ) as executor:
                futures = {
                    executor.submit(self._evaluate_one, row, scope_id, assets_by_name, request): index
                    for index, row in enumerate(rows)
                }
                for future in concurrent.futures.as_completed(tuple(futures)):
                    index = futures[future]
                    try:
                        item = future.result()
                    except Exception as exc:  # noqa: BLE001 —— 1.0 ``:3218``
                        item = {"error": repr(exc), "failed": True}
                    item["_index"] = index
                    # 「没有答案可打分的题」与「还没打分的题」是两件事（1.0 ``:3220``）：
                    # 前者标 skipped，后者标 pending。跳过判分的 item **绝不**再排进
                    # Judge 池——它没有答案可评，送进去只会产出一个假的 0 分。
                    if item.get("failed") or item.get("error"):
                        item["judge_status"] = "skipped"
                    else:
                        item.setdefault("judge_status", "pending")
                        if judge is not None and judge_pool is not None:
                            judge_futures[
                                judge_pool.submit(judge.judge_item, item, rows[index], assets_by_name)
                            ] = index
                    items[index] = item
                    state.record_phase("qa_eval", "agent_completed", len(items))
                    record_progress(len(items))
                    with state.lock:
                        state.data["items"] = [items[i] for i in sorted(items)]
                    state.persist()
                for judge_future in concurrent.futures.as_completed(tuple(judge_futures)):
                    index = judge_futures[judge_future]
                    try:
                        judge_future.result()
                    except Exception as exc:  # noqa: BLE001 —— judge 崩了不能让整轮没有结果
                        items[index]["judge_status"] = "failed"
                        items[index]["judge"] = {"score": None, "reason": f"judge_error: {exc}"}
                    judge_done += 1
                    state.record_phase("qa_eval", "judge_completed", judge_done)
                    with state.lock:
                        state.data["items"] = [items[i] for i in sorted(items)]
                    state.persist()
        finally:
            if judge_pool is not None:
                judge_pool.shutdown(wait=True)
        with state.lock:
            ordered = [items[i] for i in sorted(items)]
            for item in ordered:
                item.pop("_index", None)
            state.data["items"] = ordered
            executed = sum(1 for item in ordered if item.get("execution_status") == "completed")
            state.data["run_valid"] = bool(len(ordered) == len(rows) and executed == len(rows))
            record_progress(len(ordered))
        failed = [
            item for item in state.get("items") or [] if item.get("execution_status") in {"failed", "timeout"}
        ]
        phase_result = {
            "total_seconds": round(time.perf_counter() - started, 1),
            "qa_concurrency": concurrency,
            "failed_count": len(failed),
            "error": f"{len(failed)} 道题执行失败或超时" if failed else None,
            "error_details": [
                {
                    "sample_id": item.get("qa_id") or "QA",
                    "status": item.get("execution_status") or "failed",
                    "error_type": "agent_error",
                    "reason": str(item.get("error") or "QA execution failed"),
                }
                for item in failed
            ],
        }
        if failed:
            state.phase_partial("qa_eval", phase_result)
        else:
            state.phase_done("qa_eval", phase_result)

    def _evaluate_one(
        self,
        row: dict[str, Any],
        scope_id: str,
        assets_by_name: dict[str, list[dict[str, Any]]],
        request: RunRequest,
    ) -> dict[str, Any]:
        """1.0 ``_evaluate_one``（``:3357``）去掉 judge。

        **两段式问答的落点**：1.0 一次 ``POST /api/assistant/turn`` 就拿到 ``turn_id``
        与（可能已经完成的）响应，靠 ``wait_for_assistant_turn``（``:909``）兜底轮询。
        2.0 把建会话拆成独立的一步，所以每题先建一个会话（1.0 每题本地 ``uuid4()``
        一个 conversation_id，语义相同），多轮题共用它。
        """
        started = time.perf_counter()
        conversation = row.get("conversation") or []
        if conversation and not isinstance(conversation, list):
            raise ValueError("conversation 必须是列表")
        messages = [str(turn.get("message") or "").strip() for turn in conversation if isinstance(turn, dict)]
        query = messages[-1] if messages else str(row["question"])
        gt_refs = normalize_media_refs(row, "retrieval")
        answer_refs = normalize_media_refs(row, "answer_evidence") or gt_refs
        item: dict[str, Any] = {
            "qa_id": row.get("qa_id"),
            "question": query,
            "reference_answer": str(row.get("answer") or ""),
            "retrieval_media_refs": gt_refs,
            "retrieval_image_ids": [ref["media_id"] for ref in gt_refs if ref["media_type"] == "image"],
            "retrieval_video_ids": [ref["media_id"] for ref in gt_refs if ref["media_type"] == "video"],
            "answer_evidence_media_refs": answer_refs,
            "answer_evidence_image_ids": [
                ref["media_id"] for ref in answer_refs if ref["media_type"] == "image"
            ],
            "answer_evidence_video_ids": [
                ref["media_id"] for ref in answer_refs if ref["media_type"] == "video"
            ],
            "execution_status": "scheduled",
        }
        for name in PASSTHROUGH_FIELDS:
            if row.get(name) is not None:
                item[name] = row[name]
        try:
            item["execution_status"] = "started"
            conversation_id = str(
                self.client.start_conversation(scope_id=scope_id, title=f"{row.get('qa_id') or 'qa'}").get(
                    "id"
                )
                or ""
            )
            if not conversation_id:
                raise RuntimeError("2.0 建会话没有返回 id")
            turn_records: list[dict[str, Any]] = []
            last_result: dict[str, Any] = {}
            last_progress: list[Any] = []
            for turn_index, message in enumerate(messages or [query]):
                turn_definition = (
                    conversation[turn_index]
                    if turn_index < len(conversation) and isinstance(conversation[turn_index], dict)
                    else {}
                )
                started_turn = self.client.start_turn(
                    conversation_id=conversation_id, message=message, timeout=300
                )
                turn_id = str(started_turn.get("turn_id") or "")
                if not turn_id:
                    raise RuntimeError(f"2.0 发起回合没有返回 turn_id：{started_turn}")
                polled = self._await_turn(turn_id, request)
                last_result = polled.get("result") or {}
                last_progress = polled.get("public_progress") or []
                turn_records.append(
                    {
                        "index": turn_index,
                        "conversation_id": conversation_id,
                        "context_turn_count": turn_index,
                        "message": message,
                        "turn_id": turn_id,
                        "status": polled.get("status"),
                        "answer": str(last_result.get("answer") or ""),
                        "attachments": last_result.get("attachments") or [],
                        "requirements": last_result.get("requirements") or [],
                        "termination_reason": last_result.get("termination_reason") or "",
                        "error": polled.get("error") or "",
                        # **判分要用它**：``JudgeRunner.judge_item`` 从**回合**上读
                        # ``expected_action``（1.0 的 runtime_turns 带这个字段），
                        # 读不到就标 ``not_labeled`` 并且**一次判分都不发**——
                        # 一轮评测于是「跑完了、一个分数都没有」。多轮题按题面每一轮
                        # 的定义取，单轮题退回行级。
                        "expected_action": turn_definition.get("expected_action")
                        or row.get("expected_action"),
                        "reference_answer": str(turn_definition.get("reference_answer") or ""),
                        "agent_status": polled.get("status"),
                        # 轨迹**逐条原样**落盘（2.0 的 ProgressEventPayload），
                        # 不做投影：它是 2.0 关于「这一轮怎么走的」的全部记录。
                        "trace": last_progress,
                        "trace_summary": summarize_progress(last_progress),
                    }
                )
            selected = selected_asset_ids(last_result)
            predicted_media = resolve_predicted_media(selected, assets_by_name)
            gt_media = resolve_gt_media(gt_refs, assets_by_name, predicted_media)
            metrics = modality_metrics(gt_refs, predicted_media)
            selected_keys = {
                (str(entry.get("media_type")), str(entry.get("file_name"))) for entry in predicted_media
            }
            item.update(
                {
                    "answer": str(last_result.get("answer") or ""),
                    "conversation_id": conversation_id,
                    "conversation_turn_count": len(turn_records) if conversation else 1,
                    "conversation_context_mode": "shared_conversation_id" if conversation else "single_turn",
                    "runtime_turns": turn_records,
                    "turn_status": last_result.get("status"),
                    "termination_reason": last_result.get("termination_reason") or "",
                    "requirements": last_result.get("requirements") or [],
                    "trace_summary": summarize_progress(last_progress),
                    "selected_asset_ids": selected,
                    "predicted_media": predicted_media,
                    "predicted_file_names": sorted(str(entry["file_name"]) for entry in predicted_media),
                    "gt_media": gt_media,
                    "gt_images": [e for e in gt_media if e["media_type"] == "image"],
                    "delivery_matched_file_names": sorted(
                        str(entry["file_name"])
                        for entry in gt_media
                        if (str(entry["media_type"]), str(entry["file_name"])) in selected_keys
                    ),
                    "media_retrieval_counts": metrics["media"],
                    "image_retrieval_counts": metrics["image"],
                    "video_retrieval_counts": metrics["video"],
                    "media_retrieval_recall": metrics["media"]["recall"],
                    "media_retrieval_precision": metrics["media"]["precision"],
                    "media_retrieval_f1": metrics["media"]["f1"],
                    "image_retrieval_recall": metrics["image"]["recall"],
                    "image_retrieval_precision": metrics["image"]["precision"],
                    "image_retrieval_f1": metrics["image"]["f1"],
                    "video_retrieval_recall": metrics["video"]["recall"],
                    "video_retrieval_precision": metrics["video"]["precision"],
                    "video_retrieval_f1": metrics["video"]["f1"],
                    # 兼容别名：1.0 未分模态的 run 用这三个键（``:3547``）
                    "retrieval_recall": metrics["media"]["recall"],
                    "retrieval_precision": metrics["media"]["precision"],
                    "retrieval_f1": metrics["media"]["f1"],
                    "wall_clock_ms": round((time.perf_counter() - started) * 1000, 1),
                    # judge 三个占位与 1.0 同一位置同一文案（``:3555-3557``）。
                    # 本切片不打分，所以它们**必须**是 pending 而不是 0——
                    # 0 是一个合法的分数，会污染读侧算出来的分布。
                    "judge": {"score": None, "reason": "pending_judge"},
                    "task_judge": {"actual_action": None, "correct": None, "reason": "pending_judge"},
                    "task_judges": [],
                    "evidence_judge": {"score": None, "reason": "pending_judge"},
                    "judge_ms": None,
                    "judge_status": "pending",
                }
            )
            item["attribution"] = derive_attribution(item)
            item["execution_status"] = "completed"
        except Exception as exc:  # noqa: BLE001 —— 1.0 ``:3571``
            message = str(exc).lower()
            # **判据比 1.0 多一条 ``isinstance``**：1.0 ``:3573`` 只按文案里的
            # ``timed out`` / ``timeout`` 判，而它自己的 ``TimeoutError`` 文案是
            # "did not complete within Ns"——**里面没有那个词**，于是每次超时都被
            # 记成 ``failed``。这是照搬会连带搬过来的缺陷，不是风格差异。
            timed_out = isinstance(exc, TimeoutError) or "timed out" in message or "timeout" in message
            item["execution_status"] = "timeout" if timed_out else "failed"
            item.update(
                {
                    "error": str(exc),
                    "retrieval_recall": 0,
                    "retrieval_precision": None,
                    "retrieval_f1": None,
                    "media_retrieval_recall": 0 if gt_refs else None,
                    "media_retrieval_precision": None,
                    "media_retrieval_f1": None,
                    "judge": {"score": None, "reason": "error"},
                    "wall_clock_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )
        return item

    def _await_turn(self, turn_id: str, request: RunRequest) -> dict[str, Any]:
        """1.0 ``wait_for_assistant_turn``（``:909``）的 2.0 版。

        1.0 的判据是状态字符串落在 ``{"complete","completed","done","success"}``；
        2.0 换了枚举（``TurnStatus.is_terminal = not in {pending, running}``）——**用
        「不是 pending/running」而不是列举成功值**，因为 2.0 把失败也做成了终态
        （``failed`` / ``blocked`` / ``cancelled`` / ``partial``），列举成功值会把
        每一个业务失败都变成一次轮询超时（1.0 那个写法的后果就是「前端拿到的每一个
        失败都是一个成功的帧」，2.0 的 ``TurnStatus`` 注释点名批评过）。

        非终态之外**不抛**：``failed`` 时 ``result`` 可能为 None，状态与错误原文
        由调用方写进 item——1.0 在 ``failed`` 时抛 ``RuntimeError``，那会把
        「模型答错/被 guard 拦下」与「网络断了」压成同一个 ``execution_status``。
        """
        deadline = time.monotonic() + request.turn_timeout
        polled = self.client.get_turn(turn_id=turn_id)
        while str(polled.get("status") or "") in TURN_PENDING_STATUSES:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"assistant turn {turn_id} did not complete within {request.turn_timeout}s"
                )
            self._cancel_wait(0.5)
            polled = self.client.get_turn(turn_id=turn_id)
        return polled


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    """1.0 ``model_probe``（``:826-836``）里那段解析，抽成纯函数。

    ``[DONE]`` 与坏 JSON 都返回 ``None``（1.0 是 ``break`` 与 ``continue`` 两种出口，
    对调用方等价：两者都不产出 chunk）。
    """
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    data = stripped[5:].strip()
    if data == "[DONE]":
        return None
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        return None
    return chunk if isinstance(chunk, dict) else None


__all__ = [
    "PASSTHROUGH_FIELDS",
    "PIPELINE_FAILED_STATUSES",
    "PIPELINE_PENDING_STATUSES",
    "JudgeSettings",
    "RunExecution",
    "RunRequest",
]
