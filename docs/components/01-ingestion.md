# 1. Ingestion Engine & Connectors (`opspedia/ingestion/`)

## 1. Big picture

Pulls raw material from every source into **immutable, content-hashed snapshots** (`RawItem`s) and tells synthesis
*what changed*. It never interprets meaning — that's synthesis. It is the only component that talks to the target
systems, always **read-only**.

```
 sources ─▶ Connector.fetch() ─▶ normalize() ─▶ RawItem{uri, kind, body, meta, semantic_hash} ─▶ raw store (data/raw/…)
                                                                   │
 scheduler (cron | interval | webhook | CLI) ─▶ run ─▶ diff vs last run ─▶ ChangeSet{added, changed, removed} ─▶ synthesis
```

## 2. Connectors (v1 scope)

| Connector | Source | Reads | Emits `kind` | Priority |
|---|---|---|---|---|
| `dag_repo` | git checkout of the DAG repository (`git fetch` every 5 min) | `*.py` → Python `ast`: dag_id, operators and their args, SQL strings / `.sql` templates, index names, `Dataset`/`Asset` outlets, code excerpts with line numbers | `dag_code` | **P0** |
| `airflow_rest` | Airflow REST (`/api/v2` for Airflow 3 with JWT; `/api/v1` for Airflow 2 with basic/session auth) | dags (incl. `timezone`), dag details, **tasks with `downstream_task_ids` (the task graph for every DAG)**, import errors, recent dagRuns + failed task instances (last N days) | `dag_live`, `dag_runs` (snapshot) | **P0** |
| `ddl` | DDL files in repo **or** warehouse `information_schema` | `sqlglot.parse_one(…, dialect)` → tables, columns, types, comments, partitions | `table` | **P0** |
| `search_index` | Elasticsearch **or** OpenSearch REST, per configured cluster | `_cluster/health`, `_cat/indices?format=json&bytes=b` (incl. health), `{idx}/_mapping`, `_settings`, `_alias`, `_stats`, `_index_template` (+legacy `_template`), lifecycle: ES `_ilm/policy` + `_ilm/explain` ⟂ OpenSearch `_plugins/_ism/policies` + `_plugins/_ism/explain`; indices grouped into **families** by configured alias/pattern | `index_family`, `index`, `alias`, `index_template`, `lifecycle_policy`, `index_stats` / `cluster_health` (snapshots) | **P0** |
| `files` | folders of Markdown / text / PDF | MD as-is; PDF via pypdfium2 (text + page numbers; **tables are low-fidelity** and marked so, with pdfplumber as an option for table-heavy manuals) | `manual` | **P1** |
| `confluence` | Confluence **Cloud** REST v2 (`/wiki/api/v2/pages?space-id=…&body-format=storage`) **or Data Center** v1 (`/rest/api/content?spaceKey=…&expand=body.storage,version`) — `edition` in config | storage XHTML → Markdown converter (own): headings, lists, tables, code/`noformat`, panels/info/warning → admonitions, `ri:page` links → wiki links; **unknown macros → placeholder + link to the original page** | `manual` | **P1** |
| `incidents` | postmortem folder / ITSM: Jira **Data Center** REST v2 (`/rest/api/2/search`, wiki-markup comments) or **Cloud** v3 (ADF JSON comments → text) — `edition` in config | tickets (fields, comments, timeline), postmortem docs | `incident` | **P1** |
| `alerts` (monitoring logs) | **Airflow failures** via REST (failed taskInstances + log tail via `/dags/{id}/dagRuns/{run}/taskInstances/{task}/logs/{try}`) — always available; **Alertmanager** `GET /api/v2/alerts` (+ exported history) if used — *which monitoring stack is Q7* | alert/failure events grouped into episodes, attached to incidents or DAG pages | `alert_episode` (snapshot + incident evidence) | **P1** |
| `config` | `opspedia.yaml` | systems, teams, owners, taxonomy overrides | `org` | **P0** |

Each connector implements:

```python
class Connector(Protocol):
    name: str
    def fetch(self, since: datetime | None) -> Iterable[RawItem]: ...   # read-only, paginated, rate-limited
    def normalize(self, item: RawItem) -> RawItem: ...                   # stable semantic view + semantic_hash
```

`RawItem = {uri, kind, title, body (text/bytes), meta (dict), fetched_at, raw_hash, semantic_hash, env}`.

## 3. Normalization & change detection ([ADR-013](../decisions.md#adr-013))

