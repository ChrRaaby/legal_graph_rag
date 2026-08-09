# eval_history

Every eval artefact the harness produces, kept out of the repo root (which had
accumulated 56 `eval_results_*.jsonl` files by the end of the F-season).

| pattern | what it is | tracked? |
|---|---|---|
| `eval_results_<name>.jsonl` | one agent run: per-item answer, scores, usage (tokens + cost), E0 stamp | no (gitignored — they are large and reproducible) |
| `eval_fixtures_baseline.jsonl` | L0 retrieval fixture baseline (§-recall / rate-recall) | yes |
| `eval_fixtures_scope_baseline.jsonl` | L0 scope-classifier baseline (false-positive non-regression) | yes |

**Writing here is automatic.** `eval_run.py --output foo.jsonl` and
`eval_scope_fixtures.py --output foo.jsonl` resolve a *relative* name into this
folder via `app.eval_artifact_path()`. An **absolute** path is passed through
untouched, so throwaway runs can still target `/tmp`.

**Reading is automatic too.** The Maskinrummet Eval lens (`/api/eval/runs`,
`/api/eval/fixtures`) and `ab_judge.py` look here first and fall back to the repo
root, so an older checkout keeps working.

Baseline files are committed on purpose: they are the reference an L0 run is
compared against, and they are small.
