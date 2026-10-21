#!/usr/bin/env python3
"""Build and activate immutable releases; runtime paths remain outside the source tree.

package runs locally after CI on a clean committed revision. activate runs on each
host with its existing venv. It only replaces listeners on 8291/8872. The previous
release and all runtime data are retained. No model process is managed here.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import signal
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.request import ProxyHandler, build_opener


def read_json(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text())
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def package(root: Path, destination: Path) -> None:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip():
        raise RuntimeError("Packaging requires a clean committed working tree")
    destination.mkdir(parents=True, exist_ok=True)
    staging = destination / revision
    staging.mkdir()
    archive = destination / f"{revision}.source.tar"
    subprocess.run(["git", "archive", "--output", str(archive), revision], cwd=root, check=True)
    with tarfile.open(archive) as handle:
        handle.extractall(staging, filter="data")
    for relative in ("apps/web/dist", "apps/photobench/web/dist"):
        if not (root / relative / "index.html").is_file():
            raise RuntimeError(f"Missing built UI: {relative}")
        shutil.copytree(root / relative, staging / relative)
    write_json(staging / "build-info.json", {"revision": revision, "metrics_contract_version": 2})
    manifest = {
        str(path.relative_to(staging)): digest(path) for path in sorted(staging.rglob("*")) if path.is_file()
    }
    write_json(staging / "release-manifest.json", {"revision": revision, "sha256": manifest})
    output = destination / f"{revision}.tar.gz"
    with tarfile.open(output, "w:gz") as handle:
        handle.add(staging, arcname=revision)
    write_json(destination / f"{revision}.sha256.json", {output.name: digest(output)})
    print(json.dumps({"revision": revision, "archive": str(output), "sha256": digest(output)}))


def verify(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "release-manifest.json")
    for name, expected in manifest["sha256"].items():
        if digest(root / name) != expected:
            raise RuntimeError(f"Release hash mismatch: {name}")
    if read_json(root / "build-info.json")["revision"] != manifest["revision"]:
        raise RuntimeError("Build revision mismatch")
    return manifest


def listener(port: int) -> int | None:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    pids = {int(line) for line in result.stdout.splitlines() if line.isdecimal()}
    if len(pids) > 1:
        raise RuntimeError(f"Multiple listeners on {port}: {sorted(pids)}")
    return next(iter(pids), None)


def process_environment(pid: int) -> dict[str, str]:
    path = Path(f"/proc/{pid}/environ")
    if path.is_file():
        return dict(part.split("=", 1) for part in path.read_bytes().decode().split("\0") if "=" in part)
    psutil = importlib.import_module("psutil")
    result: dict[str, str] = psutil.Process(pid).environ()
    return result


def local_url(value: str, *, preserve_local: bool = False) -> str:
    parsed = urlparse(value)
    # Only an operator-verified local address may be normalized. Never convert a
    # genuinely remote dependency into a different local service accidentally.
    local_ips = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    import socket

    local_ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.168.0.1", 9))
        local_ips.add(probe.getsockname()[0])
    finally:
        probe.close()
    if parsed.hostname not in local_ips:
        raise RuntimeError(f"Dependency is not on this host: {parsed.hostname}")
    if preserve_local:
        return value
    port = f":{parsed.port}" if parsed.port else ""
    return urlunparse(parsed._replace(netloc=f"127.0.0.1{port}"))


def bound_path(value: str | Path, previous: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else previous / path).resolve()


def wait_ready(revision: str | None, timeout: float = 180) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last_error = "not started"
    while time.monotonic() < deadline:
        try:
            for port, route in ((8291, "health/live"), (8872, "config")):
                with opener.open(f"http://127.0.0.1:{port}/api/v1/{route}", timeout=5) as response:
                    payload = json.load(response)
                if revision and payload.get("build_revision") != revision:
                    raise RuntimeError(f"Wrong revision on {port}")
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1)
    raise RuntimeError(f"New stack did not become ready: {last_error}")


def activate(root: Path, previous: Path, runtime: Path, local_model_url: str | None = None) -> None:
    manifest = verify(root)
    runtime.mkdir(parents=True, exist_ok=True)
    binding_path = runtime / "bindings.json"
    if binding_path.exists():
        bindings = read_json(binding_path)
    else:
        environments: dict[str, dict[str, str]] = {}
        for role, port in (("api", 8291), ("photobench", 8872)):
            pid = listener(port)
            if pid is None:
                raise RuntimeError(f"Capture the current deployment before stopping {role}")
            environments[role] = process_environment(pid)
        api, pb = environments["api"], environments["photobench"]
        config = bound_path(pb.get("PHOTOBENCH_CONFIG_ROOT", previous / "apps/photobench/config"), previous)
        target_config = runtime / "config"
        shutil.copytree(config, target_config)
        connection_path = target_config / "runtime_connection.json"
        connection = read_json(connection_path)
        if local_model_url:
            if urlparse(local_model_url).port != urlparse(connection["model_base_url"]).port:
                raise RuntimeError("A local address override cannot change the selected model port")
            connection["model_base_url"] = local_url(local_model_url, preserve_local=True)
        for key in ("sentrix_url", "model_base_url"):
            connection[key] = local_url(
                connection[key], preserve_local=bool(local_model_url and key == "model_base_url")
            )
        embedding = connection.get("embedding") or {}
        mode = embedding.get("mode")
        for key in ["wemm_url"] if mode == "unified_multimodal" else ["text_url", "image_url"]:
            embedding[key] = local_url(embedding[key])
        connection["embedding"] = embedding
        # This release workflow runs fixed external endpoints, never a Manager.
        # The reviewed product uses QdrantClient(path=...), inside the API PID.
        vector_source = (root / "apps/api/src/sentrix/infrastructure/vector/qdrant_index.py").read_text()
        container_source = (root / "apps/api/src/sentrix/bootstrap/container.py").read_text()
        embedded_qdrant = (
            "QdrantClient(path=str(self.path))" in vector_source
            and "QdrantVectorIndex(settings.qdrant_dir)" in container_source
        )
        removed = []
        product = []
        for spec in connection.get("product") or []:
            if spec.get("id") == "vllm_manager" or (spec.get("id") == "qdrant" and embedded_qdrant):
                removed.append(spec)
            else:
                product.append(spec)
        connection["product"] = product
        connection["vllm_manager_url"] = ""
        write_json(
            runtime / "topology-adjustments.json",
            {
                "removed_standalone_services": removed,
                "qdrant_embedded_in_api": embedded_qdrant,
                "inference_lifecycle": "external_fixed_local_endpoint",
                "local_model_endpoint_override": local_model_url,
            },
        )
        write_json(connection_path, connection)
        bindings = {
            "previous": str(previous),
            "environments": environments,
            "venv": str((previous / ".venv").resolve()),
            "data": str(bound_path(api.get("SENTRIX_DATA_DIR", previous / "data"), previous)),
            "datasets": str(
                bound_path(pb.get("PHOTOBENCH_DATA_ROOT", previous / "apps/photobench/datasets"), previous)
            ),
            "results": str(
                bound_path(pb.get("PHOTOBENCH_RESULTS_ROOT", previous / "apps/photobench/results"), previous)
            ),
            "config": str(target_config.resolve()),
        }
        write_json(binding_path, bindings)
        binding_path.chmod(0o600)
        for path in target_config.rglob("*"):
            if path.is_file():
                path.chmod(0o600)
    launch(root, runtime, bindings, manifest["revision"])


def launch(root: Path, runtime: Path, bindings: dict[str, Any], revision: str | None) -> None:
    for relative, source in (
        (".venv", bindings["venv"]),
        ("data", bindings["data"]),
        ("apps/photobench/datasets", bindings["datasets"]),
        ("apps/photobench/results", bindings["results"]),
    ):
        target = root / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)
    # Validate both listeners before stopping either, so a failed check leaves the stack running.
    targets = []
    for port in (8872, 8291):
        pid = listener(port)
        if pid is None:
            continue
        command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True)
        if "uvicorn" not in command or f"--port {port}" not in command:
            raise RuntimeError(f"Unexpected process on {port}; refusing to signal it")
        if port == 8872:
            opener = build_opener(ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/api/v1/runs", timeout=30) as response:
                current = json.load(response)
            if current.get("active_count", 0):
                raise RuntimeError("An active run must finish or be explicitly cancelled before deployment")
        targets.append((port, pid))
    for port, pid in targets:
        if listener(port) != pid:
            raise RuntimeError(f"Listener {port} changed during deployment")
        os.kill(pid, signal.SIGTERM)
        for _ in range(60):
            if listener(port) is None:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"Listener {port} did not stop gracefully")
    source_path = os.pathsep.join([str(root / "apps/api/src"), str(root / "apps/photobench/src")])
    for role, port in (("api", 8291), ("photobench", 8872)):
        env = dict(bindings["environments"][role])
        env.update(
            PYTHONPATH=source_path,
            PHOTOBENCH_CONFIG_ROOT=bindings["config"],
            SENTRIX_DATA_DIR=bindings["data"],
            PHOTOBENCH_DATA_ROOT=bindings["datasets"],
            PHOTOBENCH_RESULTS_ROOT=bindings["results"],
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            SENTRIX_WEB_DIST_DIR=str(root / "apps/web/dist"),
            PHOTOBENCH_WEB_DIST_ROOT=str(root / "apps/photobench/web/dist"),
            SENTRIX_API_PORT="8291",
            PHOTOBENCH_PORT="8872",
            PHOTOBENCH_SENTRIX_API_URL="http://127.0.0.1:8291",
            PHOTOBENCH_SENTRIX_REPO_ROOT=str(root),
            PHOTOBENCH_SENTRIX_START_COMMAND=f"bash {root}/scripts/deploy/start_api.sh auto",
            PHOTOBENCH_SENTRIX_PID_FILE=str(runtime / "sentrix-process.json"),
            PHOTOBENCH_SENTRIX_LOG_PATH=str(runtime / "api.log"),
            FACE_PROVIDERS="CPUExecutionProvider",
            RETINAFACE_PROVIDERS="CPUExecutionProvider",
        )
        connection = read_json(Path(bindings["config"]) / "runtime_connection.json")
        env.update(
            BENCH_SENTRIX_URL=connection["sentrix_url"],
            BENCH_VLLM_BASE_URL=connection["model_base_url"],
            BENCH_VLLM_API_URL="",
        )
        model_host = urlparse(connection["model_base_url"]).hostname
        local_hosts = ["127.0.0.1", "localhost", "::1"]
        if model_host and model_host not in local_hosts:
            local_hosts.append(model_host)
        env["NO_PROXY"] = env["no_proxy"] = ",".join(local_hosts)
        check = subprocess.check_output(
            [
                str(root / ".venv/bin/python"),
                "-c",
                "import sentrix,photobench; print(sentrix.__file__); print(photobench.__file__)",
            ],
            env=env,
            cwd=root,
            text=True,
        ).splitlines()
        if len(check) != 2 or any(not str(Path(line)).startswith(str(root) + "/") for line in check):
            raise RuntimeError(f"Python imports escape this release: {check}")
        with (runtime / f"{role}.log").open("ab") as output:
            process = subprocess.Popen(
                ["bash", str(root / f"scripts/deploy/start_{role}.sh")],
                env=env,
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        write_json(runtime / f"{role}.pid.json", {"pid": process.pid, "port": port})
        if role == "api":
            # start_api.sh execs uvicorn, preserving this PID. Register the process
            # we just launched so a full run recognizes the already-bound endpoint.
            write_json(
                runtime / "sentrix-process.json",
                {
                    "pid": process.pid,
                    "endpoint": connection["model_base_url"],
                    "runtime_connection": connection,
                },
            )
    wait_ready(revision)
    activation_path = runtime / "activation.json"
    if activation_path.exists():
        shutil.copy2(activation_path, runtime / f"activation-{time.time_ns()}.json")
    write_json(activation_path, {"revision": revision, "root": str(root)})
    print(json.dumps({"revision": revision, "runtime": str(runtime), "imports": check}))


def rollback(root: Path, runtime: Path) -> None:
    bindings = read_json(runtime / "bindings.json")
    previous = Path(bindings["previous"]).resolve()
    revision = verify(previous)["revision"] if (previous / "release-manifest.json").exists() else None
    if previous == root:
        raise RuntimeError("Rollback target equals current release")
    launch(previous, runtime, bindings, revision)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["package", "verify", "activate", "rollback"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument(
        "--local-model-url", help="Explicit same-host listener address; does not change model port"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "package":
        if args.destination is None:
            parser.error("--destination is required")
        package(root, args.destination.resolve())
    elif args.action == "verify":
        print(json.dumps({"revision": verify(root)["revision"], "verified": True}))
    elif args.action == "rollback":
        if args.runtime is None:
            parser.error("--runtime is required")
        rollback(root, args.runtime.resolve())
    else:
        if args.previous is None or args.runtime is None:
            parser.error("--previous and --runtime are required")
        activate(root, args.previous.resolve(), args.runtime.resolve(), args.local_model_url)


if __name__ == "__main__":
    main()
