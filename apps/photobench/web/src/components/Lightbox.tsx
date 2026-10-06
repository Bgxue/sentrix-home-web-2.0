/**
 * 共享图片灯箱。1.0 里它是 **app 根级的单例元素**（`App.vue:3767-3772`）——runs 的逐题结果
 * （`openImage` `:2194`）与 QA 数据集浏览（`:3740`）两个视图都写同一个 `lightbox.value`。
 *
 * 2.0 把状态提到组合层（`App.tsx`），两个视图各自通过 `onOpenMedia` 回调上抛点击目标。
 * 标记与 class 逐字照搬 1.0：
 *
 *   <div v-if="lightbox" class="lightbox" @click="lightbox = null">
 *     <div><img :src="lightbox.url" :alt="lightbox.name" /><p>{{ lightbox.name }}</p></div>
 *   </div>
 */
export interface LightboxTarget {
  url: string;
  name: string;
}

export interface LightboxProps {
  /** 当前要展示的图片；`null` 表示关闭（1.0 的 `v-if="lightbox"`）。 */
  target: LightboxTarget | null;
  /** 点击遮罩关闭（1.0 `@click="lightbox = null"`）。 */
  onClose: () => void;
}

export function Lightbox({ target, onClose }: LightboxProps) {
  if (!target) return null;
  return (
    <div className="lightbox" onClick={onClose}>
      <div>
        <img src={target.url} alt={target.name} />
        <p>{target.name}</p>
      </div>
    </div>
  );
}
