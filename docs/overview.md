# ai-opspedia — Overview & Master Plan

> Status: **plan v1 — reviewed ×3** ([review-log.md](review-log.md)) · Owner: huklee · Last update: 2026-09-28

## 1. Big picture

**What:** an AI-assembled operations encyclopedia for the **recommendation batch system** (Airflow DAGs,
feature / candidate / ranking / training batches, their tables) and the **search indices** it feeds
(Elasticsearch/OpenSearch indices, aliases, reindex/rollover jobs). It turns raw operational material —
DAG code, SQL DDL, index metadata, manuals, incident postmortems/tickets — into one browsable, searchable,
cross-linked wiki that both **people** (20–50 operators/engineers) and **AI agents** (AIOps assistants) can query.

**Why:** ops knowledge is scattered (DAG repo, Confluence, tickets, Slack, people's heads). During an incident the
questions are always the same — *what does this DAG do, what reads this table, which index does it feed, what broke
last time and how was it fixed* — and today answering them means opening five tools.

**How (one sentence):** Python batch pipelines parse structured sources **deterministically** and let an LLM
(Claude) **write only the prose**, store everything in one SQLite file (documents + BM25 + vectors + graph edges)
behind a single Python web server that serves a tree-navigated wiki, `⌘K` search and a JSON API for agents.

### Goals / non-goals

| Goals (v1) | Non-goals (v1) |
|---|---|
| One page per DAG, table, **index family** (stable across rollovers), alias, template, lifecycle policy, incident, runbook, service, system, team — auto-generated and kept fresh | Replacing Airflow/ES consoles or live monitoring (we snapshot, we don't operate) |
| **Search-index oversight:** health, alias/build freshness, doc-count drift, mapping changes, rollover/reindex/alias-swap runbooks | Executing index operations (reindex, alias swap) — read-only by design in v1 ([ADR-016](decisions.md#adr-016)) |
| Every fact traceable to its source (file + line, API + timestamp, ticket id) | Horizontal scale, multi-tenant, HA |
| Hybrid search (keyword + semantic) that handles **Korean + English + identifiers** | Rich WYSIWYG editing (humans edit Markdown / curate status) |
| "Blast radius" answers: DAG ↔ table ↔ index ↔ incident links | Graph database, workflow engine, search cluster of our own |
| Agent-ready API (search, page, tree context, entity lookup) | Public/internet exposure (tailnet only) |

### Chosen approach — Option A "Lean Python monolith on SQLite"

Three options were evaluated (full comparison in [options.md](options.md)):

| Option | Stack | Verdict |
|---|---|---|
| **A. Lean monolith** | 1 Python process (FastAPI) + **1 SQLite file** (docs, FTS5 BM25, vector BLOBs + numpy, edge table) + Markdown content in git | ✅ **chosen** — fastest to build, zero infra, fully hands-on, easy to change |
| B. Postgres-centric | FastAPI + PostgreSQL (tsvector, pgvector, ltree) | Good upgrade path; needs a DB server and Korean FTS still needs work |
| C. PRD-literal polyglot | Postgres + OpenSearch + Qdrant + Neo4j (+ Airflow for our own jobs) | Most capable, ~4 extra services to run; contradicts "hands-on, fast, small" |

Key numbers behind the choice (measured on the target host, [research.md](research.md) §2):
brute-force cosine over **50 000 × 1024-d** vectors = **5.3 ms / 205 MB**; SQLite FTS5 with our own Korean bigram +
identifier analyzer matches `장애`, `파이프라인`, `feature store → feature_store_daily` (stock `trigram` misses 2-syllable Korean words).
Every storage call goes through a small interface, so Option B stays a one-module swap if we ever outgrow A ([ADR-003](decisions.md#adr-003)).

### Architecture at a glance

```
 Raw inputs ─────────────────────────────────────────────────────────────────────────────────────────
  DAG repo (.py)   Airflow REST   SQL DDL / info_schema   ES/OpenSearch APIs   Manuals (MD/PDF/Confluence)   Incidents (tickets/postmortems/logs)
        │               │                 │                      │                        │                          │
        ▼               ▼                 ▼                      ▼                        ▼                          ▼
 ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ 1 ingestion/   connectors → RawItem (content-hashed snapshot) · scheduler (cron + webhook) · run log      │
 └───────────────────────────────────────────────┬──────────────────────────────────────────────────────────┘
                                                 ▼  only changed items (hash diff)
 ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ 2 synthesis/   deterministic renderers (DAG/table/index pages)  +  Claude (prose, extraction, category)   │
 │                → standard Markdown + YAML frontmatter + provenance  → chunker (+contextual header) → embed │
 └───────────────────────────────────────────────┬──────────────────────────────────────────────────────────┘
                                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ 3 storage/   content/ (Markdown in git = source of truth)   +   opspedia.db (SQLite, rebuildable index):  │
 │              documents · FTS5 (BM25) · chunks+vectors · entities · edges · snapshots · jobs · search log  │
 └───────────────┬──────────────────────────────────────────────────────────┬───────────────────────────────┘
                 ▼                                                          ▼
 ┌──────────────────────────────────────┐              ┌────────────────────────────────────────────────────┐
 │ 4 catalog/  tree resolver · entity   │              │ 5 api/  hybrid search (BM25 ⊕ vector → RRF →       │
 │   registry · backlinks · graph walks │─────────────▶│   optional rerank) · page · tree context · entity  │
 └──────────────────────────────────────┘              │   · graph · /api/context (one call) · auth         │
                                                       └───────────────────────┬────────────────────────────┘
                                                                               ▼
 ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ 6 frontend/  server-rendered wiki: directory tree · Markdown reader (code/math/tables/anchors/mermaid)   │
 │              · ⌘K quick search · entity pages · backlinks & "blast radius" panel · review status         │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Components & documents

| # | Component (code dir) | One-line responsibility | Doc |
|---|---|---|---|
| 1 | Ingestion Engine & Connectors (`ingestion/`) | Pull raw material from DAG repo, Airflow, DB schemas, ES, manuals, incidents; snapshot + hash; schedule | [components/01-ingestion.md](components/01-ingestion.md) |
| 2 | Knowledge Synthesis Engine (`synthesis/`) | Raw → standard Markdown with frontmatter, entities, tree path, summary, chunks, embeddings | [components/02-synthesis.md](components/02-synthesis.md) |
| 3 | Multi-Layer Storage (`storage/`) | Git content + one SQLite: documents/versions, FTS5, vectors, entities/edges, jobs | [components/03-storage.md](components/03-storage.md) |
| 4 | Tree & Catalog Manager (`catalog/`) | Tree, entity registry, backlinks, cross-refs, dependency graph queries | [components/04-catalog.md](components/04-catalog.md) |
| 5 | Search & Retrieval API (`api/`) | Hybrid search + rerank, page/tree-context/entity/graph endpoints, agent tools, auth | [components/05-search-api.md](components/05-search-api.md) |
| 6 | Wiki Frontend Viewer (`frontend/`) | Tree navigation, Markdown reader, ⌘K search, entity & backlink panels | [components/06-frontend.md](components/06-frontend.md) |
| — | Knowledge model (cross-cutting) | Document types, frontmatter schema, entity & edge types, IDs, provenance | [knowledge-model.md](knowledge-model.md) |
| — | Platform (cross-cutting) | Runtime, config, auth, security, deploy, backup, cost, observability | [platform.md](platform.md) |
| — | Decisions | ADR log (why each choice) | [decisions.md](decisions.md) |
| — | Research | Investigation notes, prior art, measurements, sources | [research.md](research.md) |
| — | Options | 3 options, scoring, choice | [options.md](options.md) |
| — | Roadmap | Milestones, tasks by priority, exit criteria | [roadmap.md](roadmap.md) |
| — | Review log | The 3 review passes and what changed | [review-log.md](review-log.md) |

### Design principles (apply to every component)

1. **Facts from parsers, prose from the LLM.** DAG schedules, task graphs, columns, mappings, aliases are rendered
   deterministically from code/APIs; Claude only summarizes, explains, links and classifies. No LLM-invented facts.
2. **Provenance on everything.** Each document and chunk records its sources (path@commit:line, API@time, ticket id),
   generator version and input hash; the UI shows "generated from …".
3. **Incremental & idempotent.** Content hashes gate every stage; re-running a pipeline on unchanged input is a no-op
   (no diff noise, no LLM spend).
4. **Git is the source of truth, SQLite is a rebuildable index.** `opspedia rebuild` recreates the DB from `content/`.
5. **Hands-on, replaceable pieces.** Build the small things ourselves; use libraries (not platforms) only where they
   save days; hide each behind an interface ([ADR-001](decisions.md#adr-001)).
6. **Humans stay in charge.** Pages carry a review state (`generated → reviewed → verified`, or `stale`); any section can
   be corrected in one click (`human:` block that supersedes the generated text and feeds later generations);
   human-authored pages are never overwritten by the generator.
7. **3 a.m. first.** Every entity page shows on-call, links, live status *with its age* and what's downstream; alert
   handlers get the same in one call (`/api/context`, stable `/e/<entity>` URLs).

### Delivery plan (summary — details in [roadmap.md](roadmap.md))

| Release / milestone | Outcome | Size |
|---|---|---|
| **R1 On-call catalog** (M0 + M1a, no LLM) | DAG / table / index-family / alias pages from code + APIs; live status with age; downstream list; index health checks; on-call + links; tree, ⌘K, keyword search; section corrections + feedback | **8–10 d** |
| M1b Catalog depth | pipelines + lineage, column-level impact, `/api/context` for agents, templates & policies | 3–5 d |
| M2 LLM synthesis & hybrid search | manuals & incidents → standard Markdown; prose with citations; eval-driven hybrid search | 7–10 d |
| M3 Cross-links & graph | backlinks, auto-links, multi-hop blast radius, known incidents, lint | 3–4 d |
| M4 Agents & curation | retrieve & tree-context APIs, review workflow & queues, backups, metrics | 3–5 d |
| | **Total ≈ 24–34 working days; first useful release at ~day 10** | |

### Open questions for the owner

Each question has a **default** that we use unless you say otherwise, so answering takes minutes. (★ = blocks R1)

| # | Question | Default if unanswered |
|---|---|---|
| Q1 ★ | Airflow **version and access**: REST URL + read-only account? DAG repo path? | Airflow 2.x `/api/v1` with basic auth; DAG repo cloned read-only |
| Q2 ★ | Search engine **flavor/version**, clusters, read-only credentials | Elasticsearch 8, one prod cluster; OpenSearch branch only if you say so |
| Q3 ★ | Where **schemas** live (DDL files in a repo? warehouse `information_schema`?) and SQL dialect | DDL files in a repo; dialect from config |
| Q4 | Where **manuals** and **incidents** live (Confluence spaces? Jira project? postmortem folder?) and DC vs Cloud | Confluence DC + Jira DC; postmortem folder |
| Q5 | May **redacted** internal text go to **external APIs** (Anthropic; an embedding provider)? | **No** until approved → R1/M1b need no LLM; M2 waits for approval |
| Q6 | Host | this Mac mini on the tailnet (like ai-research-note) |
| Q7 | Monitoring stack for alert history (Alertmanager, Grafana, Datadog…) | Airflow task failures only |
| Q8 | Which **services** consume the recsys tables / indices (for blast radius) | a list you give in config; empty until then |
| Q9 | Are small **libraries** (FastAPI, sqlglot, markdown-it, numpy, anthropic) OK, or must it be stdlib-only? | libraries OK ([ADR-001](decisions.md#adr-001)); stdlib-only adds ≈ 3–4 d |
| Q10 ★ | Rough **counts**: DAGs, tables, indices/families in scope | ~200 DAGs, ~500 tables, ~30 families (drives cost & estimates) |
| Q11 | A **reviewer/champion per system** | the owning team lead from config |
| Q12 ★ | Is every user on the **tailnet with their own identity**? | yes → Tailscale identity; otherwise local accounts (+1 d) |
| Q13 | Which **AIOps agent / alert handler** will call `/api/context`, and what does its alert payload look like? | Airflow `on_failure_callback` + a generic JSON alert |
| Q14 | Where do existing **runbooks** live? | Confluence pages linked via `runbooks:` in config |
