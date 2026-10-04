"""run-execution 主链（S9b-2）：建空间 → 种身份 → 导入 → 处理 → 问答。

对应 1.0 ``benchmark_orchestrator.py`` 的 ``BenchmarkRun``（``:1895``）与它那九个相位
实现（``:2526``–``:3330``）。**拆出来的不是文件，是依赖**：1.0 的 ``BenchmarkRun``
自己拼 URL、自己起线程、自己写盘、自己选 provider，2.0 把四件事分别交给
``SentrixClient`` / ``GpuSampler`` / ``RunResultWriter`` / 注入的运行时三件套。

本切片覆盖的相位：``model_deploy`` / ``scope_setup`` / ``identity_seed`` /
``photo_import`` / ``pipeline_processing`` / ``qa_eval``。**不覆盖**：judge 打分
（``_judge_item`` ``:3588``）、遥测聚合（``_phase_gpu_metrics`` ``:4568`` /
``_phase_aggregate`` ``:4580``）、写侧路由——那三项是 S9b-3。
"""
