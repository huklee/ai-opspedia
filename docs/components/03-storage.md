# 3. Multi-Layer Storage Engine (`opspedia/storage/`)

## 1. Big picture

The PRD's four layers (document store, search engine, vector DB, graph DB) are implemented as **layers inside one
SQLite file**, with the Markdown `content/` git repo as the source of truth ([ADR-003](../decisions.md#adr-003)).

| PRD layer | Implementation | Recovered how? |
|---|---|---|
| Document Store (RDBMS/DocDB) | `documents` table + `content/` git repo (history = git log) | ✅ rebuilt from `content/` |
| Search Engine (ES/OpenSearch) | contentless FTS5 tables `doc_fts` / `chunk_fts` over **analyzed** text | ✅ rebuilt |
| Vector DB (pgvector/Qdrant) | `chunks.vector` + in-memory numpy matrix; vectors come from `data/embeddings.db` (cache by model + chunk hash) | ✅ rebuilt, **if** `embeddings.db` is kept (backed up), else re-embed (API cost) |
| Graph DB (Neo4j, optional) | `entities`, `edges` tables + recursive CTEs | ✅ parsed edges re-derived; **inferred/human edges are stored in frontmatter** (`edges:`) so they survive |

Where each piece of state lives (review pass 2 correction):

| State | Home | Rebuild behaviour |
|---|---|---|
| Page body, facts, `status`, `review`, source hashes, generator/prompt/model versions, inferred edges | frontmatter + body in `content/` | rebuilt from git. `raw_index` (last semantic hash per source) is re-derived from `sources[].hash`, so a rebuild does **not** trigger a full re-synthesis |
| Redirects | `content/_meta/redirects.yaml` | rebuilt |
| Review events, feedback, search log, tokens, identity cache, jobs, runs, snapshots, audit (+ `batch_items` later) | `opspedia.db` only | **backed up**, not rebuildable |
| Embedding cache | `data/embeddings.db` (separate SQLite file) | **backed up**. Losing it costs one re-embed |
| Raw snapshots | `data/raw/` | optional (90-day retention); re-fetchable |

All access goes through `storage.Repository` (Python protocol) so a Postgres implementation (Option B) can replace it.

## 2. Files on disk
```
content/                  # git repo (separate remote), Markdown + frontmatter, _meta/redirects.yaml; one commit per content job
data/opspedia.db          # SQLite (WAL): index + operational tables, ~100–500 MB expected
data/embeddings.db        # embedding cache (model, content_hash) → vector
data/vectors.npz          # {ids, doc_idx, matrix, generation} — replaced atomically (write tmp → os.replace)
data/raw/<source>/…       # raw snapshots (0700), retention 90 days
data/backups/             # nightly copies of opspedia.db + embeddings.db (7 daily + 4 weekly)
```

## 3. Schema (v1, abridged)
```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);            -- last_indexed_commit, schema_version, vectors_generation
CREATE TABLE documents (
  n INTEGER PRIMARY KEY,             -- rowid used by FTS (contentless tables need integer keys)
  id TEXT UNIQUE NOT NULL,           -- tree path (NFC, unique case-insensitively: see `id_fold`)
  id_fold TEXT UNIQUE NOT NULL,      -- casefold(id) — guards against APFS case collisions
  title TEXT NOT NULL, type TEXT NOT NULL, entity TEXT, system TEXT, team TEXT, env TEXT,
  tags TEXT,                         -- JSON array
  status TEXT NOT NULL,              -- generated|reviewed|verified|stale|archived
  summary TEXT, body TEXT NOT NULL, frontmatter TEXT NOT NULL,  -- JSON
  parent TEXT, depth INT, sort_key TEXT,
  content_hash TEXT, updated_at INT, commit_sha TEXT
);
CREATE INDEX documents_parent ON documents(parent);
CREATE TABLE search_log (at INT, user TEXT, q TEXT, mode TEXT, results INT, clicked_doc TEXT, rank INT);  -- metrics
CREATE TABLE redirects (old_id TEXT PRIMARY KEY, new_id TEXT NOT NULL, at INT);   -- mirror of _meta/redirects.yaml
CREATE TABLE chunks (n INTEGER PRIMARY KEY, id TEXT UNIQUE, doc_id TEXT, heading_path TEXT, ord INT, text TEXT, context TEXT,
  char_start INT, char_end INT, tokens INT, content_hash TEXT, embed_model TEXT);

CREATE VIRTUAL TABLE doc_fts   USING fts5(title, summary, body, content='', contentless_delete=1,
  tokenize="unicode61 remove_diacritics 2 tokenchars '_'");     -- rowid = documents.n
CREATE VIRTUAL TABLE chunk_fts USING fts5(context, text, content='', contentless_delete=1,
  tokenize="unicode61 remove_diacritics 2 tokenchars '_'");     -- rowid = chunks.n

CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, name TEXT, system TEXT, doc_id TEXT, attrs TEXT /*JSON*/);
CREATE TABLE aliases  (alias TEXT, entity_id TEXT, PRIMARY KEY(alias, entity_id));  -- names/identifiers → entity
CREATE TABLE edges (src TEXT, rel TEXT, dst TEXT, confidence TEXT, source TEXT, doc_id TEXT,
  PRIMARY KEY(src, rel, dst, source));   -- one row per asserting source; UI merges them (strongest confidence wins)
CREATE INDEX edges_dst ON edges(dst, rel);
CREATE TABLE snapshots (entity TEXT, metric TEXT, value TEXT, at INT);   -- volatile facts time series
-- single worker: fcntl.flock on data/worker.lock (ADR-010), no lease table
-- operational: tokens, identity_cache, jobs, runs, raw_index(uri, semantic_hash, run_id), audit, feedback (+ batch_items later)
```
Contentless FTS5 with `contentless_delete=1` (SQLite ≥ 3.43; host has 3.51) keeps the index small and allows deletes
by rowid. Snippets are built in Python for the top results only: we re-run the analyzer on those ~10 chunks at query
time to find matched spans (no stored offset maps; review pass 3), because `snippet()` would return analyzed bigram text.

## 4. Analyzer & query builder ([ADR-005](../decisions.md#adr-005))
Text is analyzed **in Python** before insertion; queries use the same function. The FTS tokenizer is `unicode61` with
`tokenchars '_'`, so our whole-identifier tokens survive.

1. **Hangul runs** → overlapping **bigrams**, in order (1-syllable runs kept as-is). Stock `trigram` misses 2-syllable
   words (measured).
2. **Identifiers** (`[A-Za-z0-9_.-]+`) → the whole identifier lowercased, with `.` and `-` mapped to `_`
   (`es-prod.products_v3` → `es_prod_products_v3`), **plus** its parts split on `_ . -` and camelCase
   (`feature_store_daily` → `feature_store_daily feature store daily`).
3. **Latin words** are lowercased; numbers are kept; stopwords are not removed (ops text is short and precise).
4. **Optional (M2 eval):** a kiwipiepy morpheme column, with weights chosen by the eval.

**Query builder** (never pass user text to MATCH raw):
- **Facets first:** extract `type: system: team: tag: status: env: path:` and date operators from the query into SQL
  `WHERE` clauses, so FTS5 never sees `word:` as a column filter.
- **Tokens:** analyze the remainder the same way and double-quote every token (doubling embedded `"`), so `AND/OR/NOT/NEAR`,
  `-`, `*`, `^` and `:` are always literal.
- **Hangul:** each run becomes a **phrase** of its consecutive bigrams (`"장애" "애대" "대응"` → `"장애 애대 대응"`), so
  word order is enforced.
- **Particles:** common Korean particles are stripped from the *query* run first
  (은/는/이/가/을/를/에/에서/의/로/으로/와/과/도/만), so `장애가` → `장애`.
- **1-syllable runs** fall back to a `LIKE` title/alias match.
- **Combine:** groups are AND-ed. If fewer than 5 results come back, retry with OR across groups, **and** with each
  Hangul phrase relaxed to an OR of its bigrams, ranked lower.
  - *Verified* on SQLite 3.51: a compound typed without a space (`대응장애`) does not match text written with one
    (`장애 대응`) until this fallback runs.
  - Such cases are part of the search eval set.
- **Ranking:** `bm25(doc_fts, 5.0, 3.0, 1.0)` (title, summary, body) and `bm25(chunk_fts, 2.0, 1.0)` (context, text).
  Weights are positional over the indexed columns, and lower scores rank better.
- **Exact entity match:** the raw query is also matched against `aliases` to boost that entity to rank 1.

## 5. Vectors ([ADR-004](../decisions.md#adr-004))
- **Build:** after each content job the worker writes `vectors.npz` = `{ids, doc_idx, matrix (float32, L2-normalized),
  generation}` atomically, and bumps `meta.vectors_generation`.
- **Reload:** the server checks `meta.vectors_generation` every 30 s (and when a job reaches `done`), loads the new file,
  verifies that the row count equals `COUNT(chunks)`, and swaps a single `(ids, doc_idx, matrix)` tuple reference.
  In-flight queries keep using the old tuple.
- **Query:**
  1. Build a boolean **row mask from the facet filter** (`np.isin(doc_idx, allowed_docs)`) *before* ranking, so
     selective facets are not starved.
  2. Compute `scores = matrix @ q`, set masked rows to −inf, take `argpartition` top-k (k = 50).
- **Memory:** about n × d × 4 bytes (20k × 1024 → 80 MB). mmap is unnecessary at this size.
- **Model change:** build a new matrix; the old one keeps serving until the switch completes.

## 6. Concurrency, durability & recovery
- **One server process** (`uvicorn --workers 1`, no `--reload` in production) and **one pipeline worker** (a
  subprocess started by the server's scheduler), serialized by `fcntl.flock` on `data/worker.lock` ([ADR-010](../decisions.md#adr-010)).
- **SQLite:**
  - WAL, `synchronous=NORMAL`, `busy_timeout=5000`.
  - Every write transaction starts with `BEGIN IMMEDIATE`. In WAL a read that upgrades to a write gets `SQLITE_BUSY`
    without a busy retry.
  - The worker commits in **batches of ~200 docs** (including FTS/vector rebuilds) so short web writes are never
    starved.
  - `sessions.last_seen` is written at most every 5 minutes.
- **Single git writer:**
  - Only the worker writes `content/`.
  - Web actions (review/verify, correction, keep/regenerate, override) write the DB at once, so the UI is immediate, and
    enqueue a high-priority `content-sync` job. The job flushes them to Markdown and commits within ~1 minute.
- **Content job protocol:**
  1. `git status --porcelain` must be clean. If not, log and `git stash` into a quarantine branch.
  2. Write files, then commit.
  3. Run the DB upserts, then set `meta.last_indexed_commit = HEAD`.
- **Recovery on startup:**
  1. Fail any `running` job whose pid is gone.
  2. Clean the worktree as in step 1.
  3. Reindex `git diff --name-only last_indexed_commit..HEAD`.
- **Page move:** `git mv` + update `_meta/redirects.yaml` + commit **first**. Then one DB transaction inserts the
  `redirects` row, updates `documents`, re-keys `chunks`, `entities.doc_id` and `edges.doc_id`, and rebuilds that doc's FTS rows.
- **Backups:** nightly `Connection.backup()` (Python sqlite3) of `opspedia.db` and `embeddings.db`, plus `git push` of
  `content/`. Restore = copy the DB files back, or `rebuild` from a fresh clone plus `embeddings.db`.

## 7. Tasks (scheduling source of truth: [roadmap.md](../roadmap.md); Pri = priority within the milestone)
| Milestone | Pri | Task |
|---|---|---|
| M0 | P0 | schema + migrations (numbered SQL files), `Repository` protocol + SQLite impl, content repo writer (git), `rebuild` |
| M1a | P0 | analyzer + query builder + contentless doc FTS; snapshots; worker flock; content-job protocol + recovery; search_log |
| M2 | P0 | chunks + chunk FTS; `embeddings.db`; `vectors.npz` generation + reload; masked numpy search |
| M3 | P1 | page moves + redirects (`_meta/redirects.yaml`) — ids are rule-derived, so moves are rare before M3 |
| M4 | P1 | backups + restore drill |
| later | P2 | Postgres `Repository` spike (only if triggers in [options.md](../options.md) fire) |
