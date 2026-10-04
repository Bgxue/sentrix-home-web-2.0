"""``SentrixClient`` 的 2.0 实现所在的包。

与外层 ``photobench.infrastructure`` 的其余子包同构（config / datasets / http / results /
runtime 各有一个包）。**接口在 ``photobench.domain.sentrix.ports``，实现在这里**——
与 runtime 那几个端口的拆法逐条相同：实现要打 HTTP、要拼 multipart、要知道
``/api/v1`` 这个前缀，而 ``photobench-domain-purity`` 禁止 domain 碰 httpx。
"""
