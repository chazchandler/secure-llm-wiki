"""Tests for interactive claim review and correction persistence."""
from __future__ import annotations

from secure_wiki.models import Claim, SourceRef, TrustLevel
from secure_wiki.review.interactive import review_claims_interactive
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
    assert kept[0].review_notes == "was imprecise"
    assert len(corrections) == 1
    corr = corrections[0]
    assert corr.action == "edit"
    assert corr.original_text == "original statement"
    assert corr.corrected_text == "corrected statement"
    assert corr.note == "was imprecise"
    assert corr.claim_id == claims[0].claim_id


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
