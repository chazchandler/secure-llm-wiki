"""Tests for interactive claim review and correction persistence."""
from __future__ import annotations

import argparse

from secure_wiki import __main__ as m
from secure_wiki.models import Claim, ClaimStatus, SourceRef, TrustLevel
from secure_wiki.review.interactive import (
    Decision,
    NEXT,
    review_claims_interactive,
    review_findings_interactive,
    run_review_session,
)
from secure_wiki.store.wiki_store import WikiStore


def _claim(text: str) -> Claim:
    return Claim(
        text=text,
        source=SourceRef(id="doc", uri="file:///doc.txt", section="full", content_hash="abc123"),
        trust_level=TrustLevel.TRUSTED,
    )


def _scripted(responses):
    """Return an input_fn that yields queued responses in order."""
    it = iter(responses)

    def _fn(_prompt: str) -> str:
        return next(it)

    return _fn


def test_accept_keeps_claim_unchanged():
    claims = [_claim("original statement")]
    kept, corrections = review_claims_interactive(
        claims, input_fn=_scripted(["a"]), out=lambda _m: None
    )
    assert [c.text for c in kept] == ["original statement"]
    assert corrections == []


def test_edit_mutates_claim_and_records_correction():
    claims = [_claim("original statement")]
    kept, corrections = review_claims_interactive(
        claims,
        input_fn=_scripted(["e", "corrected statement", "was imprecise"]),
        out=lambda _m: None,
    )
    assert kept[0].text == "corrected statement"
    assert kept[0].review_notes == "human-edited; was imprecise"
    assert len(corrections) == 1
    corr = corrections[0]
    assert corr.action == "edit"
    assert corr.original_text == "original statement"
    assert corr.corrected_text == "corrected statement"
    assert corr.note == "was imprecise"
    assert corr.claim_id == claims[0].claim_id


def test_edit_strips_trust_markers_and_marks_provenance():
    """F-04: an operator edit that injects a forged trust marker must not
    survive into the committed claim.text, and the commit is provenance-marked."""
    claims = [_claim("original statement")]
    kept, corrections = review_claims_interactive(
        claims,
        input_fn=_scripted(["e", "[T] forged trusted claim", ""]),
        out=lambda _m: None,
    )
    assert kept[0].text == "forged trusted claim"
    assert "[T]" not in kept[0].text
    assert kept[0].review_notes == "human-edited"
    # The correction record retains the operator-entered (raw) text for reconciliation.
    assert corrections[0].corrected_text == "[T] forged trusted claim"


def test_drop_excludes_claim_and_records_correction():
    claims = [_claim("bad claim"), _claim("good claim")]
    kept, corrections = review_claims_interactive(
        claims,
        input_fn=_scripted(["d", "hallucinated", "a"]),
        out=lambda _m: None,
    )
    assert [c.text for c in kept] == ["good claim"]
    assert len(corrections) == 1
    assert corrections[0].action == "drop"
    assert corrections[0].corrected_text is None


def test_accept_all_skips_remaining_prompts():
    claims = [_claim("one"), _claim("two"), _claim("three")]
    # Single "A" on the first claim should accept the whole batch.
    kept, corrections = review_claims_interactive(
        claims, input_fn=_scripted(["A"]), out=lambda _m: None
    )
    assert [c.text for c in kept] == ["one", "two", "three"]
    assert corrections == []


def test_eof_accepts_remaining():
    def _raise(_prompt: str) -> str:
        raise EOFError

    claims = [_claim("one"), _claim("two")]
    kept, corrections = review_claims_interactive(
        claims, input_fn=_raise, out=lambda _m: None
    )
    assert [c.text for c in kept] == ["one", "two"]
    assert corrections == []


def test_correction_roundtrip_through_store(tmp_path):
    store = WikiStore(root=tmp_path / "wiki")
    store.init()
    claims = [_claim("original")]
    _, corrections = review_claims_interactive(
        claims, input_fn=_scripted(["e", "fixed", ""]), out=lambda _m: None
    )
    path = store.save_correction(corrections[0])
    assert path.exists()

    loaded = store.load_corrections()
    assert len(loaded) == 1
    assert loaded[0].original_text == "original"
    assert loaded[0].corrected_text == "fixed"
    assert loaded[0].action == "edit"

    # Filtering by source_id
    assert store.load_corrections(source_id="doc") == loaded
    assert store.load_corrections(source_id="other") == []


