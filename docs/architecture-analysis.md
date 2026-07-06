# secure-llm-wiki: Architecture Analysis for Knowledge Graph + Lint Features

Precise file:line references throughout. All code excerpts are verbatim.

---

## A. `_run_pipeline()` — Full Step Trace

**File:** `src/secure_wiki/__main__.py:261–389`

**Signature:**
```python
def _run_pipeline(
    source: str,
    trust: TrustLevel,
    source_id_override: str | None,
    section: str,
    store: WikiStore,
    emb_store: EmbeddingStore,
    existing: list,           # mutable — mutated in place
    existing_embeddings: dict, # mutable — mutated in place
    sim_cfg: dict,
) -> tuple[int, int, int, UsageInfo]:   # (committed, quarantined, escalated, usage)
```

**Steps and variables at each point:**

| Step | Lines | Variables present after step |
|------|-------|------------------------------|
| 1. Read source | 281–285 | `source_text: str`, `uri: str` |
| 2. Sanitize | 288–292 | `report: SanitizeReport` (has `.is_suspicious`, `.flags`) |
| 3. Build SourceRef | 295–300 | `source_ref: SourceRef` (`id`, `uri`, `section`, `content_hash`) |
| 4. Save source doc | 303–309 | side-effect: `sources/<id>.md` written + committed |
| 5. Extract claims | 312–320 | `claims: list[Claim]`, `extract_usage: UsageInfo`, `parse_error: str\|None` |
| 6. Embed (parallel) | 324–338 | `new_embeddings: dict[str, list[float]]` keyed by `claim_id` |
| 7. Batch adversarial review | 341–353 | `batch_review: ReviewResult` (`.passed`, `.reasons`) |
| 8. Gate + store loop | 356–388 | mutates `existing` and `existing_embeddings` on COMMIT; increments `committed`/`quarantined`/`escalated` |

**Return:** `(committed, quarantined, escalated, extract_usage)` — embed usage is NOT included in the returned `UsageInfo`.

**Knowledge-graph hook point:** After step 5 (`claims` is populated, before step 6), or after step 8 (after `store.save_claim()` for each COMMIT decision). The post-commit hook has access to the full `Claim` object plus `source_ref`, `source_text`, and `uri`.

---

## B. Claim Flow: Extraction → Gate → Store

### Extraction (`src/secure_wiki/extraction/extractor.py`)
Called at `__main__.py:313`:
```python
claims, extract_usage, parse_error = extract_claims(source_text, source_ref, trust)
```
Each returned `Claim` at this point has:
- `text` — extracted statement
- `source` — the `SourceRef` passed in (caller-supplied)
- `trust_level` — passed in from CLI
- `claim_id` — auto-generated UUID (`models.py:65`)
- `ingested_at` — auto-generated ISO timestamp (`models.py:67`)
- `status = ClaimStatus.PENDING` (default, `models.py:69`)
- `gates_passed = []` (empty, `models.py:70`)
- `supersedes = None`, `review_notes = None`

### Gate (`src/secure_wiki/gate/write_gate.py:44–167`)
`run_write_gate()` **mutates** the `Claim` in place as gates pass:
- Gate 1 pass → `claim.gates_passed.append("sanitizing")` (line 71)
- Gate 2 pass → `claim.gates_passed.append("provenance")` (line 88)
- Gate 3 pass → `claim.gates_passed.append("trust_tier")` (line 100)
- Gate 4 pass → `claim.gates_passed.append("adversarial_review")` (line 119); on fail sets `claim.review_notes`
- Gate 5 pass → `claim.gates_passed.append("consistency")` (line 160)
- All pass → `claim.status = ClaimStatus.ACTIVE` (line 162)

Returns `GateOutcome(decision, claim, detail)`.

### Store (`src/secure_wiki/store/wiki_store.py:128–135`)
```python
def save_claim(self, claim: Claim, gate_detail: str = "") -> Path:
    path = self.pages / f"{claim.claim_id}.md"
    path.write_text(_serialize(claim, gate_detail), encoding="utf-8")
    msg = f"add {claim.claim_id[:8]} [{claim.trust_level.value}] from {claim.source.uri}"
    self._commit(path, msg)
    return path
```
By the time `save_claim()` is called, `claim` has:
- `status = ACTIVE`
- `gates_passed = ["sanitizing", "provenance", "trust_tier", "adversarial_review", "consistency"]`
- All other fields set at extraction time (claim_id, text, source, trust_level, ingested_at)

**Knowledge-graph hook:** After `store.save_claim(claim)` at `__main__.py:373`, before `existing.append(claim)`. At that point you have the fully-gated `Claim` and can generate summary/concept pages.

