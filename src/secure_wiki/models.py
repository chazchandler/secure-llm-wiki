"""Central data structures for the Secure LLM-Wiki.

This module defines the provenance schema (see Spec section 6) as the single
source of truth. All pipeline layers import from here.

Status: COMPLETE. Fields and validation are fully implemented; persistence
helpers live in store/wiki_store.py.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
import uuid

import yaml

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _validate_claim_id(claim_id: str) -> str:
    """Reject anything that isn't a canonical UUID (path-traversal defense, F-06).

    `claim_id` is interpolated into filesystem paths (wiki_store.py) as
    `f"{claim_id}.md"` with no other sanitization, so a value like
    "../../etc/passwd" must never survive `from_dict`.
    """
    if not _UUID_RE.fullmatch(claim_id):
        raise ValueError(f"invalid claim_id (must be a UUID): {claim_id!r}")
    return claim_id


class TrustLevel(str, Enum):
    """Trust level of a source or claim (Spec 4.3)."""
    TRUSTED = "trusted"            # curated/verified by the user
    SEMI_TRUSTED = "semi-trusted"  # established source, not individually reviewed
    UNTRUSTED = "untrusted"        # arbitrary web find, agentically crawled

    @classmethod
    def weakest(cls, levels: list["TrustLevel"]) -> "TrustLevel":
        """Propagation rule: a claim inherits the WEAKEST level of its sources (Spec 4.3)."""
        order = [cls.TRUSTED, cls.SEMI_TRUSTED, cls.UNTRUSTED]
        return max(levels, key=order.index) if levels else cls.UNTRUSTED


class ClaimStatus(str, Enum):
    """Lifecycle status of a claim (Spec 6)."""
    ACTIVE = "active"            # committed, visible in the wiki
    PENDING = "pending"          # awaiting review/confirmation
    QUARANTINED = "quarantined"  # failed review
    SUPERSEDED = "superseded"    # replaced by a newer claim


@dataclass
class SourceRef:
    """Source reference for a claim (Spec 6)."""
    id: str
    uri: str
    section: str
    content_hash: str  # sha256 of the normalized original section

    @staticmethod
    def compute_hash(original_text: str) -> str:
        """SHA-256 over the normalized original text (Spec 7)."""
        normalized = " ".join(original_text.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class Claim:
    """An atomic, verifiable statement with full provenance (Spec 6).

    This is the central data structure of the entire system. Every pipeline
    stage enriches it; provenance is never removed.
    """
    text: str
    source: SourceRef
    trust_level: TrustLevel
    claim_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ingested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: ClaimStatus = ClaimStatus.PENDING
    gates_passed: list[str] = field(default_factory=list)
    supersedes: Optional[str] = None
    review_notes: Optional[str] = None
    # How a non-commit gate finding (QUARANTINE/ESCALATE) was manually resolved.
    # None means the finding has not been reviewed yet ("unaddressed"). Set by
    # interactive findings review to "committed" | "kept" | "dropped".
    resolution: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trust_level"] = self.trust_level.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        source = SourceRef(
            id=d["source"]["id"],
            uri=d["source"]["uri"],
            section=d["source"]["section"],
            content_hash=d["source"]["content_hash"],
        )
        return cls(
            text=d["text"],
            source=source,
            trust_level=TrustLevel(d["trust_level"]),
            claim_id=_validate_claim_id(d["claim_id"]),
            ingested_at=d["ingested_at"],
            status=ClaimStatus(d["status"]),
            gates_passed=d.get("gates_passed", []),
            supersedes=d.get("supersedes"),
            review_notes=d.get("review_notes"),
            resolution=d.get("resolution"),
        )


@dataclass
class Correction:
    """A human correction to an extracted claim, captured during interactive
    review (``ingest --review``) before the claim is embedded.

    Corrections are persisted independently of the claim so the *original*
    source document can later be reconciled with the reviewed knowledge. Each
    record pins the source (``source_id`` + ``content_hash``) the claim was
    extracted from, the original model-produced text, and the human's action.
    """
    source_id: str
    source_uri: str
    section: str
    content_hash: str          # hash of the source doc the claim came from
    claim_id: str
    action: str                # "edit" | "drop" | "supersede"
    original_text: str         # the claim text as the model extracted it
    corrected_text: Optional[str] = None  # None for a dropped claim
    note: Optional[str] = None            # reviewer's rationale
    # For findings resolved against an existing claim: the claim that was chosen
    # over, superseded by, or merged with this one (Gate 5 consistency review).
    related_claim_id: Optional[str] = None
    corrected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Correction":
        return cls(
            source_id=d["source_id"],
            source_uri=d["source_uri"],
            section=d["section"],
            content_hash=d["content_hash"],
            claim_id=_validate_claim_id(d["claim_id"]),
            action=d["action"],
            original_text=d["original_text"],
            corrected_text=d.get("corrected_text"),
            note=d.get("note"),
            related_claim_id=d.get("related_claim_id"),
            corrected_at=d.get("corrected_at", ""),
        )


@dataclass
class WikiPage:
    """A structured wiki page (summary, concept, or entity) as an OKF concept
    document (see docs/okf-spec.md).

    The on-disk frontmatter leads with the OKF-recommended keys — ``type``
    (required), ``title``, ``description``, ``tags``, ``timestamp`` — and keeps
    the producer-specific fields (``confidence``, ``sources``, ``slug``,
    ``created``, ``updated``) as permitted extensions.
    """
    slug: str
    page_type: str          # OKF `type` value: "summary" | "concept" | "entity"
    title: str
    tags: list[str]
    confidence: str         # "high" | "medium" | "low"
    sources: list[str]
    body: str
    created: str            # ISO date
    updated: str            # ISO date
    description: str = ""    # OKF one-line summary

    def to_markdown(self) -> str:
        # `slug` is intentionally NOT written to frontmatter. OKF's Concept ID
        # (docs/okf-spec.md §2) is the bundle-relative path minus `.md`, e.g.
        # `entities/google-workspace` for wiki/entities/google-workspace.md.
        # self.slug only ever holds the bare filename stem (`google-workspace`)
        # — write/save_wiki_page() put it in a type-named subdirectory, so a
        # bare `slug:` in frontmatter always disagrees with a consumer that
        # derives slugs from the full path, and gets a page held/rejected as a
        # frontmatter/slug conflict. See the OKF spec and the regression test
        # below for the full identifier shape.
        fm = yaml.dump(
            {
                # OKF-recommended fields (type is REQUIRED)
                "type": self.page_type,
                "title": self.title,
                "description": self.description,
                "tags": self.tags,
                "timestamp": self.updated,
                # producer extensions
                "confidence": self.confidence,
                "sources": self.sources,
                "created": self.created,
                "updated": self.updated,
            },
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=True,
        )
        return f"---\n{fm}---\n\n{self.body}\n"

    @classmethod
    def from_file(cls, path: Path) -> "WikiPage":
        text = path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        if len(parts) < 3:
            raise ValueError(f"missing frontmatter in {path}")
        raw = yaml.safe_load(parts[1])
        body = parts[2].strip()
        # `timestamp` is the OKF last-modified field; fall back to `updated`.
        timestamp = raw.get("timestamp") or raw.get("updated", "")
        return cls(
            slug=raw.get("slug", path.stem),
            page_type=raw.get("type", "concept"),
            title=raw.get("title", path.stem),
            tags=raw.get("tags", []),
            confidence=raw.get("confidence", ""),
            sources=raw.get("sources", []),
            body=body,
            created=raw.get("created", ""),
            updated=raw.get("updated", "") or timestamp,
            description=raw.get("description", ""),
        )


@dataclass
class LintReport:
    """Result of a wiki lint run."""
    orphans: list[str] = field(default_factory=list)
    missing_links: list[str] = field(default_factory=list)
    contradictions: list[tuple[str, str]] = field(default_factory=list)
    incomplete_pages: list[str] = field(default_factory=list)
    low_confidence_claims: list[str] = field(default_factory=list)
    auto_fixed: list[str] = field(default_factory=list)
