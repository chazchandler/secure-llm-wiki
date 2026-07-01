"""Interactive claim review — validate and correct extracted claims before
they are embedded and committed.

This runs between extraction and embedding, and only when the user opts in with
``ingest --review``. By default the pipeline is fully non-interactive so it can
run unattended.

For each extracted claim the reviewer can:
  * accept it unchanged,
  * edit its text (the corrected text is what gets embedded/committed),
  * drop it (it is never embedded or committed).

Every edit or drop yields a :class:`Correction`, returned to the caller so it
can be persisted. Corrections are the durable record that lets the *original*
source document be reconciled with the reviewed knowledge later — see
``WikiStore.save_correction``.
"""
from __future__ import annotations

import sys
from typing import Callable

from ..models import Claim, Correction

_LINE = "─" * 60

_MENU = (
    "  [a] accept  [e] edit  [d] drop  [A] accept all remaining  [q] quit review"
)


def _make_correction(claim: Claim, action: str, corrected_text: str | None, note: str | None) -> Correction:
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


def review_claims_interactive(
    claims: list[Claim],
    *,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> tuple[list[Claim], list[Correction]]:
    """Walk *claims* interactively and return ``(kept_claims, corrections)``.

    ``kept_claims`` preserves order and excludes dropped claims. Edited claims
    are mutated in place so their new text flows through embedding and storage.
    ``input_fn`` / ``out`` are injectable for testing.
    """
    kept: list[Claim] = []
    corrections: list[Correction] = []
    accept_all = False
    total = len(claims)

    out("")
    out(f"[review]   validate {total} extracted claim(s) — edit or drop before embedding")
    out(_LINE)

    for i, claim in enumerate(claims, 1):
        if accept_all:
            kept.append(claim)
            continue

        out("")
        out(f"  claim {i}/{total}  [{claim.trust_level.value}]  {claim.source.id} · {claim.source.section}")
        out(f"    {claim.text}")

        while True:
            try:
                choice = input_fn(f"{_MENU}\n  > ").strip()
            except (EOFError, KeyboardInterrupt):
                # Treat an interrupted session as "accept the rest untouched"
                out("\n[review]   input closed — accepting remaining claims unchanged")
                kept.append(claim)
                accept_all = True
                break

            if choice in ("", "a"):
                kept.append(claim)
                break

            if choice == "A":
                accept_all = True
                kept.append(claim)
                break

            if choice == "q":
                # Keep this and every remaining claim as-is; stop prompting.
                kept.append(claim)
                accept_all = True
                break

            if choice == "e":
                new_text = input_fn("  new text (blank = keep): ").strip()
                if new_text and new_text != claim.text:
                    note = (input_fn("  note (optional): ").strip() or None)
                    corrections.append(_make_correction(claim, "edit", new_text, note))
                    claim.text = new_text
                    if note:
                        claim.review_notes = note
                    out("  edited")
                else:
                    out("  unchanged")
                kept.append(claim)
                break

            if choice == "d":
                note = (input_fn("  reason (optional): ").strip() or None)
                corrections.append(_make_correction(claim, "drop", None, note))
                out("  dropped — will not be embedded or committed")
                break

            out("  ? unrecognized — choose a / e / d / A / q")

    out("")
    out(f"[review]   {len(kept)} kept, {total - len(kept)} dropped, {len(corrections)} correction(s)")
    return kept, corrections


def is_interactive_capable() -> bool:
    """True when stdin/stdout can support an interactive prompt."""
    return sys.stdin.isatty() and sys.stdout.isatty()
