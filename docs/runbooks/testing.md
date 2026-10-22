# 测试分层与证据

2026-10-23 按用户决定拆分。日常 CI 不加载真实模型、整册相册或人脸权重；不删除这些专项测试，也不降低覆盖率阈值。

| 验证 | 命令与范围 | 执行时机 |
|---|---|---|
| 日常 CI | 仓库根 `make ci`：全部无 `real_environment` 标记的测试；类型、架构、可达性、文档、两 OpenAPI、domain/application ≥85%、两前端 | 每次代码修改，固定提交发布前 |
| 真实专项 | `make test-real`；或 `make test-real REAL_TEST_PATHS="apps/api/tests/e2e/test_search_channels.py"` | 修改对应能力或依赖适配时 |
| 全部脚本验证 | `make ci-real` | 具备全部真实资源时，完整功能验收 |
| 真机10QA | 固定 release 的 `scripts/deploy/photobench_acceptance.py`，见 xy-deployment | 执行/指标改动按影响设备验证 |

## 真实专项清单

| 文件（apps/api/tests 下） | 用例数 | 必需资源/核查内容 |
|---|---:|---|
| e2e/test_album_timeline.py | 13 | 53张真照片、GeoNames、人脸权重；导入/时间线/地点/幂等 |
| e2e/test_assistant_turn.py | 6 | 真照片、真实主模型/视觉嵌入、人脸；Agent与上下文 |
| e2e/test_identity_album.py | 6 | 真照片、人脸权重；身份/人物画像，文本与视觉模型为确定性假件 |
| e2e/test_import_and_deliver.py | 2 | 真照片、人脸权重；原图交付与EXIF |
| e2e/test_photo_inspection.py | 4 | 真照片、真实主模型/视觉嵌入、人脸；视觉问询 |
| e2e/test_search_channels.py | 3 | 53张真照片、GeoNames、人脸；词法地点召回，四条模型路径均为假件 |
| e2e/test_visual_search.py | 7 | 真实视觉嵌入；颜色/图文检索（含参数化） |
| integration/test_face_detector.py | 6 | 真照片、RetinaFace；分块检测与NMS |
| integration/test_face_embedding.py 两个 real 测试 | 2 | 真照片、buffalo_l；真实512维与SCRFD复核 |

当前49例。face_embedding其余算术、阈值与几何测试继续进入日常CI。
`pytest --collect-only -o addopts= --strict-markers -q -m real_environment` 可导出完整nodeid；
`-m "not real_environment"` 为互补集合。没有删除原用例/断言。

## 配置与缺依赖语义

使用 `make install` 创建的环境；数据、GeoNames安装见 geonames-data.md，真实模型按设备配置准备。
人脸CPU可显式设置 `FACE_PROVIDERS=CPUExecutionProvider` 和
`RETINAFACE_PROVIDERS=CPUExecutionProvider`，不宣称GPU已验收。
专项主模型/嵌入必须可调用；不得为测试擅自重启他人模型或把设备本机依赖改成远端。
当前配置字段与端点读取沿用 Settings，不新增一套业务配置来源。

专项入口保留原 pytest 输出，打印 JUnit绝对路径。可直接用
`.venv/bin/python scripts/run_real_tests.py <文件或nodeid> --junitxml /新路径/result.xml`。
已存在报告拒绝覆盖；pytest非零退出码原样返回，成功后再检查无零用例、skip、failure或error。
直接 pytest 跳过会保留原原因；不能用该退出码或缺失报告宣称真实专项完成。

10QA只能证明本轮覆盖到的流程；未覆盖的专项功能继续注明未验证。
服务/指标正确性、任务成功与受控失败、有效Judge覆盖分别报告，失败题不删除、缺分不填零。

## 本次定位与验证边界

旧完整CI在3c92474执行时4项Agent测试于导入阶段失败，原因是153 WeMM停止，未进入Agent断言。
用户确认153正在使用，暂不恢复；保留旧失败与中断证据，不能称这些专项已修复或通过。
独立本地批次2857项通过，地点专项3项setup错误：漏替换批末chat_model/person_vision，
后者读取model_id连接了未启动的8100。补齐与时间线专项相同的确定性假件，原地点断言保留。
这些结果按实际源码版本单独记录，不拼接为新提交完整CI。

本次生产改动仅Jetson监听缓存：必须执行缓存/PID复用专项与NX真实full10QA，再补NX build→reuse；
AGX待模型恢复验证同一路径，153/Mac不受该实现影响，可引用其实际SHA的既有证据。
