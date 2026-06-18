"""Tests for graph enricher orchestration."""
import subprocess
from unittest.mock import MagicMock

from secure_wiki.store.wiki_store import WikiStore
from secure_wiki.graph.enricher import enrich_wiki
from secure_wiki.graph.extractor import EnrichmentResult
from secure_wiki.llm_client import UsageInfo
from secure_wiki.models import SourceRef


def _fake_extract(claims, source_ref):
    return EnrichmentResult(
        summary="A test summary.",
        concepts=[{"slug": "elt", "title": "ELT", "definition": "Extract Load Transform", "tags": ["data"]}],
        entities=[{"slug": "temporal", "title": "Temporal", "overview": "Workflow engine", "tags": ["workflow"]}],
        usage=UsageInfo(),
    )


def _empty_extract(claims, source_ref):
    return EnrichmentResult("", [], [], UsageInfo())


def _mock_llm():
    return MagicMock(complete=MagicMock(return_value=MagicMock(text="## Content\nTest.")))


def _ref(source_id="rfc"):
    return SourceRef(id=source_id, uri=f"file:///{source_id}.md", section="full", content_hash="abc")


def test_enrich_creates_summary_and_pages(tmp_path, monkeypatch):
    store = WikiStore(root=tmp_path)
    store.init()
    monkeypatch.setattr("secure_wiki.graph.enricher.extract_graph", _fake_extract)
    monkeypatch.setattr("secure_wiki.graph.page_writer.get_review_client", _mock_llm)
    n_concepts, n_entities = enrich_wiki([], _ref(), store)
    assert n_concepts == 1 and n_entities == 1
    assert (store.wiki / "summaries" / "rfc.md").exists()
    assert (store.wiki / "concepts" / "elt.md").exists()
    assert (store.wiki / "entities" / "temporal.md").exists()


def test_enrich_empty_claims_is_noop(tmp_path, monkeypatch):
    store = WikiStore(root=tmp_path)
    store.init()
    monkeypatch.setattr("secure_wiki.graph.enricher.extract_graph", _empty_extract)
    n_c, n_e = enrich_wiki([], _ref(), store)
    assert n_c == 0 and n_e == 0


def test_enrich_makes_one_git_commit(tmp_path, monkeypatch):
    store = WikiStore(root=tmp_path)
    store.init()
    monkeypatch.setattr("secure_wiki.graph.enricher.extract_graph", _fake_extract)
    monkeypatch.setattr("secure_wiki.graph.page_writer.get_review_client", _mock_llm)
    enrich_wiki([], _ref(), store)
    log = subprocess.check_output(["git", "log", "--oneline"], cwd=tmp_path).decode()
    enrich_commits = [line for line in log.strip().splitlines() if "enrich" in line]
    assert len(enrich_commits) == 1
