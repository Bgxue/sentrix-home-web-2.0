/**
 * 「评测运行」视图的占位。真正的配置区、run 列表、run 详情、逐题结果、
 * 关键帧分析、记忆有效性、遥测图由后续切片替换这里（1.0 模板 `App.vue:2844-3674`）。
 *
 * 占位保留 1.0 的 class（`section`）与标题，好让样式文件一接入就有落点。
 */
export function RunsView() {
  return (
    <section className="section" data-testid="runs-view" aria-label="评测运行">
      <h2>评测运行</h2>
      <p>配置、运行列表与逐题结果由后续切片接入。</p>
    </section>
  );
}
