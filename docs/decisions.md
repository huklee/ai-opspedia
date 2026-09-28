# Technical Decisions (ADR log)

> Format: context → decision → consequences. Status: **accepted** unless noted. Changing a decision = add a new ADR that
> supersedes the old one; never edit history.

| ADR | Decision (one line) | Status |
|---|---|---|
| [001](#adr-001) | Build small components ourselves; use libraries, not platforms | accepted |
| [002](#adr-002) | Single Python codebase: web server + batch workers, one deployable | accepted |
| [003](#adr-003) | Markdown in git is the source of truth; one SQLite file is a rebuildable index | accepted |
| [004](#adr-004) | Vectors as float32 BLOBs + numpy brute-force cosine; no vector DB | accepted |
| [005](#adr-005) | Hybrid search = FTS5 (own Korean/identifier analyzer) ⊕ vectors via RRF; rerank optional | accepted |
| [006](#adr-006) | Pluggable embedder; default Voyage API, local bge-m3 fallback, or off — chosen by eval | proposed (needs Q5) |
| [007](#adr-007) | Claude (`claude-opus-5`) for synthesis; **synchronous calls first**, Batches later; structured outputs; caching | proposed (needs Q5) |
| [008](#adr-008) | Facts from parsers, prose from the LLM; provenance on every document/edge | accepted |
| [009](#adr-009) | FastAPI + server-rendered HTML + vanilla JS; server-side Markdown rendering | accepted |
| [010](#adr-010) | Own mini scheduler + CLI + polling; single pipeline worker guarded by a file lock | accepted |
| [011](#adr-011) | Tailnet-only HTTPS; identity from Tailscale (`whois`), roles from config; API tokens | accepted (Q12) |
| [012](#adr-012) | Graph as an `edges` table with recursive CTEs; no graph DB | accepted |
| [013](#adr-013) | Incremental pipeline gated by **semantic** content hashes | accepted |
| [014](#adr-014) | Human curation: review states, fenced regions, per-section human corrections, stale + regenerate/keep | accepted |
| [015](#adr-015) | Agent interface = REST/OpenAPI incl. one-call `/api/context`; real MCP server later | accepted |
| [016](#adr-016) | Search indices: observe, document & check — no write actions in v1; index family is the stable page | accepted |
| [017](#adr-017) | Extension by registry: one `TypeSpec` module per document type, one entry per connector kind | accepted |

---

### ADR-001
**Build small components ourselves; use libraries, not platforms.**
- *Context:* the brief asks for "python server and hands-on framework instead of opensources", fast delivery, flexibility.
- *Decision:* we **do not adopt platforms** (Elasticsearch/OpenSearch for our own index, Qdrant/Milvus, Neo4j, Airflow for our
  own jobs, Wiki.js/Outline/Backstage/DataHub). We **do use small, replaceable libraries** where they save days:
  `fastapi`/`uvicorn`, `jinja2`, `markdown-it-py`(+plugins), `pygments`, `sqlglot`, `httpx`, `numpy`, `anthropic`,
  `pypdfium2`, `pyyaml`, optional `kiwipiepy`/`voyageai`/`sentence-transformers` (local embedder only). Each sits behind one of our modules so it can be replaced.
- *Consequences:* ~1 week less setup than Option C; we own the analyzer, scheduler, tree, linker, hybrid ranker.
  *Interpretation risk:* if "instead of opensources" means **zero third-party libraries**, the thin module boundary lets us
  swap FastAPI for stdlib `http.server`+`wsgiref` and markdown-it for our own renderer, at ≈ +3–4 days.

### ADR-002
**Single Python codebase and deployable.** Web (`opspedia serve`) and batch (`opspedia run …`) share models, storage and
config. The scheduler lives in the server process and spawns pipeline runs as subprocesses (crash isolation, no GIL
contention with web requests).
- **Exactly one server process:** `uvicorn --workers 1`, no `--reload` in production. Several workers would mean
  several schedulers and several vector matrices.
- *Consequence:* one venv, one systemd/launchd unit, one log stream.

### ADR-003
**Git Markdown is the source of truth; SQLite is a rebuildable index.**
- *Context:* need versioning, diffs, human review, easy export, and freedom to change DB schema quickly.
- *Decision:* synthesis writes `content/**/*.md` and commits (one commit per pipeline run, message = run id + summary);
  `opspedia rebuild` reconstructs `opspedia.db` (documents, FTS, chunks, vectors cache, entities, edges) from `content/`
  + `embeddings.db`. The content repo also carries status, review, source hashes, generator/prompt/model versions,
  inferred edges and `_meta/redirects.yaml`, so a rebuild neither loses links nor re-synthesizes everything.
  Operational tables (tokens, identity cache, jobs, runs, review events, feedback, search log, snapshots, audit; `batch_items` later)
  live only in SQLite and are **backed up** ([03-storage](components/03-storage.md) §1).
- *Consequences:* schema changes are cheap (drop + rebuild); history = `git log`; storage interface keeps Option B possible.

### ADR-004
**Vectors in SQLite BLOBs, searched with numpy.** Measured 5.3 ms for 50k×1024 on the host; corpus estimate 5–20k chunks.
Vectors load into one normalized float32 matrix from `data/vectors.npz` (ids, doc_idx, matrix, generation), replaced
atomically after each content job; no mmap needed at this size ([03-storage](components/03-storage.md) §5).
Revisit when > 500k chunks or < 20 ms p95 no longer holds (then: sqlite-vec ANN or pgvector).

### ADR-005
**Hybrid search.** BM25 from FTS5 over analyzed text (Hangul bigrams + identifier parts + whole identifiers) and cosine
top-k are fused with **RRF (k=60)**; filters (type, system, team, tags, status, env) are applied in SQL before ranking.
Exact entity-ID / title hits are boosted to rank 1. Optional `rerank=true` asks Claude to reorder the top-20 (agent API).
P1: add a kiwipiepy morpheme column, pick analyzer weights by the eval set (`opspedia eval search`).

### ADR-006
**Pluggable embedder** (`Embedder.embed(texts) -> float32[n, d]`) with providers: `voyage` (API, multilingual, default
if external APIs are allowed), `local` (bge-m3 / KURE-v1 via sentence-transformers on CPU, ~2 GB, slower batch),
`off` (BM25-only; everything still works). Embeddings are cached by `(model, chunk_hash)` so switching providers only
re-embeds once. Final pick after the 30–50-query eval in M2. *Status proposed* until the data-egress question is answered.

### ADR-007
**Claude for synthesis.** Model `claude-opus-5` (default per current guidance). **v1 uses synchronous calls through a small thread
pool (4–8 concurrent), with the budget gate** — the simplest correct pipeline (review pass 3). **Message Batches** (−50 %)
are added later (roadmap "later", design kept in [02-synthesis](components/02-synthesis.md) §3.2) once volumes justify them.
Per-call-type model choice (e.g. a Sonnet-class model for summaries) is an owner decision exposed as config; **structured outputs** (`messages.parse` /
`output_config.format`) for all JSON (entities, category, frontmatter fields); **prompt caching** on the frozen system
prompt + schema + taxonomy; `count_tokens` for pre-run cost estimates; server-side refusal fallbacks enabled on
interactive calls. Prompts are versioned files (`synthesis/prompts/*.md` with `@version`). If external LLM use is not
allowed: `llm=off` mode keeps deterministic pages, BM25 and graph (M1 scope) fully functional.

### ADR-008
**Facts from parsers, prose from the LLM.** Deterministic renderers own all structured fields and tables; the LLM fills
fenced prose sections (`summary`, `purpose`, `how it fails`, `runbook draft`) from the provided source excerpts only,
and must cite `sources[n]` per section; uncited or unresolvable claims fail validation and the section is left empty.

### ADR-009
**Web stack.** FastAPI (API + OpenAPI docs) + Jinja2 templates + vanilla JS modules (tree, ⌘K, panels); no SPA build
chain. Markdown → HTML on the server with markdown-it-py (+ tables, anchors, footnotes, task lists, front-matter) and
Pygments (line numbers); KaTeX and Mermaid run in the browser (vendored assets for tailnet-only use). The PRD's
BlockNote/TipTap/MDX editors are deferred: v1 is a reader; editing happens in Markdown (git) or via small review actions.

### ADR-010
**Own mini scheduler.** Cron expressions (own parser for `m h dom mon dow`), interval triggers, polling, and optional
tailnet-internal webhooks all **enqueue** runs into a `jobs` table. One worker executes them by priority (SQLite has a
single writer).
- **Single worker:** an OS file lock (`fcntl.flock` on `data/worker.lock`) — released automatically on crash; the CLI
  `--direct` path takes the same lock. (Replaces the heartbeat lease table from pass 2: single host, simpler.)
- **Claiming a job:** one statement, `UPDATE jobs SET state='running', pid=? WHERE id=(SELECT id … LIMIT 1) RETURNING *`.
- **Startup:** `running` jobs whose pid is dead are marked failed and retried. Missed cron slots run **once**.
- **Timeouts:** the worker kills the subprocess.
- **Long LLM batches** (later, when Batches are enabled) are split into `synth-submit` / `synth-collect` jobs, so the
  lock is never held while waiting ([02-synthesis](components/02-synthesis.md) §3.2).
- **CLI:** `opspedia run …` enqueues into the server's queue by default. `--direct` runs in-process only when it can
  take the lock (for example, server stopped). Webhooks are P2 (polling is the default).

### ADR-011
**Access.** HTTPS on the tailnet only (TLS manager reused from ai-research-note: Tailscale cert when the tailnet has
HTTPS enabled, else self-signed); the server **binds to the Tailscale interface address** (plus loopback for health).
- **Identity (default, Q12):** the caller's Tailscale login, resolved with `tailscale whois <remote ip>` and cached. No
  local passwords, login page, or throttling are needed.
- **Roles:** `viewer` (everyone on the tailnet), `editor` / `admin` from an allow-list in config.
- **Fallback:** if some users share nodes or aren't on the tailnet (Q12 = no), enable local accounts (scrypt hashes,
  sessions) — the ai-research-note pattern.
- **API tokens** (hashed, scope `read` / `read+feedback`) for agents and callbacks; audit log of write actions.
- Webhooks only from tailnet senders (P2); outside sources are **polled**.

### ADR-012
**Graph as a table.** `edges(src, rel, dst, confidence, source, doc_id)` with indexes on both ends; impact queries
("what is downstream of table X within 3 hops") via recursive CTE; rendered as lists + a small Mermaid graph.

### ADR-013
**Semantic hashing.** Each connector emits a normalized view (sorted JSON of parsed DAG graph/params, DDL AST, mapping
JSON without volatile stats) and its hash; synthesis runs only when that hash changes. Volatile metrics (doc counts,
last run) are stored as **snapshots** (time series table), not in page bodies, so they don't create commits.

### ADR-014
**Human curation (simplified in review pass 3).**
- Review states `generated → reviewed → verified`, `stale`, `archived`. Generated text lives inside
  `<!-- gen:start … -->` fences.
- **Per-section correction:** an editor clicks "correct" on a section and writes the right text. It is stored in the
  Markdown file as a `<!-- human:start section -->` block, which **supersedes** the generated section, survives
  regeneration, and is passed to later generations as authoritative source `S0`.
- **Source changes on a reviewed/verified page:** facts tables update, the page goes `stale` with the fact diff shown,
  and an editor chooses **regenerate** (synchronous) or **keep**. There is no separate revisions table; git is the
  history and diff viewer.
- `human_override: true` freezes a whole page (rarely needed now that corrections exist).
- Each team gets a **review queue** (`status:generated team:X`) and an optional weekly digest.

### ADR-015
**Agent interface.** Stable REST endpoints with an OpenAPI contract, token-authenticated, returning source citations:
search, page, tree-context, entity, graph, and a **one-call `GET /api/context`** (entity + summary + live status +
downstream + known incidents + runbooks), designed for alert handlers and AIOps agents. A stable `/e/<entity_id>` URL
goes into Airflow `doc_md` / `on_failure_callback` and alert templates. A real MCP server wrapping the same endpoints is
P2 (review pass 3 dropped the home-grown "MCP-style" JSON-RPC).

### ADR-016
**Search indices: observe, document, check — don't operate (v1).**
- *Context:* "search indices are also a target to manage". Rollovers/reindexes create new concrete indices
  (`products_v3 → v4`); pages keyed by concrete index would churn and lose history. Operators need health, freshness,
  drift and change history plus runbooks — and a wiki with write access to production clusters is a large risk.
- *Decision:* (1) the **index family** (alias or pattern) is the stable entity/page; concrete indices are version rows on it;
  templates and lifecycle policies are first-class pages. (2) The connector reads cluster health, indices (with health),
  mappings, settings, aliases, stats, templates and ILM/ISM policies + explain, read-only. (3) Health checks: red/yellow,
  alias older than the builder DAG's last success, build age over the family's freshness expectation, doc-count drop,
  orphans, mapping change (with diff). (4) Rollover / reindex / alias-swap / rollback **runbooks** per family.
- *Consequences:* the ops system answers "is today's index built, swapped and healthy, and what do I do if not" without
  holding write credentials. *Later (P2):* guarded actions (e.g. alias swap with 2-person approval via a separate
  credential) only if operators ask for them.

### ADR-017
**Extension by registry.**
- *Context:* adding a document type otherwise touches the knowledge model, a renderer, a template, the taxonomy,
  categorizer rules, catalog views and facets; connector config keyed by name allowed only one Airflow.
- *Decision:*
  - **`TypeSpec` registry:** one module per document type under `opspedia/types/`. Each declares:
    - `id` / tree rule
    - frontmatter and entity-attrs pydantic models
    - a Jinja template with its section list
    - edge relations (label and direction)
    - catalog columns and facets
    - an optional LLM prose spec

    Renderers, validator, catalog and search facets read the registry.
  - **Connector registry:** `CONNECTORS = {kind: class}`; `sources:` is a **list** of `{kind, name, …}`, so several
    Airflows or clusters are config-only.
- *Consequences:* new page type ≈ 1 module + 1 template; new source ≈ 1 connector class; new cluster ≈ config only.
