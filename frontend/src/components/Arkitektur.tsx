import { useEffect, useState } from "react";
import { fetchSystemMap, type SystemMap, type SystemNode } from "../lib/api";

/** G3 — the whole solution, laid out from `/api/system`.
 *
 *  Kredsløbet shows how one question flows through the agent. This shows what
 *  the system is made of, including the substrate it runs on, and it is drawn
 *  from the server's answer rather than typed here — the project's standing
 *  rule after the old views drifted to a hardcoded "13 tools / Gemini 2.5
 *  Flash".
 *
 *  Nodes the running process can actually see are solid; nodes it cannot see
 *  from inside (Secret Manager, Artifact Registry, the browser itself) are
 *  dashed and labelled "erklæret". A declared node must not borrow the
 *  authority of a measured one. */

const LAYERS: { key: string; title: string }[] = [
  { key: "klient", title: "Klient" },
  { key: "tjeneste", title: "Tjeneste" },
  { key: "model", title: "Model" },
  { key: "data", title: "Data" },
  { key: "platform", title: "Platform (GCP)" },
];

function NodeCard({ n }: { n: SystemNode }) {
  const unhealthy = n.healthy === false;
  return (
    <div
      className={`sys-node${n.observed ? "" : " declared"}${unhealthy ? " bad" : ""}`}
      title={n.observed
        ? "Observeret: processen kender selv denne værdi"
        : "Erklæret: kan ikke ses indefra — skrevet ned i /api/system"}
    >
      <div className="sys-label">
        {n.label}
        {!n.observed && <span className="sys-tag">erklæret</span>}
        {unhealthy && <span className="sys-tag bad">nede</span>}
      </div>
      <div className="sys-detail">{n.detail}</div>
    </div>
  );
}

export default function Arkitektur() {
  const [map, setMap] = useState<SystemMap | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchSystemMap().then(setMap).catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="placeholder"><div>Kunne ikke hente systemkortet: {err}</div></div>;
  if (!map) return <div className="placeholder"><span className="dot" /><div>Tegner systemkortet …</div></div>;

  const observed = map.nodes.filter((n) => n.observed).length;

  return (
    <div className="sys">
      <div className="sys-head">
        <h4>Systemkort</h4>
        <span className="note">
          {observed} af {map.nodes.length} komponenter er <b>observeret</b> af den kørende proces;
          resten er erklæret og kan derfor blive forældet. Genereret {map.generated_at.slice(0, 16).replace("T", " ")}.
        </span>
      </div>

      <div className="sys-grid">
        {LAYERS.map(({ key, title }) => {
          const nodes = map.nodes.filter((n) => n.layer === key);
          if (nodes.length === 0) return null;
          return (
            <section key={key} className="sys-layer">
              <h5>{title}</h5>
              <div className="sys-row">
                {nodes.map((n) => <NodeCard key={n.id} n={n} />)}
              </div>
            </section>
          );
        })}
      </div>

      <div className="dimtable">
        <h5>Forbindelser</h5>
        <div className="sys-edges">
          {map.edges.map(([a, b]) => {
            const la = map.nodes.find((n) => n.id === a)?.label ?? a;
            const lb = map.nodes.find((n) => n.id === b)?.label ?? b;
            return <span key={`${a}-${b}`} className="sys-edge">{la} → {lb}</span>;
          })}
        </div>
      </div>
    </div>
  );
}
