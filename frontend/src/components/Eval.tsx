import { useEffect, useMemo, useState } from "react";
import {
  fetchEvalRuns, fetchEvalItems, fetchToolHealth, fetchGolden, streamEvalRun,
  type EvalRun, type EvalItem, type ToolHealthRow,
  type GoldenSet, type GoldenItem, type EvalRunVerdict,
} from "../lib/api";
import { SCOPE_FLAG_LABELS } from "../lib/events";

const pct = (r: { pass: number; total: number }) => (r.total ? Math.round((100 * r.pass) / r.total) : 0);
const shortDate = (ts: string) => (ts || "").slice(0, 10);

function runLabel(r: EvalRun): string {
  return `${r.model} · ${r.set_version} · ${r.repeat}× · ${r.mean_pass}/${r.n_items} · ${shortDate(r.ts)}`;
}

type DimField = "category" | "difficulty" | "behavior" | "pillar" | "tags";

/** Dimension matrix: rows = dimension values, one pass-% cell per selected run. */
function DimTable({ title, field, primary, compare, note }: {
  title: string;
  field: DimField;
  primary: EvalRun;
  compare: EvalRun | null;
  note?: string;
}) {
  const rowsOf = (run: EvalRun | null) => (run?.dims?.[field] ?? []);
  const values = useMemo(() => {
    const set = new Set<string>();
    rowsOf(primary).forEach((r) => set.add(r.value));
    rowsOf(compare).forEach((r) => set.add(r.value));
    return [...set].sort();
  }, [primary, compare, field]);
  const cell = (run: EvalRun | null, v: string) => {
    const row = rowsOf(run).find((r) => r.value === v);
    return row ? `${pct(row)}%` : "—";
  };
  if (values.length === 0) return null;
  return (
    <div className="dimtable">
      <h5>{title}</h5>
      {note && <div className="note">{note}</div>}
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
              const prow = rowsOf(primary).find((r) => r.value === v);
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
                <td>
                  {it.id}
                  {it.gate_flag && (
                    <span className="gatebadge sm" title={`Besvaret af skjoldet (${it.gate_flag}) — ingen agent, ingen værktøjer`}>🛡</span>
                  )}
                </td>
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
                      {it.gate_flag && (
                        <span className="gatebadge">
                          🛡 {SCOPE_FLAG_LABELS[it.gate_flag] ?? it.gate_flag} — besvaret af skjoldet
                        </span>
                      )}
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

// ── E4: golden-set browser ───────────────────────────────────────────────────
// The lens used to show only items that appeared in a *result* file, so an item
// that had never been run was invisible. This reads the set itself.

const FILTER_DIMS: { key: string; label: string }[] = [
  { key: "category", label: "Kategori" },
  { key: "expected_behavior", label: "Adfærd" },
  { key: "pillar", label: "Søjle" },
  { key: "difficulty", label: "Sværhedsgrad" },
  { key: "tags", label: "Tag" },
];

function termList(terms: (string | string[])[]): string {
  if (!terms || terms.length === 0) return "—";
  return terms.map((t) => (Array.isArray(t) ? t.join(" | ") : t)).join(" · ");
}

function GoldenBrowser({ onRun, running }: {
  onRun: (ids: string[]) => void;
  running: boolean;
}) {
  const [data, setData] = useState<GoldenSet | null>(null);
  const [q, setQ] = useState("");
  const [dim, setDim] = useState("");
  const [value, setValue] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const id = setTimeout(() => {
      fetchGolden({ q, dim, value }).then(setData).catch((e) => setErr(String(e)));
    }, 200);
    return () => clearTimeout(id);
  }, [q, dim, value]);

  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  if (err) return <div className="dimtable"><h5>Golden set</h5><div className="note">Kunne ikke hente: {err}</div></div>;
  if (!data) return <div className="dimtable"><h5>Golden set</h5><div className="eval-loading">Indlæser …</div></div>;

  const facetValues = dim ? (data.facets[dim] ?? []) : [];
  const capped = picked.length >= 5;

  return (
    <div className="dimtable golden">
      <h5>Golden set · {data.metadata.version} · {data.shown}/{data.total} items</h5>
      <div className="golden-filters">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Søg i spørgsmål, facit, noter, tags …"
          aria-label="Søg i golden set"
        />
        <select value={dim} onChange={(e) => { setDim(e.target.value); setValue(""); }} aria-label="Dimension">
          <option value="">— filtrér på —</option>
          {FILTER_DIMS.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
        </select>
        {dim && (
          <select value={value} onChange={(e) => setValue(e.target.value)} aria-label="Værdi">
            <option value="">— vælg —</option>
            {facetValues.map((f) => (
              <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
            ))}
          </select>
        )}
        {(q || value) && <button className="ghost" onClick={() => { setQ(""); setDim(""); setValue(""); }}>Ryd</button>}
      </div>

      <div className="golden-runbar">
        <span className={capped ? "cap" : ""}>{picked.length} valgt {capped && "· max 5"}</span>
        <button
          className="run"
          disabled={picked.length === 0 || running}
          onClick={() => onRun(picked)}
        >
          {running ? "Kører …" : `Kør ${picked.length || ""} smoke`}
        </button>
        {picked.length > 0 && <button className="ghost" onClick={() => setPicked([])}>Nulstil valg</button>}
      </div>
      <div className="note">
        Smoke-tier: 1–5 items, koster rigtige API-kald. Fulde matched pairs hører til på CLI
        (<code>eval_run.py</code> / <code>ab_driver.py</code>) — se backlog §2.
      </div>

      <div style={{ overflowX: "auto" }}>
        <table className="etbl items">
          <thead>
            <tr>
              <th style={{ width: 28 }}></th>
              <th>ID</th><th>Spørgsmål</th><th>Adfærd</th><th>Søjle</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((it: GoldenItem) => (
              <>
                <tr key={it.id} className="item-row">
                  <td>
                    <input
                      type="checkbox"
                      checked={picked.includes(it.id)}
                      disabled={!picked.includes(it.id) && capped}
                      onChange={() => toggle(it.id)}
                      aria-label={`Vælg ${it.id}`}
                    />
                  </td>
                  <td onClick={() => setOpen(open === it.id ? null : it.id)}>{it.id}</td>
                  <td onClick={() => setOpen(open === it.id ? null : it.id)} className="gq">{it.question}</td>
                  <td onClick={() => setOpen(open === it.id ? null : it.id)}>{it.expected_behavior}</td>
                  <td onClick={() => setOpen(open === it.id ? null : it.id)}>{it.pillar}</td>
                </tr>
                {open === it.id && (
                  <tr key={it.id + "-gd"} className="item-detail">
                    <td colSpan={5}>
                      <div className="id-q"><b>{it.question}</b></div>
                      <div className="gtags">
                        {(it.tags ?? []).map((t) => <span key={t} className="gtag">{t}</span>)}
                      </div>
                      <details open><summary>Forventet svar (facit)</summary><pre>{it.expected_answer}</pre></details>
                      <div className="id-checks">
                        <span className="det">skal indeholde: {termList(it.must_contain)}</span>
                        <span className="det">må ikke indeholde: {termList(it.must_not_contain)}</span>
                      </div>
                      {it.notes && <details><summary>Noter</summary><pre>{it.notes}</pre></details>}
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── E4: smoke runner results ─────────────────────────────────────────────────
function RunnerPanel({ progress, verdicts, error }: {
  progress: string;
  verdicts: EvalRunVerdict[];
  error: string | null;
}) {
  if (!progress && verdicts.length === 0 && !error) return null;
  return (
    <div className="dimtable">
      <h5>Smoke-kørsel</h5>
      {error && <div className="note bad">{error}</div>}
      {progress && <div className="note">{progress}</div>}
      {verdicts.map((v) => (
        <div key={v.id + v.run_id} className={`verdict ${v.scores.overall_pass ? "ok" : "bad"}`}>
          <div className="vhead">
            <b>{v.scores.overall_pass ? "✓" : "✗"} {v.id}</b>
            <span className="det">{v.scores.detected_behavior}</span>
            {v.gate_flag && (
              <span className="gatebadge">🛡 {SCOPE_FLAG_LABELS[v.gate_flag] ?? v.gate_flag}</span>
            )}
            <span className="det">{v.latency_s.toFixed(1).replace(".", ",")} s</span>
            <span className="det">{v.scores.tool_call_count ?? 0} værktøjskald</span>
          </div>
          <div className="id-checks">
            {([
              ["must_contain", v.scores.must_contain_pass],
              ["must_not_contain", v.scores.must_not_contain_pass],
              ["behavior", v.scores.behavior_match],
              ["citation", v.scores.citation_pass],
            ] as const).map(([k, ok]) => (
              <span key={k} className={ok ? "ok" : "bad"}>{ok ? "✓" : "✗"} {k}</span>
            ))}
          </div>
          <details><summary>Svar</summary><pre>{v.answer || "(intet)"}</pre></details>
        </div>
      ))}
    </div>
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
  // E4 smoke runner
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState("");
  const [verdicts, setVerdicts] = useState<EvalRunVerdict[]>([]);
  const [runErr, setRunErr] = useState<string | null>(null);

  const runSmoke = async (ids: string[]) => {
    setRunning(true);
    setVerdicts([]);
    setRunErr(null);
    setProgress(`Starter ${ids.length} item(s) …`);
    try {
      await streamEvalRun(ids, (ev) => {
        const e = ev as Record<string, unknown>;
        if (e.type === "eval_item_start") {
          setProgress(`[${e.index}/${e.total}] ${e.id} — ${String(e.question).slice(0, 70)} …`);
        } else if (e.type === "tool_call") {
          setProgress((p) => `${p.split(" · ")[0]} · kalder ${e.tool_name}`);
        } else if (e.type === "scope_gate") {
          setProgress((p) => `${p.split(" · ")[0]} · 🛡 blokeret (${e.flag})`);
        } else if (e.type === "eval_item") {
          setVerdicts((v) => [...v, e as unknown as EvalRunVerdict]);
        } else if (e.type === "eval_done") {
          setProgress(`Færdig · ${e.total} item(s)`);
        }
      });
    } catch (err) {
      setRunErr(String(err instanceof Error ? err.message : err));
      setProgress("");
    } finally {
      setRunning(false);
      // a UI run persists to mr_runs, so the run list may have new data
      fetchEvalRuns().then(setRuns).catch(() => {});
    }
  };

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

  // The golden browser + runner work even with zero result files, so they are
  // rendered before the run-dependent views bail out.
  const browser = (
    <>
      <GoldenBrowser onRun={runSmoke} running={running} />
      <RunnerPanel progress={progress} verdicts={verdicts} error={runErr} />
    </>
  );
  if (runs.length === 0) {
    return (
      <div className="eval">
        {browser}
        <div className="note">Ingen eval-kørsler fundet (eval_results_*.jsonl) — kør en smoke ovenfor.</div>
      </div>
    );
  }

  return (
    <div className="eval">
      {browser}
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
            {primary.gated != null && primary.gated > 0 && (
              <div className="tile"><div className="v">🛡 {primary.gated}</div><div className="k">besvaret af skjoldet</div></div>
            )}
          </div>

          <DimTable title="Kategori" field="category" primary={primary} compare={compare} />
          <DimTable title="Adfærd" field="behavior" primary={primary} compare={compare} />
          <DimTable title="Søjle" field="pillar" primary={primary} compare={compare} />
          <DimTable title="Sværhedsgrad" field="difficulty" primary={primary} compare={compare} />
          <DimTable
            title="Tags · fokusområder"
            field="tags"
            primary={primary}
            compare={compare}
            note="Et item tæller i hvert af sine tags, så totalerne er item-tag-par."
          />

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
