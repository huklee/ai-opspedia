# 4. Tree & Catalog Manager (`opspedia/catalog/`)

## 1. Big picture

Owns **structure**: where each page lives in the tree, which entities exist, how pages and entities link to each other,
and what depends on what. It turns the flat document set into something navigable ("Systems › Reco › DAGs") and
answerable ("what breaks if `dw.user_features` is late?").

```
 documents ─▶ Tree Resolver ─────────▶ tree nodes (virtual folders + pages, counts, status badges)
 entities/aliases ─▶ Linker ─────────▶ auto-links in rendered HTML + `mentions` edges (backlinks)
 edges ─▶ Graph service ─────────────▶ neighbors, upstream/downstream within N hops, blast radius
 all ─▶ Catalog views ───────────────▶ inventories: every DAG / table / index / incident with health columns
```

## 2. Tree Resolver
- **Path-based tree** from document IDs (`Systems/Reco/DAGs/feature_store_daily`): intermediate folders are virtual
  unless an `index.md`-style document exists for them (then the folder has its own page).
- Top-level taxonomy (config, editable): `Systems/`, `Search/`, `Runbooks/`, `Incidents/`, `Manuals/`, `Teams/`, `Notes/`.
- Order: `sort_key` frontmatter → type order (overview, pipelines, DAGs, schemas, indices…) → title.
- Node payload: `{id, title, type, status, children_count, has_page, badges: [stale, failing, sla-critical]}`;
  folders load lazily (children endpoint), with a flattened cache rebuilt after each run.
- Moves: changing `tree_path` rewrites the file path in git and leaves a **redirect** row so old links keep working.

## 3. Entity registry & linker (Backlink & Cross-reference Engine)
- Registry = `entities` + `aliases` (dag_id, table names with/without schema, index names, alias names, incident IDs,
  team handles). Built from parsed facts; LLM-extracted mentions are resolved against it (never create entities from
  prose alone — they become `unresolved` lint issues).
- **Auto-linking at render time:** a single Aho–Corasick pass (our own implementation over the alias set) runs on
  **markdown-it text tokens**, never on HTML strings. It wraps identifier occurrences with links to the entity page,
  skipping code blocks, inline code, headings and existing links. Matches must fall on word or identifier boundaries.
  The longest match wins.
- **Backlinks** = `edges` with `rel = mentions` + explicit relations; each page shows *Referenced by* grouped by type.
- Explicit wiki links `[[dag:reco.feature_store_daily]]` / `[[Systems/Reco/DAGs/…|label]]` are supported in Markdown.

## 4. Graph service ([ADR-012](../decisions.md#adr-012))
| Query | SQL shape | Used by |
|---|---|---|
| neighbors(entity, rels?) | indexed lookup on `edges(src)` + `edges(dst)` | entity page side panel |
| downstream(entity, depth ≤ 4) / upstream | recursive CTE with cycle guard (`path NOT LIKE '%'||dst||'%'`) | **blast radius** panel, agent `impact` tool |
| path(a, b) | bidirectional BFS in Python over cached adjacency | "how is X related to Y" |
| incidents_for(entity, transitive) | downstream/upstream ∪ `affected` edges | "known incidents" section |

Adjacency is cached in memory (a few thousand edges) and refreshed after runs. Output is JSON plus an optional
Mermaid `graph LR` snippet the frontend renders.

## 5. Catalog views & health
Inventory tables (sortable, filterable): all DAGs (schedule, owner, last run state from snapshots, failures 7d, SLA,
page status), all tables (producer DAG, consumers, freshness SLA), all **index families** (alias → current write/read
index, health, docs, size, lifecycle phase, builder DAG, age of current index), services, incidents (severity, affected,
runbook link).

**Search-index health checks** (computed after each snapshot, shown as badges and in lint):
| Check | Rule (defaults configurable) |
|---|---|
| Index health | any index or cluster `red` → critical, `yellow` → warning |
| Alias freshness | alias → index **older than** the builder DAG's last successful run (swap missed) → warning |
| Build freshness | current index age > family's freshness expectation (e.g. 26 h for daily builds) → warning |
| Upstream freshness | the producer DAGs of the tables the builder reads last succeeded before the index was built → shows "built on data as of …" (no warehouse access needed) |
| Doc-count drift | current docs.count dropped > 20 % vs previous version / previous snapshot → warning |
| Orphans | indices matching no family, aliases pointing to missing indices, families without a builder DAG |
| Mapping change | mapping hash differs from previous version → info + diff shown on the family page |
**Completeness scorecard** (Backstage-inspired): owner set · summary present · runbook linked for SLA-critical DAGs ·
freshness SLA documented · page reviewed within 90 days — shown per system and in lint.

**Pipelines** (config: list of DAGs or a tag) render deterministically in M1b:
- DAG order (from `waits_for` edges, falling back to schedules);
- tables and index families produced;
- a Mermaid lineage diagram;
- links to each DAG page.

An LLM overview is added in M2, and a human "Start here" page per system is pinned first.

**Column-level impact:** `column:user_age` search and a *Columns used by* section on table pages list every task whose
`columns_read` / `columns_written` include the column. Tasks with `unknown_columns` (`SELECT *`) are flagged as
"may use", so a rename answers "what breaks" at column level.

## 6. Lint (catalog part, nightly + after snapshots for index checks)
Orphan pages (no parent/links), broken links, unresolved mentions, entities without pages, REST↔AST mismatches,
aliases pointing to missing indices, stale verified pages, cycles in `upstream_of`. Results in `/admin/lint` and an
optional digest to the team channel.

## 7. Tasks (scheduling source of truth: [roadmap.md](../roadmap.md); Pri = priority within the milestone)
| Milestone | Pri | Task |
|---|---|---|
| M1a | P0 | tree resolver + lazy children API; entity registry; inventories (DAGs, tables, index families); **parsed edges + plain downstream list** (reads/writes/builds/waits_for) |
| M1a | P0 | **search-index health checks** (health, alias vs builder freshness, build age, doc-count drift, orphans, mapping change) + upstream freshness from the producer DAG's last success |
| M1b | P0 | config-defined pipelines (render + lineage); column-level impact (`column:` search, *Columns used by*) |
| M3 | P0 | Aho–Corasick auto-linker, backlinks, `[[wiki links]]`; graph service (neighbors, upstream/downstream, blast radius) + Mermaid |
| M3 | P1 | full lint; service entities from config |
| later | P2 | completeness scorecard, path(a,b), taxonomy editor UI |
