"""Tests for lint checker functions."""
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.lint.checker import (
    check_orphans,
    check_missing_links,
    check_incomplete_pages,
    check_low_confidence_claims,
)
from secure_wiki.models import WikiPage


def _make_page(store, page_type, slug, body="## Content\nText.", has_required_frontmatter=True):
    page = WikiPage(
        slug=slug,
        page_type=page_type,
        title=slug.title(),
        tags=[],
        confidence="high" if has_required_frontmatter else "",
        sources=[],
        body=body,
        created="2026-06-18",
        updated="2026-06-18",
    )
    return store.save_wiki_page(page)


def test_orphan_detected_no_inbound_links(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    _make_page(store, "concept", "elt")
    _make_page(store, "entity", "aws")
    orphans = check_orphans(store)
    assert "aws" in orphans


def test_orphan_not_detected_when_linked(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    _make_page(store, "concept", "elt", body="## Content\nSee [AWS](/entities/aws.md).")
    _make_page(store, "entity", "aws")
    orphans = check_orphans(store)
    assert "aws" not in orphans


def test_missing_link_detected(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    _make_page(store, "concept", "elt", body="## Content\nSee [Nonexistent](/entities/nonexistent.md).")
    missing = check_missing_links(store)
    assert "entities/nonexistent" in missing


def test_missing_link_not_reported_when_exists(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    _make_page(store, "entity", "aws")
    _make_page(store, "concept", "elt", body="## Content\nSee [AWS](/entities/aws.md).")
    missing = check_missing_links(store)
    assert "entities/aws" not in missing


def test_incomplete_page_missing_confidence(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    _make_page(store, "concept", "elt", has_required_frontmatter=False)
    incomplete = check_incomplete_pages(store)
    assert "elt" in incomplete


def test_complete_page_not_flagged(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    _make_page(store, "concept", "elt", body="## Definition\nELT is a data pattern.")
    incomplete = check_incomplete_pages(store)
    assert "elt" not in incomplete


def test_low_confidence_empty_store(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    result = check_low_confidence_claims(store)
    assert result == []
