import { useMemo } from "react";
import type { AgentEvent, ToolInfo } from "../lib/api";
import { buildCircuit, circuitOn } from "../lib/circuit";

interface Props {
  tools: ToolInfo[];
  log: AgentEvent[];
  t: number;
  live: boolean;
}

/** The architecture map, generated from the real tool list, lit as f(log, t). */
export default function Kredslob({ tools, log, t, live }: Props) {
  const circuit = useMemo(() => buildCircuit(tools), [tools]);
  const on = circuitOn(log, t, circuit, live ? t : null);

  return (
    <svg viewBox={`0 0 ${circuit.width} ${circuit.height}`} aria-label="Systemkredsløb">
      <g>
        {circuit.edges.map((e) => (
          <path key={e.id} d={e.d} className={`edge${on.has(e.id) ? " on" : ""}`} />
        ))}
      </g>
      <g>
        {circuit.nodes.map((n) => {
          const cls = `nd ${n.cls}${on.has(n.id) ? " on" : ""}`.trim();
          const x0 = n.x - n.w / 2;
          const y0 = n.y - n.h / 2;
          return (
            <g key={n.id} className={cls}>
              {n.cyl ? (
                <>
                  <rect x={x0} y={y0 + 7} width={n.w} height={n.h - 14} rx={4} />
                  <ellipse cx={n.x} cy={y0 + 7} rx={n.w / 2} ry={9} />
                  <ellipse cx={n.x} cy={n.y + n.h / 2 - 7} rx={n.w / 2} ry={9} />
                </>
              ) : (
                <rect x={x0} y={y0} width={n.w} height={n.h} rx={8} />
              )}
              <text x={n.x} y={n.sub ? n.y + 1 : n.y + 4} textAnchor="middle">
                {n.label}
              </text>
              {n.sub && (
                <text x={n.x} y={n.y + 15} textAnchor="middle" className="t2">
                  {n.sub}
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}
