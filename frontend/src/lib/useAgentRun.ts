import { useCallback, useRef, useState } from "react";
import { streamAsk, type AgentEvent, type ChatMessage, type SavedRun } from "./api";
import type { Citation } from "./events";

export type RunPhase = "idle" | "live" | "replay";

export interface AgentRun {
  messages: ChatMessage[];
  log: AgentEvent[];
  phase: RunPhase;
  error: string | null;
  runId: string | null;
  citations: Citation[];
  ask: (question: string) => Promise<void>;
  loadTrace: (saved: SavedRun) => void;
}

/** Owns the conversation, the current run's event log, run id + citations, and
 *  the run phase. The log is the single source the visual layers replay from —
 *  a live run and a loaded historical trace produce the same shape. */
export function useAgentRun(): AgentRun {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [log, setLog] = useState<AgentEvent[]>([]);
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const controllerRef = useRef<AbortController | null>(null);

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || phase === "live") return;
      const history: ChatMessage[] = [...messages, { role: "user", content: q }];
      setMessages(history);
      setLog([]);
      setError(null);
      setCitations([]);
      setRunId(null);
      setPhase("live");

      const controller = new AbortController();
      controllerRef.current = controller;
      try {
        await streamAsk(
          history,
          (ev) => {
            switch (ev.type) {
              case "run_start":
                if (ev.run_id) setRunId(ev.run_id);
                setLog((l) => [...l, ev]);
                break;
              case "citations":
                setCitations(ev.items);
                break;
              case "answer":
                setMessages((m) => [...m, { role: "assistant", content: ev.text }]);
                break;
              case "error":
                setError(ev.message);
                break;
              case "done":
                setLog((l) => [...l, ev]);
                setPhase("replay");
                break;
              default:
                setLog((l) => [...l, ev]);
            }
          },
          controller.signal,
        );
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setError(String(e));
          setPhase("replay");
        }
      }
    },
    [messages, phase],
  );

  const loadTrace = useCallback((saved: SavedRun) => {
    setMessages([
      { role: "user", content: saved.question },
      { role: "assistant", content: saved.answer },
    ]);
    setLog(saved.events);
    setCitations(saved.citations || []);
    setRunId(saved.run_id);
    setError(null);
    setPhase("replay");
  }, []);

  return { messages, log, phase, error, runId, citations, ask, loadTrace };
}
