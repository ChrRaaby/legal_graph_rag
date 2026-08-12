import { useEffect, useMemo, useState } from "react";
import {
  fetchEvalRuns, fetchEvalItems, fetchToolHealth, fetchGolden, streamEvalRun,
  fetchScopeFixtures, fetchArchitecture,
  type EvalRun, type EvalItem, type ToolHealthRow,
  type GoldenSet, type GoldenItem, type EvalRunVerdict, type ScopeFixture,
  type Usage,
} from "../lib/api";
import { SCOPE_FLAG_LABELS, formatKr, fmtTok, toolLabel } from "../lib/events";

/** Tokens + cost, rendered identically wherever usage appears. `null` cost means
 *  a local model (no marginal cost) or an unknown provider — never a fake 0. */
function UsageBits({ usage }: { usage?: Usage | null }) {
  if (!usage) return <span className="det">forbrug ikke registreret</span>;
  return (
    <>
      <span className="det">ind {fmtTok(usage.input_tokens)} tok</span>
      <span className="det">ud {fmtTok(usage.output_tokens)} tok</span>
      <span className="det kr">
        {usage.cost_dkk == null ? "lokal" : formatKr(usage.cost_dkk)}
      </span>
      {usage.llm_calls > 0 && <span className="det">{usage.llm_calls} LLM-kald</span>}
    </>
  );
}

const pct = (r: { pass: number; total: number }) => (r.total ? Math.round((100 * r.pass) / r.total) : 0);
const shortDate = (ts: string) => (ts || "").slice(0, 10);

/** Below this many items a run is a smoke or debug stub, not a measurement.
 *  They shared one flat list with the real runs, so a 1/1 run read as a peer of
 *  a 69-item run — and the denominators (/69 /50 /30 /13 /1) made "36/69" and
 *  "36/50" look equal at a glance. Stubs are grouped away and scores shown as %. */
const STUB_MAX_ITEMS = 15;
/** Below this many observations a pass-% is noise. Damped and labelled, never
 *  hidden — same principle as null-over-fake-zero for usage. */
const THIN_N = 10;

const isStub = (r: EvalRun) => r.n_items < STUB_MAX_ITEMS;

/** Model lives in the optgroup, so the option itself carries only what varies. */
const runOptionLabel = (r: EvalRun) =>
  `${shortDate(r.ts)} · ${r.set_version} · ${r.repeat}× · ${r.pass_pct}%`;

/** Grouped, filterable run picker. Was a flat 48-entry <select> whose labels
 *  had to be decoded to tell six near-identical local runs apart. */
