"""class 名双向差集（v2：对称提取 + CSS 类名集过滤）。

**v1 的教训**：我按「`class="..."` 静态属性」提取，而 2.0 大量用模板字面量
（`` `qa-item${open ? " open" : ""}` ``）与字符串拼接，于是 v1 报出 24 个「只在 1.0 出现」，
其中抽查的 11 个**全部是提取漏的**，不是真缺。

**v2 改成双向对称**：两边都抓所有引号字符串（含反引号），用 `photobench.css` 里
**真实存在的类名集合**过滤。这样：

- 模板字面量与拼接照样能抓到（字符串字面量仍在）；
- 噪音被 CSS 集合滤掉（一个随机字符串恰好等于某个类名的概率极低）。

剩下的差异才是**值得人看一眼**的：只在 2.0 有 → 自造的类；只在 1.0 有 → 漏了 UI 或刻意不移植。
"""

from __future__ import annotations

import pathlib
import re

VUE = pathlib.Path("/home/asus/Github/Sentrix-Home-Web/services/photobench/frontend/src/App.vue")
CSS = pathlib.Path("/home/asus/Github/Sentrix-Home-Web/services/photobench/frontend/src/styles.css")
WEB = pathlib.Path("/home/asus/Github/sentrix-home-web2.0/apps/photobench/web/src")

TEMPLATE_START = 2838  # 1.0 `<template>` 起（S10b-0 实测）

#: 任意引号字符串（"…" '…' `…`）；反引号内可能含 ${…}，按分隔符切即可
QUOTED = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')
#: CSS 里的 `.name`；只取类名本身
CSS_CLASS = re.compile(r"\.([A-Za-z_][\w-]*)")

DELIBERATELY_DROPPED = {"arbiter-grid", "arbiter-cell", "arbiter-section"}


def class_set(text: str) -> set[str]:
    """从一段文本里取「引号字符串中出现过的、且确实是 CSS 类名」的词。"""
    known = set(CSS_CLASS.findall(CSS.read_text(encoding="utf-8")))
    found: set[str] = set()
    for literal in QUOTED.findall(text):
        for token in re.split(r"[\s${}\"'`+?:,()\[\]]+", literal):
            if token in known:
                found.add(token)
    return found


def main() -> int:
    lines = VUE.read_text(encoding="utf-8").split("\n")
    legacy = class_set("\n".join(lines[TEMPLATE_START - 1 :]))

    modern: set[str] = set()
    for path in sorted(WEB.rglob("*.tsx")):
        if path.name.endswith(".test.tsx"):
            continue
        modern |= class_set(path.read_text(encoding="utf-8"))

    only_modern = sorted(modern - legacy - DELIBERATELY_DROPPED)
    only_legacy = sorted(legacy - modern - DELIBERATELY_DROPPED)

    print(f"1.0 模板用到 {len(legacy)} 个真实类名")
    print(f"2.0 组件用到 {len(modern)} 个真实类名")
    print(f"交集 {len(legacy & modern)} 个\n")

    print(f"=== 只在 2.0 出现（{len(only_modern)}）——重写时自造的？ ===")
    for name in only_modern:
        print("   ", name)
    print(f"\n=== 只在 1.0 出现（{len(only_legacy)}）——漏了 UI？或刻意不移植？ ===")
    for name in only_legacy:
        print("   ", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
