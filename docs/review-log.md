# Review Log

Three independent review passes, each by a fresh reviewer with a different focus. Every finding was either fixed
(and the fix recorded here) or explicitly declined with a reason.

| Pass | Focus | Findings | Fixed | Declined |
|---|---|---|---|---|
| 1 | PRD & brief coverage, cross-document consistency, links | 17 (2 high, 8 med, 7 low) | 17 | 0 |
| 2 | Technical feasibility & correctness | 20 (8 high, 9 med, 3 low) | 20 | 0 |
| 3 | Operator value, simplicity, delivery risk | 17 (7 high, 8 med, 2 low) | 17 (1 partly) | 0 |

---

## Pass 1 — coverage & consistency

Link check: all relative links and ADR anchors resolved (except this file, created after the pass).

| # | Sev | Finding | Fix (where) |
|---|---|---|---|
| 1 | HIGH | Search-index management only passive; pages keyed by concrete index would churn on every rollover; no cluster health, policies, templates, freshness/drift checks, no index body template | **index_family** as the stable page with concrete indices as version rows; `index_template`, `lifecycle_policy` types; connector reads `_cluster/health`, index health, templates, ILM/ISM policies; health checks (red/yellow, alias vs builder freshness, build age, doc-count drift, orphans, mapping diff); body templates incl. rollover/reindex/alias-swap runbooks; new **ADR-016** (knowledge-model §3–5, 01 §2, 04 §5, 05 §2, decisions) |
| 2 | HIGH | Roadmap order vs dependencies (graph/entity API in M4 but UI in M3; M2 exit needs P1 items) | entity/graph API → M3 P0; chunker/embedder/hybrid/eval → M2 P0 (roadmap §2) |
| 3 | MED | Component priority tables disagreed with roadmap | every component task table now has a **Milestone** column mirroring roadmap; "Pri = priority within the milestone" |
| 4 | MED | Webhooks never scheduled; "schedules" misplaced in M4 | webhooks → M1 P1; overview M4 renamed "Agents & curation" |
| 5 | MED | Chunk parameters conflicted | one spec everywhere: ~500 tokens, max 800, ~60-token overlap reset per heading |
| 6 | MED | PRD facets "category" and "date" missing; `env` missing | `path=` (category), `updated_after/before`, `env=`; date buckets in facet counts (05 §2) |
| 7 | MED | PRD "Service" graph node not modelled | `service` entity type + `service --reads--> table/index_family/alias` edges (knowledge-model §3, §5) |
| 8 | MED | Fields used but not defined (tree_path, sort_key, dynamic, redirects, offsets); name mismatches; move semantics | `id` = tree path; `sort_key`, `aliases`, `dynamic` defined; `redirects` table; chunk columns aligned (`char_start/char_end/offsets/embed_model`); page-move transaction (03 §6) |
| 9 | MED | Entity naming inconsistent; namespace rule and case mapping undefined | canonical examples; namespace rule per type; tree display-name mapping (knowledge-model §2) |
| 10 | MED | "Monitoring logs" vague | `alerts` connector: Airflow failure history (always) + Alertmanager if used; open question Q7 |
| 11 | LOW | Stale rule differed (verified only vs reviewed+verified) | reviewed **and** verified → proposed revision + `stale` |
| 12 | LOW | Cost $68 vs $70 | ≈ $70 everywhere, calc in research §5 |
| 13 | LOW | bcrypt vs scrypt | scrypt (stdlib) |
| 14 | LOW | Overview claimed "reviewed ×3" prematurely | status set to draft until pass 3 completes |
| 15 | LOW | Garbled read-only sentence (01 §7) | "GET only, except POST /auth/token (Airflow 3 JWT); no ES `_search`" |
| 16 | LOW | Dependency lists differ | `sentence-transformers` added to ADR-001 optional list |
| 17 | LOW | `edges` PK loses provenance of a second source | PK `(src, rel, dst, source)`; UI merges rows |

## Pass 2 — technical feasibility & correctness

Verification: new FTS claims re-tested on SQLite 3.51 (tokenchars `_`, contentless delete, particle stripping, quoting,
no-space compounds) — see [research.md](research.md) §2.

