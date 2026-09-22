"""进程内回合事件流的行为。

这是「前端不轮询」的落点，所以它必须经得起三件事：事件真的在产出时就被推出去、
迟到的订阅者拿得到积压、收尾之后内存里不再留着。

1.0 的 SSE 端点自己每 300ms 去问一次内存 dict（``app.py:2980``），而那份 dict 永不清理
（``_TURN_JOBS`` 全仓 8 处引用里没有任何 pop/TTL/del）。这里两件事都反着做。
"""

from __future__ import annotations

import asyncio
import threading

from sentrix.domain.assistant.models import ProgressEvent, ProgressStage, ProgressStatus
from sentrix.infrastructure.assistant.events import InProcessTurnEventBroker


def event(index: int, text: str = "") -> ProgressEvent:
    return ProgressEvent(
        step_index=index,
        stage=ProgressStage.THINKING,
        status=ProgressStatus.RUNNING,
        text=text or f"第 {index} 步",
    )


def test_subscribers_receive_events_published_before_they_attach():
    """回合还在跑的时候迟到几百毫秒，不该丢事件——SSE 连接晚于回合启动是常态。

    收尾**之后**才来的订阅者不走这里：那时 channel 已经清掉了，历史由
    ``TurnRepository.events_after`` 从库里补（见 ``test_a_late_subscriber_...``）。
    """

    async def scenario() -> list[ProgressEvent]:
        broker = InProcessTurnEventBroker()
        broker.publish("turn_1", event(1))
        broker.publish("turn_1", event(2))

        received: list[ProgressEvent] = []
        async for item in broker.subscribe("turn_1"):
            if item is None:
                break
            received.append(item)
            if len(received) == 2:
                broker.finish("turn_1")
        return received

    received = asyncio.run(scenario())

    assert [item.step_index for item in received] == [1, 2]


def test_a_running_turn_streams_events_as_they_are_published():
    """工作线程边跑边推，订阅者按顺序拿到——中间没有轮询。

    这里断言的不只是内容，还有**时序**：生产者被订阅者「解锁」才会推下一条，
    所以收到全部事件就一定意味着它们是流式到达的，不是一次性回放。
    """

    async def scenario() -> list[ProgressEvent]:
        broker = InProcessTurnEventBroker()
        received: list[ProgressEvent] = []
        # 生产者每推一条就等消费者确认收到再推下一条。于是「收到的条数」本身就证明了
        # 事件是一条条流出来的，而不是等回合结束一次性回放的。
        acked = threading.Event()
        acked.set()

        def worker() -> None:
            for index in range(1, 4):
                acked.wait()
                acked.clear()
                broker.publish("turn_1", event(index))
            broker.finish("turn_1")

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        async for item in broker.subscribe("turn_1"):
            if item is None:
                break
            received.append(item)
            acked.set()
        thread.join(timeout=2)
        return received

    received = asyncio.run(scenario())

    assert [item.text for item in received] == ["第 1 步", "第 2 步", "第 3 步"]


def test_a_closed_turn_that_nobody_watched_leaves_nothing_in_memory():
    """没有订阅者时，回合收尾就把 backlog 丢掉——历史事件已经落库了。

    1.0 的 ``_TURN_JOBS`` 永不清理，每个已完成的 turn 连同完整 result 永久驻留。
    """
    broker = InProcessTurnEventBroker()
    broker.publish("turn_1", event(1))
    assert broker.pending() == 1

    broker.finish("turn_1")

    assert broker.pending() == 0


def test_a_closed_turn_is_dropped_after_its_last_subscriber_drains():
    """有人在听的时候，收尾不立刻丢——等最后一个订阅者读完再清。"""

    async def scenario() -> tuple[int, int]:
        broker = InProcessTurnEventBroker()
        broker.publish("turn_1", event(1))

        async for item in broker.subscribe("turn_1"):
            if item is None:
                break
            broker.finish("turn_1")
            during = broker.pending()

        return during, broker.pending()

    during, after = asyncio.run(scenario())

    assert during == 1
    assert after == 0


def test_a_late_subscriber_of_a_forgotten_turn_ends_immediately():
    """迟到的订阅者不会挂住：拿不到 channel 就直接结束，历史事件由数据库补。"""

    async def scenario() -> list[ProgressEvent | None]:
        broker = InProcessTurnEventBroker()
        return [item async for item in broker.subscribe("turn_never_seen")]

    assert asyncio.run(scenario()) == [None]


def test_finishing_an_unknown_turn_is_a_no_op():
    broker = InProcessTurnEventBroker()

    broker.finish("turn_never_seen")

    assert broker.pending() == 0


def test_two_subscribers_both_get_every_event():
    async def scenario() -> tuple[list[int], list[int]]:
        broker = InProcessTurnEventBroker()
        broker.publish("turn_1", event(1))
        broker.publish("turn_1", event(2))

        first: list[int] = []
        second: list[int] = []

        async def drain(sink: list[int]) -> None:
            async for item in broker.subscribe("turn_1"):
                if item is None:
                    return
                sink.append(item.step_index)

        tasks = [asyncio.create_task(drain(first)), asyncio.create_task(drain(second))]
        await asyncio.sleep(0)  # 让两个订阅者都登记上
        for _ in range(50):
            if len(first) == 2 and len(second) == 2:
                break
            await asyncio.sleep(0.01)
        broker.finish("turn_1")
        await asyncio.gather(*tasks)
        return first, second

    first, second = asyncio.run(scenario())

    assert first == [1, 2]
    assert second == [1, 2]
