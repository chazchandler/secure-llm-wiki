"""Tests for graph linker."""
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.graph.linker import add_cross_links


def test_adds_wikilink_for_unlinked_mention(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    (store.wiki / "entities" / "temporal.md").write_text(
        "---\nslug: temporal\ntitle: Temporal\ntype: entity\nconfidence: high\n"
        "tags: []\nsources: []\ncreated: '2026-06-18'\nupdated: '2026-06-18'\n---\n\n"
        "# Temporal\nWorkflow engine."
    )
    page = store.wiki / "concepts" / "elt.md"
    page.write_text("# ELT\nWe use Temporal to orchestrate tasks.")
    modified = add_cross_links(store, new_slugs=["temporal"])
    assert page in modified
    assert "[[entities/temporal" in page.read_text()


def test_does_not_double_link(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    (store.wiki / "entities" / "temporal.md").write_text(
        "---\nslug: temporal\ntitle: Temporal\ntype: entity\nconfidence: high\n"
        "tags: []\nsources: []\ncreated: '2026-06-18'\nupdated: '2026-06-18'\n---\n\n# Temporal"
    )
    page = store.wiki / "concepts" / "elt.md"
    page.write_text("# ELT\nWe use [[entities/temporal|Temporal]] already.")
    add_cross_links(store, new_slugs=["temporal"])
    assert page.read_text().count("[[entities/temporal") == 1


def test_skips_frontmatter_and_code_blocks(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    (store.wiki / "entities" / "temporal.md").write_text(
        "---\nslug: temporal\ntitle: Temporal\ntype: entity\nconfidence: high\n"
        "tags: []\nsources: []\ncreated: '2026-06-18'\nupdated: '2026-06-18'\n---\n\n# Temporal"
    )
    page = store.wiki / "concepts" / "elt.md"
    page.write_text(
        "---\ntitle: Temporal ELT\n---\n\n```\ntemporal run\n```\n\nBody text."
    )
    add_cross_links(store, new_slugs=["temporal"])
    text = page.read_text()
    assert "```\ntemporal" in text  # code block unchanged
    # frontmatter must come before the first body content
    assert text.index("---") < text.index("title")
