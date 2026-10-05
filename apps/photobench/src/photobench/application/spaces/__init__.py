"""记忆空间（相册）列表的用例：转发 2.0 主 API 并补上「可复用基座」。

**这是 PhotoBench 唯一一处主动读 2.0 主 API 的展示层接口**（评测主链那几条走
``domain.sentrix`` 端口）。``photobench-isolation`` 禁的是 import ``sentrix.*``，
不是 HTTP——所以它走 ``JsonHttpClient`` 端口，与主链发请求的方式完全一样。
"""
