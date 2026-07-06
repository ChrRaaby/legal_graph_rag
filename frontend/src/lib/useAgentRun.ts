import { useCallback, useRef, useState } from "react";
import { streamAsk, type AgentEvent, type ChatMessage } from "./api";

export type RunPhase = "idle" | "live" | "replay";

export interface AgentRun {
  messages: ChatMessage[];
  log: AgentEvent[];
  phase: RunPhase;
  error: string | null;
  ask: (question: string) => Promise<void>;
}

/** Owns the conversation, the current run's event log, and the run phase.
 *  The log is the single source the visual layers replay from. */
export function useAgentRun(): AgentRun {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [log, setLog] = useState<AgentEvent[]>([]);
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || phase === "live") return;
      const history: ChatMessage[] = [...messages, { role: "user", content: q }];
      setMessages(history);
      setLog([]);
      setError(null);
      setPhase("live");

      const controller = new AbortController();
      controllerRef.current = controller;
      try {
        await streamAsk(
          history,
          (ev) => {
            switch (ev.type) {
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

  return { messages, log, phase, error, ask };
}
