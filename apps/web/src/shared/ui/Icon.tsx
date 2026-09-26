/** 字符图标。直译 1.0 `src/app.js:93` 的 `icon(value, extra)`——1.0 用的就是文本字符。 */

export function Icon({ value, extra = "" }: { readonly value: string; readonly extra?: string }) {
  return (
    <span className={extra ? `icon ${extra}` : "icon"} aria-hidden="true">
      {value}
    </span>
  );
}
