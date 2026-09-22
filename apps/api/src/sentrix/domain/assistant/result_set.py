"""结果集：一次检索的有序候选，以及它在对话里的分页视图。

对应 1.0 ``agent_runtime/result_set.py``。三条不变量原样保留，它们在 1.0 里被证明是
对的：

1. **每页 6 张、1-based、无游标、``page_size`` 硬顶 6**；
2. **``handle`` 是全局序号**（第 2 页从 ``photo_7`` 开始），跨页稳定——模型只需要记住
   ``photo_N``，分页不会让同一个 handle 指向不同的图；
3. **``asset_id`` 只存在于结果集里**，不进模型上下文。模型看到的是 handle，真正可交付
   的资产由运行时解引用。1.0 为此在 ``runtime.py:177-179`` 专门把 ``asset_id`` 从模型
   可见的观察里剥掉。

``handle`` → ``asset_id`` 的映射保留在结果集这一侧，于是「模型只能交付它真的看过的图」
是一个结构性事实，而不是靠提示词约束。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

PAGE_SIZE = 6


@dataclass(frozen=True, slots=True)
class ResultSetEntry:
    handle: str
    asset_id: str


@dataclass(frozen=True, slots=True)
class ResultSetPage:
    page: int
    page_size: int
    total: int
    has_more: bool
    remaining: int
    entries: tuple[ResultSetEntry, ...]


@dataclass(frozen=True, slots=True)
class ResultSet:
    id: str
    scope_id: str
    query: str
    asset_ids: tuple[str, ...]
    created_at: datetime

    def page(self, page_no: int, page_size: int = PAGE_SIZE) -> ResultSetPage:
        """第 ``page_no`` 页（1-based）。页码越界返回空页，不抛异常。

        1.0 也是这个行为（``tools.py:2073``）：越界不是错误，是「没有更多了」。抛异常
        只会让模型把一次正常的翻到底当成系统故障。
        """
        size = min(PAGE_SIZE, max(1, page_size))
        start = max(0, (max(1, page_no) - 1) * size)
        end = start + size
        entries = tuple(
            ResultSetEntry(handle=f"photo_{start + index + 1}", asset_id=asset_id)
            for index, asset_id in enumerate(self.asset_ids[start:end])
        )
        return ResultSetPage(
            page=max(1, page_no),
            page_size=size,
            total=len(self.asset_ids),
            has_more=end < len(self.asset_ids),
            remaining=max(0, len(self.asset_ids) - end),
            entries=entries,
        )

    def first_page(self) -> ResultSetPage:
        return self.page(1)

    def resolve(self, handle: str) -> str | None:
        """``photo_N`` → ``asset_id``；不在本结果集里返回 ``None``。

        1.0 的授权端点正是靠这一步把「模型给的 handle」与「真实资产」隔开
        （``app.py:2484``，handle 必须属于该结果集且 scope 匹配）。
        """
        if not handle.startswith("photo_"):
            return None
        suffix = handle[len("photo_") :]
        if not suffix.isdigit():
            return None
        index = int(suffix) - 1
        if 0 <= index < len(self.asset_ids):
            return self.asset_ids[index]
        return None

    def handle_for(self, asset_id: str) -> str | None:
        for index, candidate in enumerate(self.asset_ids):
            if candidate == asset_id:
                return f"photo_{index + 1}"
        return None
