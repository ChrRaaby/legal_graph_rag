import { useState, type KeyboardEvent } from "react";
import type { ChatMessage } from "../lib/api";
import type { RunPhase } from "../lib/useAgentRun";

interface Props {
  messages: ChatMessage[];
  phase: RunPhase;
  error: string | null;
  onSend: (q: string) => void;
  showReveal: boolean;
  onReveal?: () => void;
}

const GREETING =
  "Stil mig spørgsmål om dansk skattelovgivning — regler, paragraffer og sammenhænge mellem love som Personskatteloven, Ligningsloven og Momsloven.";

export default function Chat({ messages, phase, error, onSend, showReveal, onReveal }: Props) {
  const [draft, setDraft] = useState("");
  const live = phase === "live";
  const awaitingAnswer = live && messages[messages.length - 1]?.role === "user";

  const send = () => {
    const q = draft.trim();
    if (!q || live) return;
    setDraft("");
    onSend(q);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <section className="panel" aria-label="Samtale">
      <div className="panel-h">Samtale</div>
      <div className="chat-body">
        {messages.length === 0 && <div className="msg agent">{GREETING}</div>}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role === "user" ? "user" : "agent"}`}>
            {m.content}
          </div>
        ))}
        {awaitingAnswer && (
          <div className="thinking-line">
            <span className="dot" />
            Agenten arbejder …
          </div>
        )}
        {error && <div className="err">Fejl: {error}</div>}
        {showReveal && onReveal && (
          <button className="reveal-btn" onClick={onReveal}>
            Se, hvordan jeg fandt svaret →
          </button>
        )}
      </div>
      <div className="chat-foot">
        <div className="composer">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            placeholder="Stil et spørgsmål om dansk skattelovgivning …"
            rows={1}
            disabled={live}
          />
          <button className="send" onClick={send} disabled={live || !draft.trim()}>
            Send
          </button>
        </div>
        <div className="disclaimer">
          Svar er vejledende og genereret af en AI ud fra lovtekster — ikke juridisk rådgivning.
        </div>
      </div>
    </section>
  );
}
