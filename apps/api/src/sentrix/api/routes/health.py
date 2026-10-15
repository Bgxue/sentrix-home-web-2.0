from fastapi import APIRouter, Request

from sentrix import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    """进程与它依赖的**当前**可用性。

    **为什么嵌入器要进来。** 2026-10-10 那轮 PhotoBench 评测里，WeMM 嵌入侧车全程
    不在（354/370 次检索静默降级成 ``backend=none``），而本端点一直回
    ``{"status":"ok"}`` —— 整轮数据作废却没有任何一处报错。数据库好、嵌入器坏，
    不是 ``ok``。

    **为什么是 ``degraded`` 而不是 ``error``。** 嵌入是**可选**依赖：侧车不在时
    检索退回词法/元数据/实体三路，进程照常服务。两档分开才能让「还能用但少了东西」
    与「用不了」在监控上可区分；把两者压成一个失败码，运维就没法只对后者告警。

    探测走 ``EmbeddingHealthProbe``，它自带 10 秒 TTL（``models/wemm.py``），
    所以这个端点被高频轮询时不会每次都打一次侧车。
    """
    container = request.app.state.container
    database_ok = container.database_healthy()
    embeddings_ok = container.embedding_health.available
    payload = {
        "status": "ok" if (database_ok and embeddings_ok) else "degraded",
        "version": __version__,
        "database": "ok" if database_ok else "error",
        "embeddings": "ok" if embeddings_ok else "unavailable",
    }
    if not embeddings_ok:
        # **带上原因。** 只有「不可用」三个字的话，排查要从日志里捞——
        # 而这条路径的整个教训就是「降级必须自己走到人眼前」。
        payload["embeddings_error"] = container.embedding_health.load_error or "sidecar_unreachable"
    return payload
