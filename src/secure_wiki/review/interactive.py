"""Interactive review — validate/correct extracted claims and resolve
non-commit gate findings before they are written.

Two opt-in (``ingest --review``) checkpoints share one navigable engine:

  * :func:`review_claims_interactive` — runs after extraction, before
    embedding. Accept / edit / drop each extracted claim.
  * :func:`review_findings_interactive` — runs after the write-gate. Resolve
    every QUARANTINE / ESCALATE finding (keep / commit / edit / drop) so none
    are left unaddressed.

Both are driven by :func:`run_review_session`, an index-based loop that lets the
reviewer step **back** to a previous item to fix a mistake, not only forward.
Every edit or drop yields a :class:`Correction` (see ``WikiStore.save_correction``)
so the original source document can be reconciled later.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Optional

from ..models import Claim, Correction

_LINE = "─" * 60


# Navigation sentinels a decide-callback may return instead of a decision.
class _Nav:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<nav {self.name}>"


PREV = _Nav("prev")            # step back to the previous item
NEXT = _Nav("next")            # advance, leaving this item's decision unchanged
QUIT = _Nav("quit")            # finish the session (may leave items unaddressed)
ACCEPT_ALL = _Nav("accept_all")  # apply the bulk default to all remaining items


@dataclass
class Decision:
    """A per-item review decision. ``action`` meaning depends on the reviewer:
    claims use accept/edit/drop; findings use keep/commit/edit/drop."""
    action: str
    new_text: Optional[str] = None
    note: Optional[str] = None


def run_review_session(
    count: int,
    decide: Callable[[int, Optional[Decision]], object],
    *,
    out: Callable[[str], None] = print,
    require_all: bool = False,
    confirm_incomplete: Optional[Callable[[list[int]], bool]] = None,
    bulk_default: Optional[Callable[[int], Decision]] = None,
) -> tuple[list[Optional[Decision]], bool]:
    """Drive an index-based review with back-navigation.

    ``decide(index, prior_decision)`` renders item *index* (showing
    ``prior_decision`` when revisiting) and returns either a :class:`Decision`
    or one of the navigation sentinels ``PREV`` / ``NEXT`` / ``QUIT`` /
    ``ACCEPT_ALL``.

    Returns ``(decisions, completed)``. When ``require_all`` is set the session
    will not finish while any item is undecided: it jumps back to the first
    unresolved item unless the reviewer force-quits (``confirm_incomplete``
    returns True), in which case ``completed`` is False.
    """
    decisions: list[Optional[Decision]] = [None] * count
    i = 0
    while True:
        if i >= count:
            unresolved = [k for k, d in enumerate(decisions) if d is None]
            if require_all and unresolved:
                labels = ", ".join(f"#{k + 1}" for k in unresolved)
                out(f"  {len(unresolved)} item(s) still unaddressed: {labels}")
                i = unresolved[0]
                continue
            return decisions, True

        result = decide(i, decisions[i])

        if result is PREV:
            i = max(0, i - 1)
        elif result is NEXT:
            i += 1
        elif result is ACCEPT_ALL and bulk_default is not None:
            for k in range(i, count):
                if decisions[k] is None:
                    decisions[k] = bulk_default(k)
            i = count
        elif result is QUIT:
            unresolved = [k for k, d in enumerate(decisions) if d is None]
            if unresolved and require_all:
                if confirm_incomplete is not None and not confirm_incomplete(unresolved):
                    continue  # reviewer chose to keep going
                return decisions, False
            return decisions, True
        else:  # a Decision
            decisions[i] = result  # type: ignore[assignment]
            i += 1


def make_correction(claim: Claim, action: str, corrected_text: str | None, note: str | None) -> Correction:
    """Snapshot a correction. Call BEFORE mutating ``claim.text``."""
    return Correction(
        source_id=claim.source.id,
        source_uri=claim.source.uri,
        section=claim.source.section,
        content_hash=claim.source.content_hash,
        claim_id=claim.claim_id,
        action=action,
        original_text=claim.text,
        corrected_text=corrected_text,
        note=note,
    )


# ------------------------------------------------------------------
# Pre-embed claim review
# ------------------------------------------------------------------

_CLAIM_MENU = (
    "  [a] accept  [e] edit  [d] drop  [p] previous  [A] accept all remaining  [q] finish"
)


def review_claims_interactive(
    claims: list[Claim],
    *,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> tuple[list[Claim], list[Correction]]:
    """Walk *claims* interactively and return ``(kept_claims, corrections)``.

    Decisions are collected first (with back-navigation), then applied, so
    revising an earlier choice never leaves a half-applied edit behind.
    """
    total = len(claims)
    out("")
    out(f"[review]   validate {total} extracted claim(s) — edit or drop before embedding")
    out(_LINE)

    def _decide(i: int, prior: Optional[Decision]) -> object:
        claim = claims[i]
        out("")
        out(f"  claim {i + 1}/{total}  [{claim.trust_level.value}]  {claim.source.id} · {claim.source.section}")
        out(f"    {claim.text}")
        if prior is not None:
            _show_prior(out, prior)
        try:
            choice = input_fn(f"{_CLAIM_MENU}\n  > ").strip()
        except (EOFError, KeyboardInterrupt):
            out("\n[review]   input closed — accepting remaining claims unchanged")
            return QUIT

        if choice in ("", "a"):
            return Decision("accept")
        if choice == "A":
            return ACCEPT_ALL
        if choice == "p":
            return PREV
        if choice == "q":
            return QUIT
        if choice == "e":
            new_text = input_fn("  new text (blank = keep): ").strip()
            if not new_text or new_text == claim.text:
                out("  unchanged")
                return Decision("accept")
            note = input_fn("  note (optional): ").strip() or None
            return Decision("edit", new_text=new_text, note=note)
        if choice == "d":
            note = input_fn("  reason (optional): ").strip() or None
            return Decision("drop", note=note)
        out("  ? unrecognized — choose a / e / d / p / A / q")
        return _decide(i, prior)  # re-prompt same item

    decisions, _ = run_review_session(
        total, _decide, out=out,
        bulk_default=lambda _idx: Decision("accept"),
    )

    kept: list[Claim] = []
    corrections: list[Correction] = []
    for claim, dec in zip(claims, decisions):
        dec = dec or Decision("accept")  # undecided (early finish) → accept as-is
        if dec.action == "drop":
            corrections.append(make_correction(claim, "drop", None, dec.note))
            continue
        if dec.action == "edit" and dec.new_text and dec.new_text != claim.text:
            corrections.append(make_correction(claim, "edit", dec.new_text, dec.note))
            claim.text = dec.new_text
            if dec.note:
                claim.review_notes = dec.note
        kept.append(claim)

    out("")
    out(f"[review]   {len(kept)} kept, {total - len(kept)} dropped, {len(corrections)} correction(s)")
    return kept, corrections


# ------------------------------------------------------------------
# Post-gate findings review
# ------------------------------------------------------------------

_FINDING_MENU = (
    "  [k] keep quarantined  [c] commit anyway  [e] edit & commit  [d] drop  "
    "[p] previous  [q] finish"
)


def review_findings_interactive(
    findings: list[tuple[Claim, str, str]],
    *,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> tuple[list[Optional[Decision]], bool]:
    """Resolve every non-commit gate finding.

    ``findings`` is a list of ``(claim, kind, detail)`` where *kind* is
    ``"QUARANTINE"`` or ``"ESCALATE"`` and *detail* is the gate's explanation.

    Returns ``(decisions, completed)`` aligned to *findings*. Each decision's
    ``action`` is one of keep / commit / edit / drop. ``completed`` is False
    only if the reviewer force-quit with findings still unaddressed.
    """
    total = len(findings)
    out("")
    out(f"[review]   {total} finding(s) require resolution — none may be left unaddressed")
    out(_LINE)

    def _confirm_incomplete(unresolved: list[int]) -> bool:
        labels = ", ".join(f"#{k + 1}" for k in unresolved)
        try:
            ans = input_fn(
                f"  {len(unresolved)} finding(s) unaddressed ({labels}). "
                "Quit anyway? They stay quarantined. [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return True
        return ans == "y"

    def _decide(i: int, prior: Optional[Decision]) -> object:
        claim, kind, detail = findings[i]
        out("")
        out(f"  finding {i + 1}/{total}  {kind}  [{claim.trust_level.value}]")
        out(f"    {detail}")
        out(f"    claim: {claim.text}")
        if prior is not None:
            _show_prior(out, prior)
        try:
            choice = input_fn(f"{_FINDING_MENU}\n  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return QUIT

        if choice in ("", "k"):
            return Decision("keep")
        if choice == "c":
            note = input_fn("  note (optional): ").strip() or None
            return Decision("commit", note=note)
        if choice == "p":
            return PREV
        if choice == "q":
            return QUIT
        if choice == "e":
            new_text = input_fn("  new text (blank = keep original): ").strip()
            note = input_fn("  note (optional): ").strip() or None
            return Decision("edit", new_text=new_text or None, note=note)
        if choice == "d":
            note = input_fn("  reason (optional): ").strip() or None
            return Decision("drop", note=note)
        out("  ? unrecognized — choose k / c / e / d / p / q")
        return _decide(i, prior)  # re-prompt same item

    return run_review_session(
        total, _decide, out=out,
        require_all=True,
        confirm_incomplete=_confirm_incomplete,
    )


def _show_prior(out: Callable[[str], None], prior: Decision) -> None:
    label = prior.action
    if prior.action == "edit" and prior.new_text:
        label = f"edit → {prior.new_text}"
    out(f"    (current decision: {label})")


def is_interactive_capable() -> bool:
    """True when stdin/stdout can support an interactive prompt."""
    return sys.stdin.isatty() and sys.stdout.isatty()