| Kind | Semantic view hashed | Excluded (volatile) |
|---|---|---|
| `dag_code` | sorted JSON of {dag_id, schedule, default_args, tasks[id, operator, key args], edges, sql hashes} | comments, formatting, line numbers |
| `dag_live` | {schedule, owners, tags, paused, tasks, is_active} | last run, next run |
| `table` | sqlglot-normalized DDL (canonical SQL) | row counts |
| `index` | mapping + settings (minus uuid/creation_date/version) + ILM/ISM policy id | docs.count, store.size, shard allocation |
| `manual` / `incident` | text with normalized whitespace + key fields | view counts, last-viewed |

Volatile values are written to `snapshots(entity, metric, value, at)` (e.g. index doc counts, last DAG run status,
failure counts) and shown as live-ish facts on pages without causing regeneration.

A **run** compares new semantic hashes with the last successful run per `uri` → `ChangeSet`. Removed items produce
`archived` pages (never silent deletion).

## 4. DAG parsing: REST for the graph, AST for the code

- **Task graph (all DAGs):** REST `tasks` + `downstream_task_ids`. This is correct for TaskGroups (prefixed ids),
  `expand()` mapped tasks, `a >> [b, c]`, `cross_downstream`, `chain()`, and factory or config-generated DAGs, and it
  avoids re-implementing Airflow's DAG construction.
- **AST (code knowledge):** locate the DAG definition (`with DAG(...)`, `@dag`, `airflow.sdk` imports in Airflow 3)
  and map task ids to their operator call sites. Extract:
  - SQL (`sql=`, `SQLExecuteQueryOperator`, templated `.sql` files);
  - index names (string literals matching configured patterns);
  - `schedule=[Asset(...)]` / `outlets`;
  - cross-DAG dependencies: `ExternalTaskSensor(external_dag_id=…, external_task_id=…)` and partition/table sensors →
    `waits_for` edges (this is how recsys pipelines usually chain);
  - `columns_read` / `columns_written` per task from sqlglot (`SELECT *` → `unknown_columns`);
  - code excerpts with line numbers.

  If the AST can't map tasks (loops, constants imported from other modules), it sets `dynamic: true` and still
  contributes whatever it found.
- **SQL handling:** substitute Jinja placeholders first (`{{ ds }}` → `'2000-01-01'`, `{{ params.x }}` → the param
  default or `'__param__'`). Then `sqlglot.parse(sql, dialect)` handles multi-statement files. Statements that fail are
  kept as raw excerpts with a `parse_error` flag, not dropped.
- **Ground truth:** REST decides *which DAGs and tasks exist*; the AST decides *what the code does*. Mismatches are
  reported by lint.

## 5. Scheduler ([ADR-010](../decisions.md#adr-010))

