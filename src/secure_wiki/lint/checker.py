"""Five lint checks for the wiki knowledge graph."""
from __future__ import annotations

import re
from pathlib import Path

from ..models import WikiPage
from ..store.wiki_store import WikiStore

# OKF cross-links are standard markdown: [Title](/subdir/slug.md).
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _all_wiki_pages(store: WikiStore) -> list[Path]:
    pages = []
    for subdir in ("concepts", "entities", "summaries"):
        d = store.wiki / subdir
        if d.exists():
            pages.extend(sorted(d.glob("*.md")))
    return pages


def _extract_links(text: str) -> list[str]:
    """Extract intra-bundle link targets as ``subdir/slug``, skipping frontmatter.

    Only bundle-relative markdown links to ``.md`` files are returned; external
    URLs and anchors are ignored (OKF §5).
    """
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]

    targets: list[str] = []
    for raw in _MD_LINK_RE.findall(text):
        target = raw.strip()
        if "://" in target or target.startswith(("#", "mailto:")):
            continue
        if not target.endswith(".md"):
            continue
        parts = [p for p in target.split("/") if p not in ("", ".", "..")]
        if not parts:
            continue
        parts[-1] = parts[-1][:-3]  # strip .md
        targets.append("/".join(parts))
    return targets


def check_orphans(store: WikiStore) -> list[str]:
    """Return slugs of pages that have no inbound links from any other page."""
    pages = _all_wiki_pages(store)
    if not pages:
        return []

    # Collect all link targets (normalized to "subdir/slug")
    all_targets: set[str] = set()
    for page_path in pages:
        try:
            text = page_path.read_text(encoding="utf-8")
            all_targets.update(_extract_links(text))
        except Exception:
            pass

    orphans = []
    for page_path in pages:
        subdir = page_path.parent.name
        slug = page_path.stem
        # Check if this page is referenced
        ref_with_subdir = f"{subdir}/{slug}"
        if ref_with_subdir not in all_targets:
            orphans.append(slug)

    return orphans


def check_missing_links(store: WikiStore) -> list[str]:
    """Return link targets ("subdir/slug") whose target page does not exist."""
    pages = _all_wiki_pages(store)
    missing = []
    seen: set[str] = set()

    for page_path in pages:
        try:
            text = page_path.read_text(encoding="utf-8")
            for target in _extract_links(text):
                if target in seen:
                    continue
                seen.add(target)
                # Resolve target: "entities/temporal" → wiki/entities/temporal.md
                parts = target.split("/")
                if len(parts) == 2:
                    candidate = store.wiki / parts[0] / f"{parts[1]}.md"
                else:
                    candidate = store.wiki / f"{target}.md"
                if not candidate.exists():
                    missing.append(target)
        except Exception:
            pass

    return missing


def check_contradictions(
    store: WikiStore,
    emb_store=None,
    conflict_threshold: float = 0.85,
    max_pairs: int = 20,
) -> list[tuple[str, str]]:
    """Find claim pairs that may contradict each other.

    Uses embedding similarity to find candidate pairs, then calls the LLM.
    Falls back to empty list if embeddings are unavailable.
    """
    try:
        claims = store.load_claims()
        if len(claims) < 2:
            return []

        if emb_store is None:
            return []

        embeddings = emb_store.load_all()
        if not embeddings:
            return []

        # Find high-similarity pairs (potential contradictions)
        import itertools

        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            return dot / (na * nb) if na and nb else 0.0

        candidate_pairs = []
        for c1, c2 in itertools.combinations(claims, 2):
            e1 = embeddings.get(c1.claim_id)
            e2 = embeddings.get(c2.claim_id)
            if e1 and e2:
                sim = cosine(e1, e2)
                if sim >= conflict_threshold:
                    candidate_pairs.append((sim, c1, c2))

        candidate_pairs.sort(key=lambda x: x[0], reverse=True)
        top_pairs = [(c1, c2) for _, c1, c2 in candidate_pairs[:max_pairs]]

        if not top_pairs:
            return []

        import json
        from ..llm_client import extract_json_object, get_review_client, strip_fences
        from ..prompts import build_lint_prompt

        pair_input = [(c1.claim_id, c1.text) for c1, _ in top_pairs]
        system, user = build_lint_prompt(pair_input)
        client = get_review_client()
        result = client.complete(system, user)
        data = json.loads(extract_json_object(strip_fences(result.text)))
        confirmed = []
        for item in data.get("contradictions", []):
            confirmed.append((item.get("claim_a", ""), item.get("claim_b", "")))
        return confirmed

    except Exception:
        return []


def check_incomplete_pages(store: WikiStore) -> list[str]:
    """Return slugs of pages missing required frontmatter fields or body sections."""
    pages = _all_wiki_pages(store)
    incomplete = []

    required_sections = {
        "concept": "## Definition",
        "entity": "## Overview",
        "summary": "## Summary",
    }

    for page_path in pages:
        try:
            page = WikiPage.from_file(page_path)
            is_incomplete = False

            # Check required frontmatter
            if not page.title or not page.page_type or not page.confidence:
                is_incomplete = True

            # Check required body section
            required = required_sections.get(page.page_type, "")
            if required and required not in page.body:
                is_incomplete = True

            if is_incomplete:
                incomplete.append(page.slug)
        except Exception:
            incomplete.append(page_path.stem)

    return incomplete


def check_low_confidence_claims(store: WikiStore) -> list[str]:
    """Return claim IDs that are untrusted or have review notes."""
    try:
        claims = store.load_claims(status=None)
        return [
            c.claim_id
            for c in claims
            if c.trust_level.value == "untrusted" or c.review_notes is not None
        ]
    except Exception:
        return []