function RunPicker({ label, value, onChange, runs, allowNone }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  runs: EvalRun[];
  allowNone?: boolean;
}) {
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const shown = useMemo(() => {
    const hit = (r: EvalRun) =>
      !needle ||
      `${r.model} ${r.set_version} ${shortDate(r.ts)} ${r.name}`.toLowerCase().includes(needle);
    const list = runs.filter(hit);
    // The selected run must stay selectable even when filtered out, or typing
    // silently reassigns the selection to whatever lands first.
    const sel = runs.find((r) => r.name === value);
    return sel && !list.includes(sel) ? [sel, ...list] : list;
  }, [runs, needle, value]);

  const byModel = new Map<string, EvalRun[]>();
  shown.filter((r) => !isStub(r)).forEach((r) => {
    const k = r.model || "ukendt";
    if (!byModel.has(k)) byModel.set(k, []);
    byModel.get(k)!.push(r);
  });
  const stubs = shown.filter(isStub);

  return (
    <label className="runpick">
      {label}
      <input
        className="runfilter"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="filtrér: model, sætversion, dato …"
        aria-label={`Filtrér ${label}`}
      />
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {allowNone && <option value="">— ingen —</option>}
        {[...byModel.entries()].map(([model, rs]) => (
          <optgroup key={model} label={model}>
            {rs.map((r) => <option key={r.name} value={r.name}>{runOptionLabel(r)}</option>)}
          </optgroup>
        ))}
        {stubs.length > 0 && (
          <optgroup label={`Smoke & debug · under ${STUB_MAX_ITEMS} items`}>
            {stubs.map((r) => (
              <option key={r.name} value={r.name}>{runOptionLabel(r)} · {r.n_items} items</option>
            ))}
          </optgroup>
        )}
      </select>
    </label>
  );
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

  // Rows were sorted alphabetically, which buried a 43-point gap among forty
  // n=5 tag rows. Rank by the size of the difference instead, and fall back to
  // sample size when there is nothing to compare against.
  const rows = useMemo(() => {
    const values = new Set<string>();
    rowsOf(primary).forEach((r) => values.add(r.value));
    rowsOf(compare).forEach((r) => values.add(r.value));
    const built = [...values].map((value) => {
      const prow = rowsOf(primary).find((r) => r.value === value) ?? null;
      const crow = rowsOf(compare).find((r) => r.value === value) ?? null;
      const gap = prow && crow ? pct(crow) - pct(prow) : null;
      const n = Math.max(prow?.total ?? 0, crow?.total ?? 0);
      return { value, prow, crow, gap, n, thin: n < THIN_N };
    });
    built.sort((a, b) => {
      // thin rows always last: a 0% at n=5 is not a finding
      if (a.thin !== b.thin) return a.thin ? 1 : -1;
      if (compare) {
        const ga = a.gap == null ? -1 : Math.abs(a.gap);
        const gb = b.gap == null ? -1 : Math.abs(b.gap);
        if (ga !== gb) return gb - ga;
      }
      if (a.n !== b.n) return b.n - a.n;
      return a.value.localeCompare(b.value, "da");
    });
    return built;
  }, [primary, compare, field]);

  const modelHead = (r: EvalRun) => r.model.replace("gemini-", "").replace("gemma4:", "");
  if (rows.length === 0) return null;
  const anyThin = rows.some((r) => r.thin);
  return (
    <div className="dimtable">
      <h5>{title}{compare && <span className="det"> · sorteret efter største forskel</span>}</h5>
      {note && <div className="note">{note}</div>}
      <div style={{ overflowX: "auto" }}>
        <table className="etbl">
          <thead>
            <tr>
              <th>Dimension</th>
              <th className="num">{modelHead(primary)}</th>
              {compare && <th className="num">{modelHead(compare)}</th>}
              {compare && <th className="num">forskel</th>}
              <th className="num">items</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ value, prow, crow, gap, n, thin }) => (
              // `worst` is suppressed on thin rows — flagging a 0% at n=5 red
              // gives noise the same visual weight as a real regression.
              <tr key={value} className={[thin ? "thin" : "", !thin && prow && pct(prow) < 50 ? "worst" : ""].filter(Boolean).join(" ")}>
                <td>
                  {value}
                  {thin && <span className="nwarn" title={`Kun ${n} observationer — for få til at læse som resultat`}>n={n}</span>}
                </td>
                <td className="num">{prow ? `${pct(prow)}%` : "—"}</td>
                {compare && <td className="num">{crow ? `${pct(crow)}%` : "—"}</td>}
                {compare && (
                  <td className={`num ${gap == null || thin ? "" : gap > 0 ? "gap-pos" : gap < 0 ? "gap-neg" : ""}`}>
                    {gap == null ? "—" : gap > 0 ? `+${gap}` : `${gap}`}
                  </td>
                )}
                <td className="num">{n || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {anyThin && (
        <div className="note">
          Rækker med under {THIN_N} observationer er dæmpet og mærket <b>n=</b> — de er for tynde
          til at læse som resultater.
        </div>
      )}
    </div>
  );
}

