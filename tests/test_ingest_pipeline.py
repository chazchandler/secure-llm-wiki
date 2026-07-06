"""Tests for enrich_wiki integration in _run_pipeline."""
from unittest.mock import patch

import pytest

from secure_wiki.__main__ import _resolve_findings, _run_pipeline
from secure_wiki.llm_client import UsageInfo
from secure_wiki.models import Claim, ClaimStatus, SourceRef, TrustLevel
from secure_wiki.review.adversarial import ReviewResult
from secure_wiki.review.interactive import Decision
from secure_wiki.gate.write_gate import GateDecision, GateOutcome
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.store.embedding_store import EmbeddingStore


def _make_claim(text="test claim"):
    return Claim(
        text=text,
        source=SourceRef(id="test", uri="file:///test.md", section="full",
                         content_hash=SourceRef.compute_hash(text)),
        trust_level=TrustLevel.TRUSTED,
        status=ClaimStatus.ACTIVE,
        gates_passed=["sanitizing", "provenance", "trust_tier", "adversarial_review", "consistency"],
    )


def test_pipeline_calls_enrich_after_gate_loop(tmp_path, monkeypatch):
    """enrich_wiki is called with committed claims after the gate loop."""
    import argparse
    from pathlib import Path

    enrich_calls = []

    def fake_enrich(claims, ref, store):
        enrich_calls.append(claims)
        return 1, 1

    claim = _make_claim("AI systems require careful oversight.")
    _pass = ReviewResult(passed=True, reasons=[])

    from secure_wiki.gate.write_gate import GateOutcome
    monkeypatch.setattr("secure_wiki.__main__.enrich_wiki", fake_enrich)

    source_file = tmp_path / "source.txt"
    source_file.write_text("AI systems require careful oversight.", encoding="utf-8")

    store = WikiStore(root=tmp_path / "wiki")
    emb_store = EmbeddingStore(store)
    existing = []
    existing_embeddings = {}
    sim_cfg = {"duplicate_threshold": 0.95, "conflict_threshold": 0.85}

    with patch("secure_wiki.__main__.extract_claims", return_value=([claim], UsageInfo(), None)), \
         patch("secure_wiki.__main__.review_write", return_value=_pass), \
         patch("secure_wiki.gate.write_gate.review_write", return_value=_pass), \
         patch("secure_wiki.__main__.get_embed_client", side_effect=Exception("no embed")):
        _run_pipeline(
            str(source_file),
            TrustLevel.TRUSTED,
            None,
            "full",
            store,
            emb_store,
            existing,
            existing_embeddings,
            sim_cfg,
        )

    assert len(enrich_calls) == 1
    assert enrich_calls[0][0].text == claim.text


def _store(tmp_path):
    store = WikiStore(root=tmp_path / "wiki")
    store.init()
    return store, EmbeddingStore(store)


def test_resolve_findings_non_interactive_quarantines_all(tmp_path):
    """Default (no TTY) path writes every finding to quarantine, unaddressed."""
    store, emb = _store(tmp_path)
    pending = [
        GateOutcome(GateDecision.QUARANTINE, _make_claim("dup"), "consistency: duplicate"),
        GateOutcome(GateDecision.ESCALATE, _make_claim("conflict"), "consistency: similar"),
    ]
    c, q, e = _resolve_findings(
        pending, interactive=False, store=store, emb_store=emb,
        new_embeddings={}, existing=[], existing_embeddings={}, committed_ids=set(),
    )
    assert (c, q, e) == (0, 1, 1)
    quarantined = store.load_quarantined()
    assert len(quarantined) == 2
    assert all(qc.resolution is None for qc in quarantined)  # unaddressed


def test_resolve_findings_interactive_commit_and_keep(tmp_path, monkeypatch):
    """Under --review: commit overrides the gate; keep stays quarantined, marked."""
    store, emb = _store(tmp_path)
    dup = _make_claim("commit this duplicate")
    conflict = _make_claim("keep this conflict")
    pending = [
        GateOutcome(GateDecision.QUARANTINE, dup, "consistency: duplicate of abcd"),
        GateOutcome(GateDecision.ESCALATE, conflict, "consistency: similar to ef01"),
    ]

    monkeypatch.setattr("secure_wiki.__main__.is_interactive_capable", lambda: True)
    monkeypatch.setattr(
        "secure_wiki.__main__.review_findings_interactive",
        lambda findings, **kw: ([Decision("commit"), Decision("keep")], True),
    )

    committed_ids: set = set()
    c, q, e = _resolve_findings(
        pending, interactive=True, store=store, emb_store=emb,
        new_embeddings={}, existing=[], existing_embeddings={}, committed_ids=committed_ids,
    )
    assert (c, q, e) == (1, 0, 1)

    active = store.load_claims()
    assert [a.text for a in active] == ["commit this duplicate"]
    assert active[0].resolution == "committed"
    assert dup.claim_id in committed_ids

    quarantined = store.load_quarantined()
    assert [qc.text for qc in quarantined] == ["keep this conflict"]
    assert quarantined[0].resolution == "kept"


def test_resolve_findings_interactive_edit_and_drop(tmp_path, monkeypatch):
    """Edit rewrites text before committing; drop discards without storing."""
    store, emb = _store(tmp_path)
    to_edit = _make_claim("typo in claim")
    to_drop = _make_claim("hallucinated claim")
    pending = [
        GateOutcome(GateDecision.ESCALATE, to_edit, "consistency: similar"),
        GateOutcome(GateDecision.QUARANTINE, to_drop, "consistency: duplicate"),
    ]

    monkeypatch.setattr("secure_wiki.__main__.is_interactive_capable", lambda: True)
    monkeypatch.setattr("secure_wiki.__main__._safe_embed", lambda _t: None)
    monkeypatch.setattr(
        "secure_wiki.__main__.review_findings_interactive",
        lambda findings, **kw: (
            [Decision("edit", new_text="corrected claim", note="fixed typo"),
             Decision("drop", note="not real")],
            True,
        ),
    )

    c, q, e = _resolve_findings(
        pending, interactive=True, store=store, emb_store=emb,
        new_embeddings={}, existing=[], existing_embeddings={}, committed_ids=set(),
    )
    assert (c, q, e) == (1, 0, 0)

    active = store.load_claims()
    assert [a.text for a in active] == ["corrected claim"]
    assert store.load_quarantined() == []

    # Both the edit and the drop are preserved as corrections for source reconciliation.
    corrections = store.load_corrections()
    assert sorted(cr.action for cr in corrections) == ["drop", "edit"]
