# Implementation Report

**Plan**: `.claude/PRPs/plans/security-audit-remediation.plan.md`
**Branch**: `feature/wiki-knowledge-graph`
**Date**: 2026-07-09
**Status**: COMPLETE

---

## Summary

Closed the proven end-to-end indirect-prompt-injection chain (untrusted ingest →
UNTRUSTED claim commits as ACTIVE → enrichment writes a wiki page → raw page body
with forged `[T]` markers reaches the consuming LLM) plus 7 lower-severity
correctness/hardening gaps and 1 maintainer note from the security audit. All 15
planned tasks were completed in order, each with a regression test, and the full
test suite is green (183 passed).

---

## Assessment vs Reality

| Metric     | Predicted                              | Actual                                  | Reasoning |
| ---------- | --------------------------------------- | ---------------------------------------- | --------- |
| Complexity | MEDIUM — 9 localized fixes, pattern-completion | MEDIUM — matched | Every fix mirrored an existing sibling pattern exactly as predicted; no new design was needed. |
| Confidence | High (each fix cited file:line)        | Confirmed correct | Root causes and fix locations were accurate; only one unplanned ripple effect surfaced (see Deviations). |

**Deviation from plan:**
- Removing the Gate 4 `review_write(...)` fallback call (Task 15) also removed
  `write_gate.py`'s only use of the `review_write` import, which broke three
  pre-existing tests in `tests/test_cli.py` and two in `tests/test_ingest_pipeline.py`
  that defensively patched `secure_wiki.gate.write_gate.review_write` (a safety
  net for the old fallback that no longer exists). The plan's Task 15 GOTCHA
  called out only `test_injection_corpus.py::TestFullPipeline` as affected;
  five additional call sites needed the same dead patch removed. Fixed
  immediately per the golden rule (never accumulate broken state) — all now
  pass without the stale patch.
- Task 1 removed `existing_trusted`'s use in Gate 3; Task 15 removed its only
  other use (the Gate 4 fallback). This left `existing_trusted` as dead code,
  so it was deleted along with the now-unused `existing = existing_claims or []`
  local and the `review_write` import in `write_gate.py`. The `existing_claims`
  parameter itself was kept (API compatibility with all callers) but is now
  unused in the body — documented in the docstring.

---

## Tasks Completed

| #  | Task                                                          | File(s)                                              | Status |
| -- | -------------------------------------------------------------- | ----------------------------------------------------- | ------ |
| 1  | SEC-04: quarantine ALL untrusted writes                       | `gate/write_gate.py`                                   | ✅ |
| 2  | SEC-04 regression test                                        | `tests/test_write_gate_consistency.py`                 | ✅ |
| 3  | F-02/SEC-01: strip markers to fixpoint for pages               | `read/hygiene.py`, `prompts.py`                        | ✅ |
| 4  | F-02 regression test                                           | `tests/test_read_hygiene.py`                           | ✅ |
| 5  | F-01/SEC-02: quarantine on `parse_error` before gate loop       | `__main__.py`                                          | ✅ |
| 6  | F-01 regression test                                           | `tests/test_ingest_pipeline.py`                        | ✅ |
| 7  | F-03: nonce-delimit merge prompt                                | `graph/page_writer.py`                                 | ✅ |
| 8  | F-03 regression test                                           | `tests/test_page_writer.py`                            | ✅ |
| 9  | F-06: UUID-validate `claim_id` + path bounds-check              | `models.py`, `store/wiki_store.py`                     | ✅ |
| 10 | F-06 regression tests                                          | `tests/test_models.py`, `tests/test_wiki_store.py`     | ✅ |
| 11 | F-08: strip control chars from commit-message URI               | `store/wiki_store.py`                                  | ✅ |
| 12 | F-04: sanitize + provenance-mark operator edits                 | `review/interactive.py`                                | ✅ |
| 13 | SEC-03: feed `cleaned_text` to extraction (hash/store stay raw) | `__main__.py`                                          | ✅ |
| 14 | F-07: future-safe trust registry init (`lru_cache`)             | `trust/tiering.py`                                     | ✅ |
| 15 | F-05: fail-closed Gate 4 fallback + maintainer note             | `gate/write_gate.py`, `__main__.py`, `tests/test_injection_corpus.py` | ✅ |

---

## Validation Results

| Check                              | Result | Details |
| ----------------------------------- | ------ | ------- |
| Level 1 — static/import sanity      | ✅     | `py_compile` on all changed files, exit 0 |
| Level 2 — per-finding unit tests    | ✅     | 106 passed (8 test files targeted by the plan) |
| Level 3 — full suite                | ✅     | 183 passed, 0 failed |
| Level 4 — manual injection-chain    | ✅     | SEC-04: untrusted `[T]`-forged claim → QUARANTINE (never ACTIVE). F-02: a poisoned wiki page manually placed on disk had its forged `[T]` marker stripped by `secure-wiki context`, with content preserved and no `[T]` residue in the page body. |

