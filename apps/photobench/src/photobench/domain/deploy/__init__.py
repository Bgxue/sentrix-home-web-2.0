"""部署端口：**「换模型 = 重启 2.0」**（S9c · T923 / 用户裁决③）。

1.0 靠 ``POST /api/model-profiles/bind-runtime`` 不重启就把 Sentrix 的模型端点换掉；
2.0 的 ``vlm_base_url`` 是启动时从 ``Settings`` 读死的（``apps/api/src/sentrix/config.py``
的 ``SENTRIX_VLM_BASE_URL``，容器里 ``ServedModel(settings.vlm_base_url)`` 只缓存模型名），
**端点换不了**。用户裁决：**PhotoBench 重启 2.0（带上新的 vlm_base_url）**。

于是 PhotoBench 要管一个它本不该管的进程——**那是部署知识**。这一层只留端口（接口），
实现在 ``photobench.infrastructure.deploy``，用例在
``apps/photobench/tests/test_sentrix_process.py``。

**与 S11（服务与部署）的重叠**：计划书 §1 已登记——S11 要做「主 API / Web / PhotoBench /
model-manager 独立启停」，这里的重启逻辑到 S11 可能被接管或收敛。**把它收成一个端口 +
一个适配器、而不是散在 ``_phase_model_deploy`` 里，就是为了让那次接管只动这一个文件。**
"""
