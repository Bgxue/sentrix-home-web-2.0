"""进程内的回合事件流：把工作线程产出的事件推给 SSE 订阅者。

这是「前端不轮询」的落点。1.0 的 SSE 端点自己每 300ms 去问一次内存 dict
（``app.py:2980``），而那份 dict 永不清理——实测每个已完成的 turn 连同完整 result 永久
驻留（``_TURN_JOBS`` 全仓 8 处引用里没有任何 pop/TTL/del）。一旦前端的
``EventSource.onerror`` 触发，它就永久降级成 700ms 轮询（668,490 次请求）。

这里的做法是推送而不是轮询：工作线程 ``publish``，每个订阅者有自己的 ``asyncio.Queue``。
跨线程唤醒走 ``loop.call_soon_threadsafe``——``publish`` 从工作线程调用，事件循环在另一个
线程上跑，这是唯一安全的交接方式。

**每个订阅者一条队列，不是共用一个 Event。** 共用会有一个只在多订阅者时出现的死锁：
第二个订阅者若把 ``channel.wake`` 整个换掉，先来的那个会永远等在旧对象上。单订阅者的
测试永远发现不了它。

**生命周期有边界**：一个回合的事件只在「回合结束且最后一个订阅者读完」之前驻留内存，
之后清掉。迟到的订阅者拿不到 channel，直接结束——它的历史事件由
``TurnRepository.events_after`` 从库里补。内存与持久化各管一段，不留无主的状态。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sentrix.domain.assistant.models import ProgressEvent


@dataclass
class _Channel:
    backlog: list[ProgressEvent] = field(default_factory=list)
    closed: bool = False
    subscribers: int = 0
    loop: asyncio.AbstractEventLoop | None = None
    queues: set[asyncio.Queue[ProgressEvent | None]] = field(default_factory=set)

    def broadcast(self, item: ProgressEvent | None) -> None:
        if self.loop is None:
            return
        for queue in list(self.queues):
            self.loop.call_soon_threadsafe(queue.put_nowait, item)


class InProcessTurnEventBroker:
    def __init__(self) -> None:
        self._channels: dict[str, _Channel] = {}

    def _channel(self, turn_id: str) -> _Channel:
        return self._channels.setdefault(turn_id, _Channel())

    def open(self, turn_id: str) -> None:
        """回合开始时就登记好 channel。

        不这么做会有一个真实的竞态：SSE 连接可能比工作线程的第一条事件更早到达，于是
        ``subscribe`` 找不到 channel、直接结束，SSE 端点在回合还没跑完时就发完
        ``complete`` 收摊。提前登记把「回合存在」与「有人订阅」解耦开。
        """
        self._channel(turn_id)

    def publish(self, turn_id: str, event: ProgressEvent) -> None:
        channel = self._channel(turn_id)
        channel.backlog.append(event)
        channel.broadcast(event)

    def finish(self, turn_id: str) -> None:
        channel = self._channels.get(turn_id)
        if channel is None:
            return
        channel.closed = True
        channel.broadcast(None)
        if channel.subscribers == 0:
            # 没有人在听，也就没有理由把 backlog 留着——历史事件已经落库了。
            self._channels.pop(turn_id, None)

    async def subscribe(self, turn_id: str) -> AsyncIterator[ProgressEvent | None]:
        """先补发订阅之前积压的事件，再等增量。结束时产出 ``None``。"""
        channel = self._channels.get(turn_id)
        if channel is None:
            # 回合已经收尾并且已经有人读完了。历史事件走数据库，这里直接结束。
            yield None
            return

        queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
        channel.loop = asyncio.get_running_loop()
        # 先登记队列再读 backlog：反过来的话，两步之间发布的事件会两边都错过。
        # 代价是重叠——同一条事件可能既在 backlog 又在队列里，靠 ``step_index`` 去重。
        channel.queues.add(queue)
        channel.subscribers += 1
        seen = 0
        try:
            for buffered in list(channel.backlog):
                seen = max(seen, buffered.step_index)
                yield buffered
            while True:
                item = await queue.get()
                if item is None:
                    break
                if item.step_index <= seen:
                    continue
                seen = item.step_index
                yield item
        finally:
            # 清理必须在 ``yield None`` **之前**发生：订阅者收到 None 就 break，不会再去
            # 迭代一次生成器，于是 ``yield None`` 之后的代码与生成器的 ``finally`` 都要等
            # 垃圾回收才跑——那时 channel 已经白白多留了一轮。
            channel.queues.discard(queue)
            channel.subscribers -= 1
            if channel.closed and channel.subscribers == 0:
                self._channels.pop(turn_id, None)
        yield None

    def pending(self) -> int:
        """当前驻留在内存里的回合数。测试用它证明「收尾之后不再堆积」。"""
        return len(self._channels)
