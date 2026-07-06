"""One-shot migration of a pre-OKF wiki bundle to Open Knowledge Format v0.1.

Older bundles used Obsidian-style ``[[wikilinks]]``, timestamped log lines, and
a ``[[slug]]`` index. This upgrades a bundle in place so it conforms to
docs/okf-spec.md:

* concept/entity/summary documents get OKF frontmatter (``timestamp`` added, a
  ``description`` derived when missing) and their bodies' ``[[wikilinks]]`` are
  rewritten as bundle-relative markdown links ``[Title](/subdir/slug.md)``;
* ``index.md`` is regenerated as an OKF progressive-disclosure listing;
* ``log.md`` is regrouped under ``## YYYY-MM-DD`` headings, newest first;
* ``lint-report.md`` (a non-reserved artifact) gets minimal frontmatter so the
  whole tree is conformant.

Each logical step is committed to the wiki repo so the migration is auditable
and revertable. The command is idempotent — a bundle already in OKF form is
left unchanged (no ``[[wikilinks]]`` remain to convert).

Usage:
    python -m secure_wiki.store.migrate_okf [WIKI_ROOT]
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ..models import WikiPage
from ..graph.page_writer import _normalize_slug, _one_liner
from .wiki_store import _GIT_ENV, WikiStore

_SUBDIRS = ("concepts", "entities", "summaries")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]")


def _build_maps(store: WikiStore) -> tuple[dict[str, str], dict[str, str]]:
    """Return (title_by_key, path_by_slug).

    title_by_key maps ``"subdir/slug"`` → frontmatter title.
    path_by_slug maps a bare ``slug`` → ``"subdir/slug"`` for the first match,
    used to resolve title-only ``[[Bare Name]]`` links.
    """
    title_by_key: dict[str, str] = {}
    path_by_slug: dict[str, str] = {}
    for subdir in _SUBDIRS:
        d = store.wiki / subdir
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            slug = p.stem
            key = f"{subdir}/{slug}"
            try:
                title_by_key[key] = WikiPage.from_file(p).title or slug
            except Exception:
                title_by_key[key] = slug
            path_by_slug.setdefault(slug, key)
    return title_by_key, path_by_slug


def _convert_links(body: str, title_by_key: dict[str, str], path_by_slug: dict[str, str]) -> str:
    """Rewrite ``[[wikilinks]]`` in body text as OKF markdown links."""
    body = body.replace("[[[", "[[")  # repair malformed triple-brackets

    def repl(m: re.Match) -> str:
        target = m.group(1).strip()
        title = (m.group(2) or "").strip() or None
        if "/" in target:
            key = target
            text = title or title_by_key.get(key) or target.split("/")[-1]
            return f"[{text}]({'/' + key}.md)"
        # Bare "[[Some Name]]" — resolve via slug, else drop to plain text.
        slug = _normalize_slug(target)
        key = path_by_slug.get(slug)
        text = title or target
        if key:
            return f"[{text}]({'/' + key}.md)"
        return text

    return _WIKILINK_RE.sub(repl, body)


def _description_from_body(body: str) -> str:
    """Derive an OKF one-line description from the first prose paragraph."""
    para: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            if para:
                break
            continue
        if s.startswith(("#", "-", "*", "|", ">")):
            if para:
                break
            continue
        para.append(s)
    text = " ".join(para)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # markdown links → text
    text = re.sub(r"[*`]", "", text)                       # emphasis / code ticks
    return _one_liner(text)


def _migrate_pages(store: WikiStore) -> list[Path]:
    title_by_key, path_by_slug = _build_maps(store)
    changed: list[Path] = []
    for subdir in _SUBDIRS:
        d = store.wiki / subdir
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                page = WikiPage.from_file(p)
            except Exception:
                continue
            page.body = _convert_links(page.body, title_by_key, path_by_slug)
            if not page.description:
                page.description = _description_from_body(page.body)
            new_text = page.to_markdown()
            if new_text != p.read_text(encoding="utf-8"):
                p.write_text(new_text, encoding="utf-8")
                changed.append(p)
    return changed


def _migrate_log(store: WikiStore) -> Path | None:
    """Regroup timestamped log lines under ISO date headings, newest first."""
    log = store.wiki / "log.md"
    if not log.exists():
        return None
    original = log.read_text(encoding="utf-8")
    if original.lstrip().startswith("# ") and "\n## " in original:
        return None  # already OKF date-grouped

    header = "# Ingest Log"
    line_re = re.compile(r"^(\d{4}-\d{2}-\d{2})T[\d:]+Z\s+(.*)$")
    by_date: dict[str, list[str]] = {}
    order: list[str] = []
    for raw in original.splitlines():
        m = line_re.match(raw.strip())
        if not m:
            if raw.startswith("# "):
                header = raw
            continue
        date, entry = m.group(1), m.group(2).strip()
        if date not in by_date:
            by_date[date] = []
            order.append(date)

        by_date[date].append(entry)

    if not order:
        return None

    lines = [header, ""]
    for date in sorted(set(order), reverse=True):
        lines.append(f"## {date}")
        for entry in reversed(by_date[date]):  # newest first within a day
            lines.append(f"* **Update**: {entry}")
        lines.append("")
    log.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return log


def _migrate_lint_report(store: WikiStore) -> Path | None:
    """Give the non-reserved lint-report.md minimal OKF frontmatter."""
    report = store.wiki / "lint-report.md"
    if not report.exists():
        return None
    text = report.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        return None  # already has frontmatter
    fm = (
        "---\n"
        "type: Lint Report\n"
        "title: Lint Report\n"
        "description: Latest wiki lint findings.\n"
        "---\n\n"
    )
    report.write_text(fm + text, encoding="utf-8")
    return report


def _commit(store: WikiStore, message: str) -> bool:
    """Stage everything and commit to the wiki repo. Returns True if committed."""
    subprocess.run(["git", "add", "-A"], cwd=store.root, check=True, env=_GIT_ENV)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=store.root, env=_GIT_ENV
    )
    if staged.returncode == 0:
        return False
    subprocess.run(
        ["git", "commit", "-m", message], cwd=store.root, check=True,
        capture_output=True, env=_GIT_ENV,
    )
    return True


def migrate(root: Path | None = None, commit: bool = True) -> dict[str, int]:
    """Migrate the bundle at ``root`` to OKF, committing each logical step.

    Returns a summary of how many items each step changed.
    """
    store = WikiStore(root=root)
    if not store.wiki.exists():
        raise FileNotFoundError(f"no wiki bundle at {store.wiki}")

    summary = {"pages": 0, "index": 0, "log": 0, "lint_report": 0}

    pages = _migrate_pages(store)
    summary["pages"] = len(pages)
    if commit and pages:
        _commit(store, f"OKF: migrate {len(pages)} concept/entity/summary document(s)")

    store.update_index()
    summary["index"] = 1
    if commit:
        _commit(store, "OKF: regenerate index.md as a progressive-disclosure listing")

    if _migrate_log(store) is not None:
        summary["log"] = 1
        if commit:
            _commit(store, "OKF: regroup log.md under ISO date headings")

    if _migrate_lint_report(store) is not None:
        summary["lint_report"] = 1
        if commit:
            _commit(store, "OKF: add frontmatter to lint-report.md")

    return summary


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]) if argv else None
    summary = migrate(root)
    print(
        "OKF migration complete: "
        f"{summary['pages']} document(s), index={summary['index']}, "
        f"log={summary['log']}, lint_report={summary['lint_report']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
