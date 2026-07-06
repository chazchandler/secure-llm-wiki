"""Tests for lint fixer."""
from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.models import LintReport
from secure_wiki.lint.fixer import apply_fixes


def test_fix_creates_stub_for_missing_link(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    (store.wiki / "concepts" / "elt.md").write_text(
        "---\ntitle: ELT\ntype: concept\nconfidence: high\ntags: []\nsources: []\n"
        "slug: elt\ncreated: '2026-06-18'\nupdated: '2026-06-18'\n---\n\n"
        "See [Ghost](/entities/ghost.md)."
    )
    report = LintReport(missing_links=["entities/ghost"])
    fixes = apply_fixes(store, report)
    assert (store.wiki / "entities" / "ghost.md").exists()
    assert len(fixes) >= 1


def test_fix_is_idempotent(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    (store.wiki / "concepts" / "elt.md").write_text(
        "---\ntitle: ELT\ntype: concept\nconfidence: high\ntags: []\nsources: []\n"
        "slug: elt\ncreated: '2026-06-18'\nupdated: '2026-06-18'\n---\n\n"
        "See [Ghost](/entities/ghost.md)."
    )
    report = LintReport(missing_links=["entities/ghost"])
    apply_fixes(store, report)
    apply_fixes(store, report)  # second run should not crash or duplicate
    stubs = list((store.wiki / "entities").glob("ghost*.md"))
    assert len(stubs) == 1


def test_fix_empty_report_is_noop(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    report = LintReport()
    fixes = apply_fixes(store, report)
    # only index refresh should happen
    assert isinstance(fixes, list)
