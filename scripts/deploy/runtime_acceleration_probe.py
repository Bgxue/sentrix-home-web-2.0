#!/usr/bin/env python3
"""Bounded, offline acceleration evidence using the selected interpreter and weights.

No installation, downloads or service mutation. Run in the target venv, not via a
bare python from PATH. A provider name in available_providers is only inventory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import site
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def distribution(name: str) -> dict[str, Any] | None:
    try:
        package = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    origin = package.read_text("direct_url.json")
    parsed_origin = json.loads(origin) if origin else None
    if parsed_origin and parsed_origin.get("url"):
        address = urlsplit(parsed_origin["url"])
        parsed_origin["url"] = urlunsplit(
            (address.scheme, address.netloc.rsplit("@", 1)[-1], address.path, "", "")
        )
    record = package.read_text("RECORD")
    return {
        "record_sha256": hashlib.sha256(record.encode()).hexdigest() if record else None,
        "version": package.version,
        "location": str(package.locate_file("")),
        "origin": parsed_origin,
    }


def inventory() -> dict[str, Any]:
    packages = {
        name: distribution(name) for name in ("torch", "torchvision", "onnxruntime", "onnxruntime-gpu")
    }
    report: dict[str, Any] = {
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "user_site_enabled": site.ENABLE_USER_SITE,
        "user_site": site.getusersitepackages(),
        "ld_library_path": os.environ.get("LD_LIBRARY_PATH", ""),
        "namespace_conflict": bool(packages["onnxruntime"] and packages["onnxruntime-gpu"]),
    }
    for name in ("torch", "onnxruntime"):
        try:
            module = __import__(name)
            report[name] = {"import_path": module.__file__, "version": module.__version__}
            report[name]["user_site_import"] = str(module.__file__).startswith(
                site.getusersitepackages() + os.sep
            )
            if name == "torch":
                report[name].update(
                    cuda_build=module.version.cuda,
                    cuda_available=module.cuda.is_available(),
                    arch_flags=module._C._cuda_getArchFlags() if module.version.cuda else None,
                )
            else:
                report[name]["available_providers"] = module.get_available_providers()
                library = Path(str(module.__file__)).parent / "capi/libonnxruntime_providers_cuda.so"
                if library.exists() and sys.platform == "linux":
                    loaded = subprocess.run(
                        ["ldd", str(library)], capture_output=True, text=True, timeout=15, check=False
                    )
                    report[name]["dynamic_libraries"] = loaded.stdout.splitlines()
        except Exception as exc:  # noqa: BLE001 — import failures are diagnostic evidence
            report[name] = {"status": "unavailable", "error": str(exc), "error_type": type(exc).__name__}
    return report


def torch_operator(device: str) -> dict[str, Any]:
    import torch

    # Matrix multiplication and convolution force actual device kernels, followed by synchronization.
    matrix = torch.ones((64, 64), device=device)
    result = matrix @ matrix
    image = torch.ones((1, 3, 32, 32), device=device)
    kernel = torch.ones((8, 3, 3, 3), device=device)
    conv = torch.nn.functional.conv2d(image, kernel)
    if device == "cuda":
        torch.cuda.synchronize()
    elif device == "mps":
        torch.mps.synchronize()
    assert result.device.type == device and conv.device.type == device
    assert float(result[0, 0].cpu()) == 64 and float(conv[0, 0, 0, 0].cpu()) == 27
    return {"device": device, "matmul": "passed", "convolution": "passed", "import_path": torch.__file__}


def onnx_inference(path: Path, provider: str) -> dict[str, Any]:
    import numpy as np
    import onnxruntime as ort

    if not path.is_file():
        raise FileNotFoundError(path)
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    options.enable_profiling = True
    with tempfile.TemporaryDirectory(prefix="sentrix-ort-probe-") as directory:
        options.profile_file_prefix = str(Path(directory) / "profile")
        session = ort.InferenceSession(str(path), sess_options=options, providers=[provider])
        if provider not in session.get_providers():
            raise RuntimeError(f"Requested {provider}, got {session.get_providers()}")
        feeds = {}
        for spec in session.get_inputs():
            if spec.type not in {"tensor(float)", "tensor(float16)"}:
                raise ValueError(f"Unsupported probe input: {spec.name} {spec.type}")
            shape = [
                dim if isinstance(dim, int) and dim > 0 else (1 if i == 0 else 3 if i == 1 else 640)
                for i, dim in enumerate(spec.shape)
            ]
            feeds[spec.name] = np.zeros(
                shape, dtype=np.float16 if spec.type == "tensor(float16)" else np.float32
            )
        outputs = session.run(None, feeds)
        if not outputs or any(not np.isfinite(value).all() for value in outputs):
            raise RuntimeError("Nonfinite or absent inference outputs")
        profile = json.loads(Path(session.end_profiling()).read_text())
        executed = sorted(
            {
                event.get("args", {}).get("provider")
                for event in profile
                if event.get("args", {}).get("provider")
            }
        )
        if provider not in executed:
            raise RuntimeError(f"No kernel executed on {provider}; observed {executed}")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "requested_provider": provider,
        "executed_providers": executed,
        "outputs": [list(value.shape) for value in outputs],
        "import_path": ort.__file__,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", type=Path)
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--torch-device", choices=("cpu", "cuda", "mps"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.worker:
        try:
            process = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"],
                capture_output=True,
                text=True,
                timeout=args.timeout,
                check=False,
            )
            if process.stdout:
                print(process.stdout.strip())
            if process.stderr:
                print(process.stderr[-8000:], file=sys.stderr)
            raise SystemExit(process.returncode)
        except subprocess.TimeoutExpired:
            print(json.dumps({"status": "failed", "reason": "probe_timeout", "timeout": args.timeout}))
            raise SystemExit(1) from None
    result: dict[str, Any] = {"probe_version": 1, "inventory": inventory()}
    try:
        if args.torch_device:
            result["torch_operator"] = torch_operator(args.torch_device)
        if args.onnx:
            result["onnx_inference"] = onnx_inference(args.onnx, args.provider)
        result["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 — nonzero exit with original failure evidence
        result.update(status="failed", error=str(exc), error_type=type(exc).__name__)
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
