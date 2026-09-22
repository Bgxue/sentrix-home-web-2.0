"""Chinese-CLIP 视觉嵌入（进程内 GPU 推理）。

对应 1.0 ``backend/embeddings/chinese_clip_visual.py``。选型理由记在那里：标准
ViT-B-32 的文本-图像对齐对中文基本是随机的（AUC ~0.51），Chinese-CLIP 的文本
塔是中文 BERT，所以「毛绒睡衣」能和标成「连帽衫」的图对上。模型身份见
``domain/retrieval/scheme.py``——本文件不读任何 env 决定用哪个模型。

1.0 的两个失败模式在这里被改成显式行为：

1. ``embed_image`` 曾经静默 ``return []``，调用方拿到空向量照样 upsert，于是
   numpy ABI 冲突期间 223 张图没有任何视觉向量，直到手工清点才发现。现在失败
   仍然返回空（端口约定），但**必然**留下一条 ``log.exception``。
2. 没有 ``model.eval()`` 时 dropout 会让同一张图两次推理得到不同向量——测试
   ``test_same_image_embeds_identically`` 钉住这一点。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from sentrix.domain.retrieval import scheme

log = logging.getLogger("sentrix.chinese_clip")

DEFAULT_CHECKPOINT = Path.home() / ".cache" / "clip" / "clip_cn_vit-l-14.pt"
MODEL_NAME = "ViT-L-14"


class ChineseClipVisualEmbedder:
    """实现 ``domain.retrieval.ports.VisualEmbedder``。"""

    def __init__(self, checkpoint: Path | None = None, device: str | None = None) -> None:
        self.checkpoint = Path(checkpoint) if checkpoint else DEFAULT_CHECKPOINT
        self._device = device
        self._model: object | None = None
        self._preprocess: object | None = None
        self._error = ""
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return scheme.IMAGE_MODEL

    @property
    def dimension(self) -> int:
        return scheme.VISUAL_DIMENSION

    @property
    def device(self) -> str:
        """没有 CUDA 就用 CPU：2.0 必须能在没有 GPU 的机器上跑起来（只是慢）。"""
        if self._device is None:
            import torch

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        return self._device

    def _load(self) -> object | None:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            if not self.checkpoint.is_file():
                self._error = f"checkpoint missing: {self.checkpoint}"
                log.error("chinese-clip checkpoint 不存在: %s", self.checkpoint)
                return None
            try:
                from cn_clip.clip import load_from_name

                # cn_clip 把 model_name 解析到 download_root 下，指到权重所在目录
                # 就不会去 HuggingFace 拉一份新的。
                model, preprocess = load_from_name(
                    MODEL_NAME, device=self.device, download_root=str(self.checkpoint.parent)
                )
                model.eval()
                # 存进 self 再返回：cn_clip 没有 py.typed，load_from_name 的返回值是
                # Any，直接 return 会被 mypy --strict 的 no-any-return 拦下。
                self._model = model
                self._preprocess = preprocess
                self._error = ""
                return self._model
            except Exception as error:  # noqa: BLE001 - 加载失败必须留痕，不能静默
                self._error = f"{type(error).__name__}: {error}"
                log.exception("chinese-clip 加载失败: %s", self.checkpoint)
                return None

    @property
    def available(self) -> bool:
        return self._load() is not None

    @property
    def load_error(self) -> str:
        return self._error

    def embed_query(self, text: str) -> list[float]:
        phrase = str(text or "").strip()
        if not phrase:
            return []
        model = self._load()
        if model is None:
            return []
        try:
            import torch
            from cn_clip.clip import tokenize

            with self._inference_lock, torch.no_grad():
                tokens = tokenize([phrase]).to(self.device)
                vector = model.encode_text(tokens)  # type: ignore[attr-defined]
            return [float(value) for value in vector[0].cpu().tolist()]
        except Exception:
            log.exception("chinese-clip embed_query 失败: text=%r", phrase[:80])
            return []

    def embed_image(self, path: Path) -> list[float]:
        if not Path(path).is_file():
            log.error("chinese-clip embed_image 收到不存在的文件: %s", path)
            return []
        model = self._load()
        if model is None or self._preprocess is None:
            return []
        try:
            import torch
            from PIL import Image

            from sentrix.infrastructure.media.image_io import ensure_heif_support

            ensure_heif_support()
            with Image.open(path) as handle:
                image = handle.convert("RGB")
                batch = self._preprocess(image).unsqueeze(0).to(self.device)  # type: ignore[operator]
            with self._inference_lock, torch.no_grad():
                vector = model.encode_image(batch)  # type: ignore[attr-defined]
            return [float(value) for value in vector[0].cpu().tolist()]
        except Exception:
            # 这条日志是 1.0 缺的那一条：没有它，「这张图没有向量」在整条链路上不可见。
            log.exception("chinese-clip embed_image 失败: path=%s", path)
            return []
