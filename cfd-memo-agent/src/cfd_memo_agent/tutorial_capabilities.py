"""Installed, fail-closed descriptions for reviewed OpenFOAM tutorials."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class TutorialCapability:
    capability_id: str
    tutorial_id: str
    openfoam_version: str
    solver: str
    physics_model: str
    adapter_id: str
    required_fields: tuple[str, ...]
    mesh_tools: tuple[str, ...]
    whitelist: tuple[str, ...]
    task_template: dict[str, Any]

    def public_status(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "tutorial_id": self.tutorial_id,
            "openfoam_version": self.openfoam_version,
            "solver": self.solver,
            "physics_model": self.physics_model,
            "adapter_id": self.adapter_id,
            "required_fields": list(self.required_fields),
            "mesh_tools": list(self.mesh_tools),
            "whitelist": list(self.whitelist),
            "case_type": self.task_template["case_type"],
        }

    def build_task(self, manifest_sha256: str) -> dict[str, Any]:
        task = deepcopy(self.task_template)
        task["tutorial_reference"]["manifest_sha256"] = manifest_sha256
        return task


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _parse(item: dict[str, Any]) -> TutorialCapability:
    required = {
        "capability_id", "tutorial_id", "openfoam_version", "solver",
        "physics_model", "adapter_id", "required_fields", "mesh_tools",
        "whitelist", "task_template",
    }
    if set(item) != required:
        raise ValueError("教程能力字段不完整或包含未知字段")
    strings = ("capability_id", "tutorial_id", "openfoam_version", "solver",
               "physics_model", "adapter_id")
    if any(not isinstance(item[key], str) or not item[key] for key in strings):
        raise ValueError("教程能力标识必须是非空字符串")
    for key in ("required_fields", "mesh_tools", "whitelist"):
        value = item[key]
        if (not isinstance(value, list) or not value
                or any(not isinstance(entry, str) or not entry for entry in value)
                or len(value) != len(set(value))):
            raise ValueError(f"教程能力 {key} 必须是非空且无重复的字符串列表")
    if not _safe_relative(item["tutorial_id"]):
        raise ValueError("tutorial_id 必须是安全相对路径")
    if any(not _safe_relative(relative) for relative in item["whitelist"]):
        raise ValueError("教程白名单包含不安全路径")
    task = item["task_template"]
    if not isinstance(task, dict) or task.get("solver") != item["solver"]:
        raise ValueError("task_template 与教程求解器不一致")
    if task.get("physics", {}).get("flow_model") != item["physics_model"]:
        raise ValueError("task_template 与教程物理模型不一致")
    reference = task.get("tutorial_reference", {})
    if (reference.get("tutorial_id") != item["tutorial_id"]
            or reference.get("openfoam_version") != item["openfoam_version"]
            or reference.get("confirmed") is not True
            or "manifest_sha256" in reference):
        raise ValueError("task_template 的教程来源声明不一致")
    return TutorialCapability(
        **{key: item[key] for key in strings},
        required_fields=tuple(item["required_fields"]),
        mesh_tools=tuple(item["mesh_tools"]),
        whitelist=tuple(item["whitelist"]),
        task_template=deepcopy(task),
    )


def load_tutorial_capabilities() -> tuple[TutorialCapability, ...]:
    resource = files("cfd_memo_agent").joinpath("tutorial_capabilities.json")
    value = json.loads(resource.read_text(encoding="utf-8"))
    if set(value) != {"schema_version", "capabilities"} or value["schema_version"] != 1:
        raise ValueError("不支持的教程能力清单版本")
    capabilities = tuple(_parse(item) for item in value["capabilities"])
    ids = [item.capability_id for item in capabilities]
    tutorials = [item.tutorial_id for item in capabilities]
    if len(ids) != len(set(ids)) or len(tutorials) != len(set(tutorials)):
        raise ValueError("教程能力 ID 或 tutorial_id 重复")
    return capabilities


def get_tutorial_capability(tutorial_id: str) -> TutorialCapability:
    matches = [item for item in load_tutorial_capabilities()
               if item.tutorial_id == tutorial_id]
    if len(matches) != 1:
        supported = ", ".join(item.tutorial_id for item in load_tutorial_capabilities())
        raise ValueError(f"教程尚未批准：{tutorial_id!r}；当前能力：{supported}")
    return matches[0]


def list_tutorial_capabilities() -> list[dict[str, Any]]:
    return [item.public_status() for item in load_tutorial_capabilities()]


def tutorial_manifest(
    source: Path | str, capability: TutorialCapability,
) -> tuple[str, dict[str, str]]:
    requested = Path(source)
    if requested.is_symlink():
        raise ValueError("教程源目录不允许使用链接")
    root = requested.resolve()
    if not root.is_dir():
        raise ValueError(f"教程源目录不存在：{root}")
    digest = hashlib.sha256()
    file_hashes: dict[str, str] = {}
    for relative in capability.whitelist:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"教程白名单文件缺失或为链接：{relative}")
        data = path.read_bytes()
        file_hashes[relative] = hashlib.sha256(data).hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(data)
    return digest.hexdigest(), file_hashes


__all__ = [
    "TutorialCapability", "get_tutorial_capability", "list_tutorial_capabilities",
    "load_tutorial_capabilities", "tutorial_manifest",
]
