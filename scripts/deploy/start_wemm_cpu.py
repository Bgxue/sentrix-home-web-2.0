#!/usr/bin/env python3
"""Run an existing WeMM sidecar with the Transformers reference CPU kernels.

The checkpoint, processor and HTTP protocol are unchanged. Transformers 5.2 may
select the installed CUDA-only FLA norm even when the requested device is CPU.
Select its built-in PyTorch fallback before loading the model. This is an
operator entry point, not a product-managed model lifecycle.
"""
from __future__ import annotations

import argparse
import importlib
import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8102)
    args = parser.parse_args()
    os.environ.update(SENTRIX_WEMM_DEVICE="cpu", SENTRIX_WEMM_MODEL_PATH=str(args.model))
    modeling = importlib.import_module("transformers.models.qwen3_5.modeling_qwen3_5")
    for name in ("FusedRMSNormGated", "chunk_gated_delta_rule", "fused_recurrent_gated_delta_rule"):
        if not hasattr(modeling, name):
            raise RuntimeError(f"This Transformers version does not provide the reference CPU path: {name}")
        setattr(modeling, name, None)
    sys.argv = [str(args.sidecar), "--host", "127.0.0.1", "--port", str(args.port)]
    runpy.run_path(str(args.sidecar), run_name="__main__")


if __name__ == "__main__":
    main()
