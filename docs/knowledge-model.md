# Knowledge Model

> Cross-cutting contract shared by all components. If a field is not defined here, no component may rely on it.

## 1. Big picture

Everything in ai-opspedia is a **document** (a Markdown file with YAML frontmatter). Documents describe **entities**
(things that exist in the target system: a DAG, a table, an index…) and are connected by **edges** (DAG *writes*
table, index *built_by* DAG, incident *affected* DAG…). Documents are split into **chunks** for retrieval.

```
 entity (dag:reco.feature_store_daily) ──described_by──▶ document (Systems/Reco/DAGs/feature_store_daily.md)
     │                                                         │  frontmatter + body + provenance
     ├──writes──▶ entity (table:dw.user_features)              └──▶ chunks (heading-bounded, ~500 tokens)
     └──builds──▶ entity (index:search-prod.products_v3) ──version_of──▶ entity (index_family:search-prod.products)
                        ◀──affected── entity (incident:ops.inc-2291)      ◀──reads── entity (service:reco.ranking-api)
```

## 2. Identifiers

| Kind | Canonical ID format | Example |
|---|---|---|
| Entity | `<type>:<namespace>.<name>` (lowercase, stable) | `dag:reco.feature_store_daily`, `table:dw.user_features`, `index:search-prod.products_v3`, `index_family:search-prod.products`, `alias:search-prod.products` |
| Document | tree path without extension | `Systems/Reco/DAGs/feature_store_daily` |
| Chunk | `<doc_id>#<heading-slug>[~n]` | `Systems/Reco/DAGs/feature_store_daily#backfill~2` |
| Source | URI-like | `git://reco-dags@9f3c2e1:dags/feature_store.py#L12-L88`, `airflow://prod/dags/feature_store_daily@2026-09-28T02:00Z`, `es://prod/products_v3/_mapping@…`, `jira://OPS-2291` |

IDs never contain secrets or hostnames of credentials.

**Namespace rule:** `dag`, `pipeline`, `model`, `service` → owning **system** key (`reco`); `table` → **database/dataset**
(`dw`); `index`, `index_family`, `alias`, `index_template`, `lifecycle_policy` → configured **cluster key**
(`search-prod`, which also encodes the environment); `incident`, `runbook` → source project (`ops`); `team`, `system` → none.
The environment of DAGs/tables lives in `env:` frontmatter, not in the ID.

**Tree-path case mapping:** tree segments use display names from config (`systems.reco.title: Reco`,
`clusters.search-prod.title: search-prod`); leaf names keep the original identifier (`feature_store_daily`).
The document `id` **is** its tree path; the categorizer's output is that `id`. A move (id change) writes a `redirects`
row (old → new) and re-keys chunks, `entities.doc_id`, `edges.doc_id` in the same transaction ([03-storage](components/03-storage.md) §3).

## 3. Document types

| `type` | Generated from | Generator | Example tree path |
|---|---|---|---|
| `dag` | DAG code (AST) + Airflow REST snapshot | deterministic + LLM summary | `Systems/<system>/DAGs/<dag_id>` |
| `task` | (section inside the DAG page; entity only) | deterministic | — |
| `table` | DDL / information_schema | deterministic + LLM summary | `Systems/<system>/Schemas/<db>/<table>` |
| `index_family` | concrete indices grouped by alias / index pattern (`products_v*` → `products`) — the **stable page** that survives rollovers & reindexes | deterministic + LLM summary | `Search/<cluster>/Indices/<family>` |
| `index` | one concrete index (`products_v3`): mapping, settings, stats, lifecycle state — a **version section/row** of its family page, no own page | deterministic | (section of family page) |
| `alias` | `_alias` / `_cat/aliases` | deterministic | `Search/<cluster>/Aliases/<alias>` |
| `index_template` | `_index_template` (+ legacy `_template`) | deterministic | `Search/<cluster>/Templates/<name>` |
| `lifecycle_policy` | ES `_ilm/policy` / OpenSearch `_plugins/_ism/policies` | deterministic + LLM summary | `Search/<cluster>/Policies/<name>` |
| `service` | config (v1): serving APIs / consumers of tables & indices | config + LLM | `Systems/<system>/Services/<name>` |
| `pipeline` | **config**: list of DAGs or a DAG tag (e.g. "candidate generation") → deterministic DAG order + lineage; LLM overview (M2) | deterministic + LLM | `Systems/<system>/Pipelines/<name>` |
| `start_here` | human-written onboarding page per system (hand-authored `note`, pinned first in the tree) | human | `Systems/<system>/Start here` |
| `model` | ML model registry / DAG params (optional) | deterministic + LLM | `Systems/<system>/Models/<name>` |
| `incident` | postmortems, ITSM tickets, alert logs | LLM (source-grounded) | `Incidents/<yyyy>/<id>-<slug>` |
| `runbook` | manuals, extracted procedures from incidents | LLM (source-grounded) + human | `Runbooks/<area>/<slug>` |
| `manual` | existing docs (MD/PDF/Confluence) | LLM standardizer | `Manuals/<space>/<slug>` |
| `system`, `team` | config + LLM aggregation | deterministic + LLM | `Systems/<system>`, `Teams/<team>` |
| `note` | human-authored | human | anywhere |

