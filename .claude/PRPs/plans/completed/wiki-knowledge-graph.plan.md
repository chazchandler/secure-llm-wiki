# Feature: Wiki Knowledge Graph — Enrichment + Lint

## Summary

Transform secure-llm-wiki from a flat claim store into a Karpathy-style structured knowledge base. During ingest, a new enrichment step runs after the gate loop commits claims: it generates a summary page per source, creates or updates concept and entity pages, adds bidirectional wikilinks, and updates a master index and append-only log — all inside `wiki_data/wiki/`. A new `lint` CLI command audits the full wiki for orphans, contradictions, missing links, incomplete pages, and low-confidence claims; it auto-fixes what it can and reports the rest.

## User Story

As a knowledge base maintainer,
I want ingest to automatically build structured summary, concept, and entity pages with cross-links, index, and log,
So that my knowledge compounds over time and stays consistent without manual bookkeeping.

## Problem Statement

Claims in `wiki_data/pages/` are verified facts but have no structure above the claim level. There is no summary of what a source document said, no entity page for "Temporal" or "AWS", no concept page for "ELT orchestration", and no way to audit the wiki for stale or conflicting content. Knowledge does not compound — each query must reason from raw claims.

## Solution Statement

Add a **graph enrichment** step at the end of `_run_pipeline()` that calls the review LLM to extract concepts and entities from the committed claims, then generates/updates wiki pages under `wiki_data/wiki/`. Add a **`lint`** CLI command that loads all wiki pages and claims, runs five checks, auto-fixes mechanical issues, and reports the rest. All wiki pages are git-tracked in the same wiki_data repo; they are derived content (not gated like claims).

## Metadata

| Field | Value |
|---|---|
| Type | NEW_CAPABILITY |
| Complexity | HIGH |
| Systems Affected | ingest pipeline, WikiStore, prompts, models, CLI, new graph/ and lint/ modules |
| Dependencies | anthropic>=0.40.0, openai>=1.0 (already installed) |
| Estimated Tasks | 14 |

---

## UX Design

### Before State

```
secure-wiki ingest doc.md
  → 43 claims committed to wiki_data/pages/
  → wiki_data/wiki/ is empty

secure-wiki query
  → answers from raw claims only, no structure

(no lint command)
```

### After State

```
secure-wiki ingest doc.md
  → 43 claims committed to wiki_data/pages/
  [enrich] generating summary…
  [enrich] summary written  → wiki_data/wiki/summaries/rfc-data-architecture-v2.md
  [enrich] extracting concepts and entities…
  [enrich] 8 concept(s), 5 entity(ies) found
  [enrich] concept: event-sourcing          (updated)
  [enrich] concept: elt-orchestration       (created)
  [enrich] entity:  temporal                (created)
  [enrich] entity:  aws-lake-formation      (updated)
  [enrich] cross-links added to 6 page(s)
  [enrich] index and log updated

secure-wiki lint
  ────────────────────────────────────────────────
  orphans:          2 page(s) with no inbound links
  missing links:    3 [[wikilink]] target(s) not found
  contradictions:   1 pair flagged for review
  incomplete pages: 0
  low-confidence:   4 claim(s)
  ────────────────────────────────────────────────
  auto-fixed:       5 (added missing cross-links, created stub pages)
  needs review:     1 contradiction  → wiki_data/wiki/lint-report.md
```

### Interaction Changes

| Location | Before | After | User Impact |
|---|---|---|---|
| `ingest` output | stops at gate summary | continues with `[enrich]` section | wiki grows automatically |
| `wiki_data/wiki/` | empty | summaries/, concepts/, entities/, index.md, log.md | browsable, linkable knowledge |
| CLI commands | ingest, list, context, query, clear, init | + **lint** | can audit and repair the wiki |
| `secure-wiki query` | answers from 43 raw claims | answers from claims + wiki pages | richer, structured context |

---

## Mandatory Reading

**Implementation agent MUST read these files before starting:**

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `src/secure_wiki/store/wiki_store.py` | 1–251 | ALL git commit patterns, _serialize, _deserialize, _commit — mirror exactly |
| P0 | `src/secure_wiki/__main__.py` | 261–389 | `_run_pipeline()` — where enrichment step plugs in |
| P0 | `src/secure_wiki/prompts.py` | all | ALL existing prompts — mirror nonce pattern, add new prompts here |
| P1 | `src/secure_wiki/models.py` | all | Claim, SourceRef fields — enrichment reads these |
| P1 | `src/secure_wiki/extraction/extractor.py` | all | LLM call + JSON parse pattern to mirror in graph extractor |
| P1 | `src/secure_wiki/review/adversarial.py` | all | Structured LLM response pattern |
| P1 | `src/secure_wiki/read/hygiene.py` | all | load_for_context — lint reads same source |
| P2 | `tests/` | all | Mock patterns, fixture setup |
| P2 | `/Volumes/Projects/Bespoke/llm-wiki/AGENTS.md` | all | Page schema, frontmatter, required sections for each page type |

**External references:**
| Source | Why |
|---|---|
| Karpathy gist `gist.github.com/karpathy/442a6bf...` | Canonical design — three operations, page types, cross-link conventions |
| `../llm-wiki/AGENTS.md` | Frontmatter schema: title, type, tags, created, updated, sources, confidence |

---

## Patterns to Mirror