---

## C. LLM Client Methods

**File:** `src/secure_wiki/llm_client.py`

Three concrete classes: `OllamaClient`, `AnthropicClient`, `OpenAIEmbedClient`.

### `complete(self, system: str, user: str) -> CompletionResult`
- `OllamaClient:97`, `AnthropicClient:142`
- Returns `CompletionResult(text: str, usage: UsageInfo)`
- Blocking. Temperature=0, max 4096 tokens (Ollama) / 8192 (Anthropic).

### `stream(self, system: str, user: str) -> Iterator[str | UsageInfo]`
- `OllamaClient:112`, `AnthropicClient:157`
- Yields `str` chunks, then a terminal `UsageInfo` sentinel as the last item.
- Used only in `cmd_query` for interactive output.

### `embed(self, text: str) -> list[float]`
- `OllamaClient:131` — calls `ollama.Client.embed()`, returns `list[float]`
- `AnthropicClient:172` — raises `NotImplementedError` (no embedding support)
- `OpenAIEmbedClient:182` — calls `openai.Client.embeddings.create()`, returns `list[float]`

**Factory functions** (`llm_client.py:216–240`):
- `get_extraction_client()` → `OllamaClient | AnthropicClient`
- `get_review_client()` → `OllamaClient | AnthropicClient`
- `get_embed_client()` → `OllamaClient | OpenAIEmbedClient`

**Knowledge-graph hook:** Use `get_extraction_client()` (or `get_review_client()`) with `.complete(system, user)` for LLM-driven summarization, concept extraction, and lint analysis. The same `CompletionResult` return type applies.

---

## D. `load_for_context()` — Return Value and Consumption

**File:** `src/secure_wiki/read/hygiene.py:36–72`

```python
def load_for_context(
    store: WikiStore | None = None,
    min_trust: TrustLevel = TrustLevel.SEMI_TRUSTED,
    include_pending: bool = False,
) -> WikiContext:
```

Returns `WikiContext` dataclass (`hygiene.py:28–33`):
```python
@dataclass
class WikiContext:
    system_note: str    # append to the session's system prompt
    context_block: str  # nonce-delimited claim content: "<wiki-context-{nonce}>...</wiki-context-{nonce}>"
    nonce: str
    claim_count: int
```

**Filters applied** (`hygiene.py:50–60`):
- Status: `ACTIVE` only (or also `PENDING` if `include_pending=True`)
- Trust: `>= min_trust` AND never `UNTRUSTED` (hard floor at `semi-trusted`)

**Format of `context_block`:** Each claim rendered as `[T] <text>` or `[S] <text>` per trust symbol (`_format_claims`, `hygiene.py:83–91`).

### `cmd_query` consumption (`__main__.py:481–516`)
```python
ctx = load_for_context(min_trust=min_trust, include_pending=args.include_pending)
system = f"{ctx.system_note}\n\n{ctx.context_block}\n\n{QUERY_TASK_PROMPT}"
client = get_review_client()
# streams client.stream(system, question) to stdout
```
Uses `ctx.system_note`, `ctx.context_block`, `ctx.claim_count`.

### `cmd_context` consumption (`__main__.py:519–529`)
```python
ctx = load_for_context(min_trust=min_trust, include_pending=args.include_pending)
print(ctx.system_note)
print(ctx.context_block)
```
Purely prints both fields to stdout. No LLM call.

**Knowledge-graph hook:** `load_for_context` only reads `pages/` via `store.load_claims()`. A new `graph/` directory would need its own load path, or `load_for_context` would need an opt-in parameter to include graph pages.

---

## E. `WikiStore._commit()` — Git Pattern

**File:** `src/secure_wiki/store/wiki_store.py:275–278`

```python
def _commit(self, path: Path, message: str) -> None:
    rel = str(path.relative_to(self.root))
    _git(["add", rel], self.root)
    _git(["commit", "-m", message], self.root)
```

**Critical detail:** `_commit` stages exactly **one file** by relative path. It does NOT use `git add -A` or `git add .`.

**`_git()` helper** (`wiki_store.py:58–65`): runs `subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=_GIT_ENV)`. Raises `subprocess.CalledProcessError` on failure.

**Implications for new directories:**
- `init()` (`wiki_store.py:104–122`) creates `pages/`, `quarantine/`, `sources/` via `mkdir`. Any new directory (e.g. `graph/`) must be similarly created before first write.
- `_commit()` will fail if the directory doesn't exist or git can't stage the relative path.
- `_delete_dir()` uses `_git(["add", "-A"], self.root)` (line 220, 263) — this broad staging is only used on delete operations, not normal writes.
- `load_claims()` hardcodes `self.pages` — it will never see files in `graph/`.
- `reset(keep_history=False)` calls `shutil.rmtree(self.root)` — destroys everything including `graph/`. New directory must be re-created in `init()`.

