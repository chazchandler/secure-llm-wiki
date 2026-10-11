"""Tests for WikiPage and LintReport dataclasses."""
import pytest

from secure_wiki.models import Claim, Correction, LintReport, WikiPage


def test_wiki_page_roundtrip(tmp_path):
    page = WikiPage(
        slug="elt-orchestration",
        page_type="concept",
        title="ELT Orchestration",
        description="How ELT jobs are orchestrated.",
        tags=["elt", "data"],
        confidence="high",
        sources=["rfc-v2"],
        body="## Definition\nELT is ...",
        created="2026-06-18",
        updated="2026-06-18",
    )
    path = tmp_path / "elt-orchestration.md"
    path.write_text(page.to_markdown())
    loaded = WikiPage.from_file(path)
    assert loaded.slug == page.slug
    assert loaded.body == page.body
    assert loaded.title == page.title
    assert loaded.page_type == page.page_type
    assert loaded.tags == page.tags
    assert loaded.confidence == page.confidence
    assert loaded.sources == page.sources
    assert loaded.description == page.description


def test_lint_report_defaults():
    r = LintReport()
    assert r.orphans == []
    assert r.missing_links == []
    assert r.contradictions == []
    assert r.incomplete_pages == []
    assert r.low_confidence_claims == []
    assert r.auto_fixed == []


def _claim_dict(claim_id: str) -> dict:
    return {
        "text": "a claim",
        "source": {
            "id": "s", "uri": "file:///s.md", "section": "full",
            "content_hash": "abc123",
        },
        "trust_level": "trusted",
        "claim_id": claim_id,
        "ingested_at": "2026-06-18T00:00:00+00:00",
        "status": "active",
        "gates_passed": [],
    }


def _correction_dict(claim_id: str) -> dict:
    return {
        "source_id": "s",
        "source_uri": "file:///s.md",
        "section": "full",
        "content_hash": "abc123",
        "claim_id": claim_id,
        "action": "edit",
        "original_text": "orig",
        "corrected_text": "fixed",
    }


def test_claim_from_dict_rejects_traversal_claim_id():
    with pytest.raises(ValueError):
        Claim.from_dict(_claim_dict("../../etc/passwd"))


def test_correction_from_dict_rejects_traversal_claim_id():
    with pytest.raises(ValueError):
        Correction.from_dict(_correction_dict("../../etc/passwd"))


def test_claim_from_dict_accepts_canonical_uuid():
    import uuid
    valid_id = str(uuid.uuid4())
    claim = Claim.from_dict(_claim_dict(valid_id))
    assert claim.claim_id == valid_id


def test_wiki_page_to_markdown_has_frontmatter():
    page = WikiPage(
        slug="temporal",
        page_type="entity",
        title="Temporal",
        tags=["orchestration"],
        confidence="high",
        sources=["rfc"],
        body="## Overview\nTemporal is a workflow engine.",
        created="2026-06-18",
        updated="2026-06-18",
    )
    md = page.to_markdown()
    assert md.startswith("---\n")
    assert "title: Temporal" in md
    assert "type: entity" in md          # OKF required field
    assert "timestamp: '2026-06-18'" in md  # OKF last-modified field
    assert "## Overview" in md


def test_wiki_page_to_markdown_omits_bare_slug():
    """Regression test: frontmatter must not carry a bare `slug:` field.

    save_wiki_page() writes the page to wiki/<type>s/<slug>.md — the bare
    slug is only the filename stem, not the full bundle-relative identifier
    (OKF Concept ID, docs/okf-spec.md §2) that includes the type subdirectory
    (e.g. "entities/google-workspace"). A consumer that derives a page's
    canonical slug from the full path will see this bare value disagree with
    its own derivation and reject/hold the file as a slug conflict. Since
    nothing in this codebase reads `slug` back out of frontmatter for an
    purpose other than cosmetic (from_file() already falls back to
    path.stem when absent), the field is dropped entirely rather than
    corrected to the qualified form.
    """
    page = WikiPage(
        slug="google-workspace",
        page_type="entity",
        title="Google Workspace",
        tags=["identity"],
        confidence="medium",
        sources=[],
        body="## Overview\nGoogle Workspace is Google's identity platform.",
        created="2026-07-07",
        updated="2026-07-07",
    )
    md = page.to_markdown()
    import yaml as _yaml
    frontmatter = _yaml.safe_load(md.split("---\n", 2)[1])
    assert "slug" not in frontmatter