| # | Sev | Finding | Fix (where) |
|---|---|---|---|
| 1 | HIGH | Batch `custom_id` `doc_id:call` violates `^[a-zA-Z0-9_-]{1,64}$` | `sha1(doc_id|call|prompt_version)[:32]` + `batch_items` mapping table (02 §3) |
| 2 | HIGH | Writer lock held during up-to-24 h batches | `synth-submit` / `synth-collect` jobs; errored/expired resubmitted ≤ 3×; validation retry sync, capped (02 §3, ADR-010) |
| 3 | HIGH | Single process & scheduler lease undefined (multi-worker duplicates, stuck locks, CLI racing) | `--workers 1`, no reload; `leases(name, owner_pid, host, expires_at)` heartbeat; atomic job claim `UPDATE … RETURNING`; dead-pid recovery; missed cron slots run once; CLI enqueues by default (ADR-002, ADR-010) |
| 4 | HIGH | Two git writers; crash recovery undefined; move "transaction" spans git | single git writer (worker); web actions write DB then enqueue `content-sync`; clean-worktree check; `meta.last_indexed_commit` + startup diff reindex; move = git first, then DB (03 §6) |
| 5 | HIGH | "Rebuildable from git" table wrong | state table rewritten; status/review/hashes/versions/inferred edges in frontmatter; `_meta/redirects.yaml`; `embeddings.db` separate and backed up; revisions/feedback etc. backed up (03 §1, ADR-003) |
| 6 | HIGH | Webhooks can't reach a tailnet-only host; GitLab token ≠ HMAC; no replay protection | polling default (git fetch 5 min, issue trackers 30 min); webhooks only from tailnet senders; compare_digest, timestamp window, delivery-id dedupe (01 §5, ADR-011) |
| 7 | HIGH | Confluence v2 is Cloud-only; Jira DC vs Cloud formats; converter effort | `edition` config; DC v1 paths; ADF handling; unknown macros → placeholder + link; converter budgeted in M2 (01 §2) |
| 8 | MED | `unicode61` splits `_`; contentless vs external undecided; TEXT id; `snippet()` returns bigrams | `tokenchars '_'` + `.`/`-` → `_`; contentless + `contentless_delete=1`, INTEGER rowids; Python highlighting via offset map (03 §3–4, 05 §3) |
| 9 | MED | Query builder unspecified (particles, phrase precision, escaping, facet prefixes, bm25 arg order) | full spec: facets first, quoting, bigram phrases, particle stripping, 1-syllable fallback, positional `bm25` weights (03 §4) |
| 10 | MED | SQLite contention details | `BEGIN IMMEDIATE`, 200-doc commit batches, throttled `last_seen`, `Connection.backup()`, atomic `vectors.npz` (03 §6) |
| 11 | MED | Vector facet filtering after top-k; reload coordination | row mask before argpartition; `.npz` with generation + row-count check; poll `meta.vectors_generation`; no mmap (03 §5) |
| 12 | MED | ES privileges (`_index_template` needs write-capable privilege), `_cat` not for apps, data streams, Airflow encoding/pagination/JWT/map_index/base URL | least-privilege list, templates optional, `_cluster/health?level=indices` + `_stats`, `_data_stream`; Airflow config `base_url` + notes (01 §6–7) |
| 13 | MED | Static parsing gaps (Jinja SQL, multi-statement, TaskGroups, expand, Assets) | REST `downstream_task_ids` = task graph for all DAGs; AST for code/SQL only; Jinja placeholder substitution; `sqlglot.parse` (01 §4) |
| 14 | MED | markdown-it commonmark preset allows raw HTML; Mermaid/KaTeX trust; linker on HTML; IP filter behind proxy | `html: False`, scheme allow-list, Mermaid strict, KaTeX trust false, token-level linker, bind to Tailscale IP (platform §4, 04 §3, ADR-011) |
| 15 | MED | Idempotency key misses model/config; cost ignores multiple calls & thinking; cache minimums | key = semantic hash + generator + prompt + model + config hash; cost revised to **$120–250** full / $4–10 daily; cache verification (02 §3–4, research §5, platform §7) |
| 16 | MED | macOS determinism (case-insensitive APFS, NFD), YAML order, timezones | NFC ids, casefold-unique `id_fold`, `core.precomposeunicode`, own YAML dumper, DAG-timezone rendering (02 §5, 03 §3) |
| 17 | LOW | Citation check is only mechanical | stated explicitly; semantic support judged by reviewers/golden set (02 §5) |
| 18 | LOW | Regex redaction misses secrets; deterministic pages can leak `default_args` | secret scanner on every page blocks commit; renderer masks secret-like keys (02 §5, platform §4) |
| 19 | LOW | pypdfium2 loses tables | marked low-fidelity; pdfplumber optional (01 §2) |
| 20 | HIGH | Estimates too optimistic (M1 3–5 d, total 12–20 d) | M1 split into M1a/M1b; M2 7–10 d; total **≈ 23–34 d** (roadmap §1, overview) |

