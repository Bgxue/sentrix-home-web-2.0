"""已确认人物的目录：把实体仓储适配成助手工具要的档案。

S3 装配的是 ``NoIdentityDirectory``——那时身份层还没建起来，「查不到」与「没有这个人」
是同一件事，如实回 ``None`` 是唯一诚实的结果。S5 起库里有真的已确认人物，所以这条查询
有了真答案。**工具契约、账本与门禁一行都没动**（S3 那个占位实现的模块注释当初就是这么
约定的）：换掉的只是容器里那一个字段的实参。

**只认已确认的人。** 判据是 ``find_confirmed_person_by_name`` 本身——它查的正是
``entity_type='person' AND status='confirmed'``，先精确名再扫别名。未确认的候选人物簇
不进这个目录：那还只是一次聚类猜测，把它当档案答出去，等于让猜测变成检索事实
（``link_confirmed_mention`` 拒绝未确认实体，是同一条理由）。

**状态码与错误码不在这里。** 这个端口只有一个答案形状（``PersonProfile | None``），
「没有这个人」不是错误，是 ``None``——工具据此输出 gap 而不是抛异常。
"""

from __future__ import annotations

from sentrix.domain.assistant.models import PersonProfile
from sentrix.domain.identity.ports import EntityRepository


class EntityPersonDirectory:
    def __init__(self, *, entities: EntityRepository) -> None:
        self._entities = entities

    def lookup(self, name: str, scope_id: str) -> PersonProfile | None:
        clean_name = str(name or "").strip()
        if not clean_name:
            return None
        match = self._entities.find_confirmed_person_by_name(scope_id, clean_name)
        if match is None:
            return None
        entity = self._entities.get(match.entity_id)
        if entity is None:
            return None
        return PersonProfile(
            # 报**实体的本名**而不是查询串：按别名查到时，用户问的是别名、答案里的名字
            # 得是本名，否则模型会把这个人的两个名字当成两个人。
            name=entity.canonical_name,
            family_role=entity.family_role or "",
            confirmed=True,
        )
