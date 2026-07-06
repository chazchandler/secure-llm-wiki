"""Gate 5 consistency: auto-drop of near-identical duplicates and the
embedding-unavailable fallback (regression for the bogus 'contradicts N in the
same section' escalations)."""
from __future__ import annotations

from secure_wiki.gate.write_gate import GateDecision, run_write_gate
from secure_wiki.ingestion.sanitizer import SanitizeReport
from secure_wiki.models import Claim, ClaimStatus, SourceRef, TrustLevel
from secure_wiki.review.adversarial import ReviewResult

_CLEAN = SanitizeReport(cleaned_text="")
_PASS = ReviewResult(passed=True, reasons=[])


def _claim(text="a claim"):
    return Claim(
        text=text,
        source=SourceRef(id="s", uri="file:///s.md", section="full",
                         content_hash=SourceRef.compute_hash(text)),
        trust_level=TrustLevel.TRUSTED,
    )


def _gate(new_embedding, existing_embeddings, **kw):
    return run_write_gate(
        _claim(), _CLEAN, existing_claims=[],
        new_embedding=new_embedding, existing_embeddings=existing_embeddings,
        review_result=_PASS, **kw,
    )


def test_near_identical_is_auto_dropped():
    # cosine 1.0 ≥ auto_duplicate_threshold (0.97) → DISCARD, not QUARANTINE.
    out = _gate([1.0, 0.0], {"x": [1.0, 0.0]})
    assert out.decision == GateDecision.DISCARD
    assert out.related_id == "x"


def test_duplicate_band_still_quarantines_for_review():
    # cosine 0.96 → below auto (0.99 here), at/above duplicate (0.95) → QUARANTINE.
    out = _gate([0.96, 0.28], {"x": [1.0, 0.0]}, auto_duplicate_threshold=0.99)
    assert out.decision == GateDecision.QUARANTINE
    assert "duplicate" in out.detail


def test_conflict_band_escalates():
    # cosine ~0.88 → between conflict (0.85) and duplicate (0.95) → ESCALATE.
    out = _gate([0.88, 0.4747], {"x": [1.0, 0.0]})
    assert out.decision == GateDecision.ESCALATE


def test_missing_embedding_defers_not_contradicts():
    # No embedding but the store has vectors → DEFER, never the old section
    # heuristic that reported "contradicts N high-trust claim(s)".
    out = _gate(None, {"x": [1.0, 0.0]})
    assert out.decision == GateDecision.QUARANTINE
    assert "embedding unavailable" in out.detail
    assert "contradicts" not in out.detail


def test_missing_embedding_and_empty_store_commits():
    # Nothing to compare against → commit (first claims into a fresh wiki).
    out = _gate(None, {})
    assert out.decision == GateDecision.COMMIT
    assert out.claim.status == ClaimStatus.ACTIVE
