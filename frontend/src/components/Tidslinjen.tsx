import { useMemo } from "react";
import type { AgentEvent } from "../lib/api";
import { spansFromLog } from "../lib/events";

interface Props {
  log: AgentEvent[];
  t: number;
  end: number;
  live: boolean;
  playing: boolean;
  onPlay: () => void;
  onScrub: (ms: number) => void;
}

const TLW = 760;
const sec = (ms: number) => (ms / 1000).toFixed(1).replace(".", ",");

/** The waterfall reborn as a scrubber: LLM (violet) + tool (aqua) spans on one
 *  track; dragging the playhead sets t, which re-renders every layer. */
export default function Tidslinjen({ log, t, end, live, playing, onPlay, onScrub }: Props) {
  const spans = useMemo(() => spansFromLog(log), [log]);
  const has = end > 0 && spans.length > 0;
  const px = (ms: number) => (has ? (ms / end) * TLW : 0);

  const ticks = useMemo(() => {
    if (!has) return [];
    const stepS = end > 20000 ? 5 : 2;
    const out: number[] = [];
    for (let s = 0; s * 1000 <= end; s += stepS) out.push(s);
    return out;
  }, [end, has]);

  return (
    <div className="tl">
      <div className="tl-head">
        <button className="play" onClick={onPlay} disabled={live || !has} aria-label="Afspil forløbet">
          {live ? "●" : playing ? "⏸" : t >= end && end > 0 ? "↺" : "▶"}
        </button>
        <span className="tl-label">{live ? "Kører …" : "Tidslinje — træk for at spole"}</span>
        <span className="tl-time">
          {sec(Math.min(t, end || t))} s / {sec(end)} s
        </span>
      </div>
      {has ? (
        <div className="tl-track">
          <svg viewBox={`0 0 ${TLW} 46`} preserveAspectRatio="none">
            {spans.map((s, i) => (
              <rect
                key={i}
                x={px(s.t0)}
                y={s.kind === "llm" ? 4 : 20}
                width={Math.max(2, px(s.t1) - px(s.t0) - 2)}
                height={13}
                rx={3}
                className={`sp ${s.kind}`}
              />
            ))}
            {ticks.map((s) => (
              <text key={s} x={px(s * 1000)} y={43} fill="var(--ink-3)" fontSize={9}>
                {s}s
              </text>
            ))}
            <line x1={px(t)} y1={0} x2={px(t)} y2={34} className="playhead" />
          </svg>
          <input
            type="range"
            className="scrub"
            min={0}
            max={Math.max(1, Math.round(end))}
            value={Math.round(Math.min(t, end))}
            step={25}
            disabled={live}
            onChange={(e) => onScrub(Number(e.target.value))}
            aria-label="Spol i forløbet"
          />
        </div>
      ) : (
        <div className="tl-empty">Ingen kørsel endnu — stil et spørgsmål for at se forløbet.</div>
      )}
      <div className="tl-legend">
        <span>
          <i style={{ background: "var(--llm)" }} />
          LLM tænker
        </span>
        <span>
          <i style={{ background: "var(--tool)" }} />
          Værktøj / graf-opslag
        </span>
      </div>
    </div>
  );
}
