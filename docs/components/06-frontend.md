# 6. Wiki Frontend Viewer (`opspedia/frontend/`)

## 1. Big picture

A fast, **server-rendered** reader ([ADR-009](../decisions.md#adr-009)): left **directory tree**, center **Markdown
page**, right **context panel** (facts, backlinks, blast radius, incidents), and a **⌘K** dialog reachable everywhere.
No SPA build chain; small vanilla-JS modules progressively enhance the HTML.

```
┌ tree ───────────────┐┌ page ─────────────────────────────────────────┐┌ context ─────────────────┐
│ Systems             ││ Systems › Reco › DAGs                          ││ facts: schedule 02:00 KST │
│  ▾ Reco             ││ # feature_store_daily        [generated ▾]     ││ owner reco-platform       │
│    ▸ Pipelines      ││ Summary … (AI-generated · sources ▸)           ││ last run ✅ 02:41          │
│    ▾ DAGs           ││ ## Tasks  (table)  ## Inputs/Outputs            ││ Blast radius ▸ 3 tables,  │
│      feature_store… ││ ## Backfill ```bash …```  ## Known incidents    ││   1 index, 2 DAGs         │
│ Search              ││                                                ││ Referenced by (12)        │
│ Incidents           ││ toc ▸                                          ││ Incidents: INC-2291 …     │
└─────────────────────┘└────────────────────────────────────────────────┘└───────────────────────────┘
 ⌘K  quick search anywhere (titles, entities, full text) · / focuses tree filter · g g goes to page top
```

## 2. Features
| Feature | Details | Pri |
|---|---|---|
| Directory tree | lazy folders, filter box (`/`), status badges (stale ⚠, failing ●), remembers expanded state, resizable, deep links `/p/<id>` | P0 |
| Markdown reader | server HTML from markdown-it-py: tables, task lists, footnotes, heading **anchors** (copy link), code with **line numbers** + Pygments, **math** (KaTeX), **Mermaid**, admonitions; TOC; "AI-generated · sources" banner with source links (git line links, API snapshot time) | P0 |
| ⌘K quick search | modal; debounced `/api/suggest` then `/api/search`; keyboard nav; type/system chips; Enter opens, ⌘Enter opens in new tab; recent pages | P0 |
| Search page | full results with facets, snippets, mode toggle (keyword/hybrid) | P1 |
| Context panel | facts from entity attrs + snapshots, backlinks, blast radius (list + Mermaid), related incidents & runbooks | P1 |
| Catalog views | DAG / table / index / incident inventories with health columns & scorecards | P1 |
| **Correct a section** | editor clicks "correct" on any section → textarea prefilled with the current text → saved as a `human:` block that supersedes the generated one (live in the UI at once, committed within a minute) | P0 (R1) |
| Review actions | editor: mark reviewed/verified; on `stale` pages see the fact diff and choose regenerate / keep; team review queue (`status:generated team:X`) | P1 |
| Feedback | 👍/👎 + note on page and on search results (feeds the eval set and review queue) | P0 (R1) |
| Admin | runs (status, cost, errors), lint, accounts, API tokens, budgets | P1 |
| Editing | "edit in git" link (opens file in repo); inline editor deferred (PRD BlockNote/TipTap → P2) | P2 |

## 3. Look & rendering rules
- Readable doc typography (not terminal style) with dark/light themes; monospace for identifiers and code.
- Every generated page shows provenance (`generated from …`, `updated_at`, `status`); `stale` pages get a warning bar.
- **Page header for entities:** on-call contact, buttons from `links:` (Airflow grid, task log, Kibana, dashboards), and
  **live status with its age** ("last run ❌ failed 02:10 · as of 02:12").
- Stable entity URLs `/e/<entity_id>` are copyable from the header (for `doc_md`, alert templates).
- Links: internal links navigate in place; external links open a new tab (same rule as ai-research-note);
  no external assets at runtime (KaTeX/Mermaid vendored under `/static`) so the app works tailnet-only.
- Accessibility: full keyboard navigation for tree and ⌘K; ARIA roles on tree items; no layout shift on load.

## 4. Implementation notes
- Jinja2 templates: `base.html` (shell + tree + ⌘K), `page.html`, `search.html`, `catalog.html`, `admin/*.html`.
- JS modules (ES modules, no bundler): `tree.js`, `cmdk.js`, `panel.js`, `review.js`, `mermaid-init.js`.
- Page HTML cached by `(doc_id, content_hash, linker_version)`; tree JSON cached per run.
- Browser tests (Playwright over the tailnet, as in ai-research-note) for tree, ⌘K, anchors, review flow, mobile layout.

## 5. Tasks (scheduling source of truth: [roadmap.md](../roadmap.md); Pri = priority within the milestone)
| Milestone | Pri | Task |
|---|---|---|
| M0 | P0 | shell + identity (Tailscale login shown in the header) |
| M1a | P0 | tree + page reader (code/line numbers/math/tables/anchors/Mermaid) + ⌘K + deep links |
| M1a | P0 | entity header (on-call, links, live status + age), downstream list, index-family health badges; section correction; feedback |
| M1b | P1 | search page with facets; catalog views; admin runs & metrics pages |
| M3 | P0 | context panel (facts, backlinks, blast radius, known incidents) |
| M4 | P0 | review actions (reviewed/verified, stale → regenerate/keep), team review queue; admin tokens/budgets |
| later | P2 | inline editor, graph explorer view, saved searches |
