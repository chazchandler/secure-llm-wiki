"""Wiki store — persists claims as Markdown + YAML frontmatter under a
dedicated git repository separate from the project source repo (Spec 4.6).

Every write (commit or quarantine) produces a git commit in the wiki repo,
referencing the source URI in the message. This gives a full forensic audit
trail: git log shows every ingestion event, git diff shows what changed,
git revert rolls back a poisoned claim.

Directory layout of the wiki repo:
  <root>/
    pages/       — committed (ACTIVE) claims
    quarantine/  — QUARANTINED / PENDING claims
    trust_rules.yaml  — user-editable trust rules (see trust/tiering.py)

The root defaults to ./wiki_data/ or $WIKI_DATA_PATH if set.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import yaml

from ..models import Claim, ClaimStatus, Correction, WikiPage

_TRUST_RULES_TEMPLATE = """\
# Trust rules for this wiki instance.
# Rules are matched top-to-bottom against the source URI; first match wins.
# See project README for built-in defaults and documentation.
#
# Example:
#   rules:
#     - pattern: "internal\\.corp\\.example\\.com"
#       level: trusted
#       comment: "Internal knowledge base"

rules: []

# Gate 5 similarity thresholds (embedding-based duplicate/conflict detection).
# duplicate_threshold: cosine similarity >= this → quarantine as duplicate
# conflict_threshold:  cosine similarity >= this → escalate for human review
similarity:
  duplicate_threshold: 0.95
  conflict_threshold: 0.85
