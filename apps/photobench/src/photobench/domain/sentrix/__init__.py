"""PhotoBench 打向主产品（Sentrix 2.0）的**语义化**端口。

与 ``photobench.domain.http`` 的区别是这一层要回答的问题不同：
``http.ports.JsonHttpClient`` 是「把这段 JSON 发出去/取回来」，这一层是
「在评测主链上，PhotoBench 需要主产品做哪几件事」。1.0 没有这一层——
``benchmark_orchestrator.py`` 里每一处都直接拼 URL 调 ``request_json``
（``/api/memory-spaces`` ``:2644``、``/api/people/seed-batch`` ``:2691``、
``/api/import`` ``:2760``、``/api/assistant/turn`` ``:3402``…），端点形状于是散在
主链的相位实现里，改一个前缀要改十几处。

**包名沿用 1.0 的用词**（1.0 把主产品叫 Sentrix，配置项叫 ``sentrix_url``）。
它与 ``photobench-isolation`` 契约无关——那条契约管的是 import，不是命名。
"""
