"""Tests for wiki context loading including wiki pages."""
from secure_wiki.read.hygiene import load_for_context
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.models import TrustLevel, WikiPage


def test_context_includes_wiki_section(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_DATA_PATH", str(tmp_path))
    store = WikiStore(root=tmp_path)
    store.init()
    page = WikiPage(
        slug="elt",
        page_type="concept",
        title="ELT",
        tags=[],
        confidence="high",
        sources=[],
        body="## Definition\nELT.",
        created="2026-06-18",
        updated="2026-06-18",
    )
    store.save_wiki_page(page)
    ctx = load_for_context(store=store, min_trust=TrustLevel.SEMI_TRUSTED)
    assert "ELT" in ctx.context_block or "elt" in ctx.context_block


def test_context_no_wiki_pages_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_DATA_PATH", str(tmp_path))
    store = WikiStore(root=tmp_path)
    store.init()
    ctx = load_for_context(store=store, min_trust=TrustLevel.SEMI_TRUSTED)
    assert ctx.claim_count == 0
    assert "wiki-context-" in ctx.context_block


def test_context_wiki_section_includes_body_excerpt(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_DATA_PATH", str(tmp_path))
    store = WikiStore(root=tmp_path)
    store.init()
    page = WikiPage(
        slug="temporal",
        page_type="entity",
        title="Temporal Workflow Engine",
        tags=[],
        confidence="high",
        sources=[],
        body="## Overview\nTemporal orchestrates long-running workflows reliably.",
        created="2026-06-18",
        updated="2026-06-18",
    )
    store.save_wiki_page(page)
    ctx = load_for_context(store=store)
    assert "Temporal" in ctx.context_block