**WIKI STORE COMMIT (one file per commit):**
```python
# SOURCE: src/secure_wiki/store/wiki_store.py:232-237
def _commit(self, path: Path, message: str) -> None:
    rel = str(path.relative_to(self.root))
    _git(["add", rel], self.root)
    _git(["commit", "-m", message], self.root)
```
For graph updates that touch N pages, add `_commit_many(paths, message)` using `git add -A` then one commit.

**WIKI STORE SERIALIZE (YAML frontmatter + body):**
```python
# SOURCE: src/secure_wiki/store/wiki_store.py:68-75
def _serialize(claim: Claim, gate_detail: str = "") -> str:
    d = claim.to_dict()
    frontmatter = yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=True)
    body = claim.text
    return f"---\n{frontmatter}---\n\n{body}\n"
```
Wiki pages use same `---\nfrontmatter\n---\n\nbody\n` format. Frontmatter keys: title, type, tags, created, updated, sources, confidence.

**PROMPT BUILDER WITH NONCE:**
```python
# SOURCE: src/secure_wiki/prompts.py (pattern used by all builders)
import secrets
def build_extraction_prompt(source_text: str) -> str:
    nonce = secrets.token_hex(8)
    return f"...<nonce>{nonce}</nonce>..."
```
Every new prompt builder MUST inject a fresh nonce in both system and user messages.

**LLM COMPLETE CALL:**
```python
# SOURCE: src/secure_wiki/extraction/extractor.py (pattern)
client = get_extraction_client()
result = client.complete(system_prompt, user_prompt)
raw = strip_fences(result.text)
data = json.loads(extract_json_object(raw))
```
Graph extractor mirrors this exactly. Use `get_review_client()` (different model = 4-eyes independence preserved).

**INGEST PIPELINE STEP:**
```python
# SOURCE: src/secure_wiki/__main__.py:261-389 (_run_pipeline)
# Steps 1-8 exist. Step 9 plugs in AFTER the gate loop:
committed_claims = [c for c in claims if c.claim_id in committed_ids]
enrich_result = enrich_wiki(committed_claims, source_ref, source_text, store)
```

**TEST MOCK PATTERN:**
```python
# SOURCE: tests/ — mirror existing fixture approach
# Mock get_review_client(), get_extraction_client() via monkeypatch
# Use tmp_path fixture for wiki_data root (WikiStore(root=tmp_path))
```

---

## Architecture: New Directory Layout

```
wiki_data/
  pages/          ← existing (claims)
  quarantine/     ← existing
  sources/        ← existing (raw source text)
  embeddings/     ← existing (not git-tracked)
  wiki/           ← NEW (git-tracked, LLM-owned)
    summaries/    ← one .md per source_id
    concepts/     ← one .md per slug (e.g. elt-orchestration.md)
    entities/     ← one .md per slug (e.g. temporal.md)
    index.md      ← master catalog, updated each ingest
    log.md        ← append-only ingest activity log
    lint-report.md ← written by `secure-wiki lint`
```

## Architecture: New Source Layout

```
src/secure_wiki/
  graph/
    __init__.py
    enricher.py      ← orchestrates enrichment (called from _run_pipeline)
    extractor.py     ← LLM call: concepts + entities from claims
    page_writer.py   ← create/update wiki pages with frontmatter
    linker.py        ← add [[wikilinks]] bidirectionally
  lint/
    __init__.py
    checker.py       ← five audit checks
    fixer.py         ← auto-fix mechanical issues
```

## Security Model for Graph Layer

Graph pages are **derived content**, not ingested claims. They are generated from already-vetted committed claims, so they do NOT go through the write gate. They ARE git-tracked (forensic trail). The enrichment step reads `committed_claims` (post-gate) only — it never reads raw `source_text` after sanitize — so no injection risk from the original source document.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/secure_wiki/store/wiki_store.py` | UPDATE | Add `self.wiki`, `wiki/` subdirs to init/reset; add `save_wiki_page()`, `_commit_many()`, `update_index()`, `append_log()` |
| `src/secure_wiki/prompts.py` | UPDATE | Add `SUMMARIZE_SYSTEM_PROMPT`, `ENRICH_SYSTEM_PROMPT`, `LINT_SYSTEM_PROMPT` and builders |
| `src/secure_wiki/__main__.py` | UPDATE | Call `enrich_wiki()` after gate loop in `_run_pipeline()`; add `cmd_lint()`; add `lint` subparser |
| `src/secure_wiki/models.py` | UPDATE | Add `WikiPage` dataclass; add `LintReport` dataclass |
| `src/secure_wiki/graph/__init__.py` | CREATE | Empty |
| `src/secure_wiki/graph/enricher.py` | CREATE | Orchestrator: summarize → extract → write pages → link → update index/log |
| `src/secure_wiki/graph/extractor.py` | CREATE | LLM call returning `{concepts: [...], entities: [...], summary: str}` |
| `src/secure_wiki/graph/page_writer.py` | CREATE | `create_page()`, `update_page()` — merge new claims into existing page |
| `src/secure_wiki/graph/linker.py` | CREATE | Scan pages for unlinked concept/entity names; insert `[[wikilinks]]` |
| `src/secure_wiki/lint/__init__.py` | CREATE | Empty |
| `src/secure_wiki/lint/checker.py` | CREATE | Five check functions; returns `LintReport` |
| `src/secure_wiki/lint/fixer.py` | CREATE | Auto-fix: stub pages, missing links, index refresh |
| `tests/test_graph_enricher.py` | CREATE | Unit tests for enrichment orchestration |
| `tests/test_lint_checker.py` | CREATE | Unit tests for each lint check |

---

## NOT Building (Scope Limits)

- **Synthesis/comparison pages** — Karpathy mentions these; out of scope for v1. Only summary, concept, entity.
- **Query-answer → wiki page** — Filing good query answers back into wiki. Future work.
- **Embeddings-backed lint contradiction detection** — Gate 5 already does this for incoming claims. Lint contradiction check uses a lightweight LLM call on high-similarity claim pairs only.
- **Dashboard / flashcard / analytics pages** — Those are in llm-wiki but out of scope here.
- **Real-time index** — index.md is regenerated on each ingest and lint run, not event-sourced.
- **Removing the `../llm-wiki` directory** — The user may keep both; this project creates its own `wiki_data/wiki/` layer.

---

## Step-by-Step Tasks

Each task follows TDD order: **write failing test → implement → confirm test passes**.

### Task 1: ADD `WikiPage` and `LintReport` to `models.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_models.py
from secure_wiki.models import WikiPage, LintReport

