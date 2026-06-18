"""Tests for wiki-graph additions to WikiStore."""
import subprocess

from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.models import WikiPage


def _make_page(slug="temporal", page_type="entity", title="Temporal"):
    return WikiPage(
        slug=slug,
        page_type=page_type,
        title=title,
        tags=["orchestration"],
        confidence="high",
        sources=["rfc"],
        body="## Overview\nTemporal is ...",
        created="2026-06-18",
        updated="2026-06-18",
    )


def test_save_wiki_page_writes_file(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    page = _make_page()
    path = store.save_wiki_page(page)
    assert path.exists()
    assert "Temporal" in path.read_text()


def test_save_wiki_page_path_is_under_wiki(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    path = store.save_wiki_page(_make_page())
    assert str(store.wiki) in str(path)


def test_commit_many_makes_one_commit(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    p1 = store.wiki / "concepts" / "elt.md"
    p1.write_text("# ELT")
    p2 = store.wiki / "entities" / "aws.md"
    p2.write_text("# AWS")
    store._commit_many([p1, p2], "test bulk commit")
    log = subprocess.check_output(["git", "log", "--oneline"], cwd=tmp_path).decode()
    assert log.count("\n") == 2  # init commit + our commit


def test_commit_many_skips_when_nothing_staged(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    log_before = subprocess.check_output(["git", "log", "--oneline"], cwd=tmp_path).decode()
    store._commit_many([], "should not commit")
    log_after = subprocess.check_output(["git", "log", "--oneline"], cwd=tmp_path).decode()
    assert log_before == log_after


def test_append_log_grows(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    store.append_log("first entry")
    store.append_log("second entry")
    text = (store.wiki / "log.md").read_text()
    assert "first entry" in text and "second entry" in text


def test_update_index_lists_all_pages(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    (store.wiki / "concepts" / "elt.md").write_text("# ELT")
    (store.wiki / "entities" / "aws.md").write_text("# AWS")
    store.update_index()
    index = (store.wiki / "index.md").read_text()
    assert "elt" in index and "aws" in index