**Knowledge-graph new directory pattern:**
```python
self.graph = self.root / "graph"  # add in __init__
self.graph.mkdir(parents=True, exist_ok=True)  # add in init()
```
Then write graph files with a dedicated `save_graph_page(path, content)` that calls `self._commit(path, msg)`.

---

## F. Gate Loop — `existing` and `existing_embeddings` Incremental Build

**File:** `src/secure_wiki/__main__.py`

**Initial load** (in `cmd_ingest`, lines 419–420):
```python
existing = store.load_claims()           # all ACTIVE claims from pages/
existing_embeddings = emb_store.load_all()  # dict[claim_id, list[float]]
```
Both are passed into each `_run_pipeline()` call as the same mutable objects.

**Inside `_run_pipeline` gate loop** (lines 372–377):
```python
if outcome.decision == GateDecision.COMMIT:
    store.save_claim(claim)
    existing.append(claim)                          # mutated in place
    if claim.claim_id in new_embeddings:
        emb_store.save(claim.claim_id, new_embeddings[claim.claim_id])
        existing_embeddings[claim.claim_id] = new_embeddings[claim.claim_id]  # mutated
    committed += 1
```

**Effect:** Each committed claim is immediately available to subsequent gate evaluations within the same folder run. Claims 2–N in a batch see all previously-committed claims from the same batch, enabling intra-batch duplicate/conflict detection.

**`existing_embeddings` passed to gate** (line 366):
```python
outcome = run_write_gate(
    claim,
    report,
    existing,
    new_embedding=new_embeddings.get(claim.claim_id),
    existing_embeddings=existing_embeddings or None,
    ...
)
```
Gate 5 (`write_gate.py:122–160`) computes cosine similarity of `new_embedding` against all values in `existing_embeddings`.

---

## G. `prompts.py` — All Constants and Builder Functions

**File:** `src/secure_wiki/prompts.py`

| Constant/Function | Line | Purpose |
|-------------------|------|---------|
| `EXTRACTION_SYSTEM_PROMPT` | 49 | System prompt template for claim extraction. Contains `{nonce}` placeholder. Instructs model to output JSON list of claim strings. Raw template — do not use directly. |
| `REVIEW_SYSTEM_PROMPT` | 97 | System prompt template for adversarial review. Contains `{nonce}`. Instructs independent model to detect manipulation, NOT judge truth. Expects `PASS`/`BLOCK` + reasons in JSON. |
| `CONTEXT_SYSTEM_NOTE` | ~145 | (referenced at `hygiene.py:17`) Appended to downstream session system prompts. Declares wiki content as curated data, not instructions. Contains `{nonce}`. |
| `QUERY_TASK_PROMPT` | 177 | Static task framing injected into `cmd_query` system prompt. No `{nonce}` — not attacker-influenced. |
| `_TRUST_MARKER_RE` | 189 | `re.compile(r"\[[TSU]\]")` — strips forged trust tokens from claim text. |
| `_READTIME_MARKER` | 192 | `{"trusted": "[T]", "semi-trusted": "[S]"}` — maps trust levels to display tokens. |
| `_new_nonce()` | 195 | Returns `secrets.token_hex(8)` — per-call random delimiter. |
| `_trust_value(claim)` | 200 | Extracts trust level string safely from enum or str. |
| `_status_value(claim)` | 207 | Extracts status string safely. |
| `_safe_claim_text(text)` | 213 | Strips forged `[T]/[S]/[U]` markers from claim text before embedding in prompts. |
| `build_extraction_prompt(source_text)` | 227 | Returns `(system, user, nonce)`. Wraps `source_text` in nonce delimiters. Caller gets nonce for verification. |
| `build_review_prompt(proposed, source_text, existing_high_trust)` | 252 | Returns `(system, user, nonce)`. Wraps proposed claims + source in nonce delimiters. |
| `build_context_prompt(claims, nonce)` | 292 | Returns `(system_note, context_block)`. Used by `load_for_context`. Renders claims with trust markers. |
| `build_query_prompt(claims)` | 326 | Returns `(full_system, nonce)`. Combines `system_note + context_block + QUERY_TASK_PROMPT`. |
| `strip_trust_markers(text)` | ~189 | Applies `_TRUST_MARKER_RE` to sanitize text. |

**Security invariant** (prompts.py:9–14): Every stage that touches source-derived text wraps it in a per-call nonce delimiter. Builder functions enforce this — raw constants must never be used directly.

