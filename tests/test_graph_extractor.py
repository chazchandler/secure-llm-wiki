"""Tests for graph extractor."""
from unittest.mock import MagicMock

from secure_wiki.graph.extractor import extract_graph, EnrichmentResult
from secure_wiki.models import SourceRef

FAKE_RESPONSE = (
    '{"summary": "Test summary.", '
    '"concepts": [{"slug": "elt", "title": "ELT", "definition": "Extract Load Transform", "tags": ["data"]}], '
    '"entities": [{"slug": "temporal", "title": "Temporal", "overview": "Workflow engine", "tags": ["orchestration"]}]}'
)


def _ref():
    return SourceRef(id="test", uri="file:///test.md", section="full", content_hash="abc")


def test_extract_graph_returns_result(monkeypatch):
    mock_client = MagicMock()
    mock_client.complete.return_value = MagicMock(text=FAKE_RESPONSE, usage=MagicMock(input_tokens=0, output_tokens=0))
    monkeypatch.setattr("secure_wiki.graph.extractor.get_review_client", lambda: mock_client)
    result = extract_graph(claims=[], source_ref=_ref())
    assert isinstance(result, EnrichmentResult)
    assert result.summary == "Test summary."
    assert result.concepts[0]["slug"] == "elt"
    assert result.entities[0]["slug"] == "temporal"


def test_extract_graph_malformed_json_returns_empty(monkeypatch):
    mock_client = MagicMock()
    mock_client.complete.return_value = MagicMock(text="not json at all", usage=MagicMock(input_tokens=0, output_tokens=0))
    monkeypatch.setattr("secure_wiki.graph.extractor.get_review_client", lambda: mock_client)
    ref = SourceRef(id="x", uri="file:///x.md", section="full", content_hash="x")
    result = extract_graph(claims=[], source_ref=ref)
    assert result.concepts == [] and result.entities == []
