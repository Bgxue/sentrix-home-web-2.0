"""数据集展示层用例：manifest 列表 / 详情、QA 数据集、相册媒体。

1.0 这四条都在 ``OrchestratorHandler.do_GET`` 里（``:7685`` / ``:7690`` / ``:7713``），
与「读 run」不是同一件事——它们把**本地数据集**端出来，是控制台可用的硬依赖
（S10a 的 T1001/T1002）。纯投影在 ``domain.datasets.catalog`` 与
``domain.datasets.qa_media``，文件与目录在 ``AlbumSource`` 端口上。
"""
