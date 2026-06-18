"""Graph enrichment extractor — calls LLM to extract concepts/entities from claims."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

from ..llm_client import UsageInfo, extract_json_object, get_review_client, strip_fences
from ..models import Claim, SourceRef
from ..prompts import build_enrich_prompt


@dataclass
class EnrichmentResult:
    summary: str
    concepts: list[dict]
    entities: list[dict]
    usage: UsageInfo = field(default_factory=UsageInfo)


def extract_graph(claims: list[Claim], source_ref: SourceRef) -> EnrichmentResult:
    """Call the review LLM to extract concepts, entities, and summary from claims."""
    client = get_review_client()
    system, user = build_enrich_prompt(claims, source_ref.id)
    result = client.complete(system, user)
    try:
        raw = strip_fences(result.text)
        data = json.loads(extract_json_object(raw))
        return EnrichmentResult(
            summary=data.get("summary", ""),
            concepts=data.get("concepts", []),
            entities=data.get("entities", []),
            usage=result.usage,
        )
    except Exception as exc:
        print(f"[enrich]   graph extraction failed ({exc}), skipping", file=sys.stderr)
        return EnrichmentResult("", [], [], UsageInfo())
