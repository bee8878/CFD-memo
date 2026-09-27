"""Deterministic local vector encoding for structured CFD memories."""
from __future__ import annotations

from hashlib import sha256
import math
import re
import unicodedata
from typing import Any

VECTOR_VERSION = "hashing-charword-v1"
VECTOR_DIMENSIONS = 256

PROBLEM_SEMANTICS = {
    "BOUNDARY_NAMES": "边界缺失 边界名称不一致 入口出口 圆柱壁面 boundary missing inlet outlet cylinder",
    "BOUNDARY_MISSING": "边界缺失 缺少入口速度 压力边界 boundary field missing inlet velocity pressure",
    "TRANSPORT_VALUE": "运动黏度错误 物性参数错误 nu viscosity transport physical properties",
    "TRANSPORT_MISMATCH": "运动黏度不一致 雷诺数公式 nu viscosity Reynolds mismatch",
}

FILE_SEMANTICS = {
    "0/U": "速度场 入口速度 速度边界 velocity inlet boundary",
    "0/p": "压力场 压力边界 pressure outlet boundary",
    "constant/physicalProperties": "物性参数 运动黏度 nu viscosity transport",
}


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    words = re.findall(r"[a-z0-9_./+-]+|[\u3400-\u9fff]+", normalized)
    tokens: list[str] = []
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            tokens.extend(word[index:index + 2] for index in range(max(1, len(word) - 1)))
        else:
            tokens.append(word)
    return tokens


def encode(text: str) -> dict[int, float]:
    """Encode words and Chinese bigrams into a normalized sparse vector."""
    values: dict[int, float] = {}
    for token in _tokens(text):
        bucket = int.from_bytes(sha256(token.encode("utf-8")).digest()[:4], "big") % VECTOR_DIMENSIONS
        values[bucket] = values.get(bucket, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in values.values()))
    return {key: value / norm for key, value in values.items()} if norm else {}


def cosine(left: str, right: str) -> float:
    left_vector = encode(left)
    right_vector = encode(right)
    if len(left_vector) > len(right_vector):
        left_vector, right_vector = right_vector, left_vector
    score = sum(value * right_vector.get(key, 0.0) for key, value in left_vector.items())
    return min(1.0, max(0.0, score))


def experience_text(record: dict[str, Any]) -> str:
    applicability = record["applicability"]
    codes = record["problem_codes"]
    files = record["action"]["files"]
    parts = [
        applicability["case_type"], "二维圆柱绕流 cylinder flow",
        applicability["solver"], applicability["flow_model"], applicability["dimension"],
        " ".join(codes), " ".join(files),
        " ".join(PROBLEM_SEMANTICS.get(code, code) for code in codes),
        " ".join(FILE_SEMANTICS.get(path, path) for path in files),
    ]
    return " ".join(parts)


def task_text(task: dict[str, Any], query_text: str = "") -> str:
    return " ".join((
        query_text,
        task["case_type"], "二维圆柱绕流 cylinder flow",
        task["solver"], task["physics"]["flow_model"], task["geometry"]["dimension"],
        f"Re {task['physics']['reynolds_number']}",
    ))


def reynolds_similarity(source: float | None, target: float | None) -> float:
    if source is None or target is None or source <= 0 or target <= 0:
        return 0.0
    return min(source, target) / max(source, target)


__all__ = [
    "VECTOR_DIMENSIONS", "VECTOR_VERSION", "cosine", "encode", "experience_text",
    "reynolds_similarity", "task_text",
]
