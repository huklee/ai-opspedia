# Options Analysis

## 1. Big picture

Three architectures were compared against the constraints: **Python server, hands-on components instead of adopted
open-source platforms, 20–50 users, fast implementation, flexibility/adaptability over scale.**
**Option A (Lean Python monolith on SQLite) wins clearly (4.75 / 5)** and keeps Option B as a cheap upgrade path.

| | **A. Lean monolith** ✅ | B. Postgres-centric | C. PRD-literal polyglot |
|---|---|---|---|
| Processes to run | **1** (Python) | 2 (Python + PostgreSQL) | 5–6 (Python + Postgres + OpenSearch + Qdrant + Neo4j [+ Airflow]) |
| Document store | Markdown in git + SQLite tables | PostgreSQL tables (+ git export) | PostgreSQL / MongoDB |
| Keyword search | SQLite FTS5 + own Korean/identifier analyzer | tsvector (+ `pg_bigm`/mecab extension for Korean) | OpenSearch BM25 + `nori` Korean analyzer |
| Vector search | float32 BLOBs + numpy brute force | pgvector (HNSW) | Qdrant / Milvus |
| Graph | `edges` table + recursive CTE | same (or Apache AGE) | Neo4j |
| Scheduling our jobs | in-process scheduler + CLI | same | Airflow |
| Frontend | server-rendered HTML + vanilla JS | same | React/MDX SPA (BlockNote/TipTap) |

## 2. Scoring (1 = poor … 5 = excellent)

| Criterion (weight) | A | B | C | Notes |
|---|---|---|---|---|
| Implementation speed (30 %) | **5** | 4 | 2 | A: no infra, one schema file; C: 4 clients, 4 schemas, sync jobs |
| Flexibility / adaptability (25 %) | **5** | 4 | 3 | A: change a schema = edit SQL + rebuild from git; C: reindex across stores |
| Fit with "hands-on, no adopted platforms" (15 %) | **5** | 4 | 1 | C adopts four platforms |
| Operational burden (15 %) | **5** | 3 | 1 | backups: A = copy 1 file + git; C = 4 backup strategies |
| Korean search quality (10 %) | 4 | 3 | **5** | C gets `nori` for free; A needs our analyzer (measured OK) |
| Scale headroom (5 %) | 2 | 4 | **5** | irrelevant at 20–50 users / ≤100k chunks |
| **Weighted total** | **4.75** | 3.75 | 2.40 | |

### Re-validation after the three reviews
The plan grew (index oversight, recovery rules, corrections, context API) and the estimate rose to ≈ 23–34 days. The
same requirements apply to **B and C** — they would still need the connectors, renderers, curation and API, plus
their extra stores — so the growth is option-independent and the ranking holds. Pass 3 simplifications keep A fast:
- synchronous LLM calls before Batches;
- a file lock instead of leases;
- Tailscale identity instead of accounts;
- no revisions table or offset maps;
- webhooks and MCP deferred.

## 3. Option details

### A. Lean Python monolith on SQLite ✅
- **Shape:** one FastAPI app serving the wiki and API; batch jobs are CLI commands (`opspedia run <pipeline>`) triggered
  by an in-process scheduler or webhooks, sharing the same code; one SQLite file (WAL) + a git repo of Markdown.
- **Why it fits:** at 5–20k chunks, BM25 over FTS5 and a numpy matrix scan answer in single-digit milliseconds
  ([research.md](research.md) §2); SQLite handles dozens of concurrent readers and one batch writer easily.
- **Risks & mitigations:**
  - *Single writer* → one pipeline worker with a DB lock; web writes are tiny (review status, accounts).
  - *Korean BM25 quality* → own analyzer now, kiwipiepy column as P1, evaluated on real queries.
  - *Outgrowing SQLite* → all access via `storage.Repository`; B is a module swap + rebuild from git.

### B. Postgres-centric
- **Shape:** same app; PostgreSQL holds documents, `tsvector` FTS, `pgvector`, `ltree` for paths.
- **Pros:** concurrent writers, HNSW, mature backups, easy BI access.
- **Cons:** a database server to run and secure; Korean FTS still requires an extension (`pg_bigm`/`textsearch_ko`) or our
  analyzer anyway; slower iteration on schema. **Choose if** several teams write concurrently or corpus > ~1M chunks.

### C. PRD-literal polyglot
- **Shape:** PostgreSQL/MongoDB + OpenSearch (+ nori) + Qdrant/Milvus + Neo4j, fan-out writes from synthesis,
  React/MDX frontend, Airflow for our own pipelines.
- **Pros:** best-in-class per layer, familiar to the ops team (they already run ES/Airflow), unlimited scale.
- **Cons:** 4 stores to keep consistent, 4 clients, ~4× the setup and on-call surface, weeks not days; directly contradicts
  "hands-on instead of open-source platforms". **Choose if** this becomes a company-wide platform.

## 4. Sub-decisions inside Option A

| Question | Options considered | Choice | ADR |
|---|---|---|---|
| Source of truth | DB rows / Markdown files in git / both | **git Markdown = truth, SQLite = rebuildable index** | [ADR-003](decisions.md#adr-003) |
| Web layer | stdlib `http.server` / Starlette / **FastAPI** / Django | **FastAPI** (typed, OpenAPI for agents, tiny) behind our own thin modules | [ADR-009](decisions.md#adr-009) |
| Frontend | React SPA (BlockNote/TipTap/MDX) / **server-rendered + vanilla JS** | server-rendered (no build step), progressive JS for tree, ⌘K, panels | [ADR-009](decisions.md#adr-009) |
| Markdown rendering | client-side (marked) / **server-side (markdown-it-py + Pygments)** / MkDocs | server-side: one renderer for UI, API and exports; KaTeX/Mermaid in the browser | [ADR-009](decisions.md#adr-009) |
| Embeddings | none / **pluggable: Voyage API ▸ local bge-m3 ▸ off** | pluggable, chosen by eval | [ADR-006](decisions.md#adr-006) |
| Rerank | none / cross-encoder / **RRF + optional Claude rerank** | RRF default; Claude rerank for agent calls | [ADR-005](decisions.md#adr-005) |
| Scheduling | cron + CLI / APScheduler / Airflow / **own mini scheduler + CLI + webhook** | own (~150 LOC) with a jobs table | [ADR-010](decisions.md#adr-010) |
