"""Auto-fix mechanical lint issues in the wiki."""
from __future__ import annotations

from pathlib import Path

from ..models import LintReport, WikiPage
from ..store.wiki_store import WikiStore


def fix_missing_links(store: WikiStore, missing: list[str]) -> list[str]:
    """Create stub wiki pages for broken [[wikilinks]].

    Returns descriptions of fixes applied.
    """
    from datetime import datetime, timezone
    fixes = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for target in missing:
        parts = target.split("/")
        if len(parts) != 2:
            continue
        subdir, slug = parts
        path = store.wiki / subdir / f"{slug}.md"
        if path.exists():
            continue

        # Map subdir to page_type
        page_type = subdir.rstrip("s")  # "concepts" → "concept", "entities" → "entity"
        if subdir == "summaries":
            page_type = "summary"

        required_section = {
            "concept": "## Definition\n\n<!-- TODO: fill in definition -->",
            "entity": "## Overview\n\n<!-- TODO: fill in overview -->",
            "summary": "## Summary\n\n<!-- TODO: fill in summary -->",
        }.get(page_type, "## TODO\n\n<!-- auto-generated stub -->")

        page = WikiPage(
            slug=slug,
            page_type=page_type,
            title=slug.replace("-", " ").title(),
            tags=[],
            confidence="low",
            sources=[],
            body=f"{required_section}\n\n<!-- auto-generated stub -->",
            created=today,
            updated=today,
        )
        store.save_wiki_page(page)
        fixes.append(f"created stub page: {target}")

    return fixes


def fix_cross_links(store: WikiStore) -> list[str]:
    """Re-run add_cross_links across all known slugs."""
    from ..graph.linker import add_cross_links
    all_slugs: list[str] = []
    for subdir in ("concepts", "entities"):
        d = store.wiki / subdir
        if d.exists():
            all_slugs.extend(p.stem for p in d.glob("*.md"))
    modified = add_cross_links(store, new_slugs=all_slugs)
    return [f"added cross-links: {p.stem}" for p in modified]


def refresh_index(store: WikiStore) -> list[str]:
    """Regenerate the wiki index."""
    store.update_index()
    return ["refreshed index.md"]


def apply_fixes(store: WikiStore, report: LintReport) -> list[str]:
    """Apply all auto-fixable issues. Returns list of fix descriptions."""
    all_fixes: list[str] = []

    if report.missing_links:
        all_fixes.extend(fix_missing_links(store, report.missing_links))

    all_fixes.extend(fix_cross_links(store))
    all_fixes.extend(refresh_index(store))

    # Commit all fixes in one go
    changed: list[Path] = []
    for subdir in ("concepts", "entities", "summaries"):
        d = store.wiki / subdir
        if d.exists():
            changed.extend(d.glob("*.md"))
    index = store.wiki / "index.md"
    if index.exists():
        changed.append(index)

    if all_fixes:
        store._commit_many(changed, f"lint: auto-fixed {len(all_fixes)} issue(s)")

    return all_fixes
