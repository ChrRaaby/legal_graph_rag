// Kredsløbet — the circuit. Layout is generated from the ACTUAL tool list
// (runtime truth, so it can never show stale wiring), and its hot-state is a
// pure function of (event_log, t): the same map is an idle diagram, a live
// trace, and a replay frame.

import type { AgentEvent, ToolInfo } from "./api";
import { toolLabel, spansFromLog, liveMaxMs } from "./events";

export interface CircuitNode {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  sub?: string;
  cls: "" | "agent" | "tool" | "db";
  cyl?: boolean;
}
export interface CircuitEdge {
  id: string;
  d: string; // svg path
}
export interface Circuit {
  nodes: CircuitNode[];
  edges: CircuitEdge[];
  width: number;
  height: number;
  shownTools: Set<string>; // tool names with their own node; the rest fold into __more__
}

const H = 400;
const W = 760;
const MAX_SHOWN = 7;

const rightMid = (n: CircuitNode): [number, number] => [n.x + n.w / 2, n.y];
const leftMid = (n: CircuitNode): [number, number] => [n.x - n.w / 2, n.y];
const bez = ([x1, y1]: [number, number], [x2, y2]: [number, number]): string => {
  const mx = (x1 + x2) / 2;
  return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
};

export function buildCircuit(tools: ToolInfo[]): Circuit {
  const shown = tools.slice(0, MAX_SHOWN);
  const hasMore = tools.length > MAX_SHOWN;
  const rackIds = [...shown.map((t) => t.name), ...(hasMore ? ["__more__"] : [])];

  const nodes: CircuitNode[] = [
    { id: "user", x: 64, y: H / 2, w: 86, h: 46, label: "Bruger", cls: "" },
    { id: "agent", x: 250, y: H / 2, w: 120, h: 60, label: "Agent", sub: "LangGraph · ReAct", cls: "agent" },
    { id: "db", x: 690, y: 150, w: 110, h: 66, label: "Neo4j Aura", sub: "vidensgraf", cls: "db", cyl: true },
    { id: "emb", x: 690, y: 300, w: 110, h: 44, label: "e5-large", sub: "embeddings", cls: "db" },
  ];

  const rackX = 480;
  const rackW = 150;
  const rackH = 30;
  const rows = rackIds.length;
  const top = 40;
  const bottom = H - 40;
  const step = rows > 1 ? (bottom - top) / (rows - 1) : 0;
  rackIds.forEach((id, i) => {
    const y = rows > 1 ? top + i * step : H / 2;
    const label =
      id === "__more__" ? `+${tools.length - MAX_SHOWN} flere værktøjer` : toolLabel(id);
    nodes.push({ id, x: rackX, y, w: rackW, h: rackH, label, cls: "tool" });
  });

  const byId: Record<string, CircuitNode> = {};
  nodes.forEach((n) => (byId[n.id] = n));

  const edges: CircuitEdge[] = [
    { id: "u-a", d: bez(rightMid(byId.user), leftMid(byId.agent)) },
    ...rackIds.map((id) => ({ id: `a-${id}`, d: bez(rightMid(byId.agent), leftMid(byId[id])) })),
    { id: "t-db", d: bez([rackX + rackW / 2, H / 2], leftMid(byId.db)) },
    { id: "emb-db", d: `M${byId.emb.x},${byId.emb.y - byId.emb.h / 2} L${byId.db.x},${byId.db.y + byId.db.h / 2}` },
  ];

  return { nodes, edges, width: W, height: H, shownTools: new Set(shown.map((t) => t.name)) };
}

/** Which node/edge ids are "hot" at time t. During a live run, if t has run past
 *  the last recorded event, the agent is treated as thinking (it emits its llm
 *  event only after the turn completes, so there is no event yet to light it). */
export function circuitOn(
  log: AgentEvent[],
  tMs: number,
  circuit: Circuit,
  liveNowMs: number | null,
): Set<string> {
  const on = new Set<string>();
  const spans = spansFromLog(log);

  let agentOn = spans.some((s) => s.kind === "llm" && tMs >= s.t0 && tMs < s.t1);
  if (liveNowMs != null && tMs > liveMaxMs(log) + 150 && log.length > 0) agentOn = true;

  // Active tool at t → light its rack node (or the collapsed __more__ node), the
  // agent→tool edge, and the graph/embeddings path. Tool windows are paired
  // call→result by name (spansFromLog drops the raw name we need for mapping).
  let activeToolNode: string | null = null;
  const callTimes: Record<string, number[]> = {};
  for (const ev of log) {
    if (ev.type === "tool_call") {
      (callTimes[ev.tool_name] ??= []).push(ev.elapsed_s * 1000);
    } else if (ev.type === "tool_result") {
      const t0 = callTimes[ev.tool_name]?.shift() ?? ev.elapsed_s * 1000;
      const t1 = ev.elapsed_s * 1000;
      if (tMs >= t0 && tMs < t1) {
        activeToolNode = circuit.shownTools.has(ev.tool_name) ? ev.tool_name : "__more__";
      }
    }
  }

  if (agentOn || activeToolNode) on.add("agent");
  if (activeToolNode) {
    on.add(activeToolNode);
    on.add(`a-${activeToolNode}`);
    on.add("t-db");
    on.add("db");
    on.add("emb");
    on.add("emb-db");
  } else if (agentOn) {
    on.add("u-a");
  }
  return on;
}
