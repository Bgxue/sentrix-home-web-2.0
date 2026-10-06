/**
 * 模型服务配置面板。逐行移植 1.0 `App.vue:2913-2979` 的
 * `<div class="config-group config-group-model">` 整块。
 *
 * **换模型的语义在这里被重写（用户 2026-10-05 裁决③）**：1.0 的
 * `POST /api/model-profiles/bind-runtime` 注释写着 "no restart"，而 2.0 的
 * `vllm_base_url` 是启动时从 Settings 读死的，换模型 = **重启 2.0**
 * （`docs/refactor/s9-deviations.md` §7.3）。`useModelEndpoint` 只提供**纯查询**
 * （`/current-model`、`/test-model`、`/profiles`，S10a 确认无副作用），没有「切换
 * 端点」这个动作。所以这里：
 *
 *   - **保留查询与探活**：popover（当前模型快照）、可用模型下拉、`测试 POST`、
 *     模型注册表扫描，全部照搬 1.0（它们本来就不改运行态）；
 *   - **popover 明确定位成只读**：不再暗示「点一下就把运行中的模型换掉」；
 *   - **把代价写清**：模型服务地址在 2.0 启动时绑定，改地址不会热切换，启动评测时
 *     会用它重启 2.0，重启失败会中止本次评测。这条提示是新增文案（1.0 没有对应
 *     概念），其余文案逐字照搬。
 */
import type { useModelEndpoint } from "../../hooks/useModelEndpoint.ts";

type ModelApi = ReturnType<typeof useModelEndpoint>;

/** `config.cloud_model_profile`（1.0 `:17-20`）；缺省时由调用方给 1.0 的兜底对象。 */
export interface CloudModelProfile {
  id: string;
  label?: string;
  model?: string;
  available?: boolean;
  source?: string;
  [key: string]: unknown;
}

export type ModelServicePanelProps = Pick<
  ModelApi,
  | "profiles"
  | "vllmManagerUrl"
  | "modelEndpoint"
  | "selectedEndpointModel"
  | "endpointModels"
  | "currentModelInfo"
  | "currentModelLoading"
  | "currentModelError"
  | "currentModelPopoverOpen"
  | "modelTestState"
  | "modelTestMessage"
  | "setVllmManagerUrl"
  | "setModelEndpoint"
  | "setSelectedEndpointModel"
  | "setCurrentModelPopoverOpen"
  | "loadProfiles"
  | "loadCurrentModel"
  | "onEndpointModelChange"
  | "testEndpointModel"
  | "onModelManagerInput"
  | "onModelEndpointInput"
  | "applySavedConnection"
> & {
  /** 1.0 `selectedModels`（勾选集合）；这里以 `new Set(...)` 写回。 */
  selectedModels: ReadonlySet<string>;
  setSelectedModels: (next: Set<string>) => void;
  cloudModelProfile: CloudModelProfile;
};

/**
 * `currentModelInfo.state` 里被 popover 读到的四个键（1.0 `:2932-2934`）。
 * 用 `type` 而非 `interface`：类型别名能隐式满足 `Record<string, unknown>`，
 * 从 `state` 断言过来才不会被 TS 判成「不相关的转换」。
 */
type CurrentModelState = {
  profile?: string;
  max_model_len?: number;
  max_num_seqs?: number;
  dtype?: string;
};

/**
 * 1.0 `:335-348` 的 `setModelSelected`。1.0 就地改 `reactive` Set；React 里必须换新
 * Set 才会重渲染（`useModelEndpoint` 的注释也这么要求）。
 * 语义：勾 `__current__` 会清掉其余勾选；勾任何其它模型会取消 `__current__`。
 */
function setModelSelected(
  selected: ReadonlySet<string>,
  modelId: string,
  checked: boolean,
): Set<string> {
  const next = new Set(selected);
  if (checked && modelId === "__current__") {
    next.clear();
    next.add(modelId);
  } else if (checked) {
    next.delete("__current__");
    next.add(modelId);
  } else {
    next.delete(modelId);
  }
  return next;
}

