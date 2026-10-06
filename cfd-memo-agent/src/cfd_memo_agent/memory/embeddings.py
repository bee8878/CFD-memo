"""Replaceable embedding interface with a deterministic offline default."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import math
from typing import Protocol, runtime_checkable

from .vector import VECTOR_VERSION, cosine


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Minimal interface used by memory retrieval."""

    name: str
    version: str

    def similarity(self, query: str, document: str) -> float:
        """Return a finite similarity score in the inclusive range [0, 1]."""


class HashingEmbeddingProvider:
    """Free, deterministic lexical baseline that never sends text over the network."""

    name = "local-hashing"
    version = VECTOR_VERSION

    def similarity(self, query: str, document: str) -> float:
        return cosine(query, document)


class CallableEmbeddingProvider:
    """Adapter for a local or hosted function returning dense embeddings."""

    def __init__(self, name: str, version: str,
                 embed: Callable[[str], Sequence[float]]):
        if not name.strip() or not version.strip() or not callable(embed):
            raise ValueError("embedding provider 需要名称、版本和可调用 embed 函数")
        self.name = name.strip()
        self.version = version.strip()
        self._embed = embed

    @staticmethod
    def _vector(value: Sequence[float]) -> tuple[float, ...]:
        try:
            vector = tuple(float(item) for item in value)
        except (TypeError, ValueError) as exc:
            raise ValueError("embedding 必须是有限数值序列") from exc
        if not vector or not all(math.isfinite(item) for item in vector):
            raise ValueError("embedding 必须是非空有限数值序列")
        return vector

    def similarity(self, query: str, document: str) -> float:
        left = self._vector(self._embed(query))
        right = self._vector(self._embed(document))
        if len(left) != len(right):
            raise ValueError("query 与 document embedding 维度不一致")
        left_norm = math.sqrt(sum(item * item for item in left))
        right_norm = math.sqrt(sum(item * item for item in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        score = sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
        return min(1.0, max(0.0, score))


def validate_provider(provider: EmbeddingProvider | None) -> EmbeddingProvider:
    selected = provider or HashingEmbeddingProvider()
    if (not isinstance(selected, EmbeddingProvider)
            or not selected.name.strip() or not selected.version.strip()):
        raise ValueError("embedding provider 不符合 EmbeddingProvider 接口")
    return selected


__all__ = [
    "CallableEmbeddingProvider", "EmbeddingProvider", "HashingEmbeddingProvider",
    "validate_provider",
]
