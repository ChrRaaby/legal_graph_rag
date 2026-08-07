# Scope Guardrails — design v2 (Phase F0)

**Author:** Fable 5 session; v1 2026-08-02, **v2 same day after user review** — v1's
layered prompt-section approach judged overcomplicated. This version follows the
user's directive: **one dedicated LLM classifier call in front of the agent**, flagging
three content types to refuse — **PII · potentially illegal · non-tax** — and passing
everything else through **unchanged**.
**Status:** ✅ **APPROVED 2026-08-02** — user signed off on the architecture, the §2.3
rulings table, the §3 templates, and the §6 decisions ("go with your recommendations").
This document is the F1 implementation spec.
**Downstream:** F1 (implementation, Opus-lane — kickoff prompt in IMPROVEMENT_BACKLOG.md
Phase F), F2 (golden items, user legal-review gate), F3 (measured gate).

---

## 1. Architecture

```
user prompt ──► classify_request()  ── any flag? ──► template response (no agent, no tools)
                (1 cheap LLM call)        │
                                          └─ no flags ──► agent, EXACTLY as today
```

- **`classify_request(question, recent_history) -> {pii, illegal, non_tax, reason}`** —
  a single strict-JSON LLM call, temperature 0, its own small prompt, independent of the
  agent prompt. Modular by construction: the agent knows nothing about it.
- **Flag raised → deterministic template response** per flag type (§3), emitted with a
  synthetic `scope_gate` tool_event so Kredsløbet/Tankestrømmen show a lit guard node
  with the flag + reason (dev mode) instead of a dead end. `(answer, tool_events)`
  contract unchanged (ground rule 4).
- **No flags → pass-through.** The prompt reaches the agent byte-identically to today.
  This makes measurement clean: the treatment surface is *only* the blocked set.
- **Placement:** `app.py`, at the top of `stream_agent_answer()` (ground rule 5 —
  server.py and eval_run.py inherit it through the stub-import).
- **Hatch:** `F_SCOPE_GUARD=off` skips the classifier entirely = current behaviour,
  byte-for-byte, for the OFF cell in F3.
- **Fail-open:** classifier error/timeout → log a warning, let the prompt through. A
  guardrail outage must not take down the product, and the false-positive asymmetry
  (§2) points the same way. (Open question 6.3: user may prefer fail-closed for PII.)
- **Classifier model:** pin an explicit cheap id (candidate: `gemini-3.5-flash-lite`;
  fall back to the agent's flash if lite misbehaves on Danish). Never aliases (traps
  index). One extra LLM call ≈ +0.3–1s latency and a sub-cent cost per request —
  acceptable; measure in F3.
- **Context:** the classifier sees the question **plus a short recent-history window**.
  A follow-up like "hvad med i 2026?" is unclassifiable alone and must not be flagged
  non-tax just because the tax context lives two turns up.

## 2. The three flags (⚠ classifier-prompt rulings = USER REVIEW)

**Shared policy: when in doubt, let it through.** Wrongly refusing a real tax question
costs more than occasionally letting a borderline prompt reach the agent (which has its
own behaviours for vague/illegal/unknown). The classifier prompt states this asymmetry
explicitly per flag.

### 2.1 `pii` — personal data in the prompt
Flag when the prompt contains data identifying a specific person: CPR-numre,
navne+adresse-kombinationer, helbredsoplysninger, konkrete kontonumre. General/abstract
cases ("en person med en årsløn på 600.000 kr.") are NOT PII — that's how tax questions
are asked. Response invites **rephrase and retry**, not refusal (§3). Rationale: prompts
are logged (`mr_runs`, eval jsonl) and sent to a hosted LLM; keeping PII out is hygiene
for the user's own data.

### 2.2 `illegal` — requests for help with unlawful acts
Skatteunddragelse, skjulning af aktiver, fiktive fakturaer, sort arbejde — the class the
agent's AFVIS prompt section handles today (gs-026/034/035/036). Asking what the law
*says* about an illegal act stays allowed ("hvad er straffen for skattesvig?" is a legal
information question). ⚠ Migration note: the gate now answers these *before* the agent —
§3's template is written to satisfy the four existing golden items' checks
("skatteunddragelse", "ulovlig", "Det vil jeg ikke hjælpe med", offer the legal rules)
so they keep passing with the gate ON. The agent's AFVIS section stays as
defense-in-depth for anything the classifier misses.

### 2.3 `non_tax` — outside Danish tax law
The v1 boundary work carries over as the classifier prompt's rulings — **the tax angle
decides**:

| Ruling | IN (allow) | OUT (flag) |
|---|---|---|
| Taxation of anything is tax | "skal jeg betale skat af min SU?" | — |
| Benefit size/eligibility | — | "hvor meget kan jeg få i dagpenge?" |
| Adjacent legal fields | boafgift (BAL) | arveret, selskabsret, ansættelsesret as law |
| Bookkeeping | momsfradrag, skattemæssig behandling | bogføringsmekanik |
| Foreign | danske regler om udenlandsk indkomst (DBO, KSL § 2) | andre landes interne skatteret |
| Investment advice | beskatning af investeringer | "hvilken aktie bør jeg købe?" |
| Meta-questions about the system | **always IN** ("hvilke love kender du?") | — |
| Clearly unrelated | — | opskrifter, kode, geografi, jokes |

