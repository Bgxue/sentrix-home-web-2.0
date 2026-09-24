"""CUDA 静默退化那道防线：请求了 CUDA 而 session 实际落在 CPU 上时必须**抛错拒启**。

**为什么单独一个文件、而且放在 unit 层。** 这道防线要防的失败模式是「不报错」——
onnxruntime 在 EP 加载失败时既不抛错也不换 provider 名，只是静静地在 CPU 上跑（第 56 条）；
真实仓库的 venv 被依赖覆盖成 CPU 版那次，外表是 ``make ci`` 从 21 分钟变成 55 分钟的
「卡住」（第 111 条）。所以真正的门禁**不是**「守卫函数会抛」，是**三个 session 建立点都
真的接了它，而且抛出来的东西没被上层的 ``except Exception`` 吞掉**——下面第二半用假
session 逐个骑在装配点上，不碰权重、不碰 GPU。

**反向那一半是主体。** 「请求 CUDA、只拿到 CPU」必须红；同时正向两条也要有（真拿到 CUDA
时不抛、显式纯 CPU 部署时不抛），否则一个「永远抛」的实现同样能全绿。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime
import pytest

from sentrix.infrastructure.models.face_adapter import (
    MODELS_SUBDIR,
    RECOGNITION_WEIGHTS,
    FaceAdapter,
)
from sentrix.infrastructure.models.face_detector import RetinaFaceTiledDetector
from sentrix.infrastructure.models.onnx_runtime import (
    CPU_EXECUTION_PROVIDER,
    CUDA_EXECUTION_PROVIDER,
    FACE_PROVIDERS_ENV,
    RETINAFACE_PROVIDERS_ENV,
    CudaProviderMissingError,
    require_requested_providers,
)

CPU_ONLY = [CPU_EXECUTION_PROVIDER]
CUDA_AND_CPU = [CUDA_EXECUTION_PROVIDER, CPU_EXECUTION_PROVIDER]


class FakeSession:
    """一个**session 该被读到的全部**：``get_providers()`` 与 ``get_inputs()``。

    ``get_providers`` 是唯一能被证伪的判据——``get_available_providers()`` 在退化时给出
    相反的答案，所以假件里**故意没有**那个属性：调用方一旦改用它就 AttributeError。
    """

    def __init__(self, providers: list[str]) -> None:
        self._providers = list(providers)

    def get_providers(self) -> list[str]:
        return list(self._providers)

    def get_inputs(self) -> list[Any]:
        return [type("Input", (), {"name": "input.1"})()]


def fake_session_factory(providers: list[str]):  # noqa: ANN201 - 测试内的工厂
    return lambda *args, **kwargs: FakeSession(providers)


# ---- 判据本身 --------------------------------------------------------------------


def test_the_guard_passes_when_the_session_really_landed_on_cuda(monkeypatch: pytest.MonkeyPatch):
    """正向：请求 CUDA 且真的拿到 CUDA → 不抛。少了这条，「一律抛」也能全绿。"""
    monkeypatch.delenv(FACE_PROVIDERS_ENV, raising=False)

    actual = require_requested_providers(FakeSession(CUDA_AND_CPU), FACE_PROVIDERS_ENV, label="检测")

    assert actual == CUDA_AND_CPU


def test_the_guard_refuses_when_cuda_was_requested_and_the_session_landed_on_cpu(
    monkeypatch: pytest.MonkeyPatch,
):
    """反向：默认链请求了 CUDA 而实际只有 CPU → 抛。"""
    monkeypatch.delenv(FACE_PROVIDERS_ENV, raising=False)

    with pytest.raises(CudaProviderMissingError) as raised:
        require_requested_providers(FakeSession(CPU_ONLY), FACE_PROVIDERS_ENV, label="RetinaFace 检测")

    assert "RetinaFace 检测" in str(raised.value)
    assert str(CPU_ONLY) in str(raised.value)


def test_the_error_says_how_to_fix_it(monkeypatch: pytest.MonkeyPatch):
    """报错必须**可执行**：只说「CUDA 没生效」的报错，下一个人还得再查一遍才动得了手。"""
    monkeypatch.delenv(RETINAFACE_PROVIDERS_ENV, raising=False)

    with pytest.raises(CudaProviderMissingError) as raised:
        require_requested_providers(FakeSession(CPU_ONLY), RETINAFACE_PROVIDERS_ENV, label="检测")
    message = str(raised.value)
    version = onnxruntime.__version__

    assert f"pip install --force-reinstall --no-deps onnxruntime-gpu=={version}" in message
    # 修复命令里的版本号与 pyproject 钉的必须是同一个——两处漂开，命令就装回一个错的版本。
    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert f"onnxruntime-gpu=={version}" in pyproject
    # 逃生阀要写清楚**怎么写**，否则读到报错的人只知道「有办法」而不知道办法。
    assert f"{RETINAFACE_PROVIDERS_ENV}={CPU_EXECUTION_PROVIDER}" in message


def test_the_escape_hatch_is_the_explicit_cpu_chain(monkeypatch: pytest.MonkeyPatch):
    """**唯一**的口子：显式声明纯 CPU 部署。

    那时压根没有「请求了 CUDA」这回事，防线不参与——所以纯 CPU 的部署形态仍然是合法的，
    但必须由调用方写出来，不能由 session 自己悄悄退化出来。
    """
    monkeypatch.setenv(FACE_PROVIDERS_ENV, CPU_EXECUTION_PROVIDER)

    assert require_requested_providers(FakeSession(CPU_ONLY), FACE_PROVIDERS_ENV, label="检测") == CPU_ONLY


def test_a_cpu_only_chain_still_refuses_when_cuda_is_requested_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
):
    """两个变量各管一条链路，不互相顶替。

    RetinaFace 与 buffalo_l 的预算、provider 链都是各算各的（1.0 同此），所以把一边设成
    纯 CPU 不能顺带放过另一边——放过就会得到「检测在 GPU、识别在 CPU」这种一半退化。
    """
    monkeypatch.setenv(FACE_PROVIDERS_ENV, CPU_EXECUTION_PROVIDER)
    monkeypatch.delenv(RETINAFACE_PROVIDERS_ENV, raising=False)

    with pytest.raises(CudaProviderMissingError):
        require_requested_providers(FakeSession(CPU_ONLY), RETINAFACE_PROVIDERS_ENV, label="检测")


# ---- 装配点 1：RetinaFace 检测 session --------------------------------------------


def test_the_detector_refuses_to_start_when_the_session_degrades_to_cpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """守卫接在 ``_load`` 上，而且**穿得出来**：``self._session`` 保持 None。"""
    weights = tmp_path / "retinaface_r50.onnx"
    weights.write_bytes(b"stub")
    monkeypatch.setenv(RETINAFACE_PROVIDERS_ENV, f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}")
    monkeypatch.setattr(onnxruntime, "InferenceSession", fake_session_factory(CPU_ONLY))
    detector = RetinaFaceTiledDetector(model_path=str(weights))

    with pytest.raises(CudaProviderMissingError):
        detector._load()

    # 半建状态没留下：留下的话下一次 ``_load`` 会在 ``self._session is not None`` 处返回，
    # 硬报错只响一次，之后整条链路静默降级。
    assert detector._session is None

    with pytest.raises(CudaProviderMissingError):
        detector._load()


def test_the_detector_still_loads_when_cuda_is_really_there(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """正向：同一个装配点，真拿到 CUDA 时照常建起来并记下输入名。"""
    weights = tmp_path / "retinaface_r50.onnx"
    weights.write_bytes(b"stub")
    monkeypatch.setenv(RETINAFACE_PROVIDERS_ENV, f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}")
    monkeypatch.setattr(onnxruntime, "InferenceSession", fake_session_factory(CUDA_AND_CPU))
    detector = RetinaFaceTiledDetector(model_path=str(weights))

    detector._load()

    assert detector._session is not None
    assert detector._input_name == "input.1"


def test_the_detector_accepts_an_explicit_cpu_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """逃生阀在装配点上也生效——否则「显式声明纯 CPU」只是一句空话。"""
    weights = tmp_path / "retinaface_r50.onnx"
    weights.write_bytes(b"stub")
    monkeypatch.setenv(RETINAFACE_PROVIDERS_ENV, CPU_EXECUTION_PROVIDER)
    monkeypatch.setattr(onnxruntime, "InferenceSession", fake_session_factory(CPU_ONLY))

    detector = RetinaFaceTiledDetector(model_path=str(weights))
    detector._load()

    assert detector._session is not None


# ---- 装配点 2 / 3：buffalo_l 的两个 session ---------------------------------------


class FakeModule:
    def __init__(self, providers: list[str]) -> None:
        self.session = FakeSession(providers)


class FakeFaceAnalysis:
    """insightface ``FaceAnalysis`` 的最小复刻：``models`` 是 taskname → 模块。

    **``FaceAnalysis`` 自己确实没有 ``get_providers``**（provider 是传给每个模块各自的
    ``InferenceSession`` 的），所以这道防线只能逐模块读；假件照这个形状造，才骑得住「读这个
    对象自己的属性」那种写法——那样写在假件上会 AttributeError，在真件上会永远为真。
    """

    landed_on: list[str] = CPU_ONLY

    def __init__(self, **kwargs: Any) -> None:
        self.models = {name: FakeModule(list(self.landed_on)) for name in ("detection", "recognition")}
        self.prepared: Any = None

    def prepare(self, ctx_id: int, det_size: Any = None) -> None:
        self.prepared = (ctx_id, det_size)


def build_model_root(tmp_path: Path) -> Path:
    root = tmp_path / "insightface"
    (root / MODELS_SUBDIR / "buffalo_l").mkdir(parents=True)
    return root


def test_the_buffalo_analysis_refuses_to_start_when_a_module_session_degrades(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """守卫接在 ``_build_analysis`` 上：SCRFD 与 ArcFace 两个模块会话都要读到。"""
    import insightface.app

    monkeypatch.setenv(FACE_PROVIDERS_ENV, f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}")
    monkeypatch.setattr(insightface.app, "FaceAnalysis", FakeFaceAnalysis)
    monkeypatch.setattr(FakeFaceAnalysis, "landed_on", CPU_ONLY)
    adapter = FaceAdapter(model_root=str(build_model_root(tmp_path)))

    with pytest.raises(CudaProviderMissingError) as raised:
        adapter._build_analysis()

    assert "buffalo_l detection" in str(raised.value)


def test_the_buffalo_analysis_loads_when_both_modules_really_land_on_cuda(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import insightface.app

    monkeypatch.setenv(FACE_PROVIDERS_ENV, f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}")
    monkeypatch.setattr(insightface.app, "FaceAnalysis", FakeFaceAnalysis)
    monkeypatch.setattr(FakeFaceAnalysis, "landed_on", CUDA_AND_CPU)
    adapter = FaceAdapter(model_root=str(build_model_root(tmp_path)))

    analysis = adapter._build_analysis()

    assert set(analysis.models) == {"detection", "recognition"}
    assert analysis.prepared[0] == 0  # ctx_id=0：请求了 CUDA 就该在 GPU 上准备


def test_the_standalone_recognition_session_refuses_instead_of_returning_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """**这一条最要紧**：那个 ``return None`` 是「权重缺失」的合法降级。

    退化被折进 ``None`` 的话，上层看到的是「这条兜底路没有向量」，与真实原因无关——
    正是第 111 条那种「报错指向错的地方」。
    """
    root = build_model_root(tmp_path)
    (root / MODELS_SUBDIR / "buffalo_l" / RECOGNITION_WEIGHTS).write_bytes(b"stub")
    monkeypatch.setenv(FACE_PROVIDERS_ENV, f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}")
    monkeypatch.setattr(onnxruntime, "InferenceSession", fake_session_factory(CPU_ONLY))
    adapter = FaceAdapter(model_root=str(root))

    with pytest.raises(CudaProviderMissingError):
        adapter._load_buffalo_recognition()


def test_the_standalone_recognition_session_still_falls_back_when_weights_are_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """正向的反面：**权重缺失**仍然返回 ``None``（那是合法降级），别把两件事一起改掉。"""
    monkeypatch.setenv(FACE_PROVIDERS_ENV, f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}")
    adapter = FaceAdapter(model_root=str(build_model_root(tmp_path)))

    assert adapter._load_buffalo_recognition() is None


def test_a_degraded_chain_keeps_refusing_on_every_later_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """硬报错必须是**黏的**，不能第一次响完就悄悄降级成「一张脸都没有」。

    ``_ensure_loaded`` 的守卫写的是 ``self._app is not None``。识别 session 若排在
    ``_app`` 之后建，一次退化抛错之后 ``_app`` 已经非空，第二次调用直接返回、
    ``_recognition_session`` 永远是 ``None``——而 ``_uncertain`` 对
    ``_recognition_session is None`` 的处置正是**返回空表**，于是整条链路静默变成
    「检不出任何人脸」，与没加防线时一模一样。
    """
    root = build_model_root(tmp_path)
    (root / MODELS_SUBDIR / "buffalo_l" / RECOGNITION_WEIGHTS).write_bytes(b"stub")
    monkeypatch.setenv(FACE_PROVIDERS_ENV, f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}")
    monkeypatch.setattr(onnxruntime, "InferenceSession", fake_session_factory(CPU_ONLY))
    adapter = FaceAdapter(model_root=str(root))

    for _ in range(2):
        with pytest.raises(CudaProviderMissingError):
            adapter._ensure_loaded()

    assert adapter._app is None
    assert adapter._recognition_session is None


def test_the_adapter_does_not_swallow_the_guard_in_its_broad_except(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """``detect`` 那个 ``except Exception`` 是给「人脸是可选能力」用的，**不能连它也吞掉**。

    吞掉的后果与没加防线**一模一样**：``detect`` 返回空表，调用方看到的是「这张图里没有人脸」，
    而真实原因是依赖装错了（第 111 条）。所以这一条单独骑在**最外层**的出口上：退化时
    ``detect`` 必须抛，不是返回 ``[]``。
    """
    root = build_model_root(tmp_path)
    (root / MODELS_SUBDIR / "buffalo_l" / RECOGNITION_WEIGHTS).write_bytes(b"stub")
    weights = tmp_path / "retinaface_r50.onnx"
    weights.write_bytes(b"stub")
    monkeypatch.setenv(FACE_PROVIDERS_ENV, f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}")
    monkeypatch.setenv(RETINAFACE_PROVIDERS_ENV, f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}")
    monkeypatch.setattr(onnxruntime, "InferenceSession", fake_session_factory(CPU_ONLY))
    adapter = FaceAdapter(RetinaFaceTiledDetector(model_path=str(weights)), model_root=str(root))
    monkeypatch.setattr(adapter, "_read_image", lambda path: np.zeros((64, 64, 3), dtype="uint8"))

    with pytest.raises(CudaProviderMissingError):
        adapter.detect(tmp_path / "x.jpg")

    # 也不许把它记成 ``error`` 之后照样往下走——``error`` 是给人看日志的，不是出口。
    assert adapter.error is None
