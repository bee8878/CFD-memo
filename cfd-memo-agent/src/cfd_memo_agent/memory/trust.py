"""Evidence-derived trust levels for persistent CFD experience records."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any


TRUST_LEVELS = ("candidate", "config_verified", "run_verified", "physics_verified")
_LEVEL_INDEX = {level: index for index, level in enumerate(TRUST_LEVELS)}


def strongest_level(left: str, right: str) -> str:
    """Return the stronger known level without accepting arbitrary model labels."""
    if left not in _LEVEL_INDEX or right not in _LEVEL_INDEX:
        raise ValueError("未知经验可信等级")
    return left if _LEVEL_INDEX[left] >= _LEVEL_INDEX[right] else right


def file_evidence(path: Path | str, *, role: str) -> dict[str, Any]:
    source = Path(path).resolve()
    data = source.read_bytes()
    return {
        "role": role,
        "path": str(source),
        "size_bytes": len(data),
        "sha256": sha256(data).hexdigest(),
    }


def verify_file_evidence(item: dict[str, Any]) -> bool:
    try:
        path = Path(item["path"]).resolve()
        data = path.read_bytes()
    except (KeyError, OSError, TypeError):
        return False
    return (
        item.get("size_bytes") == len(data)
        and item.get("sha256") == sha256(data).hexdigest()
    )


def verify_result_index(path: Path | str) -> dict[str, Any]:
    """Validate the result index and every artifact it declares inside its run root."""
    index_path = Path(path).resolve()
    try:
        value = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 result index：{exc}") from exc
    attempt = Path(value.get("attempt_path", "")).resolve()
    if value.get("schema_version") != 1:
        raise ValueError("result index 版本不受支持")
    if not attempt.is_dir() or index_path.parent != attempt:
        raise ValueError("result index 的 attempt 路径不一致")
    run_root = attempt.parent.parent
    if attempt.parent != run_root / "attempts":
        raise ValueError("result index 不位于可信 run/attempts 结构中")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("result index 没有可核验产物")
    roles: set[str] = set()
    names: set[str] = set()
    seen_paths: set[Path] = set()
    for artifact in artifacts:
        relative = Path(artifact.get("path", ""))
        target = (run_root / relative).resolve()
        if target in seen_paths or run_root not in target.parents or not target.is_file():
            raise ValueError(f"result index 证据缺失或越界：{relative}")
        seen_paths.add(target)
        roles.add(str(artifact.get("role")))
        names.add(target.name)
        data = target.read_bytes()
        if (artifact.get("size_bytes") != len(data)
                or artifact.get("sha256") != sha256(data).hexdigest()):
            raise ValueError(f"result index 证据哈希不一致：{relative}")
    required_evidence = {"execution.json", "mesh-evidence.json", "result-evidence.json"}
    if not required_evidence <= names or not {"log", "field"} <= roles:
        raise ValueError("result index 缺少真实执行、网格、日志或场文件证据")
    return value


def derive_level(
    *, runner_mode: str, config_valid: bool, outcome_status: str,
    result_index: dict[str, Any] | None, physical_validated: bool,
) -> str:
    """Compute trust from deterministic facts; model text is deliberately absent."""
    if not config_valid:
        return "candidate"
    level = "config_verified"
    if (runner_mode == "real" and outcome_status == "completed"
            and result_index is not None and result_index.get("status") == "completed"):
        level = "run_verified"
        if physical_validated and result_index.get("physical_validated") is True:
            level = "physics_verified"
    return level


__all__ = [
    "TRUST_LEVELS", "derive_level", "file_evidence", "strongest_level",
    "verify_file_evidence", "verify_result_index",
]
