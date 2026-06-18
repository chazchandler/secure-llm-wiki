"""Tests for WikiPage and LintReport dataclasses."""
from secure_wiki.models import WikiPage, LintReport


def test_wiki_page_roundtrip(tmp_path):
    page = WikiPage(
        slug="elt-orchestration",
        page_type="concept",
        title="ELT Orchestration",
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


def test_lint_report_defaults():
    r = LintReport()
    assert r.orphans == []
    assert r.missing_links == []
    assert r.contradictions == []
    assert r.incomplete_pages == []
    assert r.low_confidence_claims == []
    assert r.auto_fixed == []


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
    assert "type: entity" in md
    assert "## Overview" in md
