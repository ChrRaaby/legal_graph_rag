import { useEffect, useMemo, useState } from "react";
import type { AgentEvent, Subgraph, NodeDetail } from "../lib/api";
import { fetchSubgraph, fetchNodeDetail } from "../lib/api";
import { refsFromLog, layoutGraflinse } from "../lib/subgraph";

interface Props {
  log: AgentEvent[];
  answer: string;
  t: number;
  live: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

/** What the agent saw in the graph: the retrieved subgraph (Law → § + CITES),
 *  built live as retrieval happens (scrub-revealed), with a node inspector. */
export default function Graflinse({ log, answer, t, live, selectedId, onSelect }: Props) {
  const [sub, setSub] = useState<Subgraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<NodeDetail | null>(null);

  const refs = useMemo(() => refsFromLog(log), [log]);
  const refsKey = useMemo(() => refs.map((r) => `${r.title}|${r.num}`).sort().join(","), [refs]);

  // Fetch the subgraph whenever the set of retrieved refs changes.
  useEffect(() => {
    if (refs.length === 0) {
      setSub(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetchSubgraph(refs, answer)
      .then((s) => !cancelled && setSub(s))
      .catch(() => !cancelled && setSub(null))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refsKey, answer]);

  // Node inspector: fetch detail for the selected element id (from a click or a
  // kilde-chip in the chat).
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    fetchNodeDetail(selectedId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null));
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const layout = useMemo(() => (sub ? layoutGraflinse(sub, log) : null), [sub, log]);

  const visible = (revealMs: number) => t >= revealMs - 1 || (live && revealMs !== Infinity);
  const visibleSections = layout
    ? layout.nodes.filter((n) => n.kind === "section" && visible(n.revealMs)).length
    : 0;

  if (!layout) {
    return (
      <div className="placeholder">
        {loading ? (
          <>
            <span className="dot" />
            <div>Bygger delgraf …</div>
          </>
        ) : (
          <div>Stil et spørgsmål — de hentede paragraffer og deres krydshenvisninger vises her.</div>
        )}
      </div>
    );
  }

  return (
    <>
      <svg viewBox={`0 0 ${layout.width} ${layout.height}`} aria-label="Hentet delgraf" data-count={visibleSections}>
        <defs>
          <marker id="cite-arrow" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0.5,0.5 L6.5,4 L0.5,7.5" fill="none" stroke="var(--cites)" strokeWidth="1.6" />
          </marker>
        </defs>
        <g>
          {layout.edges.map((e, i) => {
            const cite = layout.nodes.find((n) => n.id === e.from)?.kind === "section" &&
              layout.nodes.find((n) => n.id === e.to)?.kind === "section";
            return (
              <path
                key={i}
                d={e.d}
                className={`ge${cite ? " cites" : ""}${visible(e.revealMs) ? "" : " hide"}`}
                markerEnd={cite ? "url(#cite-arrow)" : undefined}
              >
                {cite && e.via && <title>henviser via: «…{e.via}…»</title>}
              </path>
            );
          })}
        </g>
        <g>
          {layout.nodes.map((n) => {
            const fill =
              n.kind === "lov" ? "var(--h-lov)" : n.retrieved ? "var(--h-par)" : "var(--h-stk)";
            const cls = [
              "gn",
              visible(n.revealMs) ? "" : "hide",
              !n.retrieved && n.kind === "section" ? "dim" : "",
              n.used ? "used" : "",
              selectedId === n.element_id ? "pick" : "",
            ].join(" ").trim();
            return (
              <g
                key={n.id}
                className={cls}
                onClick={() => n.kind === "section" && onSelect(n.element_id)}
                style={{ cursor: n.kind === "section" ? "pointer" : "default" }}
              >
                <circle cx={n.x} cy={n.y} r={n.r} fill={fill} stroke="var(--line)" />
                {n.kind === "lov" ? (
                  <text x={n.x} y={n.y - n.r - 7} textAnchor="middle" className="lov-label">
                    {n.label}
                  </text>
                ) : (
                  <text x={n.x + n.r + 5} y={n.y} textAnchor="start" dominantBaseline="central">
                    {n.label}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      <div className="legend">
        <span><i style={{ background: "var(--h-lov)" }} />Lov</span>
        <span><i style={{ background: "var(--h-par)" }} />Hentet §</span>
        <span><i style={{ background: "var(--h-stk)" }} />Krydshenvist §</span>
        <span><i style={{ background: "var(--cites)", borderRadius: 2, height: 3, verticalAlign: 2 }} />Henviser til</span>
        <span><i style={{ background: "transparent", border: "2px solid var(--accent)" }} />Brugt i svaret</span>
      </div>

      {detail && (
        <aside className="node-panel show" aria-label="Nodeindhold">
          <button className="np-close" onClick={() => onSelect(null)} aria-label="Luk">✕</button>
          {detail.found ? (
            <>
              <h4>{detail.lov} {detail.section_number ? `§ ${detail.section_number}` : ""}</h4>
              <div className="np-badges">
                <span className={`np-badge${detail.is_current ? " ok" : ""}`}>
                  {detail.is_current ? "✓ gældende" : "historisk"}
                </span>
                {detail.section_title && <span className="np-badge">{detail.section_title}</span>}
              </div>
              {(detail.paragraphs || []).slice(0, 6).map((p, i) => (
                <p key={i}>
                  <b>stk. {p.number}.</b> {p.text.length > 320 ? p.text.slice(0, 320) + "…" : p.text}
                </p>
              ))}
              <div className="np-links">
                {detail.uri && (
                  <a href={detail.uri} target="_blank" rel="noopener noreferrer">
                    Åbn på retsinformation ↗
                  </a>
                )}
              </div>
            </>
          ) : (
            <p>Kunne ikke hente nodeindhold.</p>
          )}
        </aside>
      )}
    </>
  );
}
