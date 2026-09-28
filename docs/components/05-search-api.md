# 5. Search & Retrieval API (`opspedia/api/`)

## 1. Big picture

One JSON API serves **both** the wiki UI and external AI agents / AIOps services. Retrieval is **hybrid**: BM25
(keywords, identifiers, Korean) ⊕ vectors (meaning) fused with RRF, optionally reranked; every result carries citations.

```
 query ─▶ parse (filters: type: system: team: tag: status: env:, quoted phrases, entity ids)
       ├─▶ entity/title exact match ─────────────┐
       ├─▶ FTS5 BM25 over chunks & docs (top 50) ├─▶ RRF(k=60) ─▶ group by doc ─▶ [rerank top-20]* ─▶ results + snippets + citations
       └─▶ vector top 50 (if embedder on) ───────┘                                * agent calls / `rerank=true`
```

## 2. Endpoints (v1)

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /api/search?q=&type=&system=&team=&tag=&status=&env=&path=&updated_after=&updated_before=&limit=&mode=hybrid|keyword|semantic&rerank=` | ranked docs with best chunk snippets (highlighted) + facet counts (type, system, team, tag, status, env, top-level path = *category*, updated date buckets: 7d / 30d / 90d / older) | viewer |
| `GET /api/suggest?q=` | ⌘K: titles/entities prefix + top hits, < 50 ms | viewer |
| `GET /api/pages/{id}` | page (frontmatter, HTML, raw Markdown, toc, status, sources, backlinks) | viewer |
| `GET /api/tree?parent=` | children of a node (lazy) | viewer |
| `GET /api/tree/context?id=&depth=` | **Tree Context Provider**: node + ancestors (breadcrumbs + summaries) + children summaries + key facts — one call for agents/UI | viewer |
| `GET /api/context?dag_id=&task_id=` · `?index=` · `?entity=` | **one-call bundle for alert handlers & agents**: entity, summary, live status (60 s cache), downstream (≤ 2 hops), known incidents, runbooks, on-call, links, page URL | token `read` / viewer |
| `GET /e/{entity_id}` | stable redirect to the entity's current page (for Airflow `doc_md`, callbacks, alert templates) | viewer |
| `GET /api/entities/{id}` | entity attrs, doc, neighbors, snapshots | viewer |
| `GET /api/graph/{id}?dir=down|up&depth=` | blast radius / lineage (JSON + Mermaid) | viewer |
| `GET /api/catalog/{dags|tables|index-families|services|incidents}` | inventories with health columns | viewer |
| `GET /api/health/indices` | search-index health checks (see [04-catalog](04-catalog.md) §5) | viewer |
| `POST /api/retrieve` | **agent RAG**: `{question, filters, k}` → top chunks with contextual headers, citations, token budget packing | token `read` |
| `POST /api/feedback` | thumbs/notes on a result or page (feeds eval set) | viewer/token |
| `POST /api/pages/{id}/review` | set `reviewed/verified`; on `stale` pages choose regenerate / keep | editor |
| `POST /api/pages/{id}/sections/{name}/correction` | save a human correction for a section (`human:` block) | editor |
| `POST /hooks/{source}` | ingestion webhooks from tailnet senders (P2; polling is the default) | signature |
| `/admin/*` | runs, lint, accounts, tokens, budgets | admin |

OpenAPI at `/api/docs` (FastAPI) — the contract for agent integrators.

## 3. Ranking details ([ADR-005](../decisions.md#adr-005))
- **Candidate generation:** chunk-level BM25 top 50 + vector top 50 (after facet pre-filter) + exact entity/title hits.
- **Fusion:** `score = Σ 1/(60 + rank_i)` over lists; exact entity-ID match forced to position 1; small priors:
  `verified +10 %`, `stale −10 %`, `archived` excluded unless `status=archived`.
- **Doc grouping:** best chunk per document shown; up to 2 extra snippets.
- **Rerank (optional, P2):** Claude receives the question + top-20 chunk texts (with ids) and returns an ordered id list via
  structured output; used for `/api/retrieve` and `/api/context` when `rerank=true`; cached by `(query, candidate ids)`.
- **Snippets/highlights** are built in Python. We don't use FTS5 `snippet()`, which returns analyzed bigram text.
  The analyzer is re-run on the top ~10 chunks at query time to locate matched original character spans; the densest window
  of about 200 characters is cut out and those spans are wrapped in `<mark>`.
- **Vector candidates** are filtered by a facet row mask *before* top-k ([03-storage](03-storage.md) §5).

## 4. Agent-facing guarantees
- Every chunk returned includes `{doc_id, heading_path, url, sources[], status, updated_at}` so agents can cite and
  judge freshness; `stale`/`generated` status is explicit.
- `/api/retrieve` packs results to a caller-supplied `max_tokens` (default 4 000), preferring diverse docs.
- Deterministic JSON shapes, versioned (`/api/v1/...` alias of `/api/...`).

## 5. Performance targets (single host)
| Call | p95 target |
|---|---|
| suggest | 50 ms |
| search (keyword/hybrid, no rerank) | 150 ms |
| page render (cached HTML) | 80 ms |
| retrieve with rerank | 3 s |

## 6. Success metrics (from `search_log`, `feedback`, audit)
Weekly active users · zero-result rate · click-through on the top 3 · feedback 👎 rate · % of SLA-critical pages
reviewed · opspedia links cited in postmortems. Shown on `/admin/metrics`.

## 7. Evaluation (`opspedia eval search`)
30–50 real questions (Korean + English + identifiers) with expected docs, collected from operators and from
`feedback`; metrics recall@5, MRR@10 per mode (keyword / semantic / hybrid / +rerank, analyzer variants). Run on every
analyzer/embedder change; results stored under `data/evals/`.

## 8. Tasks (scheduling source of truth: [roadmap.md](../roadmap.md); Pri = priority within the milestone)
| Milestone | Pri | Task |
|---|---|---|
| M0 | P0 | identity middleware (Tailscale `whois` → user, config roles), app skeleton |
| M1a | P0 | search (keyword, doc-level) with facets, suggest, pages, tree, catalog, **`/api/health/indices`**, `/e/{entity}`, feedback; search_log |
| M1b | P0 | read tokens; **`/api/context`** (live status, downstream, runbooks, on-call); `column:` search |
| M2 | P0 | eval harness (`opspedia eval search`, keyword baseline first); chunk-level + hybrid search (vectors + RRF) if the eval shows a gap; query-time snippet highlighting |
| M3 | P0 | entities & graph endpoints |
| M4 | P0 | `/api/retrieve`, `/api/tree/context`, review actions, OpenAPI polish |
| later | P2 | real MCP server over these endpoints; Claude rerank; webhooks; query understanding; saved searches |
