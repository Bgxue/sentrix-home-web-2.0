/**
 * 「QA 数据集浏览」工具栏。逐行移植 1.0 `App.vue:3686-3700`：
 *
 *   - 相册下拉（`:3687-3689`）：选项文案 `相册名 · N人 / M图 [/ K视频]`；切换相册时把题集
 *     重置为新相册的第一个 qa_set，缺省 `compact-10q`（`:3687` 的 `@change`）；
 *   - 题集下拉（`:3690-3692`）、搜索框（`:3693`）、标签下拉（`:3694`）、刷新按钮（`:3695`）；
 *   - 错误提示（`:3697`）与 `media_resolution` 来源提示（`:3698-3700`）。后者只在
 *     `status !== 'no_media'` 时显示——`no_media` 与 `null` 都不显示。
 *
 * `albumCountLabel` 是 1.0 `:224-228` 的局部函数，此处同样保持局部（本切片不新增 lib 模块）。
 */
import type { ManifestSummary, MediaResolution } from "../../api/types.ts";

export interface QaBrowserToolbarProps {
  manifests: ManifestSummary[];
  album: string;
  setAlbum: (album: string) => void;
  qaSets: string[];
  qaSet: string;
  setQaSet: (qaSet: string) => void;
  search: string;
  setSearch: (search: string) => void;
  tags: string[];
  tag: string;
  setTag: (tag: string) => void;
  error: string;
  mediaResolution: MediaResolution | null;
  onRefresh: () => void;
}

/** 1.0 `:224-228`。视频数为 0 时不追加 `/ 0视频`。 */
function albumCountLabel(manifest: ManifestSummary): string {
  const videos = Number(manifest?.video_count || 0);
  const base = `${manifest.face_count}人 / ${manifest.photo_count}图`;
  return videos ? `${base} / ${videos}视频` : base;
}

export function QaBrowserToolbar({
  manifests,
  album,
  setAlbum,
  qaSets,
  qaSet,
  setQaSet,
  search,
  setSearch,
  tags,
  tag,
  setTag,
  error,
  mediaResolution,
  onRefresh,
}: QaBrowserToolbarProps) {
  // 1.0 `:3687` 的 `@change`：相册变了，题集跟着落到**新相册**的第一个 qa_set。
  // React 里 `qaBrowserOptions` 仍是旧相册的，所以从 `manifests` 现查新相册的 qa_sets。
  const handleAlbumChange = (nextAlbum: string) => {
    const sets = manifests.find((m) => m.album_id === nextAlbum)?.qa_sets || [];
    setAlbum(nextAlbum);
    setQaSet(sets[0] || "compact-10q");
  };

  return (
    <>
      <div className="qa-browser-toolbar">
        <label>
          <span>相册</span>
          <select value={album} onChange={(event) => handleAlbumChange(event.target.value)}>
            {manifests.map((manifest) => (
              <option key={manifest.album_id} value={manifest.album_id}>
                {manifest.album_name} · {albumCountLabel(manifest)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>QA 数据集</span>
          <select value={qaSet} onChange={(event) => setQaSet(event.target.value)}>
            {qaSets.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>搜索</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            type="search"
            placeholder="QA ID、问题或答案"
          />
        </label>
        <label>
          <span>标签</span>
          <select value={tag} onChange={(event) => setTag(event.target.value)}>
            <option value="">全部标签</option>
            {tags.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <button className="btn ghost" onClick={onRefresh}>
          刷新数据
        </button>
      </div>
      {error ? (
        <p className="error">{error}</p>
      ) : mediaResolution && mediaResolution.status !== "no_media" ? (
        <p className="muted small qa-media-source-status">
          媒体来源：Sentrix 后端 · 已解析 {mediaResolution.resolved_count || 0} · 缺失 {mediaResolution.missing_count || 0} · 不唯一 {mediaResolution.ambiguous_count || 0}
        </p>
      ) : null}
    </>
  );
}
