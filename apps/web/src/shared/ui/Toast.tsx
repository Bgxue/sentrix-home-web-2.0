/**
 * 轻量 Toast。对应 1.0 `src/app.js:1126-1132` 的 `showToast()`：右下角一条，
 * 2.6 秒后自行消失。
 *
 * **不引依赖。** 需要的只是「一条消息 + 一个计时器」，引一个 toast 库会把它变成
 * 额外的运行时与样式覆盖问题。1.0 是一条覆盖式提示；2.0 允许多条堆叠，因为
 * 批量导入可能同时完成多件事，后来的不该把前一条直接吞掉。
 *
 * Provider 挂在 `App.tsx` 最外层：壳层（相册切换失败）、导入页、设置页都要用，
 * 放在页面里就得每页各挂一个，提示会跟着页面卸载一起消失。
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import "./Toast.css";

export type ToastTone = "success" | "error" | "info";

interface ToastItem {
  readonly id: number;
  readonly message: string;
  readonly tone: ToastTone;
}

interface ToastApi {
  /** `tone` 默认 success；失败路径传 `"error"`，提示不会显示成绿色对勾。 */
  readonly notify: (message: string, tone?: ToastTone) => void;
  readonly dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** 1.0 `showToast` 的 2600ms，一个数不差。 */
export const TOAST_DURATION_MS = 2600;

const TONE_MARK: Record<ToastTone, string> = {
  success: "✓",
  error: "×",
  info: "i",
};

export function ToastProvider({ children }: { readonly children: ReactNode }) {
  const [items, setItems] = useState<readonly ToastItem[]>([]);
  const nextId = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const notify = useCallback(
    (message: string, tone: ToastTone = "success") => {
      // 空消息不产生一条空壳提示（调用方拿到空错误串时会走到这里）。
      if (message.trim() === "") return;
      const id = nextId.current++;
      setItems((current) => [...current, { id, message, tone }]);
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), TOAST_DURATION_MS),
      );
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => {
      // 卸载时把计时器全部收掉：组件没了还留一个 setTimeout 去 setState 是
      // React 里最典型的一类「已经离开的组件又活了一下」。
      for (const timer of pending.values()) clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const value = useMemo<ToastApi>(() => ({ notify, dismiss }), [notify, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="false">
        {items.map((item) => (
          <div key={item.id} className={`toast toast-${item.tone}`} role="status">
            <span aria-hidden="true">{TONE_MARK[item.tone]}</span>
            <span>{item.message}</span>
            <button
              type="button"
              className="toast-dismiss"
              aria-label="关闭提示"
              onClick={() => dismiss(item.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const value = useContext(ToastContext);
  if (value === null) throw new Error("useToast 必须在 ToastProvider 内使用");
  return value;
}
