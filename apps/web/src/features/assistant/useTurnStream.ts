import { useCallback, useEffect, useRef, useState } from "react";

import {
  startTurn,
  turnEventsUrl,
  type AssistantCompleteEvent,
  type AssistantProgressEvent,
} from "../../shared/api/client";

export interface TurnStream {
  progress: AssistantProgressEvent[];
  result: AssistantCompleteEvent | null;
  busy: boolean;
  error: string | null;
  ask: (conversationId: string, message: string) => Promise<void>;
}

/**
 * 一次提问的实时过程：``POST`` 建回合，然后**一条** SSE 连接读到底。
 *
 * 两处刻意的选择：
 *
 * 1. **不在 ``onerror`` 里降级成轮询。** 1.0 就是这么做的——``es.onerror`` 一旦触发，
 *    EventSource 永久让位给 700ms 轮询，于是轮询成了主路径（668,490 次请求）。
 *    这里连接断了就如实报错，用户重发即可；回合本身已经落库，重连还能续上。
 * 2. **``complete`` 到达后显式 close。** 服务端发完那一帧就关流，浏览器会把它当成
 *    「连接断开」触发 ``onerror``；不先标记完成的话，一个正常的回合会被显示成错误。
 */
export function useTurnStream(): TurnStream {
  const [progress, setProgress] = useState<AssistantProgressEvent[]>([]);
  const [result, setResult] = useState<AssistantCompleteEvent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const finishedRef = useRef(false);

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  useEffect(() => close, [close]);

  const ask = useCallback(
    async (conversationId: string, message: string) => {
      close();
      finishedRef.current = false;
      setProgress([]);
      setResult(null);
      setError(null);
      setBusy(true);

      let turnId: string;
      try {
        const started = await startTurn(conversationId, message);
        turnId = started.turn_id;
      } catch (cause) {
        setError((cause as Error).message);
        setBusy(false);
        return;
      }

      const source = new EventSource(turnEventsUrl(turnId));
      sourceRef.current = source;

      source.addEventListener("progress", (event) => {
        const payload = JSON.parse((event as MessageEvent<string>).data) as AssistantProgressEvent;
        setProgress((previous) => [...previous, payload]);
      });

      source.addEventListener("complete", (event) => {
        const payload = JSON.parse((event as MessageEvent<string>).data) as AssistantCompleteEvent;
        finishedRef.current = true;
        setResult(payload);
        setBusy(false);
        close();
      });

      source.onerror = () => {
        if (finishedRef.current) {
          return;
        }
        setError("实时连接中断了，请重新发送这条消息。");
        setBusy(false);
        close();
      };
    },
    [close],
  );

  return { progress, result, busy, error, ask };
}