def test_wiki_page_roundtrip(tmp_path):
    page = WikiPage(slug="elt-orchestration", page_type="concept", title="ELT Orchestration",
                    tags=["elt", "data"], confidence="high", sources=["rfc-v2"],
                    body="## Definition\nELT is ...", created="2026-06-18", updated="2026-06-18")
    path = tmp_path / "elt-orchestration.md"
    path.write_text(page.to_markdown())
    loaded = WikiPage.from_file(path)
    assert loaded.slug == page.slug
    assert loaded.body == page.body

def test_lint_report_defaults():
    r = LintReport()
    assert r.orphans == []
    assert r.auto_fixed == []
```
`uv run pytest tests/test_models.py -v` → FAIL (ImportError)

- **ACTION**: ADD two dataclasses after existing models in `models.py`
- **IMPLEMENT**:
  ```python
  @dataclass
  class WikiPage:
      slug: str
      page_type: str          # "summary" | "concept" | "entity"
      title: str
      tags: list[str]
      confidence: str         # "high" | "medium" | "low"
      sources: list[str]
      body: str
      created: str            # ISO date
      updated: str            # ISO date

      def to_markdown(self) -> str:
          """Render as YAML-frontmatter Markdown (same format as claims)."""
          ...

      @classmethod
      def from_file(cls, path: Path) -> "WikiPage":
          """Parse frontmatter + body from a wiki page file."""
          ...

  @dataclass
  class LintReport:
      orphans: list[str] = field(default_factory=list)
      missing_links: list[str] = field(default_factory=list)
      contradictions: list[tuple[str,str]] = field(default_factory=list)
      incomplete_pages: list[str] = field(default_factory=list)
      low_confidence_claims: list[str] = field(default_factory=list)
      auto_fixed: list[str] = field(default_factory=list)
  ```
- **MIRROR**: `src/secure_wiki/models.py` dataclass style; `wiki_store._serialize` frontmatter format
- **VALIDATE**: `uv run pytest tests/test_models.py -v` → PASS

---

### Task 2: ADD new prompts to `prompts.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_prompts.py (add to existing file if present)
from secure_wiki.prompts import build_enrich_prompt, build_lint_prompt
import re

def test_enrich_prompt_contains_nonce():
    system, user = build_enrich_prompt(claims=[], source_id="test-src")
    nonce_pat = re.compile(r"<nonce>[0-9a-f]{16}</nonce>")
    assert nonce_pat.search(system) and nonce_pat.search(user)

def test_enrich_prompt_nonces_differ_each_call():
    _, u1 = build_enrich_prompt([], "s")
    _, u2 = build_enrich_prompt([], "s")
    assert u1 != u2   # fresh nonce each call

def test_lint_prompt_contains_claim_pairs():
    system, user = build_lint_prompt([("id-a", "text a"), ("id-b", "text b")])
    assert "id-a" in user and "id-b" in user
```
`uv run pytest tests/test_prompts.py -v` → FAIL

- **ACTION**: APPEND to `prompts.py`
- **IMPLEMENT**:

  `ENRICH_SYSTEM_PROMPT` — instructs model to return JSON:
  ```json
  {
    "summary": "3-5 paragraph markdown summary",
    "concepts": [{"slug": "elt-orchestration", "title": "...", "definition": "...", "tags": [...]}],
    "entities":  [{"slug": "temporal",          "title": "...", "overview": "...",   "tags": [...]}]
  }
  ```
  Slugs must be lowercase-hyphenated. Inject nonce in both system and user messages.

  `LINT_SYSTEM_PROMPT` — given claim pairs, return:
  ```json
  {"contradictions": [{"claim_a": "id", "claim_b": "id", "reason": "..."}]}
  ```

  Builders: `build_enrich_prompt(claims, source_id) -> tuple[str, str]` and `build_lint_prompt(pairs) -> tuple[str, str]`.

- **MIRROR**: existing `build_extraction_prompt` — `secrets.token_hex(8)` nonce, `<claim nonce=...>` tags
- **VALIDATE**: `uv run pytest tests/test_prompts.py -v` → PASS

---

### Task 3: ADD wiki support to `wiki_store.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_wiki_store_graph.py
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.models import WikiPage