## 4. Frontmatter schema (v1)

```yaml
---
id: Systems/Reco/DAGs/feature_store_daily        # = tree path (required)
title: feature_store_daily — daily user feature build   # required
type: dag                                        # §3 (required)
entity: dag:reco.feature_store_daily             # primary entity described (optional for notes)
system: reco                                     # owning system (tree + facet)
team: reco-platform                              # owning team (facet)
tags: [features, daily, sla-critical]
env: [prod]
summary: >-                                      # 1–3 sentences, used in search results & agent context
  Builds dw.user_features every day at 02:00 KST from event logs; feeds ranking and the products index.
entities:                                        # mentioned entities (auto-extracted; drives backlinks)
  - table:dw.user_features
  - index_family:search-prod.products
runbooks: [Runbooks/Reco/feature-store-backfill]   # human-curated links (authoritative, beats extraction)
links:                                           # rendered as buttons; defaults come from config per system
  airflow: https://airflow.internal/dags/feature_store_daily/grid
oncall: reco-platform-oncall                     # from config (team → rotation/channel); shown in the page header
status: generated                                # generated | reviewed | verified | stale | archived
review: { by: null, at: null }                   # set when a human reviews/verifies
sources:                                         # provenance (required for generated docs)
  - uri: git://reco-dags@9f3c2e1:dags/feature_store.py#L12-L88
    hash: sha256:4be1…
    fetched_at: 2026-09-28T01:10:00+09:00
generator: { name: dag-renderer, version: 1.2.0, llm: claude-opus-5, prompt: dag-summary@3 }
content_hash: sha256:77a0…                        # hash of body (without frontmatter) for idempotency
updated_at: 2026-09-28T01:12:00+09:00
human_override: false                            # true → generator never rewrites this file
---
```

Rules:
- Unknown keys are preserved on rewrite (forward compatibility).
- `status: reviewed` or `verified` + a source hash change ⇒ facts tables update in place, prose becomes a *proposed
  revision*, and the page goes `status: stale` until a reviewer accepts ([02-synthesis](components/02-synthesis.md) §4).
