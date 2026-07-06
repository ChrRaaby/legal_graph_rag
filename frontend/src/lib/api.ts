// Backend API: runtime-truth architecture + the SSE run stream. /api/ask is a
// POST (it carries the conversation), so we read the event stream with fetch +
// a ReadableStream reader rather than EventSource (which is GET-only).

import type { AgentEvent } from "./events";
export type { AgentEvent } from "./events";

export interface ToolInfo {
  name: string;
  description: string;
}
export interface Architecture {
  app_mode: "user" | "dev";
  provider: string;
  graph_stats: { legislation: number; sections: number; error?: string };
  tools: ToolInfo[];
}
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export async function fetchArchitecture(): Promise<Architecture> {
  const res = await fetch("/api/architecture");
  if (!res.ok) throw new Error(`architecture ${res.status}`);
  return (await res.json()) as Architecture;
}

/** POST a conversation and invoke `onEvent` for each SSE event as it arrives. */
export async function streamAsk(
  messages: ChatMessage[],
  onEvent: (ev: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`ask ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE messages are separated by a blank line.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const json = line.slice(5).trim();
        if (!json) continue;
        try {
          onEvent(JSON.parse(json) as AgentEvent);
        } catch {
          // ignore a malformed frame rather than kill the stream
        }
      }
    }
  }
}
