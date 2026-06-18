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

from ..models import Claim, ClaimStatus, WikiPage

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
        self.wiki = self.root / "wiki"

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Create the wiki repo from scratch if it doesn't exist."""
        self.pages.mkdir(parents=True, exist_ok=True)
        self.quarantine.mkdir(parents=True, exist_ok=True)
        self.sources.mkdir(parents=True, exist_ok=True)
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
            index.write_text("# Wiki Index\n\n(no pages yet)\n", encoding="utf-8")

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
        """Regenerate wiki/index.md from all concept/entity/summary pages."""
        self._ensure_init()
        lines = ["# Wiki Index\n"]
        for subdir_name in ("summaries", "concepts", "entities"):
            subdir = self.wiki / subdir_name
            pages = sorted(subdir.glob("*.md")) if subdir.exists() else []
            if pages:
                lines.append(f"\n## {subdir_name.capitalize()}\n")
                for p in pages:
                    slug = p.stem
                    lines.append(f"- [[{subdir_name}/{slug}]]")
        index = self.wiki / "index.md"
        index.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return index

    def append_log(self, entry: str) -> Path:
        """Append a timestamped entry to wiki/log.md."""
        from datetime import datetime, timezone
        self._ensure_init()
        log = self.wiki / "log.md"
        if not log.exists():
            log.write_text("# Ingest Log\n\n", encoding="utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{ts}  {entry}\n")
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
