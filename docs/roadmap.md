# Roadmap

## 1. Big picture
Deliver the **3 a.m. answers first, without the LLM**. Release **R1 "on-call catalog"** (≈ 2 weeks) answers "this DAG
failed: what does it do, what's downstream, who's on call" and "is today's index built, swapped and healthy". It does
this from code and APIs alone. LLM synthesis, graph depth and agent features follow as increments.

| Release / milestone | Theme | Outcome (exit criteria) | Size |
|---|---|---|---|
| **R1** = M0 + M1a | **On-call catalog (no LLM)** | Every in-scope DAG, table, index family and alias has a page. Live status with its age; parsed downstream list; index health checks (alias/build/upstream freshness, drift, red/yellow); on-call + links; tree, ⌘K, keyword search p95 < 150 ms; section corrections + feedback; stable `/e/<entity>` URLs. **Drill:** scenarios 1–2 answered in ≤ 2 clicks / < 30 s | **8–10 d** |
| M1b | Catalog depth | config-defined pipelines with lineage; column-level impact (`column:`); templates & lifecycle-policy pages; second search engine if Q2 needs it; `/api/context` + read tokens; catalog views; metrics page. **Drill:** scenarios 3–5 (agent context via API) pass | 3–5 d |
| M2 | LLM synthesis & hybrid search | Manuals + incidents standardized; summaries & prose with citations on entity pages; pipeline overviews; keyword eval first, then vectors + RRF if the eval shows a gap (recall@5 ≥ +10 pts, or a documented reason to stay keyword-only) | 7–10 d |
| M3 | Cross-links & graph | Auto-links + backlinks; multi-hop blast radius + Mermaid; known incidents on DAG/table/index pages; entity/graph API; lint; page moves + redirects | 3–4 d |
| M4 | Agents & curation | `/api/retrieve`, `/api/tree/context`; review workflow (reviewed/verified, stale → regenerate/keep), team review queues + digest; backups + restore drill; `/healthz`, `/metrics` | 3–5 d |

**Total ≈ 24–34 working days** for one developer with an AI coding assistant. The first useful release comes at day
~10. The Confluence converter and eval-set collection depend on other people's time, so they are the least predictable.

## 2. Tasks by milestone (Pri = priority within the milestone; component docs mirror these rows)

### M0 — Skeleton (part of R1)
| Pri | Task | Component |
|---|---|---|
| P0 | `uv` project, config loader + `config check`, CLI skeleton, logging, `TypeSpec` + connector registries | platform |
| P0 | SQLite migrations, `Repository` protocol, content git writer, `rebuild` | 03 |
| P0 | FastAPI app, TLS manager, bind to Tailscale IP, **Tailscale identity** + role allow-list, base template | 05, 06 |
| P1 | CI: ruff, tests, type check | platform |

### M1a — On-call catalog core (rest of R1)
| Pri | Task | Component |
|---|---|---|
| P0 | `Connector`/`RawItem`/ChangeSet, semantic hashing; jobs + worker (`flock`) + cron scheduler; repo polling | 01 |
| P0 | connectors: `dag_repo` (AST incl. SQL, `ExternalTaskSensor`, columns), `airflow_rest`, `ddl`, `search_index` on one engine, `config` (systems, teams, on-call, links, services) | 01 |
| P0 | snapshots + **on-demand live status** (60 s cache) shown with its age | 01, 06 |
| P0 | renderers via TypeSpec: dag, table, index_family, alias, system, team; fences; determinism; commit per run; **parsed edges + downstream list** | 02, 04 |
| P0 | **index health checks** + upstream freshness + `/api/health/indices` | 04, 05 |
| P0 | analyzer + keyword search with facets, suggest, `search_log`; tree API; inventories; `/e/{entity}` | 03, 04, 05 |
| P0 | frontend: tree, reader (code/line numbers/math/tables/anchors/Mermaid), ⌘K, entity header (on-call, links, live status), **section correction**, **feedback** | 06 |

### M1b — Catalog depth
| Pri | Task | Component |
|---|---|---|
| P0 | read tokens + **`/api/context`** (entity, summary, live status, downstream, incidents, runbooks, on-call) | 05 |
| P0 | config-defined pipelines (render + Mermaid lineage); column-level impact (`column:`, *Columns used by*) | 02, 04 |
| P1 | templates & lifecycle-policy connector + renderers; second engine branch (only if Q2 needs it) | 01, 02 |
| P1 | search page with facets; catalog views; admin runs & metrics pages | 05, 06 |

### M2 — LLM synthesis & hybrid search
| Pri | Task | Component |
|---|---|---|
| P0 | `llm.py` (sync thread pool, `messages.parse`, caching, cost accounting, budget gate); validator with citations | 02 |
| P0 | summarizer & prose for entity pages; pipeline overviews; connectors `files` (MD/PDF), `confluence` (DC/Cloud), `incidents` (Jira DC/Cloud); standardizer | 01, 02 |
| P0 | eval harness + operator query set (30–50 questions, incl. no-space Korean compounds) — keyword baseline first | 05 |
| P0 | chunker + chunk FTS; embedder (ADR-006) + `embeddings.db`; masked numpy search; RRF — enabled if the eval shows a gap | 02, 03, 05 |
| P1 | extractor (registry-first), categorizer (rules-first); human corrections fed as `S0`; `alerts` connector (Airflow failures; Alertmanager if Q7) | 01, 02 |

### M3 — Cross-links & graph
| Pri | Task | Component |
|---|---|---|
| P0 | incident `affected` edges; linker (Aho–Corasick on tokens) + backlinks + `[[wiki links]]` | 02, 04 |
| P0 | graph service (multi-hop) + entities/graph API + context panel (blast radius, known incidents) | 04, 05, 06 |
| P1 | lint (orphans, broken links, unresolved mentions, REST↔AST mismatch, stale); services from config; page moves + redirects | 03, 04 |

### M4 — Agents & curation
| Pri | Task | Component |
|---|---|---|
| P0 | `/api/retrieve`, `/api/tree/context`, OpenAPI polish | 05 |
| P0 | review workflow (reviewed/verified, stale → regenerate/keep), team review queues + weekly digest | 02, 05, 06 |
| P1 | backups + restore drill, `/healthz`, `/metrics` | platform |

### Later (P2, only on demand)
Batch mode for LLM calls (−50 %); real MCP server; Claude rerank; tailnet-only webhooks; completeness scorecard;
contradiction lint; contextual-retrieval context lines; answer write-back; inline editor; guarded index actions
(ADR-016); `info_schema` reader; Postgres repository.

## 3. Dependencies on the owner
See the open questions in [overview.md](overview.md#open-questions-for-the-owner) — each has a default we use if there
is no answer. The questions that block R1 are Q1–Q3, Q10 and Q12.

## 4. Risks
| Risk | Impact | Mitigation |
|---|---|---|
| No external-API approval | no prose / semantic search | R1 value stands alone; local embedder; `llm=off` |
| Dynamic DAGs defeat AST | missing code details | REST is the task graph for all DAGs; `dynamic` flag |
| Korean keyword quality | missed results | bigram analyzer + particle stripping now; kiwipiepy + eval in M2 |
| Generated-doc distrust | low adoption | provenance, live-status age, "AI-generated" labels, one-click section corrections, review queues |
| LLM cost creep | budget | idempotent runs, caching, budget gate; Batches / cheaper model per call type as levers |
| Scope creep before R1 | late first value | R1 exit = drill scenarios 1–2; everything else waits |