export function ModelServicePanel(props: ModelServicePanelProps) {
  const {
    profiles,
    vllmManagerUrl,
    modelEndpoint,
    selectedEndpointModel,
    endpointModels,
    currentModelInfo,
    currentModelLoading,
    currentModelError,
    currentModelPopoverOpen,
    modelTestState,
    modelTestMessage,
    setVllmManagerUrl,
    setModelEndpoint,
    setSelectedEndpointModel,
    setCurrentModelPopoverOpen,
    loadProfiles,
    loadCurrentModel,
    onEndpointModelChange,
    testEndpointModel,
    onModelManagerInput,
    onModelEndpointInput,
    selectedModels,
    setSelectedModels,
    cloudModelProfile,
  } = props;

  const state = (currentModelInfo?.state || {}) as CurrentModelState;

  return (
    <div className="config-group config-group-model">
      <div className="config-group-head">
        <div>
          <strong>模型服务</strong>
          <span>模型服务地址必填；模型管理器仅用于扫描注册表和切换模型</span>
        </div>
      </div>
      <div className="config-model-endpoint">
        <label>
          模型服务地址
          <div className="endpoint-line">
            <input
              type="text"
              value={modelEndpoint}
              placeholder="例如 127.0.0.1:8100 或 http://127.0.0.1:8100/v1"
              onChange={(event) => {
                setModelEndpoint(event.target.value);
                onModelEndpointInput();
              }}
            />
            <div className="current-model-control">
              <button
                className={`current-model-trigger${currentModelPopoverOpen ? " active" : ""}`}
                type="button"
                disabled={currentModelLoading}
                onClick={() => {
                  if (currentModelInfo) setCurrentModelPopoverOpen(!currentModelPopoverOpen);
                  else void loadCurrentModel();
                }}
              >
                <span className="current-model-icon">{currentModelLoading ? "…" : "↗"}</span>
                <span>
                  {currentModelLoading
                    ? "正在读取"
                    : currentModelInfo
                      ? "可用模型"
                      : "获取模型列表"}
                </span>
                <span className="current-model-chevron">{currentModelPopoverOpen ? "⌃" : "⌄"}</span>
              </button>
              {currentModelInfo && currentModelPopoverOpen && (
                <div className="current-model-popover" role="status" aria-live="polite">
                  <span className="popover-arrow"></span>
                  <div className="current-model-popover-head">
                    <span>MODEL ENDPOINT</span>
                    <button
                      type="button"
                      aria-label="关闭"
                      onClick={() => setCurrentModelPopoverOpen(false)}
                    >
                      ×
                    </button>
                  </div>
                  <strong>
                    {currentModelInfo.served_model_name ||
                      `${(currentModelInfo.served_models || []).length} 个模型待选择`}
                  </strong>
                  <div className="current-model-status">
                    <i></i>
                    {currentModelInfo.manager_available
                      ? "已读取 Manager 当前运行状态"
                      : "已连接 OpenAI-compatible 端点"}
                  </div>
                  <dl>
                    <div>
                      <dt>模型服务</dt>
                      <dd>{currentModelInfo.model_base_url}</dd>
                    </div>
                    {state.profile && (
                      <div>
                        <dt>Profile</dt>
                        <dd>{state.profile}</dd>
                      </div>
                    )}
                    {state.max_model_len && (
                      <div>
                        <dt>上下文 / 并发</dt>
                        <dd>
                          {state.max_model_len} / {state.max_num_seqs || "-"}
                        </dd>
                      </div>
                    )}
                    {state.dtype && (
                      <div>
                        <dt>精度</dt>
                        <dd>{state.dtype}</dd>
                      </div>
                    )}
                  </dl>
                  <span className="config-help">
                    该模型为只读状态；更换模型服务地址需重启 2.0，不会热切换。
                  </span>
                  <button className="popover-refresh" type="button" onClick={() => void loadCurrentModel()}>
                    重新获取
                  </button>
                </div>
              )}
            </div>
          </div>
          <span className="config-help">
            填写模型服务的 IP:端口；可带或不带 /v1，实际请求会自动补齐。
          </span>
          <span className="config-help">
            模型服务地址在 2.0 启动时绑定：改地址不会热切换，启动评测时会用它重启 2.0；重启失败会中止本次评测。
          </span>
          {currentModelError && <span className="config-help error">{currentModelError}</span>}
        </label>
      </div>
      <div className="config-model-manager">
        <label>
          模型管理器地址（可选）
          <input
            type="text"
            value={vllmManagerUrl}
            placeholder="例如 127.0.0.1:8500；无管理器可留空"
            onChange={(event) => {
              setVllmManagerUrl(event.target.value);
              onModelManagerInput();
            }}
            onBlur={() => void loadProfiles()}
          />
          <span className="config-help">
            填写后从 Manager 的模型注册表自动扫描；留空时不显示普通模型选择。
          </span>
        </label>
        <button
          className="btn ghost compact model-registry-refresh"
          type="button"
          disabled={!vllmManagerUrl.trim()}
          onClick={() => void loadProfiles()}
        >
          刷新模型注册表
        </button>
      </div>
      <div className="model-current-choice">
        <label className="endpoint-model-select">
          可用模型
          <select
            value={selectedEndpointModel}
            disabled={!endpointModels.length}
            onChange={(event) => {
              setSelectedEndpointModel(event.target.value);
              void onEndpointModelChange(event.target.value);
            }}
          >
            <option value="">{endpointModels.length ? "请选择模型" : "先获取模型列表"}</option>
            {endpointModels.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </select>
        </label>
        <button
          className="btn ghost compact endpoint-test-button"
          type="button"
          disabled={!selectedEndpointModel || modelTestState === "testing"}
          onClick={() => void testEndpointModel()}
        >
          {modelTestState === "testing" ? "测试中…" : "测试 POST"}
        </button>
        {modelTestMessage && (
          <span className={`model-test-feedback state-${modelTestState}`}>{modelTestMessage}</span>
        )}
        <label
          className={`check endpoint-reuse-check${selectedModels.has("__current__") ? " active" : ""}`}
        >
          <input
            type="checkbox"
            checked={selectedModels.has("__current__")}
            disabled={!selectedEndpointModel || !currentModelInfo?.served_model_name}
            onChange={(event) =>
              setSelectedModels(setModelSelected(selectedModels, "__current__", event.target.checked))
            }
          />
          复用所选模型
          {selectedEndpointModel && <span>（{selectedEndpointModel}，不启停）</span>}
        </label>
      </div>
      <div className="model-picker cloud-model-picker">
        <span className="field-label">云端模型</span>
        <label className={`check${selectedModels.has(cloudModelProfile.id) ? " active" : ""}`}>
          <input
            type="checkbox"
            checked={selectedModels.has(cloudModelProfile.id)}
            disabled={cloudModelProfile.available === false}
            onChange={(event) =>
              setSelectedModels(
                setModelSelected(selectedModels, cloudModelProfile.id, event.target.checked),
              )
            }
          />
          {cloudModelProfile.label || cloudModelProfile.id}
          <span>（{cloudModelProfile.model}）</span>
        </label>
        <span className="config-help">
          云端 API 直连；不启动/切换本地 Manager，不执行本地 token 预检和 GPU 指标采样。
        </span>
      </div>
      {vllmManagerUrl.trim() ? (
        <div className="model-picker">
          <span className="field-label">模型注册表（可多选，串行测试）</span>
          {profiles.map((profile) => (
            <label key={profile.id} className={`check${selectedModels.has(profile.id) ? " active" : ""}`}>
              <input
                type="checkbox"
                checked={selectedModels.has(profile.id)}
                disabled={!profile.available}
                onChange={(event) =>
                  setSelectedModels(setModelSelected(selectedModels, profile.id, event.target.checked))
                }
              />
              {profile.id}
              {profile.source === "cloud_api" && <span>（云端 API）</span>}
              {!profile.available && <span>（不可用）</span>}
            </label>
          ))}
          {!profiles.length && (
            <span className="config-help">尚未从模型管理器注册表加载模型，请点击“刷新模型注册表”。</span>
          )}
        </div>
      ) : (
        <div className="model-manager-empty">
          未配置模型管理器。Ollama、llama.cpp 等端点只会按请求使用上方选中的模型；测评服务不会自动启停或切换模型。
        </div>
      )}
    </div>
  );
}
