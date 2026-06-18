"""Tests for graph page_writer."""
from unittest.mock import MagicMock

from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.graph.page_writer import create_or_update_concept_page, create_summary_page


def _mock_client(body_text: str):
    return MagicMock(complete=MagicMock(return_value=MagicMock(text=body_text)))


def test_create_concept_page_writes_file(tmp_path, monkeypatch):
    store = WikiStore(root=tmp_path)
    store.init()
    monkeypatch.setattr(
        "secure_wiki.graph.page_writer.get_review_client",
        lambda: _mock_client("## Definition\nELT moves data."),
    )
    page = create_or_update_concept_page(
        store, slug="elt", title="ELT",
        definition="Extract Load Transform", tags=["data"], relevant_claims=[],
    )
    assert (store.wiki / "concepts" / "elt.md").exists()
    assert page.slug == "elt"


def test_update_concept_page_preserves_existing_content(tmp_path, monkeypatch):
    store = WikiStore(root=tmp_path)
    store.init()
    existing = store.wiki / "concepts" / "elt.md"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(
        "---\ntitle: ELT\ntype: concept\nconfidence: high\ntags: []\nsources: []\n"
        "slug: elt\ncreated: '2026-06-18'\nupdated: '2026-06-18'\n---\n\n"
        "## Definition\nOriginal content.\n"
    )
    monkeypatch.setattr(
        "secure_wiki.graph.page_writer.get_review_client",
        lambda: _mock_client("## Definition\nOriginal content.\n\n## New Section\nAdded."),
    )
    create_or_update_concept_page(
        store, slug="elt", title="ELT", definition="ELT", tags=[], relevant_claims=[],
    )
    text = existing.read_text()
    assert "Original content." in text


def test_create_summary_page_links_concepts(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    create_summary_page(
        store, source_id="rfc", title="RFC Data Architecture",
        summary_text="This RFC covers ELT and Temporal.",
        concept_slugs=["elt"], entity_slugs=["temporal"],
    )
    text = (store.wiki / "summaries" / "rfc.md").read_text()
    assert "[[concepts/elt" in text or "[[entities/temporal" in text
