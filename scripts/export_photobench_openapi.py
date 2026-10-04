#!/usr/bin/env python3
"""把 PhotoBench 的 openapi.json 落到 packages/contracts/openapi/photobench.yaml。

体例与 ``scripts/export_openapi.py``（sentrix 那份）逐条相同：契约是唯一真相，产出的
文件提交进仓库，CI 用 ``--check`` 比对，防止实现偷偷偏离契约。

**为什么是第二个脚本而不是给第一个加个开关。** PhotoBench 是**第二个发行包**
（``apps/photobench``，源码根 ``apps/photobench/src``），它的 app 与 sentrix 的没有
任何共享入口——两个 ``create_app`` 各自装自己那套路由。一个 ``--app`` 开关要把
「sys.path 插哪个 src 根 + import 哪个工厂 + 写哪个目标」三件事都参数化，而这三件事
正是两个脚本各自不同的**全部**内容。分成两支，两边各自照抄得到，读起来也不必先
在脑子里展开一个分支。

**文件名没有 ``-v1``**：sentrix 的路由前缀是 ``/api/v1``，PhotoBench 的仍是 ``/api``
（1.0 前端按这个前缀写死，改它的前缀属 S10 的前端范围）。文件名跟着前缀走，
以免出现「名字说 v1、路径其实是 /api」这种自相矛盾的契约。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = REPO_ROOT / "packages" / "contracts" / "openapi" / "photobench.yaml"


def current_spec() -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "photobench" / "src"))
    from photobench.main import create_app

    # ``scan_in_background=False``：导出契约不该顺手起一个扫描 results/ 的线程——
    # 那会把「这个脚本跑得快不快」变成「盘上有多少个 run」的函数。
    return create_app(scan_in_background=False).openapi()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args()

    spec = current_spec()
    rendered = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)

    if args.check:
        existing = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if existing != rendered:
            print("PhotoBench openapi 契约与实现不一致，请运行 scripts/export_photobench_openapi.py 更新")
            return 1
        print("PhotoBench openapi 契约一致")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"已写入 {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