function ItemsTable({ name, onInspectRun }: {
  name: string;
  onInspectRun?: (runId: string) => void;
}) {
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
                    <div className="id-checks">
                      <UsageBits usage={it.usage} />
                      {it.latency_s != null && (
                        <span className="det">{it.latency_s.toFixed(1).replace(".", ",")} s</span>
                      )}
                      {onInspectRun && it.run_id ? (
                        <button
                          className="ghost inspect"
                          onClick={(e) => { e.stopPropagation(); onInspectRun(it.run_id!); }}
                          title="Åbn kørslen i lenserne"
                        >
                          🔬 Inspicér
                        </button>
                      ) : (
                        <span className="det" title="Kun smoke-kørsler fra UI'et gemmer et hændelseslog der kan afspilles">
                          ingen afspilning (CLI-kørsel)
                        </span>
                      )}
                    </div>
                    {(it.tool_sequence?.length ?? 0) > 0 && (
                      <div className="gtags">
                        {it.tool_sequence!.map((t, i) => (
                          <span key={`${t}-${i}`} className="gtag">{toolLabel(t)}</span>
                        ))}
                      </div>
                    )}
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

function GoldenBrowser({ onRun, running, onLoaded }: {
  onRun: (ids: string[]) => void;
  running: boolean;
  onLoaded?: (g: GoldenSet) => void;
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
      fetchGolden({ q, dim, value })
        .then((g) => { setData(g); onLoaded?.(g); })
        .catch((e) => setErr(String(e)));
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
function RunnerPanel({ progress, verdicts, error, onInspectRun }: {
  progress: string;
  verdicts: EvalRunVerdict[];
  error: string | null;
  onInspectRun?: (runId: string) => void;
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
            <UsageBits usage={v.usage} />
            {onInspectRun && v.run_id && (
              <button
                className="ghost inspect"
                onClick={() => onInspectRun(v.run_id)}
                title="Åbn kørslen i Kredsløbet, Graflinsen og Tankestrømmen — som et almindeligt spørgsmål"
              >
                🔬 Inspicér
              </button>
            )}
          </div>
          {(v.tool_sequence?.length ?? 0) > 0 && (
            <div className="gtags">
              {v.tool_sequence!.map((t, i) => (
                <span key={`${t}-${i}`} className="gtag">{toolLabel(t)}</span>
              ))}
            </div>
          )}
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

/** Scope-classifier fixtures: the zero-LLM L0 rung, kept visually distinct from
 *  agent runs because it measures the gate, not the agent. */
function ScopeFixtures() {
  const [rows, setRows] = useState<ScopeFixture[] | null>(null);
  useEffect(() => { fetchScopeFixtures().then(setRows).catch(() => setRows([])); }, []);
  if (!rows || rows.length === 0) return null;
  return (
    <div className="dimtable">
      <h5>Skjold-fixtures · klassifikator uden agent</h5>
      <div className="note">
        L0-trinnet: ét klassifikator-kald pr. item, ingen agent og ingen graf.
        <b> Falske positiver</b> er tallet den findes for — et in-scope item der bliver blokeret.
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="etbl">
          <thead>
            <tr>
              <th>Fil</th><th>Klassifikator</th><th className="num">bestået</th>
              <th className="num">falske pos.</th><th className="num">fejl</th><th>sæt · commit</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <tr key={f.name} className={f.false_positives > 0 ? "worst" : ""}>
                <td>{f.name.replace("eval_fixtures_scope_", "").replace(".jsonl", "")}</td>
                <td className="mono">{f.classifier_model}</td>
                <td className="num">{f.passed}/{f.n}</td>
                <td className="num">{f.false_positives}/{f.in_scope}</td>
                <td className="num">{f.errors}</td>
                <td className="mono">{f.set_version} · {f.git_sha}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ToolHealth() {
  const [rows, setRows] = useState<ToolHealthRow[] | null>(null);
  const [nRuns, setNRuns] = useState(0);
  const [allTools, setAllTools] = useState<string[] | null>(null);
  useEffect(() => {
    fetchToolHealth().then((d) => { setRows(d.tools); setNRuns(d.n_runs); }).catch(() => setRows([]));
    // Runtime tool list, so "never called" is measured against what the agent
    // actually has — not a hardcoded count that would go stale.
    fetchArchitecture().then((a) => setAllTools(a.tools.map((t) => t.name))).catch(() => setAllTools(null));
  }, []);
  if (!rows) return null;

  const total = rows.reduce((s, t) => s + t.calls, 0);
  const called = new Set(rows.map((t) => t.tool));
  const never = (allTools ?? []).filter((t) => !called.has(t));

  return (
    <div className="dimtable">
      <h5>
        Værktøjs-sundhed
        <span className="det"> · {nRuns} live-kørsler · {fmtTok(total)} kald
          {allTools && ` · ${never.length} af ${allTools.length} værktøjer aldrig kaldt`}</span>
      </h5>
      <div className="note">
        Kilden er <b>live-samtaler</b>, ikke eval-kørsler, og tallene er <b>ikke</b> delt op
        på substrat. Læs det som et udgangspunkt for en optælling — ikke som optællingen.
      </div>
      {rows.length === 0 ? (
        <div className="note">Ingen live-kørsler endnu — stil spørgsmål i chatten, så udfyldes tabellen.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="etbl">
            <thead>
              <tr>
                <th>Værktøj</th><th className="num">kald</th><th className="num">andel</th>
                <th className="num">tomme svar</th><th className="num">middel-tid</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => {
                const thin = t.calls < THIN_N;
                return (
                  <tr key={t.tool} className={[thin ? "thin" : "", !thin && t.empty_rate >= 50 ? "worst" : ""].filter(Boolean).join(" ")}>
                    <td>
                      {t.tool}
                      {thin && <span className="nwarn" title={`Kun ${t.calls} kald — for få til at læse som en rate`}>n={t.calls}</span>}
                    </td>
                    <td className="num">{t.calls}</td>
                    <td className="num">{total ? Math.round((100 * t.calls) / total) : 0}%</td>
                    <td className="num">{t.empty_rate}%</td>
                    <td className="num">{t.mean_duration_s == null ? "—" : `${t.mean_duration_s.toFixed(2).replace(".", ",")}s`}</td>
                  </tr>
                );
              })}
              {never.length > 0 && (
                <tr className="worst">
                  <td colSpan={5}>Aldrig kaldt: {never.join(" · ")}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function Eval({ onInspectRun }: { onInspectRun?: (runId: string) => void } = {}) {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [primaryName, setPrimaryName] = useState<string>("");
  const [compareName, setCompareName] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [sub, setSub] = useState<"suite" | "history">("suite");
  const [golden, setGolden] = useState<GoldenSet | null>(null);
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
        // Was: pin to eval_results_v4_flash_5x.jsonl, plus the first gemma v4 as
        // the comparison — index 32 and 28 of 48, both from 2026-07-05. Historik
        // therefore opened on a five-week-old pair with nothing saying so.
        // The server returns ts-descending, so the newest real run is the head.
        const newest = rs.find((r) => !isStub(r)) ?? rs[0];
        if (newest) setPrimaryName(newest.name);
        // No default comparison: auto-picking a counterpart is what produced a
        // silent cross-substrate, cross-set-version pair in the first place.
      })
      .catch((e) => setError(String(e)));
  }, []);

  const primary = runs?.find((r) => r.name === primaryName) ?? null;
  const compare = compareName ? runs?.find((r) => r.name === compareName) ?? null : null;

  if (error) return <div className="placeholder"><div>Kunne ikke indlæse eval-data: {error}</div></div>;
  if (!runs) return <div className="placeholder"><span className="dot" /><div>Indlæser eval-kørsler …</div></div>;

  // Two distinct jobs, so two sub-tabs: TESTSUITE is "what do we test, and run
  // one now"; HISTORIK is "what happened when we ran it". They previously shared
  // one long scroll, which buried the history under the browser.
  const subtabs = (
    <div className="subtabs" role="tablist" aria-label="Eval-visning">
      {([
        ["suite", `Testsuite${golden ? ` · ${golden.total}` : ""}`],
        ["history", `Historik · ${runs.length}`],
      ] as const).map(([key, label]) => (
        <button
          key={key}
          role="tab"
          aria-selected={sub === key}
          className={`subtab ${sub === key ? "on" : ""}`}
          onClick={() => setSub(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );

  if (sub === "suite") {
    return (
      <div className="eval">
        {subtabs}
        {/* The runner renders BEFORE the browser. GoldenBrowser contains the
            whole 69-row table, so with the old order the live run card was
            always below it — off-screen exactly while it was worth watching. */}
        <RunnerPanel progress={progress} verdicts={verdicts} error={runErr}
                     onInspectRun={onInspectRun} />
        <GoldenBrowser onRun={runSmoke} running={running} onLoaded={setGolden} />
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="eval">
        {subtabs}
        <div className="note">Ingen eval-kørsler fundet (eval_results_*.jsonl) — kør en smoke under Testsuite.</div>
      </div>
    );
  }

  return (
    <div className="eval">
      {subtabs}
      <div className="eval-selects">
        <RunPicker label="Kørsel" value={primaryName} onChange={setPrimaryName} runs={runs} />
        <RunPicker label="Sammenlign" value={compareName} onChange={setCompareName} runs={runs} allowNone />
      </div>
      {primary && compare && primary.set_version !== compare.set_version && (
        <div className="note bad">
          ⚠ Forskellige sætversioner ({primary.set_version} mod {compare.set_version}) — items
          er ikke de samme, så tallene kan ikke sammenlignes direkte.
        </div>
      )}
      {primary && isStub(primary) && (
        <div className="note">
          Denne kørsel har kun {primary.n_items} items — en smoke- eller debug-kørsel, ikke en måling.
        </div>
      )}

      {primary && (
        <>
          <div className="tiles">
            {/* Headline is the percentage: the raw score's denominator varies
                across eras (/69 /50 /30 /13), so "34,2" alone is unreadable. */}
            <div className="tile">
              <div className="v">{primary.pass_pct}%</div>
              <div className="k">beståelse · {primary.mean_pass}/{primary.n_items}</div>
            </div>
            <div className="tile"><div className="v">{primary.model}</div><div className="k">substrat · det.</div></div>
            <div className="tile"><div className="v">{primary.repeat}×</div><div className="k">kørsler · {primary.set_version}</div></div>
            {primary.git_sha !== "—" && (
              <div className="tile"><div className="v mono">{primary.git_sha}</div><div className="k">app-commit · {shortDate(primary.ts)}</div></div>
            )}
            {primary.gated != null && primary.gated > 0 && (
              <div className="tile"><div className="v">🛡 {primary.gated}</div><div className="k">besvaret af skjoldet</div></div>
            )}
            {primary.tool_calls != null && (
              <div className="tile"><div className="v">{fmtTok(primary.tool_calls)}</div><div className="k">værktøjskald i alt</div></div>
            )}
            {primary.usage ? (
              <>
                <div className="tile">
                  <div className="v">{fmtTok(primary.usage.input_tokens + primary.usage.output_tokens)}</div>
                  <div className="k">
                    tokens · ind {fmtTok(primary.usage.input_tokens)} / ud {fmtTok(primary.usage.output_tokens)}
                  </div>
                </div>
                <div className="tile">
                  <div className="v kr">
                    {primary.usage.cost_dkk == null ? "lokal" : formatKr(primary.usage.cost_dkk)}
                  </div>
                  <div className="k">
                    anslået pris{primary.usage.coverage && primary.usage.coverage !== `${primary.n_records}/${primary.n_records}`
                      ? ` · ${primary.usage.coverage} records` : ""}
                  </div>
                </div>
              </>
            ) : null /* pre-2026-08-08 files record no usage; collapse rather
                        than hold prime space with an em dash */}
          </div>
          {!primary.usage && (
            <div className="note">Forbrug er ikke registreret i denne fil (skrevet før 2026-08-08).</div>
          )}

          {/* Promoted above the matrices: with G4 open, which tools the agent
              actually reaches for is the most decision-relevant table here. */}
          <ToolHealth />

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
            <ItemsTable name={primary.name} onInspectRun={onInspectRun} />
          </div>

          <ScopeFixtures />
        </>
      )}
    </div>
  );
}
