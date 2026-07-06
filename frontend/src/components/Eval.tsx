import { useEffect, useMemo, useState } from "react";
import {
  fetchEvalRuns, fetchEvalItems, fetchToolHealth,
  type EvalRun, type EvalItem, type ToolHealthRow,
} from "../lib/api";

const pct = (r: { pass: number; total: number }) => (r.total ? Math.round((100 * r.pass) / r.total) : 0);
const shortDate = (ts: string) => (ts || "").slice(0, 10);

function runLabel(r: EvalRun): string {
  return `${r.model} · ${r.set_version} · ${r.repeat}× · ${r.mean_pass}/${r.n_items} · ${shortDate(r.ts)}`;
}

/** Dimension matrix: rows = dimension values, one pass-% cell per selected run. */
function DimTable({ title, field, primary, compare }: {
  title: string;
  field: "category" | "difficulty" | "behavior";
  primary: EvalRun;
  compare: EvalRun | null;
}) {
  const values = useMemo(() => {
    const set = new Set<string>();
    primary.dims[field].forEach((r) => set.add(r.value));
    compare?.dims[field].forEach((r) => set.add(r.value));
    return [...set].sort();
  }, [primary, compare, field]);
  const cell = (run: EvalRun | null, v: string) => {
    const row = run?.dims[field].find((r) => r.value === v);
    return row ? `${pct(row)}%` : "—";
  };
  return (
    <div className="dimtable">
      <h5>{title}</h5>
      <div style={{ overflowX: "auto" }}>
        <table className="etbl">
          <thead>
            <tr>
              <th>Dimension</th>
              <th className="num">{primary.model.replace("gemini-", "").replace("gemma4:", "")}</th>
              {compare && <th className="num">{compare.model.replace("gemini-", "").replace("gemma4:", "")}</th>}
              <th className="num">items</th>
            </tr>
          </thead>
          <tbody>
            {values.map((v) => {
              const prow = primary.dims[field].find((r) => r.value === v);
              const worst = prow && pct(prow) < 50;
              return (
                <tr key={v} className={worst ? "worst" : ""}>
                  <td>{v}</td>
                  <td className="num">{cell(primary, v)}</td>
                  {compare && <td className="num">{cell(compare, v)}</td>}
                  <td className="num">{prow?.total ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ItemsTable({ name }: { name: string }) {
  const [items, setItems] = useState<EvalItem[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    setItems(null);
    fetchEvalItems(name).then(setItems).catch(() => setItems([]));
  }, [name]);
  if (!items) return <div className="eval-loading">Indlæser items …</div>;
  return (
    <table className="etbl items">
      <thead>
        <tr><th>ID</th><th>Kategori</th><th>Adfærd</th><th className="num">Bestå</th></tr>
      </thead>
      <tbody>
        {items.map((it) => {
          const freq = it.runs ? it.passes / it.runs : 0;
          const cls = freq === 1 ? "always" : freq === 0 ? "never" : "flaky";
          return (
            <>
              <tr key={it.id} className={`item-row ${cls}`} onClick={() => setOpen(open === it.id ? null : it.id)}>
                <td>{it.id}</td>
                <td>{it.category}</td>
                <td>{it.expected_behavior}</td>
                <td className="num">{it.passes}/{it.runs}</td>
              </tr>
              {open === it.id && (
                <tr key={it.id + "-d"} className="item-detail">
                  <td colSpan={4}>
                    <div className="id-q"><b>{it.question}</b></div>
                    <div className="id-checks">
                      {(["must_contain", "must_not_contain", "behavior", "citation"] as const).map((k) => (
                        <span key={k} className={it.scores[k] ? "ok" : "bad"}>
                          {it.scores[k] ? "✓" : "✗"} {k}
                        </span>
                      ))}
                      <span className="det">detected: {it.scores.detected_behavior}</span>
                    </div>
                    <details><summary>Sidste svar</summary><pre>{it.answer || "(intet)"}</pre></details>
                  </td>
                </tr>
              )}
            </>
          );
        })}
      </tbody>
    </table>
  );
}

function ToolHealth() {
  const [rows, setRows] = useState<ToolHealthRow[] | null>(null);
  const [nRuns, setNRuns] = useState(0);
  useEffect(() => {
    fetchToolHealth().then((d) => { setRows(d.tools); setNRuns(d.n_runs); }).catch(() => setRows([]));
  }, []);
  if (!rows) return null;
  return (
    <div className="dimtable">
      <h5>Værktøjs-sundhed · {nRuns} live-kørsler</h5>
      {rows.length === 0 ? (
        <div className="note">Ingen live-kørsler endnu — stil spørgsmål i chatten, så udfyldes tabellen.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="etbl">
            <thead>
              <tr><th>Værktøj</th><th className="num">kald</th><th className="num">tomme svar</th><th className="num">middel-tid</th></tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.tool} className={t.empty_rate >= 50 ? "worst" : ""}>
                  <td>{t.tool}</td>
                  <td className="num">{t.calls}</td>
                  <td className="num">{t.empty_rate}%</td>
                  <td className="num">{t.mean_duration_s == null ? "—" : `${t.mean_duration_s.toFixed(2).replace(".", ",")}s`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function Eval() {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [primaryName, setPrimaryName] = useState<string>("");
  const [compareName, setCompareName] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchEvalRuns()
      .then((rs) => {
        setRuns(rs);
        const v4 = rs.find((r) => r.name.includes("v4_flash_5x")) ?? rs[0];
        if (v4) setPrimaryName(v4.name);
        const g = rs.find((r) => r.model.includes("gemma") && r.set_version.includes("4"));
        if (g) setCompareName(g.name);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const primary = runs?.find((r) => r.name === primaryName) ?? null;
  const compare = compareName ? runs?.find((r) => r.name === compareName) ?? null : null;

  if (error) return <div className="placeholder"><div>Kunne ikke indlæse eval-data: {error}</div></div>;
  if (!runs) return <div className="placeholder"><span className="dot" /><div>Indlæser eval-kørsler …</div></div>;
  if (runs.length === 0) return <div className="placeholder"><div>Ingen eval-kørsler fundet (eval_results_*.jsonl).</div></div>;

  return (
    <div className="eval">
      <div className="eval-selects">
        <label>Kørsel
          <select value={primaryName} onChange={(e) => setPrimaryName(e.target.value)}>
            {runs.map((r) => <option key={r.name} value={r.name}>{runLabel(r)}</option>)}
          </select>
        </label>
        <label>Sammenlign
          <select value={compareName} onChange={(e) => setCompareName(e.target.value)}>
            <option value="">— ingen —</option>
            {runs.map((r) => <option key={r.name} value={r.name}>{runLabel(r)}</option>)}
          </select>
        </label>
      </div>

      {primary && (
        <>
          <div className="tiles">
            <div className="tile"><div className="v">{primary.mean_pass}<span className="sub">/{primary.n_items}</span></div><div className="k">{primary.model} · det.</div></div>
            <div className="tile"><div className="v">{primary.pass_pct}%</div><div className="k">beståelse</div></div>
            <div className="tile"><div className="v">{primary.repeat}×</div><div className="k">kørsler · {primary.set_version}</div></div>
            <div className="tile"><div className="v mono">{primary.git_sha}</div><div className="k">app-commit · {shortDate(primary.ts)}</div></div>
          </div>

          <DimTable title="Kategori" field="category" primary={primary} compare={compare} />
          <DimTable title="Adfærd" field="behavior" primary={primary} compare={compare} />
          <DimTable title="Sværhedsgrad" field="difficulty" primary={primary} compare={compare} />

          <div className="dimtable">
            <h5>Items · {primary.name}</h5>
            <div className="note">Klik en række for svar + hvilke tjek der fejlede. Rød = aldrig bestået, gul = flaky.</div>
            <ItemsTable name={primary.name} />
          </div>

          <ToolHealth />
        </>
      )}
    </div>
  );
}
