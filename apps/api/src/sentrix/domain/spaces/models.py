"""记忆空间：一个 scope 的元数据。

它原来住在 ``infrastructure/persistence/repositories/spaces.py``——那里是**一行 SQL 的
返回形状**的常见住法，但记忆空间不是某个适配器的私事：``include_in_people`` 决定一个
空间里聚出来的人脸进不进人物视图（``FaceRepository.list_clusters`` 与
``EntityRepository.list_entities`` 都按它过滤），这是**领域规则**，只是恰好存在一行数据里。

四个字段就是 ``memory_spaces`` 的四列，没有派生字段：这份记录是读出来的那一行，
不是给界面看的模型。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemorySpace:
    id: str
    name: str
    kind: str
    status: str
