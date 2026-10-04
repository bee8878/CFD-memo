"""Trusted mesh capabilities and persisted mesh execution plans."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MeshCapability:
    name: str
    mesh_format: str
    source_types: tuple[str, ...]
    required_inputs: tuple[str, ...]
    preparation_commands: tuple[str, ...]
    verification_command: str
    execution_ready: bool
    gap: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("source_types", "required_inputs", "preparation_commands"):
            value[key] = list(value[key])
        return value


BLOCK_MESH = MeshCapability(
    name="blockMesh",
    mesh_format="blockMesh",
    source_types=("generated-template", "tutorial-template", "imported-blockMesh"),
    required_inputs=("system/blockMeshDict",),
    preparation_commands=("blockMesh",),
    verification_command="checkMesh",
    execution_ready=True,
)
POLY_MESH = MeshCapability(
    name="polyMesh",
    mesh_format="polyMesh",
    source_types=("existing-polyMesh",),
    required_inputs=tuple(
        f"constant/polyMesh/{name}"
        for name in ("boundary", "faces", "neighbour", "owner", "points")
    ),
    preparation_commands=(),
    verification_command="checkMesh",
    execution_ready=True,
)
GMSH = MeshCapability(
    name="gmsh",
    mesh_format="gmsh",
    source_types=("external-gmsh",),
    required_inputs=("mesh/mesh.msh", "mesh-import.json"),
    preparation_commands=("gmshToFoam",),
    verification_command="checkMesh",
    execution_ready=True,
)
UNKNOWN = MeshCapability(
    name="unknown",
    mesh_format="unknown",
    source_types=("unknown",),
    required_inputs=(),
    preparation_commands=(),
    verification_command="checkMesh",
    execution_ready=False,
    gap="无法识别网格来源，不能生成可信执行计划。",
)

_REGISTRY = {item.name: item for item in (BLOCK_MESH, POLY_MESH, GMSH, UNKNOWN)}


@dataclass(frozen=True)
class MeshSpec:
    schema_version: int
    source_type: str
    capability_id: str
    mesh_format: str
    source_path: str | None
    required_inputs: tuple[str, ...]
    preparation_commands: tuple[str, ...]
    verification_command: str
    execution_ready: bool
    physical_validated: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("MeshSpec schema_version 必须为 1")
        capability = get_mesh_capability(self.capability_id)
        if self.source_type not in capability.source_types:
            raise ValueError("MeshSpec 来源类型与网格能力不一致")
        expected = (
            capability.mesh_format,
            capability.required_inputs,
            capability.preparation_commands,
            capability.verification_command,
            capability.execution_ready,
        )
        actual = (
            self.mesh_format,
            self.required_inputs,
            self.preparation_commands,
            self.verification_command,
            self.execution_ready,
        )
        if actual != expected:
            raise ValueError("MeshSpec 执行计划与可信能力注册表不一致")
        if self.physical_validated:
            raise ValueError("网格来源计划不能宣称物理验证完成")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("required_inputs", "preparation_commands"):
            value[key] = list(value[key])
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MeshSpec":
        data = dict(value)
        data["required_inputs"] = tuple(data.get("required_inputs", ()))
        data["preparation_commands"] = tuple(data.get("preparation_commands", ()))
        return cls(**data)


def get_mesh_capability(name: str) -> MeshCapability:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"不支持网格能力：{name}") from None


def list_mesh_capabilities() -> list[dict[str, Any]]:
    return [capability.to_dict() for capability in _REGISTRY.values()]


def _build(capability_id: str, source_type: str, source_path: str | None = None) -> MeshSpec:
    capability = get_mesh_capability(capability_id)
    return MeshSpec(
        schema_version=1,
        source_type=source_type,
        capability_id=capability.name,
        mesh_format=capability.mesh_format,
        source_path=source_path,
        required_inputs=capability.required_inputs,
        preparation_commands=capability.preparation_commands,
        verification_command=capability.verification_command,
        execution_ready=capability.execution_ready,
    )


def mesh_spec_from_task(task: dict[str, Any]) -> MeshSpec:
    generator = task.get("mesh", {}).get("generator", "blockMesh")
    if generator == "tutorial-template":
        source = task.get("tutorial_reference", {}).get("tutorial_id")
        return _build("blockMesh", "tutorial-template", source)
    if generator in {"blockMesh", "manual-template"}:
        return _build("blockMesh", "generated-template")
    if generator == "gmsh":
        return _build("gmsh", "external-gmsh", task.get("mesh", {}).get("source_path"))
    return _build("unknown", "unknown")


def mesh_spec_from_case_spec(case_spec: Any) -> MeshSpec:
    source = getattr(case_spec, "mesh_source")
    path = getattr(case_spec, "source_path", None)
    if source == "blockMesh":
        return _build("blockMesh", "imported-blockMesh", path)
    if source == "polyMesh":
        return _build("polyMesh", "existing-polyMesh", path)
    return _build("unknown", "unknown", path)


def external_gmsh_spec(source_path: str) -> MeshSpec:
    return _build("gmsh", "external-gmsh", source_path)


def save_mesh_spec(path: Path | str, spec: MeshSpec) -> Path:
    target = Path(path)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(spec.to_dict(), handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return target


def load_mesh_spec(path: Path | str) -> MeshSpec:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("mesh-spec.json 顶层必须是对象")
    return MeshSpec.from_dict(value)


def resolve_mesh_spec(run_root: Path, expected: MeshSpec) -> MeshSpec:
    path = run_root / "mesh-spec.json"
    if not path.is_file():
        return expected
    saved = load_mesh_spec(path)
    if saved != expected:
        raise ValueError("保存的 MeshSpec 与任务或 CaseSpec 推导结果不一致")
    return saved


def missing_mesh_inputs(spec: MeshSpec, case_dir: Path | str) -> list[str]:
    root = Path(case_dir)
    return [relative for relative in spec.required_inputs if not (root / relative).is_file()]


def mesh_command_plan(spec: MeshSpec, solver: str) -> tuple[str, ...]:
    capability = get_mesh_capability(spec.capability_id)
    if not capability.execution_ready:
        raise ValueError(capability.gap or "网格能力尚不可执行")
    return (*spec.preparation_commands, spec.verification_command, solver)


def mesh_stage_arguments(
    spec: MeshSpec, stage: str, case_dir: Path | str, *, path_transform=str,
) -> tuple[str, ...]:
    """Return only registry-owned arguments; persisted JSON cannot add flags."""
    case = Path(case_dir)
    if stage == "gmshToFoam":
        if spec.capability_id != "gmsh":
            raise ValueError("gmshToFoam 只能由 Gmsh 网格能力调用")
        return (path_transform(case / "mesh/mesh.msh"), "-case", path_transform(case))
    if stage in {*spec.preparation_commands, spec.verification_command}:
        return ("-case", path_transform(case))
    return ("-case", path_transform(case))


__all__ = [
    "MeshCapability", "MeshSpec", "external_gmsh_spec", "get_mesh_capability",
    "list_mesh_capabilities", "load_mesh_spec", "mesh_command_plan", "mesh_stage_arguments",
    "mesh_spec_from_case_spec", "mesh_spec_from_task", "missing_mesh_inputs",
    "resolve_mesh_spec", "save_mesh_spec",
]
