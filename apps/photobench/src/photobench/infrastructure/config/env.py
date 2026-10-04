"""``SecretEnvStore`` 的文件实现：``<config_root>/.env.local`` 的 ``KEY=VALUE`` 行。

1.0 的 ``_load_dotenv_file``（``:79``）与 ``_write_local_env_secret``（``:317``）逐条搬。
**进程环境优先**：写进去的值同时改 ``os.environ``，而读的时候已经在环境里的键不会被
文件覆盖（1.0 的 ``if key in os.environ: continue``）——这让「临时用环境变量试一次」
不会被上次写进文件的密钥盖掉。

写是**原子**的（临时文件 + ``os.replace`` + ``chmod 0o600``）：密钥文件被写坏或权限
是 0644，比写失败更糟——前者让服务起不来，后者把密钥暴露给同机的其他用户。
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

ENV_FILE_NAME = ".env.local"


class DotenvSecretStore:
    def __init__(self, root: Path) -> None:
        self._path = root / ENV_FILE_NAME

    def read_all(self) -> dict[str, str]:
        if not self._path.is_file():
            return {}
        values: dict[str, str] = {}
        for raw in self._path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = self._parse_value(value.strip())
        return values

    @staticmethod
    def _parse_value(value: str) -> str:
        try:
            parsed = shlex.split(value, posix=True)
        except ValueError:
            parsed = []
        return parsed[0] if len(parsed) == 1 else value

    def read(self, name: str) -> str | None:
        return self.read_all().get(name)

    def write(self, name: str, value: str) -> None:
        if "\n" in value or "\r" in value:
            raise ValueError(f"{name} must be a single-line value")
        existing = self._path.read_text(encoding="utf-8").splitlines() if self._path.exists() else []
        prefix = f"{name}="
        lines = [line for line in existing if not line.startswith(prefix)]
        if value:
            lines.append(f"{name}={shlex.quote(value)}")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f"{self._path.name}.tmp.{os.getpid()}")
        temporary.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path)


__all__ = ["ENV_FILE_NAME", "DotenvSecretStore"]
