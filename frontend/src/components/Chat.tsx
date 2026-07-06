import { useState, type KeyboardEvent } from "react";
import type { ChatMessage } from "../lib/api";
import type { Citation } from "../lib/events";
import type { RunPhase } from "../lib/useAgentRun";

interface Props {
  messages: ChatMessage[];
  phase: RunPhase;
  error: string | null;
  citations: Citation[];
  onSend: (q: string) => void;
  onCiteClick: (c: Citation) => void;
  onFeedback: (verdict: "up" | "down") => void;
  showReveal: boolean;
  onReveal?: () => void;
}

const GREETING =
  "Stil mig spørgsmål om dansk skattelovgivning — regler, paragraffer og sammenhænge mellem love som Personskatteloven, Ligningsloven og Momsloven.";

export default function Chat({
  messages, phase, error, citations, onSend, onCiteClick, onFeedback, showReveal, onReveal,
}: Props) {
  const [draft, setDraft] = useState("");
  const [fb, setFb] = useState<"up" | "down" | null>(null);
  const live = phase === "live";
  const lastIsAssistant = messages[messages.length - 1]?.role === "assistant";
  const awaitingAnswer = live && messages[messages.length - 1]?.role === "user";

  const send = () => {
    const q = draft.trim();
    if (!q || live) return;
    setDraft("");
    setFb(null);
    onSend(q);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };
  const feedback = (v: "up" | "down") => {
    setFb(v);
    onFeedback(v);
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

        {/* Kilder + feedback under the current answer. */}
        {lastIsAssistant && !live && (citations.length > 0 || true) && (
          <div className="answer-tools">
            {citations.length > 0 && (
              <div className="kilder">
                {citations.map((c, i) => (
                  <button
                    key={i}
                    className={`kilde${c.verified ? " ok" : " warn"}`}
                    onClick={() => c.element_id && onCiteClick(c)}
                    disabled={!c.element_id}
                    title={c.verified ? "Verificeret i grafen — klik for at fremhæve" : "Ikke fundet i grafen"}
                  >
                    <span className="st" />
                    {c.label}
                    {c.uri && (
                      <a
                        href={c.uri}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="kilde-link"
                        aria-label="Åbn på retsinformation"
                      >↗</a>
                    )}
                  </button>
                ))}
              </div>
            )}
            <div className="fb-row">
              <span className="fb-label">Var svaret nyttigt?</span>
              <button className={`fb${fb === "up" ? " on" : ""}`} onClick={() => feedback("up")} aria-label="Nyttigt">👍</button>
              <button className={`fb${fb === "down" ? " on" : ""}`} onClick={() => feedback("down")} aria-label="Ikke nyttigt">👎</button>
              {fb && <span className="fb-thanks">Tak for din feedback.</span>}
            </div>
          </div>
        )}

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
