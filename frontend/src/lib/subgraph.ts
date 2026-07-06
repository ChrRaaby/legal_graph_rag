// Graflinsen layout: turn the backend subgraph into positioned nodes/edges, and
// compute per-node reveal times from the event log so scrubbing shows the graph
// build as retrieval happened. Rendered as Law + Section + CITES — stk detail
// lives in the node inspector (a real § can have >10 stk; nodes would clutter).

import type { AgentEvent, GraphRef } from "./events";
import type { Subgraph } from "./api";

const LAW_SHORT: [string, string][] = [
  ["ligningslov", "LL"], ["personskattelov", "PSL"], ["selskabsskattelov", "SEL"],
  ["kildeskattelov", "KSL"], ["merværdiafgiftslov", "ML"], ["momslov", "ML"],
  ["aktieavancebeskatningslov", "ABL"], ["kursgevinstlov", "KGL"],
  ["afskrivningslov", "AL"], ["fondsbeskatningslov", "FBL"], ["aktiesparekontolov", "ASKL"],
  ["boafgiftslov", "BAL"], ["statsskattelov", "SL"],
];
export function lawShort(title: string): string {
  const tl = (title || "").toLowerCase();
  for (const [stem, short] of LAW_SHORT) if (tl.includes(stem)) return short;
  return (title || "?").slice(0, 4);
}

/** Unique retrieved section refs from the whole log (drives the subgraph fetch). */
export function refsFromLog(log: AgentEvent[]): GraphRef[] {
  const seen = new Set<string>();
  const out: GraphRef[] = [];
  for (const ev of log) {
    if (ev.type === "tool_result" && ev.graph_refs) {
      for (const r of ev.graph_refs) {
        const k = `${r.uri}|${r.title}|${r.num}`;
        if (!seen.has(k)) {
          seen.add(k);
          out.push(r);
        }
      }
    }
  }
  return out;
}

export interface GNode {
  id: string;
  key: string;
  x: number;
  y: number;
  r: number;
  kind: "lov" | "section";
  label: string;
  retrieved: boolean;
  used: boolean;
  revealMs: number;
  element_id: string;
}
export interface GEdge {
  from: string;
  to: string;
  d: string;
  via: string | null;
  revealMs: number;
}
export interface GLayout {
  nodes: GNode[];
  edges: GEdge[];
  width: number;
  height: number;
}

const W = 760;
const H = 400;
const MAX_PER_LAW = 7;

// The "cable tray": CITES edges never cross the node band. Each edge drops from
// its source §, runs along its own lane in a horizontal channel below the band,
// and rises into the target §. Crossings of nodes/labels are impossible by
// construction; lanes are packed by interval-graph colouring.
const CHANNEL_TOP = 342;
const LANE_GAP = 9;
const MAX_LANES = 5;
const CORNER = 8; // rounded-corner radius of the drops/rises

function revealByKey(sub: Subgraph, log: AgentEvent[]): Map<string, number> {
  const reveal = new Map<string, number>();
  for (const ev of log) {
    if (ev.type !== "tool_result" || !ev.graph_refs) continue;
    const tms = ev.elapsed_s * 1000;
    for (const ref of ev.graph_refs) {
      const key = `${lawShort(ref.title)}|${ref.num.toUpperCase()}`;
      if (!reveal.has(key) || tms < reveal.get(key)!) reveal.set(key, tms);
    }
  }
  // Cited-only sections appear when the section that cites them appears.
  const keyById = new Map(sub.sections.map((s) => [s.id, s.key]));
  for (let pass = 0; pass < 3; pass++) {
    for (const c of sub.cites) {
      const fk = keyById.get(c.from);
      const tk = keyById.get(c.to);
      if (fk && tk && reveal.has(fk) && !reveal.has(tk)) reveal.set(tk, reveal.get(fk)!);
    }
  }
  return reveal;
}