def test_save_wiki_page_writes_file(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    page = WikiPage(slug="temporal", page_type="entity", title="Temporal",
                    tags=["orchestration"], confidence="high", sources=["rfc"],
                    body="## Overview\nTemporal is ...", created="2026-06-18", updated="2026-06-18")
    path = store.save_wiki_page(page)
    assert path.exists()
    assert "Temporal" in path.read_text()

def test_commit_many_makes_one_commit(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    p1 = store.wiki / "concepts" / "elt.md"; p1.write_text("# ELT")
    p2 = store.wiki / "entities" / "aws.md"; p2.write_text("# AWS")
    store._commit_many([p1, p2], "test bulk commit")
    import subprocess
    log = subprocess.check_output(["git", "log", "--oneline"], cwd=tmp_path).decode()
    assert log.count("\n") == 2   # init commit + our commit

def test_append_log_grows(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    store.append_log("first entry")
    store.append_log("second entry")
    text = (store.wiki / "log.md").read_text()
    assert "first entry" in text and "second entry" in text

def test_update_index_lists_all_pages(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    (store.wiki / "concepts" / "elt.md").write_text("# ELT")
    (store.wiki / "entities" / "aws.md").write_text("# AWS")
    store.update_index()
    index = (store.wiki / "index.md").read_text()
    assert "elt" in index and "aws" in index
```
`uv run pytest tests/test_wiki_store_graph.py -v` → FAIL

- **ACTION**: UPDATE `WikiStore`
- **IMPLEMENT**:
  - `__init__`: add `self.wiki = self.root / "wiki"`
  - `init()`: create `wiki/`, `wiki/summaries/`, `wiki/concepts/`, `wiki/entities/`; write `wiki/index.md` and `wiki/log.md` stubs on first init
  - `save_wiki_page(page) -> Path`: write `wiki/<page_type>s/<slug>.md`, no commit
  - `_commit_many(paths, message)`: git-add each path, single commit
  - `update_index() -> Path`: scan `wiki/{summaries,concepts,entities}/`, regenerate `wiki/index.md` with links to each page
  - `append_log(entry: str) -> Path`: append `{iso_timestamp}  {entry}` line to `wiki/log.md`
- **GOTCHA**: `_commit_many` with no file changes will fail (`git commit` exits 1). Check `git diff --cached --quiet` before committing; skip silently if nothing staged.
- **VALIDATE**: `uv run pytest tests/test_wiki_store_graph.py -v` → PASS

---

### Task 4: CREATE `src/secure_wiki/graph/extractor.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_graph_extractor.py
from unittest.mock import MagicMock, patch
from secure_wiki.graph.extractor import extract_graph, EnrichmentResult
from secure_wiki.models import SourceRef

FAKE_RESPONSE = '{"summary": "Test summary.", "concepts": [{"slug": "elt", "title": "ELT", "definition": "Extract Load Transform", "tags": ["data"]}], "entities": [{"slug": "temporal", "title": "Temporal", "overview": "Workflow engine", "tags": ["orchestration"]}]}'

def test_extract_graph_returns_result(monkeypatch):
    mock_client = MagicMock()
    mock_client.complete.return_value = MagicMock(text=FAKE_RESPONSE)
    monkeypatch.setattr("secure_wiki.graph.extractor.get_review_client", lambda: mock_client)
    ref = SourceRef(id="test", uri="file:///test.md", section="full", content_hash="abc")
    result = extract_graph(claims=[], source_ref=ref)
    assert isinstance(result, EnrichmentResult)
    assert result.summary == "Test summary."
    assert result.concepts[0]["slug"] == "elt"
    assert result.entities[0]["slug"] == "temporal"

def test_extract_graph_malformed_json_returns_empty(monkeypatch):
    mock_client = MagicMock()
    mock_client.complete.return_value = MagicMock(text="not json at all")
    monkeypatch.setattr("secure_wiki.graph.extractor.get_review_client", lambda: mock_client)
    ref = SourceRef(id="x", uri="file:///x.md", section="full", content_hash="x")
    result = extract_graph(claims=[], source_ref=ref)
    assert result.concepts == [] and result.entities == []
```
`uv run pytest tests/test_graph_extractor.py -v` → FAIL

- **ACTION**: CREATE `src/secure_wiki/graph/__init__.py` (empty) + `extractor.py`
- **IMPLEMENT**:
  ```python
  @dataclass
  class EnrichmentResult:
      summary: str
      concepts: list[dict]
      entities: list[dict]
      usage: UsageInfo

  def extract_graph(claims: list[Claim], source_ref: SourceRef) -> EnrichmentResult:
      client = get_review_client()
      system, user = build_enrich_prompt(claims, source_ref.id)
      result = client.complete(system, user)
      try:
          raw = strip_fences(result.text)
          data = json.loads(extract_json_object(raw))
          return EnrichmentResult(
              summary=data.get("summary", ""),
              concepts=data.get("concepts", []),
              entities=data.get("entities", []),
              usage=result.usage,
          )
      except Exception as exc:
          print(f"[enrich]   graph extraction failed ({exc}), skipping", file=sys.stderr)
          return EnrichmentResult("", [], [], UsageInfo())
  ```
- **MIRROR**: `src/secure_wiki/extraction/extractor.py` — `extract_json_object`, `strip_fences`, try/except
- **VALIDATE**: `uv run pytest tests/test_graph_extractor.py -v` → PASS

---

### Task 5: CREATE `src/secure_wiki/graph/page_writer.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_page_writer.py
from unittest.mock import MagicMock, patch
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.graph.page_writer import create_or_update_concept_page, create_summary_page

def test_create_concept_page_writes_file(tmp_path, monkeypatch):
    store = WikiStore(root=tmp_path); store.init()
    monkeypatch.setattr("secure_wiki.graph.page_writer.get_review_client",
                        lambda: MagicMock(complete=lambda s,u: MagicMock(text="## Definition\nELT moves data.")))
    page = create_or_update_concept_page(store, slug="elt", title="ELT",
                                         definition="Extract Load Transform", tags=["data"], relevant_claims=[])
    assert (store.wiki / "concepts" / "elt.md").exists()
    assert page.slug == "elt"

def test_update_concept_page_preserves_existing_content(tmp_path, monkeypatch):
    store = WikiStore(root=tmp_path); store.init()
    existing = store.wiki / "concepts" / "elt.md"
    existing.write_text("---\ntitle: ELT\ntype: concept\n---\n\n## Definition\nOriginal content.\n")
    monkeypatch.setattr("secure_wiki.graph.page_writer.get_review_client",
                        lambda: MagicMock(complete=lambda s,u: MagicMock(text="## Definition\nOriginal content.\n\n## New Section\nAdded.")))
    create_or_update_concept_page(store, slug="elt", title="ELT",
                                  definition="ELT", tags=[], relevant_claims=[])
    text = existing.read_text()
    assert "Original content." in text

def test_create_summary_page_links_concepts(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    page = create_summary_page(store, source_id="rfc", title="RFC Data Architecture",
                               summary_text="This RFC covers ELT and Temporal.",
                               concept_slugs=["elt"], entity_slugs=["temporal"])
    text = (store.wiki / "summaries" / "rfc.md").read_text()
    assert "[[concepts/elt" in text or "[[entities/temporal" in text
```
`uv run pytest tests/test_page_writer.py -v` → FAIL

- **ACTION**: CREATE `src/secure_wiki/graph/page_writer.py`
- **IMPLEMENT**:
  - `create_or_update_concept_page(store, slug, title, definition, tags, relevant_claims) -> WikiPage`
  - `create_or_update_entity_page(store, slug, title, overview, tags, relevant_claims) -> WikiPage`
  - `create_summary_page(store, source_id, title, summary_text, concept_slugs, entity_slugs) -> WikiPage`
  - For update path: read existing body, pass to LLM with merge prompt; for create path: build from definition/overview directly
- **GOTCHA**: Slug normalisation must happen here — `re.sub(r"[^a-z0-9-]", "", title.lower().replace(" ", "-"))` — so two ingests for the same concept land on the same file.
- **VALIDATE**: `uv run pytest tests/test_page_writer.py -v` → PASS

---

### Task 6: CREATE `src/secure_wiki/graph/linker.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_linker.py
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.graph.linker import add_cross_links

def _write_page(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

def test_adds_wikilink_for_unlinked_mention(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    (store.wiki / "entities" / "temporal.md").write_text("# Temporal\nWorkflow engine.")
    page = store.wiki / "concepts" / "elt.md"
    page.write_text("# ELT\nWe use Temporal to orchestrate tasks.")
    modified = add_cross_links(store, new_slugs=["temporal"])
    assert page in modified
    assert "[[entities/temporal" in page.read_text()

def test_does_not_double_link(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    (store.wiki / "entities" / "temporal.md").write_text("# Temporal")
    page = store.wiki / "concepts" / "elt.md"
    page.write_text("# ELT\nWe use [[entities/temporal|Temporal]] already.")
    add_cross_links(store, new_slugs=["temporal"])
    assert page.read_text().count("[[entities/temporal") == 1

def test_skips_frontmatter_and_code_blocks(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    (store.wiki / "entities" / "temporal.md").write_text("# Temporal")
    page = store.wiki / "concepts" / "elt.md"
    page.write_text("---\ntitle: Temporal ELT\n---\n\n```\ntemporal run\n```\n\nBody text.")
    add_cross_links(store, new_slugs=["temporal"])
    text = page.read_text()
    # Should NOT link inside frontmatter or code block
    assert text.index("---") < text.index("title")   # frontmatter unchanged
    assert "```\ntemporal" in text   # code block unchanged
```
`uv run pytest tests/test_linker.py -v` → FAIL

- **ACTION**: CREATE `src/secure_wiki/graph/linker.py`
- **IMPLEMENT**: Parse pages into three zones (frontmatter, code blocks, body). Only replace in body zone. Use regex `r'\b{slug}\b'` (case-insensitive) excluding spans already inside `[[...]]`.
- **VALIDATE**: `uv run pytest tests/test_linker.py -v` → PASS

---

### Task 7: CREATE `src/secure_wiki/graph/enricher.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_graph_enricher.py
from unittest.mock import MagicMock, patch
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.graph.enricher import enrich_wiki
from secure_wiki.models import SourceRef

def _fake_extract(claims, source_ref):
    from secure_wiki.graph.extractor import EnrichmentResult
    from secure_wiki.llm_client import UsageInfo
    return EnrichmentResult(summary="A test summary.", concepts=[{"slug":"elt","title":"ELT","definition":"Extract Load Transform","tags":["data"]}], entities=[{"slug":"temporal","title":"Temporal","overview":"Workflow engine","tags":["workflow"]}], usage=UsageInfo())

def test_enrich_creates_summary_and_pages(tmp_path, monkeypatch):
    store = WikiStore(root=tmp_path); store.init()
    monkeypatch.setattr("secure_wiki.graph.enricher.extract_graph", _fake_extract)
    monkeypatch.setattr("secure_wiki.graph.enricher.get_review_client",
                        lambda: MagicMock(complete=lambda s,u: MagicMock(text="## Definition\nELT moves data.")))
    ref = SourceRef(id="rfc", uri="file:///rfc.md", section="full", content_hash="abc")
    n_concepts, n_entities = enrich_wiki([], ref, store)
    assert n_concepts == 1 and n_entities == 1
    assert (store.wiki / "summaries" / "rfc.md").exists()
    assert (store.wiki / "concepts" / "elt.md").exists()
    assert (store.wiki / "entities" / "temporal.md").exists()

def test_enrich_empty_claims_is_noop(tmp_path, monkeypatch):
    store = WikiStore(root=tmp_path); store.init()
    called = []
    monkeypatch.setattr("secure_wiki.graph.enricher.extract_graph", lambda *a: called.append(1) or _fake_extract(*a))
    ref = SourceRef(id="x", uri="file:///x.md", section="full", content_hash="x")
    # Pass no claims AND force extract to return empty
    monkeypatch.setattr("secure_wiki.graph.enricher.extract_graph",
                        lambda c, r: __import__("secure_wiki.graph.extractor", fromlist=["EnrichmentResult"]).EnrichmentResult("", [], [], __import__("secure_wiki.llm_client", fromlist=["UsageInfo"]).UsageInfo()))
    n_c, n_e = enrich_wiki([], ref, store)
    assert n_c == 0 and n_e == 0

def test_enrich_makes_one_git_commit(tmp_path, monkeypatch):
    import subprocess
    store = WikiStore(root=tmp_path); store.init()
    monkeypatch.setattr("secure_wiki.graph.enricher.extract_graph", _fake_extract)
    monkeypatch.setattr("secure_wiki.graph.enricher.get_review_client",
                        lambda: MagicMock(complete=lambda s,u: MagicMock(text="## Content")))
    ref = SourceRef(id="rfc", uri="file:///rfc.md", section="full", content_hash="abc")
    enrich_wiki([], ref, store)
    log = subprocess.check_output(["git", "log", "--oneline"], cwd=tmp_path).decode()
    enrich_commits = [l for l in log.strip().splitlines() if "enrich" in l]
    assert len(enrich_commits) == 1
```
`uv run pytest tests/test_graph_enricher.py -v` → FAIL

- **ACTION**: CREATE `src/secure_wiki/graph/enricher.py`
- **IMPLEMENT**: Orchestrate steps 1-7 from the architecture section. Return `(0, 0)` early if `extract_graph` returns empty concepts AND entities (no pages to write, no commit).
- **GOTCHA**: `_commit_many` must handle the case where no files were modified (e.g. linker found nothing to link). Check git staged diff before committing.
- **VALIDATE**: `uv run pytest tests/test_graph_enricher.py -v` → PASS

---

### Task 8: WIRE enrichment into `_run_pipeline()` in `__main__.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_ingest_pipeline.py  (add to existing file)
def test_pipeline_calls_enrich_after_gate_loop(tmp_path, monkeypatch):
    """enrich_wiki is called with committed claims after the gate loop."""
    enrich_calls = []
    def fake_enrich(claims, ref, store):
        enrich_calls.append(claims)
        return 1, 1
    monkeypatch.setattr("secure_wiki.__main__.enrich_wiki", fake_enrich)
    # ... set up minimal mocks for extract_claims, review_write, run_write_gate
    # run _run_pipeline with 1 claim that passes all gates
    # assert enrich_calls has one entry with the committed claim
    assert len(enrich_calls) == 1
```
`uv run pytest tests/test_ingest_pipeline.py::test_pipeline_calls_enrich_after_gate_loop -v` → FAIL

- **ACTION**: UPDATE `_run_pipeline()` in `__main__.py`
- **IMPLEMENT**:
  - Add `committed_ids: set[str] = set()` before the gate loop
  - Inside the `GateDecision.COMMIT` branch, add `committed_ids.add(claim.claim_id)`
  - After the gate loop, before `return`:
    ```python
    committed_claims = [c for c in claims if c.claim_id in committed_ids]
    if committed_claims:
        with _spinner("[enrich]   generating wiki pages…"):
            n_concepts, n_entities = enrich_wiki(committed_claims, source_ref, store)
        print(f"[enrich]   {n_concepts} concept(s), {n_entities} entity(ies) written")
    ```
  - Add `from .graph.enricher import enrich_wiki` import
- **GOTCHA**: Return type `(int, int, int, UsageInfo)` stays unchanged — enrich output is print-only, not returned.
- **VALIDATE**: `uv run pytest tests/test_ingest_pipeline.py -v` → PASS

---

### Task 9: CREATE `src/secure_wiki/lint/checker.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_lint_checker.py
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.store.embedding_store import EmbeddingStore
from secure_wiki.lint.checker import (check_orphans, check_missing_links,
                                       check_incomplete_pages, check_low_confidence_claims)
from secure_wiki.models import WikiPage

def _make_page(store, page_type, slug, body="## Content\nText.", has_required_frontmatter=True):
    page = WikiPage(slug=slug, page_type=page_type, title=slug.title(),
                    tags=[], confidence="high" if has_required_frontmatter else "",
                    sources=[], body=body, created="2026-06-18", updated="2026-06-18")
    path = store.save_wiki_page(page)
    return path

def test_orphan_detected_no_inbound_links(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    _make_page(store, "concept", "elt")       # no links to "aws"
    _make_page(store, "entity", "aws")         # aws is an orphan
    orphans = check_orphans(store)
    assert "aws" in orphans

def test_orphan_not_detected_when_linked(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    _make_page(store, "concept", "elt", body="## Content\nSee [[entities/aws|AWS]].")
    _make_page(store, "entity", "aws")
    orphans = check_orphans(store)
    assert "aws" not in orphans

def test_missing_link_detected(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    _make_page(store, "concept", "elt", body="## Content\nSee [[entities/nonexistent]].")
    missing = check_missing_links(store)
    assert "entities/nonexistent" in missing

def test_incomplete_page_missing_confidence(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    _make_page(store, "concept", "elt", has_required_frontmatter=False)
    incomplete = check_incomplete_pages(store)
    assert "elt" in incomplete
```
`uv run pytest tests/test_lint_checker.py -v` → FAIL

- **ACTION**: CREATE `src/secure_wiki/lint/__init__.py` (empty) + `checker.py`
- **IMPLEMENT** five functions:
  - `check_orphans(store)`: collect all `[[...]]` targets across all wiki pages; any page whose path is never a target is an orphan
  - `check_missing_links(store)`: collect all `[[target]]` references; any where `store.wiki / target` doesn't exist is missing
  - `check_contradictions(store, emb_store)`: find committed claim pairs with cosine similarity >= `conflict_threshold`; call LLM (`build_lint_prompt`) on top-20 pairs; return confirmed pairs
  - `check_incomplete_pages(store)`: parse frontmatter; flag missing `title`, `type`, or `confidence`; flag missing required body sections (`## Definition` for concepts, `## Overview` for entities)
  - `check_low_confidence_claims(store)`: `store.load_claims()` → filter `trust_level=untrusted OR review_notes is not None`
- **VALIDATE**: `uv run pytest tests/test_lint_checker.py -v` → PASS

---

### Task 10: CREATE `src/secure_wiki/lint/fixer.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_lint_fixer.py
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.models import LintReport
from secure_wiki.lint.fixer import apply_fixes
from secure_wiki.lint.checker import check_missing_links

def test_fix_creates_stub_for_missing_link(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    (store.wiki / "concepts" / "elt.md").write_text(
        "---\ntitle: ELT\ntype: concept\nconfidence: high\ntags: []\n---\n\nSee [[entities/ghost]].")
    report = LintReport(missing_links=["entities/ghost"])
    fixes = apply_fixes(store, report)
    assert (store.wiki / "entities" / "ghost.md").exists()
    assert len(fixes) >= 1

def test_fix_is_idempotent(tmp_path):
    store = WikiStore(root=tmp_path); store.init()
    (store.wiki / "concepts" / "elt.md").write_text(
        "---\ntitle: ELT\ntype: concept\nconfidence: high\ntags: []\n---\n\nSee [[entities/ghost]].")
    report = LintReport(missing_links=["entities/ghost"])
    apply_fixes(store, report)
    apply_fixes(store, report)   # second run should not crash or duplicate
    assert list((store.wiki / "entities").glob("ghost*.md")).__len__() == 1
```
`uv run pytest tests/test_lint_fixer.py -v` → FAIL

- **ACTION**: CREATE `src/secure_wiki/lint/fixer.py`
- **IMPLEMENT**:
  - `fix_missing_links(store, missing)`: for each `[[target]]` path, create a stub page with `confidence: low`, `## TODO` body, and `<!-- auto-generated stub -->` comment
  - `fix_cross_links(store)`: re-run `add_cross_links` across all concept/entity slugs
  - `refresh_index(store)`: call `store.update_index()`
  - `apply_fixes(store, report) -> list[str]`: call all fixers, collect descriptions, commit with `_commit_many`
- **VALIDATE**: `uv run pytest tests/test_lint_fixer.py -v` → PASS

---

### Task 11: ADD `cmd_lint()` and `lint` subparser to `__main__.py`

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_cli_lint.py
from unittest.mock import MagicMock, patch
from secure_wiki.__main__ import main
from secure_wiki.store.wiki_store import WikiStore
import sys

def test_lint_command_exits_zero_on_clean_wiki(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WIKI_DATA_PATH", str(tmp_path))
    store = WikiStore(root=tmp_path); store.init()
    monkeypatch.setattr(sys, "argv", ["secure-wiki", "lint"])
    main()  # should not raise
    out = capsys.readouterr().out
    assert "orphans" in out

def test_lint_report_only_makes_no_git_commit(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setenv("WIKI_DATA_PATH", str(tmp_path))
    store = WikiStore(root=tmp_path); store.init()
    log_before = subprocess.check_output(["git", "log", "--oneline"], cwd=tmp_path).decode()
    monkeypatch.setattr(sys, "argv", ["secure-wiki", "lint", "--report-only"])
    main()
    log_after = subprocess.check_output(["git", "log", "--oneline"], cwd=tmp_path).decode()
    assert log_before == log_after
```
`uv run pytest tests/test_cli_lint.py -v` → FAIL

- **ACTION**: UPDATE `__main__.py`
- **IMPLEMENT** `cmd_lint()` and add `lint` subparser with `--report-only` flag
- **MIRROR**: `cmd_clear` / `cmd_list` style (argparse subparser, print summary table, `_LINE` separator)
- **VALIDATE**: `uv run pytest tests/test_cli_lint.py -v` → PASS

---

### Task 12: UPDATE `context` command to include wiki pages

**TEST FIRST** — write and run (expect FAIL):
```python
# tests/test_hygiene.py  (add to existing)
from secure_wiki.read.hygiene import load_for_context
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.models import TrustLevel, WikiPage

def test_context_includes_wiki_section(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_DATA_PATH", str(tmp_path))
    store = WikiStore(root=tmp_path); store.init()
    page = WikiPage(slug="elt", page_type="concept", title="ELT", tags=[],
                    confidence="high", sources=[], body="## Definition\nELT.", created="2026-06-18", updated="2026-06-18")
    store.save_wiki_page(page)
    ctx = load_for_context(min_trust=TrustLevel.SEMI_TRUSTED)
    assert "ELT" in ctx.context_block or "elt" in ctx.context_block
```
`uv run pytest tests/test_hygiene.py -v` → FAIL

- **ACTION**: UPDATE `read/hygiene.py` `load_for_context()`
- **IMPLEMENT**: After loading claims, check if `store.wiki` has any pages. If so, append a `# Wiki Pages\n` section to `context_block` with titles and truncated bodies of all wiki pages (concepts, entities, summaries). Guard: only append if wiki directory has pages.
- **VALIDATE**: `uv run pytest tests/test_hygiene.py -v` → PASS

---

## Testing Strategy

Tests are embedded in each task (TDD). Every task ends with its test passing before moving on. The test files created across all tasks:

| Test File | Tasks | Covers |
|---|---|---|
| `tests/test_models.py` | Task 1 | WikiPage roundtrip, LintReport defaults |
| `tests/test_prompts.py` | Task 2 | Nonce injection, prompt content |
| `tests/test_wiki_store_graph.py` | Task 3 | save_wiki_page, _commit_many, append_log, update_index |
| `tests/test_graph_extractor.py` | Task 4 | LLM parse, malformed JSON fallback |
| `tests/test_page_writer.py` | Task 5 | Create/update pages, content preservation |
| `tests/test_linker.py` | Task 6 | Link insertion, no double-link, frontmatter/code exclusion |
| `tests/test_graph_enricher.py` | Task 7 | Full orchestration, noop on empty, single commit |
| `tests/test_ingest_pipeline.py` | Task 8 | enrich_wiki called post-gate-loop |
| `tests/test_lint_checker.py` | Task 9 | All five checks |
| `tests/test_lint_fixer.py` | Task 10 | Stub creation, idempotence |
| `tests/test_cli_lint.py` | Task 11 | CLI exit codes, --report-only makes no commit |
| `tests/test_hygiene.py` | Task 12 | Wiki section in context block |

### Edge Cases (covered by embedded tests)

- [ ] 0 committed claims → enrichment noop, no git commit
- [ ] LLM returns malformed JSON → empty result, no crash
- [ ] Second ingest of same concept → page updated, not duplicated
- [ ] `[[wikilink]]` to existing target → no double-link inserted
- [ ] Linker skips frontmatter and code blocks
- [ ] Lint on empty wiki → all counts 0, exits 0
- [ ] `--report-only` → no file writes, no git commits
- [ ] No embeddings available → contradiction check skipped with warning

### Final Validation Commands

After all 12 tasks pass their embedded tests:

**Level 1 — Static analysis:**
```bash
uv run python -m pyright src/
```

**Level 2 — Full test suite:**
```bash
uv run pytest -v
```

**Level 3 — Smoke test:**
```bash
secure-wiki clear --reset
secure-wiki ingest ../llm-wiki/rfc-data-architecture-v2.md
ls wiki_data/wiki/summaries/ wiki_data/wiki/concepts/ wiki_data/wiki/entities/
git -C wiki_data log --oneline | head -5
secure-wiki lint
```

---

## Acceptance Criteria

- [ ] After ingesting any source, `wiki_data/wiki/summaries/`, `concepts/`, `entities/` contain pages
- [ ] Each page has correct YAML frontmatter (title, type, tags, created, updated, sources, confidence)
- [ ] Pages contain `[[wikilinks]]` to other pages
- [ ] `wiki_data/wiki/index.md` is updated after every ingest
- [ ] `wiki_data/wiki/log.md` has one entry per ingest run
- [ ] `secure-wiki lint` runs without error and reports the five check counts
- [ ] `secure-wiki lint` auto-creates stub pages for broken wikilinks
- [ ] Full test suite passes
- [ ] Git log of wiki_data shows one enrichment commit per ingest

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM-generated concept slugs are inconsistent across ingests | HIGH | MED | Slug normalisation: lowercase, strip punctuation, replace spaces with `-`, in `page_writer.py` |
| Concept/entity page merge overwrites user edits | MED | HIGH | Merge prompt: "preserve all existing content, only append new sections/claims" |
| Enrichment LLM call adds significant latency to every ingest | HIGH | MED | Batch claims per source into a single LLM call; `[enrich]` spinner shows progress |
| `_commit_many` fails if no files were modified (git: nothing to commit) | MED | LOW | Check for staged changes before committing; skip commit if diff is empty |
| Lint contradiction check is slow on large wikis | MED | MED | Only check top-K pairs by embedding similarity (K=20); configurable via `trust_rules.yaml` |
| `git add -A` in `_delete_dir` picks up uncommitted wiki files | LOW | MED | Document: always commit enrichment before running `clear`; add assertion in `_delete_dir` |

---

## Notes

- The `wiki_data/wiki/` layer is **LLM-owned** per Karpathy's model — humans read it, LLM writes it. User edits to wiki pages are preserved by the merge strategy but may be overwritten if the user re-ingests a dramatically different version of the same source.
- Frontmatter `confidence` field maps to claim `trust_level`: trusted → high, semi-trusted → medium, untrusted → low.
- The `AGENTS.md` schema from `../llm-wiki` defines the required sections for each page type. These are enforced by `check_incomplete_pages` in lint.
- Future: the `query` command can use wiki pages as a richer context layer than raw claims alone, enabling the compounding-knowledge effect Karpathy describes.
