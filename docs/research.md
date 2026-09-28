# Research & Investigation

> Evidence tags: **[measured]** run on the target host · **[strong]** primary docs/papers · **[moderate]** vendor or
> secondary sources · **[inferred]** our reasoning. Research date: 2026-09-28.

## 1. Summary — what the investigation changed in the plan

| Finding | Consequence in the plan |
|---|---|
| Stock SQLite FTS5 `trigram` cannot match 2-syllable Korean words (`장애`, `배치`) **[measured]** | Own analyzer: Hangul bigrams + identifier splitting before FTS5 ([03-storage](components/03-storage.md) §4) |
| Brute-force cosine is fast enough (5.3 ms @ 50k×1024; ~9 ms @ 100k) **[measured]** | No vector DB / ANN index; float32 BLOBs + numpy ([ADR-004](decisions.md#adr-004)) |
| DeepWiki-style generation misses important components and is mistaken for official docs **[moderate]** | Generate **one page per inventoried entity**, label pages "AI-generated", show sources ([02-synthesis](components/02-synthesis.md)) |
| Karpathy "LLM Wiki": raw/ → LLM wiki → schema; operations **ingest / query / lint**; append-only log **[strong]** | `raw` snapshots vs `content/`; nightly **lint** job; `log` table; answer write-back (P2) |
| Hash-based staleness over-triggers (~34 % of raw-hash changes mattered) **[moderate]** | Hash a **normalized semantic view** (parsed DAG graph / DDL AST / mapping JSON), not raw bytes |
| Airflow REST is ground truth; AST fails on dynamic DAGs **[strong/moderate]** | DAG connector = REST snapshot + AST for code detail; mark `dynamic: true` when AST can't resolve |
| OpenSearch uses **ISM** (`_plugins/_ism/explain`), Elasticsearch uses **ILM** (`_ilm/explain`) **[strong]** | Index connector branches by engine |
| Contextual Retrieval: −49 % retrieval failures with contextual embeddings + BM25, −67 % with rerank **[strong]** | Deterministic contextual header per chunk (free); LLM context lines optional (P1) |
| RRF (k=60) needs no score normalization; robust for k∈[20,100] **[strong]** | Fusion = RRF; rerank optional |
| Anthropic has **no embeddings endpoint**; Korean-capable options: Voyage (API), bge-m3 / KURE-v1 (local) **[moderate]** | Pluggable embedder; default Voyage API, local bge-m3 fallback; decide with a 30–50 query eval ([ADR-006](decisions.md#adr-006)) |

## 2. Environment investigation (target host: `huklee-01`, Mac mini on the tailnet)

| Item | Found | Note |
|---|---|---|
| Python | system 3.9.6, **Python 3.13.7** at `/usr/local/bin/python3.13`, **uv 0.9.5** | use 3.13 via `uv` project |
| SQLite | **3.51.0** with FTS5 (unicode61, trigram) | JSON1, window functions, WAL available |
| Docker / Postgres / Airflow / ES locally | not installed | reinforces Option A (no local infra); target Airflow/ES are remote |
| Existing projects | `ai-research-note` (Go, tailnet HTTPS app with accounts, channels, SQLite), `peekadoc` / docserv (read-only Markdown browser rendered via MkDocs Material), `claude-slack-bridge` | reuse patterns: tailnet-only + TLS manager, account model, terminal UI guide; peekadoc's tree + deep-link UX |

### Measurements [measured]

**Korean-aware FTS5 prototype** (Hangul → overlapping bigrams; identifiers split on `_ . camelCase` and kept whole; `unicode61`):

| Query | Hit | Why it matters |
|---|---|---|
| `장애` (2 syllables) | ✅ docs 1,3 | stock `trigram` returns **0** |
| `파이프라인` | ✅ | bigrams AND-ed |
| `feature store` | ✅ `feature_store_daily` | identifier splitting |
| `alias 전환`, `임베딩 생성` | ✅ | mixed Korean/English |

**Query-builder check** (review pass 2) on contentless FTS5 with `tokenchars '_'`:

| Case | Result |
|---|---|
| `feature_store_daily` | kept as one token (plus parts) |
| `es-prod.products_v3` | matches via the `es_prod_products_v3` token |
| `장애가` | → `장애` (particle stripped) |
| quoted `NOT OR` | treated as literal words |
| delete by rowid | works |
| `대응장애` (no space) vs `장애 대응` | needs the relaxed OR fallback |

**Vector search:** numpy float32, normalized, matmul + argpartition — 50 000 × 1024 → **5.3 ms, 205 MB**;
research agent: 100k × 1024 ≈ 9 ms (410 MB), 20k × 1024 ≈ 2 ms. Expected corpus: 5–20k chunks → **< 3 ms**.

## 3. Prior art — what we borrow (and what we don't adopt)

| System | Borrow | Don't adopt because |
|---|---|---|
| Karpathy "LLM Wiki" (2026) — [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | raw/wiki/schema layers, **lint** (contradictions, stale, orphans, missing links), `index`/`log`, write-back of good answers | it's a pattern, not a product — perfect fit |
| DeepWiki (Cognition) — [docs](https://docs.devin.ai/work-with-devin/deepwiki) | links back to source lines, diagrams | coverage bias; we drive generation from an entity inventory |
| Backstage catalog & TechDocs — [annotations](https://backstage.io/docs/features/software-catalog/well-known-annotations/) | entity kinds + owner/relations in YAML, **scorecards** (completeness checks) | platform (Node, plugins) — too heavy |
| OpenMetadata / DataHub — [ES connector](https://docs.open-metadata.org/latest/connectors/search/elasticsearch/yaml), [Airflow lineage](https://docs.datahub.com/docs/lineage/airflow) | entity model Pipeline→Task→Table→SearchIndex, sqlglot-based lineage | full metadata platforms; operating them is a project itself |
| incident.io — [AI](https://incident.io/ai-platform) | fixed postmortem template, "similar past incidents" block | SaaS |
| Anthropic Contextual Retrieval — [post](https://www.anthropic.com/engineering/contextual-retrieval) | chunk context headers, BM25 + embeddings + rerank | — (technique) |
| peekadoc (own) | tree + deep links + reader UX | MkDocs renderer is a heavy external dependency; we render in-process |

## 4. Techniques

### 4.1 Korean + identifiers in keyword search
- `unicode61` treats Korean words-with-particles as distinct tokens (서울은 ≠ 서울) → misses **[moderate]**.
- `trigram` handles particles but needs ≥ 3 characters **[moderate]**; workarounds: LIKE fallback, dual index, bigram tokenizer (fts5-cjk).
- Morphological pre-tokenization with **kiwipiepy** (pip, pure wheel) gives higher precision in Korean BM25 benchmarks (AutoRAG) **[moderate]**.
- **Decision:** v1 = own bigram analyzer (zero deps, measured). P1 = add a kiwipiepy morpheme column and pick per the eval set ([ADR-005](decisions.md#adr-005)). Identifiers are never morph-split.

### 4.2 Hybrid retrieval
- RRF, k = 60 (Cormack et al. 2009) **[strong]**.
- Rerank: cross-encoder `bge-reranker-v2-m3` (<100 ms GPU, slower CPU) vs LLM rerank (0.6–2 s for 50) **[moderate]**.
  **Decision:** RRF only for the UI; optional Claude rerank of top-20 for agent calls (`rerank=true`), no local model in v1.
- Chunking: heading-bounded, ~500 tokens (max 800), ~60-token overlap reset at each heading, code/tables intact **[moderate]**.

### 4.3 Embeddings (Korean evidence)
MTEB-ko-retrieval nDCG@10: KURE-v1 0.762, bge-m3 0.751, KoE5 0.734 **[moderate]**; Voyage multilingual models claim
strong Korean results (vendor benchmark) **[moderate]**. Our text is mixed Korean/English + identifiers where BM25 does
the heavy lifting **[inferred]** → embeddings matter mostly for natural-language questions.

### 4.4 Source parsing
| Source | Method | Evidence |
|---|---|---|
| Airflow | REST `/api/v2` (Airflow 3, JWT via `POST /auth/token`) or `/api/v1` (Airflow 2, basic/session auth): `dags`, `dags/{id}/details`, `dags/{id}/tasks`, `dagRuns`, `importErrors`, `dagSources` (endpoint paths to be verified against the live instance) + Python `ast` for SQL/args | [API](https://airflow.apache.org/docs/apache-airflow/stable/security/api.html) [strong/moderate] |
| SQL DDL | `sqlglot.parse_one(ddl, dialect=…)`; `sqlglot.lineage` for column lineage when schema known | [sqlglot](https://sqlglot.com/sqlglot/lineage.html) [strong] |
| ES/OpenSearch | `_cat/indices?format=json&bytes=b`, `{idx}/_mapping`, `_alias`, `_settings`, `_stats`, `_ilm/explain` / `_plugins/_ism/explain` | [ILM explain](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-ilm-explain-lifecycle) [strong] |
| Confluence | `GET /wiki/api/v2/pages/{id}?body-format=storage` → XHTML (`ac:` macros) → Markdown (to verify on the instance) | [exporter ref](https://github.com/Spenhouet/confluence-markdown-exporter) [moderate] |
| PDF | pypdfium2 (fast, permissive) default; Docling for table-heavy manuals (optional) | [benchmarks](https://arxiv.org/html/2410.09871v1) [moderate] |

### 4.5 Recsys batch & search-index ops knowledge [moderate/inferred]
What operators need on each page: **freshness SLA** per table/feature (and "pipeline stuck" vs "source stuck" runbooks),
producers/consumers, **backfill procedure & idempotency notes**, model version ↔ training DAG ↔ serving batch ↔ output
table/index, index **alias → current index**, ILM/ISM policy, **rollover / reindex / alias-swap runbook**, `_stats` snapshot,
recent incidents. These become the section templates in [knowledge-model.md](knowledge-model.md) §4.

### 4.6 LLM-generated docs: pitfalls → mitigations
| Pitfall | Mitigation in this plan |
|---|---|
| Hallucinated facts | structured fields rendered by parsers; LLM writes prose only; per-section `sources`; inferred edges flagged |
| Coverage bias | page per inventoried entity; lint reports entities without pages |
| Staleness | source hash per page → `stale` state; `updated_at` / `fetched_at` shown in UI |
| Churn / diff noise | semantic hashing, temperature-free deterministic templates, per-section regeneration, fenced generated regions |
| Human edits lost | `human_override`, fenced regions, verified pages get *proposed* revisions instead of overwrites |

## 5. Claude API facts used in the plan (checked 2026-09-28)
| Fact | Use |
|---|---|
| Default model **`claude-opus-5`** ($5 / $25 per MTok); Batches API **−50 %**, ≤100k requests or 256 MB per batch, most finish < 1 h, results kept 29 days | full rebuilds and nightly synthesis via Batches |
| Structured outputs: `output_config.format` / `client.messages.parse()` (validated JSON) | entity extraction, categorization, frontmatter fields |
| Prompt caching: prefix match, cache reads ≈ 0.1× input price, ≤ 4 breakpoints | shared system prompt + schema + taxonomy cached across calls |
| Token counting: `messages.count_tokens` | cost estimates before a rebuild |
| Server-side refusal fallbacks (`fallbacks: "default"`, beta header) recommended for Opus 5 | enabled on interactive calls (not available on Batches) |
| No embeddings API | separate embedder ([ADR-006](decisions.md#adr-006)) |

**Cost estimate (inferred; revised in review pass 2):**
- One call per item: ≈ 2 000 source items × ~6k input + ~1.5k output tokens ≈ 12 M in / 3 M out. On Batches with
  `claude-opus-5`: 12 × $2.5 + 3 × $12.5 = $67.5 per call type.
- Items actually make **2–4 calls** (prose/standardize, summary, extraction, category) and emit thinking tokens.
- **With Batches: full build ≈ $120–250**, daily incremental (≈ 2–5 % changed) ≈ $4–10/day.
- **v1 uses synchronous calls** (review pass 3 simplification) → about 2×: **≈ $250–500 full build, $8–20/day**.
- The budget assumes no cache benefit inside Batches. Measure on the first run and re-baseline.
