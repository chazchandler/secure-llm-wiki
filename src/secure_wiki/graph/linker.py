"""Add bidirectional [[wikilinks]] to wiki pages."""
from __future__ import annotations

import re
from pathlib import Path

from ..store.wiki_store import WikiStore


def _split_zones(text: str) -> tuple[str, list[tuple[str, bool]]]:
    """Split page text into (frontmatter, [(zone_text, is_body)]) segments.

    Returns the frontmatter string and a list of (text, is_linkable) segments
    where is_linkable=True for body text outside code blocks.
    """
    # Split out YAML frontmatter
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            frontmatter = text[: end + 5]  # includes closing ---\n
            body = text[end + 5 :]
        else:
            frontmatter = ""
            body = text
    else:
        frontmatter = ""
        body = text

    # Split body into code-block and non-code-block zones
    segments: list[tuple[str, bool]] = []
    code_fence_re = re.compile(r"(```[^\n]*\n.*?```)", re.DOTALL)
    last = 0
    for m in code_fence_re.finditer(body):
        if m.start() > last:
            segments.append((body[last : m.start()], True))
        segments.append((m.group(), False))
        last = m.end()
    if last < len(body):
        segments.append((body[last:], True))

    return frontmatter, segments


def _slug_to_link(slug: str, subdir: str) -> str:
    return f"[[{subdir}/{slug}]]"


def _already_linked(text: str, subdir: str, slug: str) -> bool:
    return f"[[{subdir}/{slug}" in text


def _link_text_zone(zone: str, slug: str, title: str, subdir: str) -> str:
    """Insert [[subdir/slug|Title]] around unlinked bare mentions of title in zone."""
    if _already_linked(zone, subdir, slug):
        return zone
    pattern = re.compile(rf"\b{re.escape(title)}\b", re.IGNORECASE)
    replacement = f"[[{subdir}/{slug}|{title}]]"

    def replace_if_not_linked(m: re.Match) -> str:
        start = m.start()
        # Check if already inside a [[...]] span by scanning backwards
        before = zone[:start]
        open_count = before.count("[[") - before.count("]]")
        if open_count > 0:
            return m.group()
        return replacement

    return pattern.sub(replace_if_not_linked, zone)


def _collect_slug_map(store: WikiStore) -> dict[str, tuple[str, str]]:
    """Build {slug: (title, subdir)} from all wiki pages."""
    slug_map: dict[str, tuple[str, str]] = {}
    for subdir in ("concepts", "entities"):
        d = store.wiki / subdir
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            slug = p.stem
            # Try to read title from frontmatter, fall back to slug
            try:
                from ..models import WikiPage
                page = WikiPage.from_file(p)
                title = page.title or slug
            except Exception:
                title = slug
            slug_map[slug] = (title, subdir)
    return slug_map


def add_cross_links(store: WikiStore, new_slugs: list[str]) -> list[Path]:
    """Scan all wiki pages and insert [[wikilinks]] for new_slugs where missing.

    Returns list of modified paths.
    """
    slug_map = _collect_slug_map(store)
    # Only link slugs that are in new_slugs AND exist in slug_map
    to_link = {s: slug_map[s] for s in new_slugs if s in slug_map}

    modified: list[Path] = []
    all_pages: list[Path] = []
    for subdir in ("concepts", "entities", "summaries"):
        d = store.wiki / subdir
        if d.exists():
            all_pages.extend(d.glob("*.md"))

    for page_path in all_pages:
        original = page_path.read_text(encoding="utf-8")
        frontmatter, segments = _split_zones(original)

        new_segments = []
        changed = False
        for zone_text, is_linkable in segments:
            if not is_linkable:
                new_segments.append(zone_text)
                continue
            updated = zone_text
            for slug, (title, subdir) in to_link.items():
                # Don't self-link
                if page_path.stem == slug and page_path.parent.name == subdir:
                    continue
                updated = _link_text_zone(updated, slug, title, subdir)
            if updated != zone_text:
                changed = True
            new_segments.append(updated)

        if changed:
            new_text = frontmatter + "".join(new_segments)
            page_path.write_text(new_text, encoding="utf-8")
            modified.append(page_path)

    return modified