- Optional ordering/structure keys: `sort_key` (tree order), `aliases` (extra names for the linker).
- Generated regions are fenced with `<!-- gen:start name --> … <!-- gen:end -->` so humans can add text outside them safely.
- **Human corrections** are fenced with `<!-- human:start name --> … <!-- human:end -->`. They supersede the generated
  section of the same name, survive regeneration, and are given to the LLM as authoritative source `S0` ([ADR-014](decisions.md#adr-014)).
- Every entity has a **stable URL** `/e/<entity_id>` that redirects to its current page. It survives moves and index
  rollovers, which makes it safe for Airflow `doc_md`, `on_failure_callback` messages and alert templates.

### Body templates (section order is fixed → stable diffs)

| Type | Sections |
|---|---|
| `dag` | Summary · Schedule & SLA · Tasks (table) · Inputs / Outputs · Downstream impact · Backfill & idempotency · Known incidents · Runbooks · Source |
| `table` | Summary · Columns (table) · Partitioning & freshness SLA · Producers · Consumers (DAGs, services, indices) · Known incidents · Source |
| `index_family` | Summary · Current state (alias → write/read index, health, docs, size, lifecycle phase) · **Versions** (table of concrete indices: created, docs, size, health, mapping hash) · Mapping (current, with diff vs previous version) · Lifecycle policy · Builder DAG & schedule · Consumers (services) · **Runbooks: rollover / reindex / alias swap / rollback** · Known incidents · Source |
| `alias` | Points to (index + is_write_index) · History of swaps (from snapshots) · Consumers · Source |
| `incident` | Summary · Impact · Timeline · Detection · Root cause · Fix · Follow-ups · Affected entities · Sources |
| `runbook` | Trigger / symptoms · Pre-checks · Steps · Verification · Rollback · Owner · Related |

## 5. Entity & edge types

| Entity type | Key attributes (stored in `entities.attrs` JSON) |
|---|---|
| `dag` | schedule, owner, tags, retries, sla, catchup, start_date, tasks[], datasets[], `dynamic` (bool: AST could not resolve the task list) |
| `task` | dag, operator, upstream[], downstream[], pool, sla, callable/sql ref, `columns_read[]` / `columns_written[]` (from sqlglot; `SELECT *` → `unknown_columns: true`), `external_deps[]` (from `ExternalTaskSensor` / `external_dag_id`) |
| `table` | db, schema, columns[{name,type,nullable,comment}], partition keys, row estimate |
| `index_family` | cluster, pattern, alias, current write/read index, builder DAG, policy, freshness expectation (max age of current index) |
| `index` | family, created, mapping hash, shards/replicas, health, docs.count, store.size (snapshots), lifecycle phase/state |
| `alias` | cluster, target indices, is_write_index |
| `index_template` / `lifecycle_policy` | patterns, priority, phases/states, rollover conditions |
| `service` | system, owners, reads (tables/indices/aliases), endpoints (config) |
| `incident` | id, severity, started/resolved, detection, root cause class, affected entities |
| `runbook` | trigger/symptom, steps, verification, rollback, owner |
| `system`, `team`, `model`, `pipeline` | name, owners, links |

| Edge (`src --rel--> dst`) | Derived from |
|---|---|
| `dag --contains--> task` | DAG AST |
| `task --upstream_of--> task` | REST `tasks.downstream_task_ids` |
| `dag --waits_for--> dag/task` | `ExternalTaskSensor` / `external_dag_id` args in the AST (cross-DAG chaining, typical in recsys pipelines) |
| `task/dag --reads--> table`, `--writes--> table` | SQL in operators (sqlglot), dataset/outlets, LLM extraction (flagged `inferred`) |
| `dag --builds--> index`, `alias --points_to--> index`, `index --version_of--> index_family` | ES alias API, index pattern config, DAG code (index names), LLM extraction |
| `index_template/lifecycle_policy --applies_to--> index_family` | template patterns, policy assignment |
| `service --reads--> table/index_family/alias` | config (v1), LLM extraction from manuals (inferred) |
| `incident --affected--> dag/table/index` | incident extraction |
| `runbook --fixes--> incident/dag/index` | extraction + human |
| `doc --mentions--> entity` | entity extractor (drives backlinks) |
| `entity --owned_by--> team`, `--part_of--> system/pipeline` | config / frontmatter |

Every edge has `confidence` (`parsed` = from code/API, `inferred` = from LLM, `human`) and `source` (provenance URI).
The UI shows inferred edges dashed; agents receive the confidence field.

## 6. Chunks

- Split on Markdown headings (H2, then H3), then pack paragraphs to **~500 tokens (max 800)** with **~60 tokens of
  overlap that resets at each heading**; code blocks and tables are never split mid-block (oversized ones become their own chunk).
- Each chunk stores a **contextual header** (`title › section path · type · entity · system`) that is prepended for
  embedding and BM25 (a lightweight form of Anthropic's *Contextual Retrieval*, [research.md](research.md) §3).
- Chunk rows keep `doc_id`, `heading_path`, `char_start`, `char_end`, `offsets` (analyzed-token → original-text map for
  snippet highlighting), `content_hash`, `embed_model`, `vector` (same names as [03-storage](components/03-storage.md) §3).