---

## Files Changed

| File                                     | Action | Lines      |
| ----------------------------------------- | ------ | ---------- |
| `src/secure_wiki/gate/write_gate.py`       | UPDATE | dropped Gate 3 conjunct, fail-closed Gate 4, removed dead `existing_trusted`/`review_write` |
| `src/secure_wiki/read/hygiene.py`          | UPDATE | `_format_wiki_pages` strips title+body markers |
| `src/secure_wiki/prompts.py`               | UPDATE | `strip_trust_markers` iterates to fixpoint |
| `src/secure_wiki/__main__.py`              | UPDATE | `parse_error` pre-gate quarantine, `cleaned_text` to extraction, reembed comment |
| `src/secure_wiki/graph/page_writer.py`     | UPDATE | nonce-delimited merge prompt |
| `src/secure_wiki/models.py`                | UPDATE | UUID validation on `Claim`/`Correction.from_dict` |
| `src/secure_wiki/store/wiki_store.py`      | UPDATE | `_safe_page_path` bounds-check, `_clean_uri` for commit messages |
| `src/secure_wiki/review/interactive.py`    | UPDATE | operator edits stripped + provenance-marked |
| `src/secure_wiki/trust/tiering.py`         | UPDATE | `lru_cache`-backed `_get_registry` |
| `tests/test_write_gate_consistency.py`     | UPDATE | +2 tests (SEC-04, F-05 fail-closed) |
| `tests/test_read_hygiene.py`               | UPDATE | +2 tests (F-02 strip, nested fixpoint) |
| `tests/test_ingest_pipeline.py`            | UPDATE | +2 tests (F-01, SEC-03) |
| `tests/test_page_writer.py`                | UPDATE | +1 test (F-03 nonce delimiters) |
| `tests/test_models.py`                     | UPDATE | +3 tests (F-06 Claim/Correction) |
| `tests/test_wiki_store.py`                 | UPDATE | +2 tests (F-06 bounds-check, F-08 URI) |
| `tests/test_review_interactive.py`         | UPDATE | +1 test (F-04), 1 existing assertion updated |
| `tests/test_trust_tiering.py`              | UPDATE | +1 test (F-07 singleton) |
| `tests/test_injection_corpus.py`           | UPDATE | explicit `review_result`, doc comments updated |
| `tests/test_cli.py`                        | UPDATE | removed 3 stale `write_gate.review_write` patches (unplanned, see Deviations) |

---

## Deviations from Plan

See "Assessment vs Reality" above — the plan's Task 15 GOTCHA underestimated the
blast radius of removing the Gate 4 fallback's `review_write` import; five
additional test call sites (in `test_cli.py` and `test_ingest_pipeline.py`)
needed the same stale defensive patch removed. All fixed; no scope beyond the
plan's intent.

---

## Issues Encountered

None beyond the deviation above — every fix mirrored its cited sibling pattern
without needing design changes.

---

## Tests Written

| Test File                              | Test Cases |
| ---------------------------------------- | ---------- |
| `tests/test_write_gate_consistency.py`   | `test_untrusted_claim_quarantined_on_empty_wiki`, `test_missing_review_result_raises_fail_closed` |
| `tests/test_read_hygiene.py`             | `test_page_body_trust_markers_are_stripped`, `test_nested_trust_markers_leave_no_residue` |
| `tests/test_ingest_pipeline.py`          | `test_pipeline_quarantines_all_claims_on_parse_error`, `test_pipeline_feeds_cleaned_text_to_extraction_only` |
| `tests/test_page_writer.py`              | `test_merge_prompt_contains_nonce_delimiters` |
| `tests/test_models.py`                   | `test_claim_from_dict_rejects_traversal_claim_id`, `test_correction_from_dict_rejects_traversal_claim_id`, `test_claim_from_dict_accepts_canonical_uuid` |
| `tests/test_wiki_store.py`               | `test_safe_page_path_rejects_traversal`, `test_commit_message_strips_newline_from_uri` |
| `tests/test_review_interactive.py`       | `test_edit_strips_trust_markers_and_marks_provenance` |
| `tests/test_trust_tiering.py`            | `test_get_registry_returns_cached_singleton` |

---

## Next Steps

- [ ] Review implementation
- [ ] Create PR: `gh pr create`
- [ ] Merge when approved
