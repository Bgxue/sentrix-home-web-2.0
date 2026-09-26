import { useEffect, useRef, useState, type FormEvent } from "react";

import { deliveredPhotoUrl } from "../../shared/api/client";
import { useTurnStream } from "./useTurnStream";

interface Exchange {
  question: string;
  answer: string;
  attachments: { handle: string; asset_id: string; file_name: string }[];
  status: string;
  reason: string;
}

/**
 * 助手对话。问一句「给我看看在海边的照片」，回答下面就是能直接点开的照片。
 *
 * 交付的照片只来自后端 ``complete`` 帧里的 ``attachments``——那是**模型显式调用
 * get_original_photos 交出去的那些**。前端不自己从候选里挑图，也不做任何相关性判断。
 */
export function AssistantPanel({ conversationId }: { conversationId: string | null }) {
  // 面板**不收 scopeId**：会话的创建与切换在 AssistantPage，scope 是那一层的事。
  // 收一个自己不用的 prop 会让人以为这里也参与范围决策。
  const [text, setText] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const { progress, result, busy, error, ask } = useTurnStream();
  const settled = useRef<string | null>(null);

  // 换会话就清屏。回合是落库的，历史在服务端，重新进入时由上层决定要不要回放。
  useEffect(() => {
    setExchanges([]);
    settled.current = null;
  }, [conversationId]);

  // ``complete`` 到了就把答案补到最后一条提问上——回合是先落库再执行的，
  // 提问在发出去的那一刻就已经显示出来了。
  useEffect(() => {
    if (result === null || settled.current === result.turn_id) {
      return;
    }
    settled.current = result.turn_id;
    setExchanges((previous) =>
      previous.map((item, index) =>
        index === previous.length - 1
          ? {
              ...item,
              answer: result.answer,
              attachments: result.attachments,
              status: result.status,
              reason: result.termination_reason,
            }
          : item,
      ),
    );
  }, [result]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const question = text.trim();
    if (question === "" || conversationId === null || busy) {
      return;
    }
    setText("");
    setExchanges((previous) => [
      ...previous,
      { question, answer: "", attachments: [], status: "running", reason: "" },
    ]);
    await ask(conversationId, question);
  }

  return (
    <section className="assistant">
      <h2>问一句</h2>
      <form className="assistant-ask" onSubmit={submit}>
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="例如：给我看看在海边的照片"
          aria-label="向助手提问"
          disabled={conversationId === null}
        />
        <button type="submit" disabled={busy || conversationId === null || text.trim() === ""}>
          {busy ? "处理中…" : "发送"}
        </button>
      </form>

      {error ? <p className="error">{error}</p> : null}

      {busy && progress.length > 0 ? (
        <ol className="assistant-progress">
          {progress.map((item) => (
            <li key={`${item.step_index}-${item.kind}`} className={`stage-${item.stage}`}>
              {item.text}
            </li>
          ))}
        </ol>
      ) : null}

      <div className="assistant-log">
        {exchanges.map((exchange, index) => (
          <article key={index} className="assistant-exchange">
            {exchange.question ? <p className="question">{exchange.question}</p> : null}
            {exchange.answer ? <p className="answer">{exchange.answer}</p> : null}
            {exchange.status !== "running" && exchange.status !== "complete" ? (
              <p className="note">
                这一轮的状态：{exchange.status}（{exchange.reason}）
              </p>
            ) : null}
            {exchange.attachments.length > 0 ? (
              <ul className="assistant-photos">
                {exchange.attachments.map((photo) => (
                  <li key={photo.handle}>
                    <a
                      href={deliveredPhotoUrl(photo.asset_id, photo.file_name)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <img
                        src={deliveredPhotoUrl(photo.asset_id, photo.file_name)}
                        alt={photo.file_name}
                        loading="lazy"
                      />
                    </a>
                  </li>
                ))}
              </ul>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}