| Trigger | Example | Notes |
|---|---|---|
| cron | `0 1 * * *` full structured sync; `*/30 * * * *` airflow_rest + index stats | own 5-field cron parser, KST |
| polling (default) | `git fetch` of the DAG/DDL repos every 5 min; Jira/Confluence `updated >= last_run` every 30 min | works when sources are outside the tailnet |
| webhook (optional) | `POST /hooks/git`, `POST /hooks/incident`. **Only for senders inside the tailnet** (a tailnet-only host can't receive from GitHub.com / Jira Cloud) | HMAC-SHA256 (GitHub-style `X-Hub-Signature-256`) or shared token (GitLab `X-Gitlab-Token`), checked with `hmac.compare_digest`; timestamp window ±5 min where the sender provides one; **dedupe on delivery id**; a webhook only enqueues a normal ingest job |
| manual | `opspedia run ingest --source search_index` / UI "resync" (admin) | |

**Live status on demand:** when a DAG or index-family page is opened, the server fetches the last DAG run (Airflow
REST) or the alias/health state (ES) **read-only**, caches it for 60 s, and shows it with its age ("as of 02:12, 30 s
ago"). A 3 a.m. failure is visible immediately, not after the next 30-minute snapshot.

Default schedule: `0 1 * * *` full structured sync (DAG repo, DDL, templates, policies), `*/30 * * * *` Airflow REST +
index/cluster snapshots (volatile only, no page rewrite unless the semantic hash changes).

`jobs(id, pipeline, params, trigger, priority, state, attempts, pid, started, finished, batch_id, log)`.
- **Execution:** one worker takes jobs by priority, then FIFO. `content-sync` (review flushes) runs before long ingests.
- **Failures:** a per-run timeout kills the subprocess. Transient HTTP errors get 3 retries with backoff. A failing
  connector never blocks the others: the run keeps a partial ChangeSet, and the error is recorded on the run and shown
  in `/admin/runs`.
- **Missed cron slots** (host asleep or restarted) run **once** on startup, without backfilling every slot.
  Worker lock (`flock`), job claim and recovery rules are in [ADR-010](../decisions.md#adr-010).

## 6. Configuration

Sources are a **list** of `{kind, name, …}` resolved through the connector registry ([ADR-017](../decisions.md#adr-017)),
so two Airflow instances or three clusters are config-only. Shown below in the compact map form for readability:

```yaml
sources:
  dag_repo:      { path: /srv/repos/reco-dags, branch: main, system_map: { "dags/reco/*": reco } }
  airflow_rest:  { base_url: https://airflow.internal, api: v2, auth: { user: env:AIRFLOW_USER, password: env:AIRFLOW_PASS }, envs: [prod] }
                 # v2 → POST {base_url}/auth/token for a JWT, refreshed before expiry; paths /api/v2/...; ids URL-encoded;
                 # pagination limit ≤ 100 + offset; mapped-task logs need map_index
  ddl:           { dialect: bigquery, paths: [/srv/repos/schemas/**/*.sql] }   # or info_schema: {dsn: env:DW_DSN}
  search_index:  { clusters: { search-prod: { engine: elasticsearch, url: https://es.internal:9200, auth: env:ES_API_KEY } },
                   families: { products: { pattern: "products_v*", alias: products, builder_dag: reco.products_index_build, max_age: 26h } } }
  confluence:    { edition: datacenter, url: https://wiki.internal, spaces: [RECO, SEARCH], auth: env:CONFLUENCE_TOKEN }
  incidents:     { jira: { edition: datacenter, url: …, jql: "project = OPS AND labels in (reco, search)" }, folders: [/srv/postmortems] }
```
Secrets only via `env:` references; credentials must be read-only service accounts.

## 7. Failure handling & safety
- Read-only: **GET only**, except `POST /auth/token` (Airflow 3 JWT). No ES `_search`, no write/admin endpoints.
- **Least privilege for ES/OpenSearch.** The service role needs `monitor` (cluster health/stats), `view_index_metadata`
  (mappings, settings, aliases, ILM explain) and `read_ilm`. In Elasticsearch, `GET _index_template` requires
  `manage_index_templates`, which can also *write*. So templates are **optional**: read only if the role is granted,
  otherwise shown as "not visible" and families rely on configured patterns.
- **APIs:** use `_cluster/health?level=indices` and `_stats` for machine data. `_cat/*` is documented "not for
  applications" and is used only as a fallback. Read `_data_stream` when data streams are present.
- Per-source rate limits and page size caps; 30 s request timeout; circuit-break a source after 5 consecutive failures.
- Raw snapshots may contain sensitive text → stored under `data/raw/` (0700), redaction pass (tokens, emails, IPs as
  configured regexes) **before** anything is sent to an LLM ([platform.md](../platform.md) §4).

## 8. Tasks (scheduling source of truth: [roadmap.md](../roadmap.md); Pri = priority within the milestone)
| Milestone | Pri | Task |
|---|---|---|
| M1a | P0 | `Connector` protocol, `RawItem`, raw store, run/ChangeSet logic, semantic hashing |
| M1a | P0 | `dag_repo` (AST), `airflow_rest`, `ddl`, `search_index` on **one** engine (families, mappings, aliases, stats, cluster health), `config` (systems, teams, on-call, links, pipelines, services) |
| M1b | P1 | `search_index`: templates, lifecycle policies; second engine branch only if Q2 needs it |
| M1a | P0 | jobs table, worker, cron parser, CLI `opspedia run ingest` |
| M1a | P0 | snapshots (index stats, cluster health, run outcomes) + on-demand live status (60 s cache); repo polling; `ExternalTaskSensor` + column extraction in the AST |
| M2 | P0 | `files` (MD/PDF), `confluence`, `incidents` (Jira + folder) |
| M2 | P1 | `alerts` (Airflow failures; Alertmanager if Q7 = yes) |
| later | P2 | tailnet-only webhooks; `info_schema` live warehouse reader, Slack thread export, alert-rule reader |

## 9. Tests
Fixture repos (static & dynamic DAGs), recorded HTTP fixtures for Airflow/ES/OpenSearch/Confluence/Jira, golden
normalized-view JSON; property test: re-formatting a DAG file does not change `semantic_hash`.
