# 2. Knowledge Synthesis Engine (`opspedia/synthesis/`)

## 1. Big picture

Turns each `ChangeSet` into **standard Markdown documents** (frontmatter + templated body) plus entities, edges,
chunks and embeddings. Structured sources are rendered **deterministically**; Claude is used only for prose,
extraction from unstructured text, categorization and summaries ([ADR-008](../decisions.md#adr-008)).

```
 ChangeSet ─▶ route by kind
   ├─ dag_code/dag_live/table/index/alias ─▶ Renderer (Jinja template, facts)  ─┐
   │                                            └─▶ LLM prose request (batched) ─┤
   ├─ manual/incident ─▶ Standardizer (LLM, source-grounded, structured output) ─┤
   ▼                                                                             ▼
 Validator (schema, citations, links) ─▶ Merge with existing file (fences, overrides, review state) ─▶ content/ + commit
   ─▶ Entity & edge writer ─▶ Chunker (+contextual header) ─▶ Embedder (cached) ─▶ index update (storage)
```

## 2. Sub-modules

| Module | Input → output | LLM? |
|---|---|---|
| `renderers/{dag,table,index_family,alias,index_template,lifecycle_policy,system,team,service}.py` | normalized RawItem(s) → Markdown body sections ([knowledge-model](../knowledge-model.md) §4 templates) + frontmatter facts + parsed edges; index families render a **Versions** table and a mapping diff vs the previous version | no |
| `renderers/pipeline.py` | pipelines defined in config (list of DAGs or a tag) → DAG order, tables and index families produced, Mermaid lineage diagram built from parsed edges; LLM overview added in M2 | no (M1b), yes (M2) |
| `standardizer.py` (Markdown Standardizer) | manual/incident text → type-specific template (e.g. incident: *Summary · Impact · Timeline · Root cause · Fix · Follow-ups · Affected entities*) | **yes** |
| `extractor.py` (Entity & Metadata Extractor) | any body → `{entities[], system, team, tags[], edges[] (inferred)}`; resolves mentions against the entity registry | hybrid (regex/registry first, LLM for the rest) |
| `categorizer.py` (Auto Categorizer) | doc + taxonomy → document `id` (= tree path) | rules first (type + system/cluster), LLM only for manuals/notes |
| `summarizer.py` | body → `summary` (1–3 sentences) + optional prose sections for deterministic pages | **yes** |
| `chunker.py` (Summarizer & Chunker) | body → heading-bounded chunks (~500 tokens, max 800, ~60-token overlap reset at each heading) with contextual header | no |
| `embedder.py` | chunk texts → vectors (provider per [ADR-006](../decisions.md#adr-006)), cache by `(model, chunk_hash)` | embedding API |
| `llm.py` | request building, synchronous thread-pool execution (v1), Batches submit/collect (later), retries, cost accounting | — |
| `lint.py` (nightly) | whole corpus → issues (orphans, broken links, stale verified pages, missing owner/SLA, REST↔AST mismatch, contradictions) | optional LLM for contradictions |

## 3. LLM usage ([ADR-007](../decisions.md#adr-007))

| Call | Mode | Output contract |
|---|---|---|
| DAG/table/index **prose** (purpose, how it's used, failure modes) | sync pool (v1) → Batches (later) | JSON `{sections: {purpose, usage, failure_modes}, citations: {section: [source_idx]}}` |
| Manual / incident **standardization** | sync pool (v1) → Batches (later) | JSON `{title, type, summary, sections[{heading, markdown, citations[]}], entities[], tags[]}` |
| **Entity extraction** leftovers | sync pool (v1) → Batches (later) | JSON `{mentions[{text, entity_id|null, type, confidence}], edges[{src, rel, dst, evidence}]}` |
| **Categorization** | sync pool (v1) → Batches (later) | JSON `{tree_path, reason}` constrained to the taxonomy enum |
| **Contradiction lint** (P2) | nightly Batches | JSON `{conflicts[{doc_a, doc_b, claim_a, claim_b}]}` |

- Model `claude-opus-5`; structured outputs via `client.messages.parse()` / `output_config.format`, so JSON is validated
  against the Pydantic schema; one retry on validation failure, then the item is parked as `synthesis_error`.
- **Prompt layout for caching:** `[system: role + rules + output schema + taxonomy + glossary] (cached)` →
  `[user: source excerpts with numbered source ids + task]`. Volatile data only after the cache breakpoint.
- **Source-grounding rules** in the system prompt: use only provided excerpts; every section cites `[S1]…[Sn]`;
  unknown → write "Unknown from sources" rather than guessing; never invent schedules, column names or index names.
- **3.1 Execution in v1 — synchronous calls** (review pass 3):
  - A run collects its LLM requests and executes them through a thread pool (4–8 concurrent, respecting rate-limit
    headers), with server-side refusal fallbacks enabled.
  - Each result is validated as it arrives and merged at the end of the run.
  - A **validation failure** gets one retry with the validator errors appended, capped at 20 per run. The rest are
    parked as `synthesis_error` and listed on the run page.
- **3.2 Batch mode (later, when volume justifies −50 %) — two jobs, so no lock is held while waiting:**
  1. `synth-submit`: write one `batch_items(custom_id, batch_id, doc_id, call, prompt_version, state)` row per request,
     then `messages.batches.create`, then store `batch_id` on the job.
     `custom_id = sha1(doc_id | call | prompt_version)[:32]`, because the API requires `^[a-zA-Z0-9_-]{1,64}$`.
  2. `synth-collect` (every 10 min while batches are open): map results back by `custom_id` (they arrive in any order),
     then validate, merge and commit.
  3. `errored` / `expired` items are resubmitted up to 3 times, then parked.
- **Cost guard:** `count_tokens` estimate per run. Runs above the configured budget (e.g. $20) need admin approval
  (`opspedia run synth --approve`). Usage is stored per run: input, output (incl. thinking), `cache_creation_input_tokens`,
  `cache_read_input_tokens`, $.
- **Caching check:** the frozen prefix must exceed the model's minimum cacheable length, or it is silently not cached.
  Verify `cache_read_input_tokens > 0` in the first run. Cache hits inside Batches (later) are best-effort, so the budget
  assumes no cache benefit.

## 4. Merge rules ([ADR-014](../decisions.md#adr-014))

| Existing page state | New generation | Result |
|---|---|---|
| none | any | create, `status: generated` |
| `generated` | changed | replace `gen` fences; keep human text outside fences and every `human:` correction block |
| `reviewed` / `verified` | changed | facts tables update in place; prose is **not** rewritten; page → `stale` with the fact diff shown; an editor picks **regenerate** (sync call) or **keep** |
| any, with `<!-- human:start S -->` correction | any | the correction supersedes generated section S and is passed to the LLM as authoritative source `S0` for the rest of the page |
| `human_override: true` | any | untouched; lint notes "source changed" |
| source removed | — | `status: archived`, kept in tree under *Archived* filter |

Idempotency: identical `(semantic_hash, generator_version, prompt_version, model_id, config_hash)` ⇒ skip entirely
(no LLM call, no commit). `config_hash` covers the taxonomy, glossary, templates and system/team config, so changing any
of them regenerates exactly the affected pages. The key is stored in frontmatter (`generator`, `sources[].hash`), so it
survives a DB rebuild.

## 5. Validation before write
YAML schema check ([knowledge-model.md](../knowledge-model.md) §4); every generated section has ≥ 1 valid citation
(*mechanical check only*: the `[Sn]` must exist and point into the excerpt set. Whether the source actually supports the
claim is judged by reviewers and the golden-set rubric, §7); all
`[[wiki links]]`/entity IDs resolve or are downgraded to plain text; code blocks/tables parse; **secrets scanner** (tokens,
keys, DSNs, `password=`…) on *every* page, deterministic ones included. Content pushed to the remote can't be taken back.
Failures block the write and are listed on the run page.

**Determinism** (needed for byte-identical re-runs):
- YAML is emitted by our own dumper with a fixed key order (schema order, then unknown keys sorted).
- Floats are fixed-precision; dates are ISO-8601 with an explicit offset.
- `updated_at` changes only when the body or facts change.
- Lists are sorted by a stable key.
- Ids and filenames are **NFC-normalized** and unique case-insensitively, because macOS APFS is case-insensitive.
  The content repo sets `core.precomposeunicode=true`.
- Schedules are rendered in the **DAG's own timezone** (from REST `timezone`), with UTC in the tooltip. Never assume KST.
- Renderers also mask secret-looking keys in `default_args`/`params` (`password|secret|token|key|dsn` → `***`).

## 6. Tasks (scheduling source of truth: [roadmap.md](../roadmap.md); Pri = priority within the milestone)
| Milestone | Pri | Task |
|---|---|---|
| M1a | P0 | renderers (dag, table, index_family, alias, system, team) via the `TypeSpec` registry ([ADR-017](../decisions.md#adr-017)); fences; merge (generated state); commit per run; idempotency keys; determinism rules; parsed edges written with the page |
| M1b | P0 | pipeline renderer (config-defined pipelines, Mermaid lineage) |
| M1b | P1 | renderers for index_template and lifecycle_policy |
| M2 | P0 | `llm.py` (sync thread pool, `parse()`, caching, cost accounting, budget gate); validator with citation checks |
| M2 | P0 | summarizer + prose for entity pages; pipeline overviews; standardizer for manuals & incidents |
| M2 | P0 | chunker (contextual header); embedder providers (voyage / local / off) + cache — vectors enabled if the keyword eval shows a gap |
| M2 | P1 | extractor (registry-first); categorizer (rules-first); human corrections (`S0`) fed into prompts |
| M4 | P0 | stale + regenerate/keep flow for reviewed/verified pages |
| later | P2 | Batch mode (§3.2); contradiction lint; LLM-written chunk context lines (Contextual Retrieval); answer write-back pages |

## 7. Evaluation
- **Golden set:** 20 hand-checked pages (5 DAG, 5 table, 5 index, 5 incident) — facts must match sources exactly
  (automated diff), prose judged with a rubric (accuracy, citation validity, usefulness) by a reviewer.
- **Regression:** re-run synthesis on fixtures; unchanged inputs must produce byte-identical files.