**Scope ≠ coverage** (unchanged from v1, gs-039's lesson): a *tax* law missing from the
graph is NOT non_tax — the prompt passes through and the agent's `admit_unknown`
behaviour handles it. The classifier rules on subject matter only and does not know
what's loaded.

## 3. Template responses (deterministic, Danish)

- **pii:** »Dit spørgsmål ser ud til at indeholde personoplysninger (fx CPR-nummer eller
  navn). Af hensyn til dit privatliv behandler jeg ikke personoplysninger — stil gerne
  spørgsmålet igen i generel form, fx "en person med…".«
- **illegal:** »Det vil jeg ikke hjælpe med — det, du beskriver, kan udgøre
  skatteunddragelse eller anden ulovlig adfærd. Jeg forklarer gerne de lovlige regler på
  området i stedet.«
- **non_tax:** »Det ligger uden for mit område — jeg svarer kun på spørgsmål om dansk
  skattelovgivning. Du er velkommen til at spørge om fx fradrag, moms, aktiebeskatning
  eller boafgift.«

Signal-collision check (v1 finding, still applies): the non_tax template must not
contain `_BEHAVIOR_SIGNALS["refuse"]` phrases ("kan ikke hjælpe" etc.) — it doesn't; the
illegal template deliberately DOES match the refuse signals (that's correct — it *is* a
refusal, keeping the 4 golden items green). New `out_of_scope` signals for detection:
`["uden for mit område"]`; `pii_block` signals: `["personoplysninger", "generel form"]`.
Both added to `_BEHAVIOR_SIGNALS`/`BEHAVIOR_PRIORITY` + the judge prompt. ⚠ A1 debt:
extend the live scorer path (eval_run-imported), not the stale app.py copy.

## 4. What v1 evidence still stands (don't re-derive)

- **Embedding pre-gate: falsified** (probes `scratchpad/f0_scope_probe_*.py`,
  2026-08-02): e5 cosine can't separate scope ("fortæl mig en joke" outscores five
  golden questions; contrastive-margin boundary 0.0005 wide; the adjacent band is
  invisible to any embedding gate). This is *why* the classifier is an LLM call, not an
  embedding threshold — the user's architecture is consistent with the measurement.
- The adjacent-band rulings and false-positive asymmetry (now §2.3/§2).
- Scope-vs-coverage (now §2.3), incl. the gs-039 rework direction: retag as
  coverage-honesty, flip to `answer` (user reviews the § 16 values), replacement
  absence-item on a still-missing tax law.

## 5. Eval & measurement (F2/F3)

- **F2 items (`gs-051+`, user review gate):** ≥3 per flag that must be blocked (incl.
  the probes' hardest non-tax cases), **≥4 must-pass-through traps per §2's IN column**
  (the false-positive tests — most important), 1 PII-in-general-form allow, 1
  legal-information-about-illegal-act allow, 2 injection/smuggling.
- **Non-regression:** none of the existing 50 golden items may raise any flag. Cheap to
  assert: run `classify_request` alone over all 50 questions — ~50 lite calls, no agent.
  This becomes the **L0-equivalent fixture** for every classifier-prompt iteration.
- **F3 (L2):** full matched pair `F_SCOPE_GUARD` on/off, diff-first judging. Expected
  footprint ≈ only blocked items (pass-through is byte-identical), so diff-first is
  maximally effective here. Judge prompt must learn `out_of_scope`/`pii_block` first.
  Decision rule per backlog §2.

## 6. Settled decisions (user approved 2026-08-02)

1. **Classifier model:** pin `gemini-3.5-flash-lite`. **Probe it before first use**
   (one tiny generateContent call, traps index) and verify it follows strict-JSON on
   Danish input; if it misbehaves, fall back to the agent's pinned flash id. Env var
   `SCOPE_CLASSIFIER_MODEL` so the swap is config, not code.
2. **History window:** classifier input = the question + last **4** messages of
   history (same truncation style as `AGENT_HISTORY_MESSAGES` handling).
3. **Fail policy: fail-open** on classifier error/timeout for all three flags — log a
   warning event so quiet failures are visible in Tankestrømmen/logs, then run the
   agent normally.
4. **PII logging: redact.** When the pii flag fires, persist the prompt as
   `[REDACTED-PII]` in `mr_runs` and any eval/observability records; only the flag +
   reason are stored. The raw prompt goes to the classifier call and nowhere else.
5. **§2.3 rulings table and §3 template wordings: approved as written.**

Still gated on the user separately (legal content, not design): the gs-039 rework
values (§4) and every F2 item before it becomes ground truth.
