"""Tests for enrich_wiki integration in _run_pipeline."""
from unittest.mock import patch

import pytest

from secure_wiki.__main__ import _run_pipeline
from secure_wiki.llm_client import UsageInfo
from secure_wiki.models import Claim, ClaimStatus, SourceRef, TrustLevel
from secure_wiki.review.adversarial import ReviewResult
from secure_wiki.gate.write_gate import GateDecision
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