**Knowledge-graph hook:** New prompts for graph enrichment (summarize, extract entities, detect contradictions) follow the same pattern: define a raw `*_SYSTEM_PROMPT` constant, write a `build_*_prompt(...)` function that inserts a nonce, and always wrap claim text with `_safe_claim_text()`.

---

## H. `Claim.to_dict()` / `from_dict()` — Frontmatter YAML Contract

**File:** `src/secure_wiki/models.py:74–98`

### `to_dict()` (line 74)
```python
def to_dict(self) -> dict:
    d = asdict(self)             # flattens all dataclass fields recursively
    d["trust_level"] = self.trust_level.value   # enum → string
    d["status"] = self.status.value              # enum → string
    return d
```
`asdict()` recursively converts nested dataclasses. `SourceRef` becomes a nested dict.

**All fields serialized to YAML frontmatter:**

| Key | Type in YAML | Source |
|-----|-------------|--------|
| `claim_id` | str (UUID) | `Claim.claim_id` |
| `text` | str | `Claim.text` |
| `trust_level` | str (`"trusted"`, `"semi-trusted"`, `"untrusted"`) | `Claim.trust_level.value` |
| `status` | str (`"active"`, `"pending"`, `"quarantined"`, `"superseded"`) | `Claim.status.value` |
| `ingested_at` | str (ISO 8601) | `Claim.ingested_at` |
| `gates_passed` | list[str] | `Claim.gates_passed` |
| `supersedes` | str or null | `Claim.supersedes` |
| `review_notes` | str or null | `Claim.review_notes` |
| `source.id` | str | `Claim.source.id` |
| `source.uri` | str | `Claim.source.uri` |
| `source.section` | str | `Claim.source.section` |
| `source.content_hash` | str (SHA-256 hex) | `Claim.source.content_hash` |

### `from_dict()` (line 81)
Manually reconstructs `SourceRef` from `d["source"]` nested dict, then calls `cls(...)` with all fields. Uses `.get()` with defaults for `gates_passed`, `supersedes`, `review_notes` — these are backward-compatible optional fields.

### Serialization format (`wiki_store.py:68–75`)
```python
def _serialize(claim: Claim, gate_detail: str = "") -> str:
    d = claim.to_dict()
    frontmatter = yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=True)
    body = claim.text
    if gate_detail:
        body += f"\n\n> Gate decision: {gate_detail}"
    return f"---\n{frontmatter}---\n\n{body}\n"
```
File structure: `---\n<YAML>\n---\n\n<claim text>\n`

### Deserialization (`wiki_store.py:78–85`)
```python
def _deserialize(path: Path) -> Claim:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---\n", 2)   # splits on first two "---\n" occurrences
    raw = yaml.safe_load(parts[1])
    return Claim.from_dict(raw)
```

**Knowledge-graph hook for new page types:** Graph pages (summary pages, concept pages) can use the same frontmatter pattern but with a different schema — they do not need to be `Claim` instances. They would need their own `_serialize_graph_page()` / `_deserialize_graph_page()` functions, a new `WikiStore.save_graph_page()` method, and their own `load_graph_pages()` reader. The `_commit()` method works unchanged for any file path under `self.root`.

---

## Integration Summary for New Features

### Feature 1: Knowledge Graph Enrichment (ingest hook)

**Where to hook in `_run_pipeline`:** After line 373 (`store.save_claim(claim)`), before line 374 (`existing.append(claim)`). At this point:
- `claim` is fully gated, `status=ACTIVE`, all `gates_passed` set
- `source_text`, `uri`, `source_ref` are in scope
- `new_embeddings` has the embedding for this claim if available
- `store` and `emb_store` are available for writes

**New directory:** Add `self.graph = self.root / "graph"` to `WikiStore.__init__` and `self.graph.mkdir()` to `WikiStore.init()`.

**New WikiStore methods needed:** `save_graph_page(page_id, content) -> Path`, `load_graph_pages() -> list`.

**LLM calls:** Use `get_extraction_client().complete(system, user)` — same client, same `CompletionResult` return type.

### Feature 2: `lint` CLI Command

**Where to add:** New `cmd_lint(args)` function in `__main__.py`, registered with a new `lint` subparser.

**Data access:**
- `store.load_claims()` — all ACTIVE claims
- `store.load_quarantined()` — quarantined claims
- `emb_store.load_all()` — all embeddings for similarity analysis
- `load_for_context()` — returns `WikiContext` with claim count; can be used to detect orphans (claims not reachable via any graph page)
- `get_extraction_client().complete()` — for LLM-driven contradiction/low-confidence detection

**No new data model needed** — `lint` is read-only; findings can be printed to stdout or written to a report file outside the wiki repo.
