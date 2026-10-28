"""In-memory face templates for fast 1:N matching."""

from __future__ import annotations

import numpy as np


class Gallery:
    """Templates of one embedding model; person score = best template similarity."""

    def __init__(self, model_id: str, sample_ids, employee_ids, matrix):
        self.model_id = model_id
        self.sample_ids = np.asarray(sample_ids, np.int64)
        self.employee_ids = np.asarray(employee_ids, np.int64)
        self.matrix = np.asarray(matrix, np.float32)
        self.people, self._inverse = np.unique(self.employee_ids, return_inverse=True)

    def __len__(self) -> int:
        return len(self.people)

    @property
    def empty(self) -> bool:
        return self.matrix.shape[0] == 0

    def person_scores(self, emb: np.ndarray) -> dict[int, float]:
        if self.empty:
            return {}
        sims = self.matrix @ np.asarray(emb, np.float32)
        best = np.full(len(self.people), -1.0, np.float32)
        np.maximum.at(best, self._inverse, sims)
        return {int(p): float(s) for p, s in zip(self.people, best)}

    def top(self, emb: np.ndarray, k: int = 3) -> list[tuple[int, float]]:
        scores = self.person_scores(emb)
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]

    def templates_of(self, employee_id: int) -> np.ndarray:
        return self.matrix[self.employee_ids == employee_id]

    def cross_report(self) -> list[tuple[int, int, float]]:
        """Most similar *other* person for every enrolled person (look-alike check)."""
        out = []
        for i, p in enumerate(self.people):
            mine = self.matrix[self._inverse == i]
            others = self._inverse != i
            if not others.any():
                continue
            sims = mine @ self.matrix[others].T
            j = int(np.unravel_index(np.argmax(sims), sims.shape)[1])
            out.append((int(p), int(self.employee_ids[others][j]), float(sims.max())))
        return out
