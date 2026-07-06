"""Tests for the pre-OKF → OKF bundle migration."""
import subprocess

from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.store.migrate_okf import migrate
from secure_wiki.models import WikiPage


def _old_page(store, subdir, slug, title, body):
    """Write a page in the legacy (pre-OKF, no description/timestamp) format."""
    fm = (
        f"---\nconfidence: medium\ncreated: '2026-06-18'\nslug: {slug}\n"
        f"sources: []\ntags: []\ntitle: {title}\ntype: {subdir[:-1]}\n"
        f"updated: '2026-06-18'\n---\n\n{body}\n"
    )
    p = store.wiki / subdir / f"{slug}.md"
    p.write_text(fm, encoding="utf-8")
    return p


def _setup(tmp_path):
    store = WikiStore(root=tmp_path)
    store.init()
    _old_page(
        store, "entities", "temporal", "Temporal",
        "## Overview\n\nTemporal is a durable workflow engine used widely.",
    )
    _old_page(
        store, "concepts", "elt-orchestration", "ELT Orchestration",
        "## Definition\n\nWe orchestrate ELT with [[entities/temporal|Temporal]] and "
        "a bare [[Temporal]] plus an unknown [[Ghost Concept]].",
    )
    return store


def test_migrates_links_to_markdown(tmp_path):
    store = _setup(tmp_path)
    migrate(store.root)
    text = (store.wiki / "concepts" / "elt-orchestration.md").read_text()
    assert "[Temporal](/entities/temporal.md)" in text   # titled link
    assert "[[" not in text                               # no wikilinks remain
    assert "Ghost Concept" in text                        # unresolved → plain text


def test_adds_description_and_timestamp(tmp_path):
    store = _setup(tmp_path)
    migrate(store.root)
    page = WikiPage.from_file(store.wiki / "entities" / "temporal.md")
    assert page.description.startswith("Temporal is a durable workflow engine")
    raw = (store.wiki / "entities" / "temporal.md").read_text()
    assert "timestamp:" in raw


def test_index_and_log_are_okf(tmp_path):
    store = _setup(tmp_path)
    # Seed a legacy timestamped log line.
    (store.wiki / "log.md").write_text(
        "# Ingest Log\n\n2026-06-18T18:18:30Z  enrich rfc: 1 concept(s), 1 entity(ies)\n",
        encoding="utf-8",
    )
    migrate(store.root)
    index = (store.wiki / "index.md").read_text()
    assert 'okf_version: "0.1"' in index
    assert "](/entities/temporal.md)" in index
    log = (store.wiki / "log.md").read_text()
    assert "## 2026-06-18" in log
    assert "* **Update**: enrich rfc" in log


def test_commits_each_step(tmp_path):
    store = _setup(tmp_path)
    before = subprocess.check_output(["git", "log", "--oneline"], cwd=store.root).decode()
    migrate(store.root)
    after = subprocess.check_output(["git", "log", "--oneline"], cwd=store.root).decode()
    assert after.count("OKF:") >= 2  # at least pages + index committed
    assert len(after) > len(before)


def test_idempotent(tmp_path):
    store = _setup(tmp_path)
    migrate(store.root)
    first = (store.wiki / "concepts" / "elt-orchestration.md").read_text()
    summary = migrate(store.root)
    second = (store.wiki / "concepts" / "elt-orchestration.md").read_text()
    assert first == second
    assert summary["pages"] == 0  # nothing left to convert