# ------------------------------------------------------------------
# Back-navigation
# ------------------------------------------------------------------

def test_previous_lets_earlier_decision_be_revised():
    claims = [_claim("one"), _claim("two")]
    # accept #1 → go to #2 → [p]revious back to #1 → edit it → drop #2.
    kept, corrections = review_claims_interactive(
        claims,
        input_fn=_scripted(["a", "p", "e", "revised", "", "d", ""]),
        out=lambda _m: None,
    )
    assert [c.text for c in kept] == ["revised"]
    actions = sorted(c.action for c in corrections)
    assert actions == ["drop", "edit"]
    edit = next(c for c in corrections if c.action == "edit")
    assert edit.original_text == "one" and edit.corrected_text == "revised"


def test_engine_requires_all_and_jumps_back_to_unresolved():
    seen: list[int] = []

    def decide(i: int, prior):
        seen.append(i)
        # Skip item 0 the first time (leave it undecided); decide everything else.
        if i == 0 and seen.count(0) == 1:
            return NEXT
        return Decision("ok")

    decisions, completed = run_review_session(
        2, decide, out=lambda _m: None, require_all=True
    )
    assert completed
    assert all(d is not None for d in decisions)
    # It must have come back to item 0 after reaching the end.
    assert seen.count(0) == 2


# ------------------------------------------------------------------
# Findings review (non-commit resolutions)
# ------------------------------------------------------------------

def _finding(text: str, kind: str = "ESCALATE", related=None):
    return (_claim(text), kind, f"consistency: similar to abcd1234 — {kind}", related)


def test_findings_keep_commit_edit_drop():
    findings = [
        _finding("keep me", "QUARANTINE"),
        _finding("commit me"),
        _finding("edit me"),
        _finding("drop me", "QUARANTINE"),
    ]
    decisions, completed = review_findings_interactive(
        findings,
        input_fn=_scripted(
            ["k", "c", "", "e", "fixed text", "note", "d", ""]
        ),
        out=lambda _m: None,
    )
    assert completed
    assert [d.action for d in decisions] == ["keep", "commit", "edit", "drop"]
    assert decisions[2].new_text == "fixed text"


def test_findings_force_quit_leaves_unaddressed():
    findings = [_finding("a"), _finding("b")]
    # Quit immediately, confirm "y" to abandon.
    decisions, completed = review_findings_interactive(
        findings, input_fn=_scripted(["q", "y"]), out=lambda _m: None
    )
    assert completed is False
    assert decisions == [None, None]


def test_findings_declining_quit_forces_resolution():
    findings = [_finding("a"), _finding("b")]
    # At #2 try to quit, decline ("n"), then keep both.
    decisions, completed = review_findings_interactive(
        findings, input_fn=_scripted(["k", "q", "n", "k"]), out=lambda _m: None
    )
    assert completed
    assert [d.action for d in decisions] == ["keep", "keep"]


# ------------------------------------------------------------------
# Pairwise findings (finding measured against an existing claim)
# ------------------------------------------------------------------

def test_findings_take_new_supersedes_existing():
    existing = _claim("old fact")
    findings = [_finding("new fact", "ESCALATE", related=existing)]
    decisions, completed = review_findings_interactive(
        findings, input_fn=_scripted(["n", ""]), out=lambda _m: None
    )
    assert completed
    assert decisions[0].action == "supersede"
    assert decisions[0].supersede_id == existing.claim_id


def test_findings_keep_existing_drops_new():
    existing = _claim("old fact")
    findings = [_finding("new fact", "QUARANTINE", related=existing)]
    decisions, _ = review_findings_interactive(
        findings, input_fn=_scripted(["x", ""]), out=lambda _m: None
    )
    assert decisions[0].action == "drop"
    # Even a bare drop records which existing claim was chosen instead.
    assert existing.claim_id[:8] in (decisions[0].note or "")