export function layoutGraflinse(sub: Subgraph, log: AgentEvent[]): GLayout {
  const reveal = revealByKey(sub, log);
  const revealOf = (key: string) => reveal.get(key) ?? 0;

  // Laws with any retrieved section come first (left).
  const retrievedLawIds = new Set(sub.sections.filter((s) => s.retrieved).map((s) => s.law_id));
  const laws = [...sub.laws].sort((a, b) => {
    const ra = retrievedLawIds.has(a.id) ? 0 : 1;
    const rb = retrievedLawIds.has(b.id) ? 0 : 1;
    return ra - rb || a.short.localeCompare(b.short);
  });
  const N = Math.max(laws.length, 1);

  const nodes: GNode[] = [];
  const posById = new Map<string, GNode>();

  laws.forEach((law, i) => {
    const x = ((i + 1) * W) / (N + 1);
    const lawNode: GNode = {
      id: "law:" + law.id, key: "law:" + law.short, x, y: 44, r: 16,
      kind: "lov", label: law.short, retrieved: retrievedLawIds.has(law.id),
      used: false, revealMs: Infinity, element_id: law.id,
    };
    nodes.push(lawNode);
    posById.set(lawNode.id, lawNode);

    const secs = sub.sections
      .filter((s) => s.law_id === law.id)
      .sort((a, b) => (Number(b.used) - Number(a.used)) || (Number(b.retrieved) - Number(a.retrieved)))
      .slice(0, MAX_PER_LAW);
    const top = 112;
    const bottom = CHANNEL_TOP - 22; // keep the band clear of the cable tray
    const gap = secs.length > 1 ? Math.min(64, (bottom - top) / (secs.length - 1)) : 0;
    secs.forEach((s, j) => {
      const y = secs.length > 1 ? top + j * gap : (top + bottom) / 2;
      const node: GNode = {
        id: s.id, key: s.key, x, y, r: s.retrieved ? 14 : 11,
        kind: "section", label: `§ ${s.section_number}`,
        retrieved: s.retrieved, used: s.used, revealMs: revealOf(s.key), element_id: s.id,
      };
      nodes.push(node);
      posById.set(node.id, node);
      lawNode.revealMs = Math.min(lawNode.revealMs, node.revealMs);
    });
    if (lawNode.revealMs === Infinity) lawNode.revealMs = 0;
  });

  // Edges are trimmed to the circle boundary so no line runs through a node,
  // and rendered behind the (solid-filled) nodes.
  const edges: GEdge[] = [];
  for (const n of nodes) {
    if (n.kind !== "section") continue;
    const lawNode = nodes.find((m) => m.kind === "lov" && m.element_id === sectionLawId(sub, n.id));
    if (lawNode) {
      const [ax, ay] = trim(lawNode.x, lawNode.y, n.x, n.y, lawNode.r + 1);
      const [bx, by] = trim(n.x, n.y, lawNode.x, lawNode.y, n.r + 1);
      edges.push({
        from: lawNode.id, to: n.id, via: null, revealMs: n.revealMs,
        d: `M${ax},${ay} C${ax},${(ay + by) / 2} ${bx},${(ay + by) / 2} ${bx},${by}`,
      });
    }
  }
  // CITES edges via the cable tray. Lane assignment = greedy interval colouring:
  // an edge takes the first lane whose existing intervals don't overlap its
  // horizontal span, so co-lane edges can never touch.
  const lanes: Array<Array<[number, number]>> = [];
  const cites = sub.cites
    .map((c) => ({ c, a: posById.get(c.from), b: posById.get(c.to) }))
    .filter((e): e is { c: (typeof sub.cites)[0]; a: GNode; b: GNode } => !!e.a && !!e.b && e.a.id !== e.b.id)
    .sort((e1, e2) => Math.abs(e2.a.x - e2.b.x) - Math.abs(e1.a.x - e1.b.x)); // long spans first → inner lanes

  for (const { c, a, b } of cites) {
    // Same-column edges get a lateral offset so the drop and rise don't coincide.
    const sameCol = Math.abs(a.x - b.x) < 1;
    const sx = sameCol ? a.x - 5 : a.x;
    const tx = sameCol ? b.x + 5 : b.x;
    const lo = Math.min(sx, tx) - CORNER - 4;
    const hi = Math.max(sx, tx) + CORNER + 4;

    let lane = lanes.findIndex((iv) => iv.every(([l, h]) => hi < l || lo > h));
    if (lane === -1) {
      if (lanes.length < MAX_LANES) {
        lane = lanes.length;
        lanes.push([]);
      } else {
        lane = lanes.reduce((best, iv, i) => (iv.length < lanes[best].length ? i : best), 0);
      }
    }
    lanes[lane].push([lo, hi]);
    const laneY = CHANNEL_TOP + lane * LANE_GAP;

    const dir = tx > sx ? 1 : -1;
    const cr = Math.min(CORNER, Math.abs(tx - sx) / 2); // short spans: shrink corners so the middle never runs backwards
    const d =
      `M${sx},${a.y + a.r + 1} ` +
      `L${sx},${laneY - cr} Q${sx},${laneY} ${sx + cr * dir},${laneY} ` +
      `L${tx - cr * dir},${laneY} Q${tx},${laneY} ${tx},${laneY - cr} ` +
      `L${tx},${b.y + b.r + 4}`;
    edges.push({
      from: a.id, to: b.id, via: c.via, d,
      revealMs: Math.max(a.revealMs, b.revealMs),
    });
  }

  return { nodes, edges, width: W, height: H };
}

function sectionLawId(sub: Subgraph, sectionId: string): string | undefined {
  return sub.sections.find((s) => s.id === sectionId)?.law_id;
}

/** Point `d` px from (px,py) toward (tx,ty) — used to stop edges at the node rim. */
function trim(px: number, py: number, tx: number, ty: number, d: number): [number, number] {
  const dx = tx - px;
  const dy = ty - py;
  const len = Math.hypot(dx, dy) || 1;
  return [px + (dx / len) * d, py + (dy / len) * d];
}
