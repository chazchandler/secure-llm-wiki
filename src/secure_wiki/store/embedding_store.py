"""Embedding store — persists claim embeddings alongside the wiki data repo.

Embeddings live in wiki_data/embeddings/<claim_id>.json so they are
co-located with the claim files but kept separate from the Markdown pages.
The files are git-tracked in the same wiki repo as the claims: each write
and delete is committed so the (expensive to recompute) vectors travel with
the wiki bundle and survive a clone. Writes commit by default; pass
``commit=False`` to batch several writes into a single caller-made commit.

Each file records the embedding model that produced the vector
(``{"claim_id", "embedding", "model"}``) so an ``EMBED_MODEL`` swap is
detectable — cosine similarity (Gate 5) is only comparable within one model.
See :meth:`EmbeddingStore.model_drift`.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..store.wiki_store import WikiStore


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class EmbeddingStore:
    """Read/write embeddings keyed by claim_id."""

    def __init__(self, wiki_store: WikiStore | None = None) -> None:
        self._store = wiki_store or WikiStore()
        self._dir = self._store.root / "embeddings"
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        claim_id: str,
        embedding: list[float],
        commit: bool = True,
        model: str | None = None,
    ) -> Path:
        """Write the embedding and (by default) commit it to the wiki repo.

        The producing embedding model is recorded alongside the vector so a
        later ``EMBED_MODEL`` swap is detectable (see :meth:`model_drift`);
        cosine similarities are only comparable within a single model. When
        ``model`` is None it is resolved from the environment.

        Returns the file path so callers passing ``commit=False`` can batch the
        paths into a single ``WikiStore.commit_paths`` call.
        """
        if model is None:
            from ..llm_client import get_embed_model
            model = get_embed_model()
        path = self._dir / f"{claim_id}.json"
        path.write_text(
            json.dumps({"claim_id": claim_id, "embedding": embedding, "model": model}),
            encoding="utf-8",
        )
        if commit:
            self._store.commit_paths([path], f"embed {claim_id[:8]}")
        return path

    def load_all(self) -> dict[str, list[float]]:
        result: dict[str, list[float]] = {}
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                result[data["claim_id"]] = data["embedding"]
            except Exception:
                pass
        return result

    def load_models(self) -> dict[str, str | None]:
        """Map claim_id -> the model that produced its embedding.

        ``None`` marks a file written before model tracking (model unknown).
        """
        result: dict[str, str | None] = {}
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                result[data["claim_id"]] = data.get("model")
            except Exception:
                pass
        return result

    def load_comparable(self, current_model: str) -> dict[str, list[float]]:
        """Load only embeddings whose vectors are safe to compare with fresh ones.

        Cosine similarity across different embedding models is meaningless, so
        this is the safeguard behind Gate 5: it drops any vector produced by a
        *known-different* model, keeping only current-model vectors (plus
        legacy files with no recorded model, whose model is unknown and — on an
        un-swapped install — is almost always the current one; excluding them
        would silently disable dedup until ``reembed``). Returns {} when the
        provider has no embedding model (embeddings disabled).
        """
        if not current_model:
            return {}
        result: dict[str, list[float]] = {}
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                model = data.get("model")
                if model is None or model == current_model:
                    result[data["claim_id"]] = data["embedding"]
            except Exception:
                pass
        return result

    def model_drift(self, current_model: str) -> set[str]:
        """Return stored model names that differ from ``current_model``.

        A non-empty result means some embeddings were produced by a different
        model, so their Gate 5 cosine similarities are not comparable to fresh
        vectors and should be rebuilt (``reembed``). Files predating model
        tracking (model unknown) are excluded — their model is unverifiable,
        not necessarily different.
        """
        if not current_model:
            return set()  # embeddings disabled for this provider — nothing to compare
        return {m for m in self.load_models().values() if m and m != current_model}

    def delete(self, claim_id: str) -> None:
        path = self._dir / f"{claim_id}.json"
        if path.exists():
            path.unlink()
            self._store.commit_paths([path], f"drop embedding {claim_id[:8]}")

    def delete_many(self, claim_ids: list[str]) -> None:
        """Delete several embeddings and commit their removal in one commit."""
        removed: list[Path] = []
        for claim_id in claim_ids:
            path = self._dir / f"{claim_id}.json"
            if path.exists():
                path.unlink()
                removed.append(path)
        if removed:
            self._store.commit_paths(removed, f"drop {len(removed)} embedding(s)")

    def delete_all(self) -> None:
        removed: list[Path] = []
        for path in self._dir.glob("*.json"):
            path.unlink()
            removed.append(path)
        if removed:
            self._store.commit_paths(removed, f"drop all embeddings ({len(removed)})")
