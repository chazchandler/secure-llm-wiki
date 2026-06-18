"""Orchestrates wiki graph enrichment after a successful ingest."""
from __future__ import annotations

import sys
from pathlib import Path

from ..models import Claim, SourceRef
from ..store.wiki_store import WikiStore
from .extractor import extract_graph
from .linker import add_cross_links
from .page_writer import (
    create_or_update_concept_page,
    create_or_update_entity_page,
    create_summary_page,
)


def enrich_wiki(
    claims: list[Claim],
    source_ref: SourceRef,
    store: WikiStore,
) -> tuple[int, int]:
    """Enrich wiki with summary, concept, and entity pages derived from committed claims.

    Returns (n_concepts, n_entities). Returns (0, 0) if extraction finds nothing.
    """
    result = extract_graph(claims, source_ref)

    if not result.concepts and not result.entities:
        return 0, 0

    written_paths: list[Path] = []
    concept_slugs: list[str] = []
    entity_slugs: list[str] = []

    # Write concept pages
    for c in result.concepts:
        slug = c.get("slug", "")
        if not slug:
            continue
        try:
            page = create_or_update_concept_page(
                store,
                slug=slug,
                title=c.get("title", slug),
                definition=c.get("definition", ""),
                tags=c.get("tags", []),
                relevant_claims=claims,
            )
            written_paths.append(store.wiki / "concepts" / f"{page.slug}.md")
            concept_slugs.append(page.slug)
            print(f"[enrich]   concept: {page.slug:<30} (created/updated)", file=sys.stderr)
        except Exception as exc:
            print(f"[enrich]   concept {slug} failed: {exc}", file=sys.stderr)

    # Write entity pages
    for e in result.entities:
        slug = e.get("slug", "")
        if not slug:
            continue
        try:
            page = create_or_update_entity_page(
                store,
                slug=slug,
                title=e.get("title", slug),
                overview=e.get("overview", ""),
                tags=e.get("tags", []),
                relevant_claims=claims,
            )
            written_paths.append(store.wiki / "entities" / f"{page.slug}.md")
            entity_slugs.append(page.slug)
            print(f"[enrich]   entity:  {page.slug:<30} (created/updated)", file=sys.stderr)
        except Exception as exc:
            print(f"[enrich]   entity {slug} failed: {exc}", file=sys.stderr)

    # Write summary page
    if result.summary:
        try:
            summary_page = create_summary_page(
                store,
                source_id=source_ref.id,
                title=source_ref.id,
                summary_text=result.summary,
                concept_slugs=concept_slugs,
                entity_slugs=entity_slugs,
            )
            written_paths.append(store.wiki / "summaries" / f"{summary_page.slug}.md")
        except Exception as exc:
            print(f"[enrich]   summary failed: {exc}", file=sys.stderr)

    # Add cross-links
    new_slugs = concept_slugs + entity_slugs
    try:
        modified = add_cross_links(store, new_slugs=new_slugs)
        written_paths.extend(modified)
    except Exception as exc:
        print(f"[enrich]   cross-link failed: {exc}", file=sys.stderr)

    # Update index and log
    try:
        index = store.update_index()
        written_paths.append(index)
        log = store.append_log(
            f"enrich {source_ref.id}: {len(concept_slugs)} concept(s), {len(entity_slugs)} entity(ies)"
        )
        written_paths.append(log)
    except Exception as exc:
        print(f"[enrich]   index/log update failed: {exc}", file=sys.stderr)

    # Single commit for all wiki changes
    unique_paths = list(dict.fromkeys(p for p in written_paths if p.exists()))
    store._commit_many(unique_paths, f"enrich {source_ref.id}: wiki pages updated")

    return len(concept_slugs), len(entity_slugs)