"""

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "secure-llm-wiki",
    "GIT_AUTHOR_EMAIL": "wiki@local",
    "GIT_COMMITTER_NAME": "secure-llm-wiki",
    "GIT_COMMITTER_EMAIL": "wiki@local",
}


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )


def _serialize(claim: Claim, gate_detail: str = "") -> str:
    """Render claim as YAML-frontmatter Markdown."""
    d = claim.to_dict()
    frontmatter = yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=True)
    body = claim.text
    if gate_detail:
        body += f"\n\n> Gate decision: {gate_detail}"
    return f"---\n{frontmatter}---\n\n{body}\n"


def _deserialize(path: Path) -> Claim:
    """Parse a frontmatter Markdown file back into a Claim."""
    text = path.read_text(encoding="utf-8")
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        raise ValueError(f"missing frontmatter in {path}")
    raw = yaml.safe_load(parts[1])
    return Claim.from_dict(raw)


class WikiStore:
    """Manages the wiki data repository.

    Call init() once (or let the first save call do it automatically).
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(os.environ.get("WIKI_DATA_PATH", "wiki_data"))
        self.pages = self.root / "pages"
        self.quarantine = self.root / "quarantine"
        self.sources = self.root / "sources"
        self.corrections = self.root / "corrections"
        self.wiki = self.root / "wiki"

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Create the wiki repo from scratch if it doesn't exist."""
        self.pages.mkdir(parents=True, exist_ok=True)
        self.quarantine.mkdir(parents=True, exist_ok=True)
        self.sources.mkdir(parents=True, exist_ok=True)
        self.corrections.mkdir(parents=True, exist_ok=True)
        (self.wiki / "summaries").mkdir(parents=True, exist_ok=True)
        (self.wiki / "concepts").mkdir(parents=True, exist_ok=True)
        (self.wiki / "entities").mkdir(parents=True, exist_ok=True)

        if (self.root / ".git").exists():
            return

        _git(["init"], self.root)
        _git(["config", "user.name", "secure-llm-wiki"], self.root)
        _git(["config", "user.email", "wiki@local"], self.root)

        rules_file = self.root / "trust_rules.yaml"
        if not rules_file.exists():
            rules_file.write_text(_TRUST_RULES_TEMPLATE, encoding="utf-8")

        index = self.wiki / "index.md"
        if not index.exists():
            # The bundle-root index.md is the only place OKF permits
            # frontmatter, used to declare the targeted spec version (§11).
            index.write_text(
                '---\nokf_version: "0.1"\n---\n\n# Wiki Index\n\n(no pages yet)\n',
                encoding="utf-8",
            )

        log = self.wiki / "log.md"
        if not log.exists():
            log.write_text("# Ingest Log\n\n", encoding="utf-8")

        _git(["add", "."], self.root)
        _git(["commit", "-m", "Initialize wiki store"], self.root)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_claim(self, claim: Claim, gate_detail: str = "") -> Path:
        """Write an ACTIVE claim to pages/ and commit."""
        self._ensure_init()
        path = self.pages / f"{claim.claim_id}.md"
        path.write_text(_serialize(claim, gate_detail), encoding="utf-8")
        msg = f"add {claim.claim_id[:8]} [{claim.trust_level.value}] from {claim.source.uri}"
        self._commit(path, msg)
        return path

    def save_quarantined(self, claim: Claim, gate_detail: str = "") -> Path:
        """Write a QUARANTINED claim to quarantine/ and commit."""
        self._ensure_init()
        claim.status = ClaimStatus.QUARANTINED
        path = self.quarantine / f"{claim.claim_id}.md"
        path.write_text(_serialize(claim, gate_detail), encoding="utf-8")
        reason = gate_detail[:72] if gate_detail else "gate failed"
        msg = f"quarantine {claim.claim_id[:8]} from {claim.source.uri}: {reason}"
        self._commit(path, msg)
        return path

    def promote_from_quarantine(self, claim: Claim, gate_detail: str = "") -> Path:
        """Move a quarantined claim into pages/ as ACTIVE (human override).

        Writes the ACTIVE claim to pages/, removes the quarantine copy, and
        records both in a single commit.
        """
        self._ensure_init()
        claim.status = ClaimStatus.ACTIVE
        dest = self.pages / f"{claim.claim_id}.md"
        dest.write_text(_serialize(claim, gate_detail), encoding="utf-8")
        src = self.quarantine / f"{claim.claim_id}.md"
        paths = [dest]
        if src.exists():
            src.unlink()
            paths.append(src)
        self._commit_many(paths, f"promote {claim.claim_id[:8]} from quarantine")
        return dest

    def mark_quarantine_reviewed(self, claim: Claim, gate_detail: str = "") -> Path:
        """Rewrite a quarantined claim in place (e.g. to record its resolution)."""
        self._ensure_init()
        claim.status = ClaimStatus.QUARANTINED
        path = self.quarantine / f"{claim.claim_id}.md"
        path.write_text(_serialize(claim, gate_detail), encoding="utf-8")
        self._commit(path, f"review {claim.claim_id[:8]} — {claim.resolution or 'kept'}")
        return path

    def delete_quarantined_claim(self, claim_id: str) -> bool:
        """Remove a single quarantined claim file and commit. Returns True if removed."""
        path = self.quarantine / f"{claim_id}.md"
        if not path.exists():
            return False
        path.unlink()
        self._commit_many([path], f"drop {claim_id[:8]} from quarantine")
        return True

    def supersede_claim(self, claim_id: str) -> bool:
        """Mark an ACTIVE claim in pages/ as SUPERSEDED and commit.

        Returns True if the claim was found and updated. The file is retained
        (status SUPERSEDED) so it drops out of load_claims(ACTIVE) but the audit
        trail survives.
        """
        path = self.pages / f"{claim_id}.md"
        if not path.exists():
            return False
        try:
            claim = _deserialize(path)
        except Exception:
            return False
        claim.status = ClaimStatus.SUPERSEDED
        path.write_text(_serialize(claim), encoding="utf-8")
        self._commit(path, f"supersede {claim_id[:8]}")
        return True

    def save_source(
        self,
        source_id: str,
        uri: str,
        content_hash: str,
        trust_level: str,
        text: str,
    ) -> Path:
        """Write sanitized source text to sources/ and commit.

        Skips the commit if the same content_hash is already stored (idempotent
        re-ingestion of an unchanged source produces no extra git commit).
        """
        from datetime import datetime, timezone

        self._ensure_init()
        self.sources.mkdir(parents=True, exist_ok=True)
        path = self.sources / f"{source_id}.md"

        # Skip if already stored with the same hash
        if path.exists() and f"content_hash: {content_hash}" in path.read_text(encoding="utf-8"):
            return path

        frontmatter = yaml.dump(
            {
                "content_hash": content_hash,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "source_id": source_id,
                "trust_level": trust_level,
                "uri": uri,
            },
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=True,
        )
        path.write_text(f"---\n{frontmatter}---\n\n{text}\n", encoding="utf-8")
        self._commit(path, f"add source {source_id} [{trust_level}] from {uri}")
        return path

    def save_correction(self, correction: Correction) -> Path:
        """Persist an interactive-review correction and commit it.

        Stored one-file-per-correction under corrections/ so the record set can
        later drive reconciliation of the original source document. The body
        keeps the original and corrected text human-readable in ``git diff``.
        """
        self._ensure_init()
        self.corrections.mkdir(parents=True, exist_ok=True)
        path = self.corrections / f"{correction.claim_id}.md"
        frontmatter = yaml.dump(
            correction.to_dict(),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=True,
        )
        body = f"## original\n\n{correction.original_text}\n"
        if correction.action == "edit":
            body += f"\n## corrected\n\n{correction.corrected_text}\n"
        else:
            body += "\n## corrected\n\n(dropped — claim removed before embedding)\n"
        if correction.note:
            body += f"\n## note\n\n{correction.note}\n"
        path.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
        self._commit(
            path,
            f"correct {correction.claim_id[:8]} [{correction.action}] "
            f"from {correction.source_uri}",
        )
        return path

    def load_corrections(self, source_id: str | None = None) -> list[Correction]:
        """Load stored corrections, optionally filtered to one source_id."""
        if not self.corrections.exists():
            return []
        out: list[Correction] = []
        for path in sorted(self.corrections.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            parts = text.split("---\n", 2)
            if len(parts) < 3:
                continue
            raw = yaml.safe_load(parts[1])
            corr = Correction.from_dict(raw)
            if source_id is None or corr.source_id == source_id:
                out.append(corr)
        return out

    _PAGE_SUBDIR = {"summary": "summaries", "concept": "concepts", "entity": "entities"}

    def save_wiki_page(self, page: WikiPage) -> Path:
        """Write a wiki page to wiki/<type>s/<slug>.md (no commit)."""
        self._ensure_init()
        subdir_name = self._PAGE_SUBDIR.get(page.page_type, f"{page.page_type}s")
        subdir = self.wiki / subdir_name
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / f"{page.slug}.md"
        path.write_text(page.to_markdown(), encoding="utf-8")
        return path

    def _commit_many(self, paths: list[Path], message: str) -> None:
        """Stage the given paths and make a single commit. Skips if nothing staged."""
        for path in paths:
            rel = str(path.relative_to(self.root))
            _git(["add", rel], self.root)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=self.root,
            env=_GIT_ENV,
        )
        if result.returncode == 0:
            return  # nothing staged
        _git(["commit", "-m", message], self.root)

    def update_index(self) -> Path:
        """Regenerate wiki/index.md as an OKF index file (see docs/okf-spec.md §6).

        The root index carries the ``okf_version`` frontmatter (§11) — the only
        place OKF permits frontmatter in an index. Each section lists its
        concepts as bundle-relative markdown links with their descriptions.
        """
        self._ensure_init()
        lines = ['---', 'okf_version: "0.1"', '---', '', '# Wiki Index', '']
        section_titles = {
            "summaries": "Summaries",
            "concepts": "Concepts",
            "entities": "Entities",
        }
        for subdir_name in ("summaries", "concepts", "entities"):
            subdir = self.wiki / subdir_name
            pages = sorted(subdir.glob("*.md")) if subdir.exists() else []
            if not pages:
                continue
            lines.append(f"## {section_titles[subdir_name]}")
            lines.append("")
            for p in pages:
                slug = p.stem
                try:
                    page = WikiPage.from_file(p)
                    title = page.title or slug
                    desc = page.description
                except Exception:
                    title, desc = slug, ""
                entry = f"* [{title}](/{subdir_name}/{slug}.md)"
                if desc:
                    entry += f" - {desc}"
                lines.append(entry)
            lines.append("")
        index = self.wiki / "index.md"
        index.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return index

    def append_log(self, entry: str, kind: str = "Update") -> Path:
        """Record an entry in wiki/log.md as an OKF log file (see §7).

        Entries are grouped under ``## YYYY-MM-DD`` date headings, newest first,
        each written as ``* **<kind>**: <entry>``.
        """
        from datetime import datetime, timezone
        self._ensure_init()
        log = self.wiki / "log.md"
        text = log.read_text(encoding="utf-8") if log.exists() else ""

        lines = text.splitlines()
        header = "# Ingest Log"
        if lines and lines[0].startswith("# "):
            header = lines[0]
            body_lines = lines[1:]
        else:
            body_lines = lines
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        body = "\n".join(body_lines).strip()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_heading = f"## {today}"
        entry_line = f"* **{kind}**: {entry}"

        if body.startswith(date_heading):
            # Insert under the existing (newest) date section, newest first.
            rest = body[len(date_heading):].lstrip("\n")
            body = f"{date_heading}\n{entry_line}\n{rest}".rstrip()
        else:
            prefix = f"{date_heading}\n{entry_line}"
            body = f"{prefix}\n\n{body}".rstrip() if body else prefix

        log.write_text(f"{header}\n\n{body}\n", encoding="utf-8")
        return log

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def load_claims(self, status: ClaimStatus | None = ClaimStatus.ACTIVE) -> list[Claim]:
        """Load claims from pages/, optionally filtered by status."""
        return self._load_dir(self.pages, status)

    def load_quarantined(self) -> list[Claim]:
        """Load all claims from quarantine/."""
        return self._load_dir(self.quarantine, status=None)

    def load_quarantined_detailed(self) -> list[tuple[Claim, str]]:
        """Load quarantined claims paired with their stored gate finding detail.

        The detail is the ``> Gate decision:`` line written to the file body by
        :func:`_serialize`; it carries the finding that must be resolved.
        """
        if not self.quarantine.exists():
            return []
        out: list[tuple[Claim, str]] = []
        for path in sorted(self.quarantine.glob("*.md")):
            try:
                claim = _deserialize(path)
            except Exception:
                continue
            detail = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("> Gate decision:"):
                    detail = line[len("> Gate decision:"):].strip()
                    break
            out.append((claim, detail))
        return out

    # ------------------------------------------------------------------
    # Deletes
    # ------------------------------------------------------------------

    def delete_quarantine(self) -> list[str]:
        """Remove all files from quarantine/ and commit. Returns deleted claim IDs."""
        return self._delete_dir(self.quarantine, "clear quarantine")

    def delete_by_trust(self, trust_level: str) -> list[str]:
        """Remove all claims (pages + quarantine) matching trust_level and commit."""
        deleted: list[str] = []
        for directory in (self.pages, self.quarantine):
            for path in sorted(directory.glob("*.md")):
                try:
                    claim = _deserialize(path)
                    if claim.trust_level.value == trust_level:
                        path.unlink()
                        deleted.append(claim.claim_id)
                except Exception:
                    pass
        if deleted:
            _git(["add", "-A"], self.root)
            _git(["commit", "-m", f"clear {trust_level} claims ({len(deleted)} removed)"], self.root)
        return deleted

    def reset(self, keep_history: bool = False) -> int:
        """Remove all claims from pages/ and quarantine/.

        keep_history=False (default): delete and re-init the git repo so no
        claim content survives in the history.
        keep_history=True: commit a removal on top of the existing history.
        """
        # Count before we delete anything
        total = len(list(self.pages.glob("*.md"))) + len(list(self.quarantine.glob("*.md")))

        if keep_history:
            self._delete_dir(self.pages, commit=False)
            self._delete_dir(self.quarantine, commit=False)
            if total:
                _git(["add", "-A"], self.root)
                _git(["commit", "-m", f"reset wiki — removed all {total} claims"], self.root)
        else:
            # Preserve trust_rules.yaml, then wipe and re-init
            rules_path = self.root / "trust_rules.yaml"
            rules_backup = rules_path.read_text(encoding="utf-8") if rules_path.exists() else None
            shutil.rmtree(self.root)
            self.root.mkdir(parents=True)
            if rules_backup is not None:
                rules_path.write_text(rules_backup, encoding="utf-8")
            self.init()

        return total

    def _delete_dir(self, directory: Path, message: str = "", commit: bool = True) -> list[str]:
        """Delete all .md files in directory, optionally committing. Returns claim IDs."""
        deleted: list[str] = []
        for path in sorted(directory.glob("*.md")):
            try:
                claim = _deserialize(path)
                deleted.append(claim.claim_id)
            except Exception:
                pass
            path.unlink()
        if deleted and commit:
            _git(["add", "-A"], self.root)
            _git(["commit", "-m", message or f"removed {len(deleted)} claims"], self.root)
        return deleted

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_init(self) -> None:
        if not (self.root / ".git").exists():
            self.init()

    def _commit(self, path: Path, message: str) -> None:
        rel = str(path.relative_to(self.root))
        _git(["add", rel], self.root)
        _git(["commit", "-m", message], self.root)

    def _load_dir(self, directory: Path, status: ClaimStatus | None) -> list[Claim]:
        if not directory.exists():
            return []
        claims = []
        for path in sorted(directory.glob("*.md")):
            try:
                claim = _deserialize(path)
                if status is None or claim.status == status:
                    claims.append(claim)
            except Exception:
                pass
        return claims