## Pass 3 — operator value, simplicity, delivery risk

Scenario walk-through: (1) DAG failed at 02:10, (2) search results stale, (3) column rename, (4) new-engineer onboarding,
(5) AIOps agent needs context.

| # | Sev | Finding | Fix (where) |
|---|---|---|---|
| 1 | HIGH | First release lacked downstream view, live status, deep links, on-call, cross-DAG sensors | parsed edges + downstream list in M1a; `ExternalTaskSensor` → `waits_for`; **on-demand live status (60 s cache) with age**; `links:` and `oncall:` config (01 §4–5, 04 §7, 06 §3, knowledge-model §4) |
| 2 | HIGH | "Is today's index built & swapped?" only at P1; no data freshness | index health checks + `/api/health/indices` in **M1a P0**; upstream freshness from producer DAG's last success (04 §5, roadmap) |
| 3 | MED | Column rename answered only at table level | `columns_read/written` in task attrs; `column:` search; *Columns used by*; `SELECT *` flagged (knowledge-model §5, 04 §5) |
| 4 | HIGH | Nothing generated "how does candidate generation work" | config-defined **pipelines** rendered deterministically with Mermaid lineage (M1b), LLM overview (M2), human "Start here" pages (knowledge-model §3, 02 §2, 04 §5) |
| 5 | HIGH | Agent needed many calls; tokens only in M4 | one-call **`/api/context`**; read tokens in M1b; human `runbooks:` field; stable `/e/<entity>` URLs for `doc_md`/callbacks/alerts (05 §2, ADR-015) |
| 6 | HIGH | Batches machinery premature | v1 = **synchronous thread pool** + budget gate; Batches kept as a "later" design; cost shown for both (02 §3, ADR-007, platform §7, research §5) |
| 7 | MED | Lease table overkill on a single host | `fcntl.flock` on `data/worker.lock` (ADR-010, 03 §6) |
| 8 | MED | Review machinery heavy (revisions table, diff UI) | stale + fact diff + regenerate/keep; git is the history (ADR-014, 02 §4) |
| 9 | LOW | Further cuts | stored offset maps dropped (query-time re-analysis); redirects → M3; webhooks → P2; home-grown JSON-RPC dropped (real MCP later); Claude rerank + scorecard → P2; vectors only if the keyword eval shows a gap. **Declined in part:** KaTeX stays (PRD requires math; vendored, cheap) |
| 10 | MED | Auth heavier than needed | **Tailscale identity** (`whois`) + role allow-list; local accounts only as fallback (Q12). TLS manager kept because the tailnet has no HTTPS certificates yet (ADR-011) |
| 11 | HIGH | No named 2-week release | **R1 "on-call catalog"** = M0 + M1a with drill exit criteria (roadmap §1) |
| 12 | MED | No extension mechanism; single Airflow per config | **ADR-017** `TypeSpec` + connector registries; `sources:` list (platform §8, 01 §6) |
| 13 | HIGH | Correcting a wrong statement slow | one-click **section correction** (`human:` block, fed as `S0`) and feedback in R1; team review queues + digest (ADR-014, 06 §2) |
| 14 | MED | No success metrics or query log | `search_log`; metrics + "3 a.m. drill" targets (05 §6, platform §9) |
| 15 | MED | Option scores predate plan growth | re-validation note: growth is option-independent, A still wins (options §2) |
| 16 | LOW | `.npy`/mmap vs `.npz`; stale namespace in example | ADR-004 → `vectors.npz`, no mmap; frontmatter example → `index_family:search-prod.products` |
| 17 | MED | Blocking questions missing, no defaults | Q1–Q14 with defaults; ★ marks R1 blockers (overview) |

Final sweep after pass 3: link and ADR-anchor check (0 broken, 17 ADRs), grep for terms removed by earlier passes
(leases, proposed revisions, offset maps, `/agent/call`, `vectors.npy`, `bcrypt`, `M1 |`) — remaining hits are only
the notes explaining what was replaced.