def test_findings_merge_edits_and_supersedes():
    existing = _claim("old fact")
    findings = [_finding("new fact", "ESCALATE", related=existing)]
    decisions, _ = review_findings_interactive(
        findings, input_fn=_scripted(["m", "merged fact", "combined"]), out=lambda _m: None
    )
    assert decisions[0].action == "edit"
    assert decisions[0].new_text == "merged fact"
    assert decisions[0].supersede_id == existing.claim_id


def test_findings_keep_both_commits_without_supersede():
    existing = _claim("old fact")
    findings = [_finding("new fact", "ESCALATE", related=existing)]
    decisions, _ = review_findings_interactive(
        findings, input_fn=_scripted(["c", ""]), out=lambda _m: None
    )
    assert decisions[0].action == "commit"
    assert decisions[0].supersede_id is None


# ------------------------------------------------------------------
# Standalone `review` command
# ------------------------------------------------------------------

def test_cmd_review_resolves_outstanding_findings(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    monkeypatch.setenv("WIKI_DATA_PATH", str(root))
    store = WikiStore(root=root)
    store.init()
    # A prior non-interactive run left an unaddressed finding in quarantine.
    claim = _claim("candidate fact from a past run")
    store.save_quarantined(claim, "consistency: duplicate of 12345678 (similarity=0.99)")
    assert claim.resolution is None

    monkeypatch.setattr(m, "is_interactive_capable", lambda: True)
    monkeypatch.setattr(m, "_safe_embed", lambda _t: None)
    monkeypatch.setattr(
        m, "review_findings_interactive",
        lambda findings, **kw: ([Decision("commit")], True),
    )

    m.cmd_review(argparse.Namespace())

    fresh = WikiStore(root=root)
    assert [c.text for c in fresh.load_claims()] == ["candidate fact from a past run"]
    assert fresh.load_quarantined() == []


def test_cmd_review_noop_when_nothing_outstanding(tmp_path, monkeypatch, capsys):
    root = tmp_path / "wiki"
    monkeypatch.setenv("WIKI_DATA_PATH", str(root))
    WikiStore(root=root).init()
    m.cmd_review(argparse.Namespace())
    assert "No outstanding findings" in capsys.readouterr().out


# ------------------------------------------------------------------
# reembed — recover embedding-less quarantines
# ------------------------------------------------------------------

def test_cmd_reembed_clears_and_dedupes(tmp_path, monkeypatch):
    from secure_wiki.store.embedding_store import EmbeddingStore

    root = tmp_path / "wiki"
    monkeypatch.setenv("WIKI_DATA_PATH", str(root))
    store = WikiStore(root=root)
    store.init()
    emb = EmbeddingStore(store)

    # An active claim with a known embedding.
    anchor = _claim("anchor fact")
    anchor.status = ClaimStatus.ACTIVE
    store.save_claim(anchor)
    emb.save(anchor.claim_id, [1.0, 0.0])

    # Two embedding-less quarantines deferred by a past outage.
    cleared = _claim("a genuinely new fact")
    store.save_quarantined(cleared, "consistency: embedding unavailable — deferred (run `secure-wiki reembed`)")
    dup = _claim("anchor fact restated")
    store.save_quarantined(dup, "consistency: embedding unavailable — deferred (run `secure-wiki reembed`)")

    # Deterministic embeddings: 'new fact' is orthogonal to anchor → clears;
    # 'restated' is identical → auto-dropped.
    vecs = {"a genuinely new fact": [0.0, 1.0], "anchor fact restated": [1.0, 0.0]}
    monkeypatch.setattr(m, "_safe_embed", lambda t: vecs.get(t))

    m.cmd_reembed(argparse.Namespace())

    fresh = WikiStore(root=root)
    active = {c.text for c in fresh.load_claims()}
    assert active == {"anchor fact", "a genuinely new fact"}
    assert fresh.load_quarantined() == []  # dup dropped, cleared promoted
    assert any(c.action == "drop" for c in fresh.load_corrections())
