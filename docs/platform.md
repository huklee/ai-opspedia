# Platform (cross-cutting)

## 1. Big picture
One Python 3.13 project managed by `uv`, one process (`opspedia serve`) on one host inside the tailnet, one SQLite file,
one content git repo. Everything else in this doc — config, auth, security, deploy, backup, cost, observability,
testing — is sized for **20–50 users** and a **single operator**.

## 2. Code layout
```
ai-opspedia/
  pyproject.toml  uv.lock  opspedia.yaml.example
  opspedia/
    __main__.py          # CLI: serve | run <pipeline> | rebuild | lint | eval | user | token | backup
    config.py            # YAML + env: references, pydantic-validated
    ingestion/           # connectors/, scheduler.py, raw_store.py, changeset.py
    synthesis/           # renderers/, prompts/, llm.py, standardizer.py, extractor.py, categorizer.py,
                         # summarizer.py, chunker.py, embedder.py, pipeline.py, lint.py
    storage/             # repository.py (protocol), sqlite_repo.py, migrations/*.sql, analyzer.py, vectors.py, content_git.py
    catalog/             # tree.py, registry.py, linker.py (Aho–Corasick), graph.py, scorecard.py
    api/                 # app.py (FastAPI), routes_*.py, search.py, auth.py, agent.py, hooks.py
    frontend/            # templates/, static/ (css, js, vendored katex/mermaid)
    types/               # TypeSpec registry: one module per document type (ADR-017)
    common/              # models (pydantic), hashing.py, redact.py, markdown.py (markdown-it + pygments), logging.py
  content/               # separate git repo (checked out here), generated + human Markdown
  data/                  # opspedia.db, embeddings.db, vectors.npz, worker.lock, raw/, backups/, evals/   (gitignored)
  tests/                 # unit, fixtures (recorded HTTP), golden pages, e2e (Playwright)
  docs/
```
Dependencies (ADR-001): `fastapi uvicorn jinja2 pydantic pyyaml httpx markdown-it-py mdit-py-plugins pygments sqlglot
numpy anthropic pypdfium2` (+ optional `voyageai`, `kiwipiepy`, `sentence-transformers`).

## 3. Configuration
`opspedia.yaml` (sources, schedules, taxonomy, systems/teams, LLM & embedder settings, budgets) + environment for
secrets (`env:NAME` references only). `opspedia config check` validates and pings each source read-only.

