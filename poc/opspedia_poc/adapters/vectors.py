"""VectorIndex: numpy 전수 코사인 (ADR-004). Embedder: sentence-transformers (선택)."""
from __future__ import annotations

import numpy as np


class NumpyVectorIndex:
    def __init__(self):
        self.ids: list[str] = []
        self.m = np.zeros((0, 1), dtype=np.float32)

    def rebuild(self, ids: list[str], vectors: np.ndarray) -> None:
        v = vectors.astype(np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
        self.ids, self.m = ids, v

    def search(self, vector: np.ndarray, k: int = 50) -> list[tuple[str, float]]:
        if not self.ids:
            return []
        q = vector.astype(np.float32).ravel()
        q /= np.linalg.norm(q) + 1e-9
        s = self.m @ q
        top = np.argsort(-s)[:k]
        return [(self.ids[i], float(s[i])) for i in top]


class STEmbedder:
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model, device="cpu")
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts, batch_size=8, normalize_embeddings=True, convert_to_numpy=True)
