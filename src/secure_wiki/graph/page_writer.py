"""Wiki page creator/updater for graph enrichment."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..llm_client import get_review_client
from ..models import Claim, WikiPage
from ..store.wiki_store import WikiStore

_TODAY = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _normalize_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", text.lower().replace(" ", "-"))


def _one_liner(text: str, limit: int = 160) -> str:
    """Condense text into a single-sentence OKF `description`."""
    flat = " ".join(text.strip().split())
    sentence = re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0] if flat else ""
    summary = sentence or flat
    return summary[: limit - 1].rstrip() + "…" if len(summary) > limit else summary


def _merge_body_via_llm(existing_body: str, new_info: str) -> str:
    """Ask the review LLM to merge new info into existing body, preserving all content."""
    client = get_review_client()
    system = (
        "You are a wiki page editor. Merge new information into the existing page body. "
        "Preserve ALL existing content. Only append new sections or claims. "
        "Return ONLY the merged markdown body — no frontmatter, no preamble."
    )
    user = (
        f"Existing body:\n{existing_body}\n\n"
        f"New information to merge:\n{new_info}\n\n"
        "Return the merged markdown body."
    )
    result = client.complete(system, user)
    return result.text.strip()


def create_or_update_concept_page(
    store: WikiStore,
    slug: str,
    title: str,
    definition: str,
    tags: list[str],
    relevant_claims: list[Claim],
) -> WikiPage:
    slug = _normalize_slug(slug) or _normalize_slug(title)
    path = store.wiki / "concepts" / f"{slug}.md"
    today = _TODAY()

    if path.exists():
        existing = WikiPage.from_file(path)
        new_info = f"## Definition\n{definition}"
        merged_body = _merge_body_via_llm(existing.body, new_info)
        page = WikiPage(
            slug=slug,
            page_type="concept",
            title=existing.title or title,
            description=existing.description or _one_liner(definition),
            tags=list(set(existing.tags + tags)),
            confidence=existing.confidence or "medium",
            sources=existing.sources,
            body=merged_body,
            created=existing.created or today,
            updated=today,
        )
    else:
        client = get_review_client()
        system = (
            "You are a wiki page writer. Write a concise concept page body in markdown. "
            "Start with '## Definition'. Return ONLY the markdown body — no frontmatter."
        )
        user = f"Concept: {title}\nDefinition: {definition}\nWrite the page body."
        result = client.complete(system, user)
        body = result.text.strip()
        page = WikiPage(
            slug=slug,
            page_type="concept",
            title=title,
            description=_one_liner(definition),
            tags=tags,
            confidence="medium",
            sources=[],
            body=body,
            created=today,
            updated=today,
        )

    store.save_wiki_page(page)
    return page


def create_or_update_entity_page(
    store: WikiStore,
    slug: str,
    title: str,
    overview: str,
    tags: list[str],
    relevant_claims: list[Claim],
) -> WikiPage:
    slug = _normalize_slug(slug) or _normalize_slug(title)
    path = store.wiki / "entities" / f"{slug}.md"
    today = _TODAY()

    if path.exists():
        existing = WikiPage.from_file(path)
        new_info = f"## Overview\n{overview}"
        merged_body = _merge_body_via_llm(existing.body, new_info)
        page = WikiPage(
            slug=slug,
            page_type="entity",
            title=existing.title or title,
            description=existing.description or _one_liner(overview),
            tags=list(set(existing.tags + tags)),
            confidence=existing.confidence or "medium",
            sources=existing.sources,
            body=merged_body,
            created=existing.created or today,
            updated=today,
        )
    else:
        client = get_review_client()
        system = (
            "You are a wiki page writer. Write a concise entity page body in markdown. "
            "Start with '## Overview'. Return ONLY the markdown body — no frontmatter."
        )
        user = f"Entity: {title}\nOverview: {overview}\nWrite the page body."
        result = client.complete(system, user)
        body = result.text.strip()
        page = WikiPage(
            slug=slug,
            page_type="entity",
            title=title,
            description=_one_liner(overview),
            tags=tags,
            confidence="medium",
            sources=[],
            body=body,
            created=today,
            updated=today,
        )

    store.save_wiki_page(page)
    return page


def create_summary_page(
    store: WikiStore,
    source_id: str,
    title: str,
    summary_text: str,
    concept_slugs: list[str],
    entity_slugs: list[str],
) -> WikiPage:
    today = _TODAY()
    concept_links = "\n".join(f"- [{s}](/concepts/{s}.md)" for s in concept_slugs)
    entity_links = "\n".join(f"- [{s}](/entities/{s}.md)" for s in entity_slugs)

    body_parts = [f"## Summary\n\n{summary_text}"]
    if concept_links:
        body_parts.append(f"\n## Related Concepts\n\n{concept_links}")
    if entity_links:
        body_parts.append(f"\n## Related Entities\n\n{entity_links}")

    body = "\n".join(body_parts)
    page = WikiPage(
        slug=source_id,
        page_type="summary",
        title=title,
        description=_one_liner(summary_text),
        tags=[],
        confidence="high",
        sources=[source_id],
        body=body,
        created=today,
        updated=today,
    )
    store.save_wiki_page(page)
    return page