## 4. Security & privacy
| Concern | Control |
|---|---|
| Network exposure | HTTPS, tailnet-only (non-tailnet IP → 403), TLS via Tailscale cert or self-signed (pattern from ai-research-note) |
| Authentication | **Tailscale identity** (`tailscale whois` on the peer IP, cached) — no passwords to manage; fallback local accounts (scrypt, sessions, throttling) if Q12 = no ([ADR-011](decisions.md#adr-011)); CSRF header on writes |
| Authorization | viewer = anyone on the tailnet; editor/admin allow-list in config; API tokens (hashed, scoped `read`, `read+feedback`), per-token rate limit |
| Source credentials | read-only service accounts; secrets from env; never logged; never written to content or DB |
| **Data egress to LLM/embedding APIs** | redaction pass before sending (tokens, keys, DSNs, emails, IPs, configurable regexes); allow-list of source kinds that may be sent; per-source `llm: off` switch; **needs owner approval (Q5)** |
| Prompt injection from sources | sources are data inside delimited blocks; system prompt forbids following instructions in sources; outputs are schema-validated |
| Rendering safety | `MarkdownIt("commonmark", {"html": False})` (the commonmark preset enables raw HTML by default); link schemes limited to http/https/mailto/relative; Mermaid `securityLevel: "strict"`; KaTeX `trust: false`; the linker works on markdown-it tokens, never on HTML strings; strict CSP header |
| Content safety | secrets scanner on **every** page, deterministic ones included (DAG `default_args`, params, connection strings), before commit. Redaction by regex is best-effort and will miss things, so the scanner blocks the commit and nothing is pushed to the remote unscanned. Audit log of all write actions |
| Process model | one uvicorn process (`--workers 1`, no `--reload` in production), bound to the Tailscale IP; scheduler + one worker subprocess guarded by `fcntl.flock` ([ADR-002](decisions.md#adr-002), [ADR-010](decisions.md#adr-010)) |

## 5. Deployment & operations
- **Host:** Mac mini on the tailnet (launchd plist) or a small Linux VM (systemd unit) — same command `uv run opspedia serve`.
- **Upgrades:** `git pull && uv sync && opspedia migrate && restart`; migrations are forward-only numbered SQL.
- **Backups:** nightly DB `.backup` (7 daily + 4 weekly), `git push` of `content/` to a remote; quarterly restore drill
  (`opspedia rebuild` from a fresh clone must succeed).
- **Runbook for the tool itself** lives in the wiki under `Systems/ai-opspedia/` (dogfooding).

## 6. Observability
Structured JSON logs (request id, user, route, latency; run id, source, items, LLM tokens, $); `/admin/runs` with per-run
timeline and errors; `/healthz` (DB, content repo, last successful run per source, embedder reachable); metrics endpoint
(`/metrics`, Prometheus text) for request latency and pipeline counts; daily digest (failures, lint, spend) optional to Slack.

## 7. Cost model (LLM)
| Item | Estimate |
|---|---|
| Initial full build (≈ 2 000 items × 2–4 calls), **synchronous** calls on `claude-opus-5` (v1) | ≈ **$250–500** (calc in [research.md](research.md) §5) |
| same with Batches enabled (later) | ≈ $120–250 |
| Daily incremental (2–5 % changed), synchronous | ≈ **$8–20 / day** (Batches: $4–10) |
| Agent rerank calls (optional) | ≈ $0.01–0.03 per call |
| Embeddings (API) | small (chunks re-embedded only on change) |
Controls: per-run budget gate, monthly budget alert, prompt caching, idempotency skips. Levers if spend matters: enable
Batches (−50 %), or pick a cheaper model per call type (owner decision; config only). Model choice beyond the default
(`claude-opus-5`) is the owner's decision; the config makes it a one-line change per call type.

## 8. Extension mechanism ([ADR-017](decisions.md#adr-017))
| To add… | Do |
|---|---|
| a page type | one module in `opspedia/types/` (TypeSpec: id rule, pydantic models, template, edges, catalog columns, facets) + one Jinja template |
| a source kind | one `Connector` class registered in `CONNECTORS` + a config schema |
| another Airflow / cluster / Confluence space | config only (`sources:` list entry) |
| an entity/edge type | declare it in the TypeSpec that produces it; the graph, linker and facets pick it up |

## 9. Success metrics (reviewed monthly)
| Metric | Target after 1 month |
|---|---|
| Weekly active users | ≥ 60 % of the team |
| Search zero-result rate | < 10 % |
| SLA-critical pages reviewed | ≥ 80 % |
| **"3 a.m. drill"**: 5 scenarios (DAG failure, stale index, column rename, onboarding, agent context) | each answered in ≤ 2 clicks / < 30 s |
| Postmortems citing opspedia links | ≥ 50 % |

## 10. Testing strategy
| Level | What |
|---|---|
| Unit | analyzer, hashing, cron parser, renderers, merge rules, linker, RRF, graph CTEs |
| Fixture/integration | recorded HTTP for Airflow/ES/OpenSearch/Confluence/Jira; fixture DAG repos (static + dynamic); golden Markdown (byte-identical) |
| LLM contract | schema validation of recorded LLM outputs; offline mode with canned responses; small live smoke test gated by env |
| Retrieval eval | `opspedia eval search` (recall@5, MRR@10) on the operator query set |
| E2E | Playwright over the tailnet: login, tree, ⌘K, page anchors, review flow, admin runs, mobile layout |
