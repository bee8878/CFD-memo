"""Generated-case adapters selected by case identity, not by workflow branches."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable, Protocol

from cfd_memo_agent.capabilities import get_capability
from cfd_memo_agent.case_spec import CaseSpec


CYLINDER_BOUNDARIES = {
    "inlet": {"U": "fixedValue", "p": "zeroGradient"},
    "outlet": {"U": "zeroGradient", "p": "fixedValue"},
    "cylinder": {"U": "noSlip", "p": "zeroGradient"},
    "top": {"U": "slip", "p": "zeroGradient"},
    "bottom": {"U": "slip", "p": "zeroGradient"},
    "frontAndBack": {"U": "empty", "p": "empty"},
}

CAVITY_BOUNDARIES = {
    "movingWall": {"U": "fixedValue", "p": "zeroGradient"},
    "fixedWalls": {"U": "noSlip", "p": "zeroGradient"},
    "frontAndBack": {"U": "empty", "p": "empty"},
}

BACKWARD_STEP_BOUNDARIES = {
    "inlet": {"U": "fixedValue", "p": "zeroGradient"},
    "outlet": {"U": "zeroGradient", "p": "fixedValue"},
    "upperWall": {"U": "noSlip", "p": "zeroGradient"},
    "lowerWall": {"U": "noSlip", "p": "zeroGradient"},
    "frontAndBack": {"U": "empty", "p": "empty"},
}

PITZDAILY_TUTORIAL_ID = "incompressible/simpleFoam/pitzDaily"
CAVITY_TUTORIAL_ID = "incompressible/icoFoam/cavity/cavity"

CYLINDER_FILE_BINDINGS = {
    "inlet_velocity": "0/U",
    "pressure_boundaries": "0/p",
    "kinematic_viscosity": "constant/physicalProperties",
    "time_control": "system/controlDict",
    "mesh_geometry": "system/blockMeshDict",
    "geometry_metadata": "constant/geometry.json",
}

CAVITY_FILE_BINDINGS = {
    "lid_velocity": "0/U",
    "pressure_boundaries": "0/p",
    "kinematic_viscosity": "constant/physicalProperties",
    "time_control": "system/controlDict",
    "mesh_geometry": "system/blockMeshDict",
    "geometry_metadata": "constant/geometry.json",
}


def _problem(code: str, message: str, location: str) -> dict[str, str]:
    return {"code": code, "message": message, "location": location}


def _close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-10)


class CaseAdapter(Protocol):
    adapter_id: str
    case_type: str
    solver: str
    physics_model: str
    dimension: str
    template_id: str
    required_files: tuple[str, ...]

    @property
    def file_bindings(self) -> dict[str, str]: ...
    def support_issues(self, task: dict[str, Any]) -> list[dict[str, str]]: ...
    def physical_issues(self, task: dict[str, Any]) -> list[dict[str, str]]: ...
    def task_warnings(self, task: dict[str, Any]) -> list[dict[str, str]]: ...
    def command_plan(self, task: dict[str, Any]) -> tuple[str, ...]: ...
    def to_case_spec(self, task: dict[str, Any]) -> CaseSpec: ...
    def generate(self, task: dict[str, Any], **options) -> dict[str, Any]: ...
    def validate(self, task: dict[str, Any], case_dir: Path | str) -> dict: ...
    def collect_mesh_evidence(self, case: Path, task: dict[str, Any], default: Callable) -> dict: ...
    def collect_field_evidence(self, case: Path, task: dict[str, Any], mesh: dict, default: Callable) -> dict: ...
    def collect_physical_evidence(self, case: Path, task: dict[str, Any], default: Callable) -> dict | None: ...
    def public_status(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Cylinder2DAdapter:
    adapter_id: str = "cylinder-2d-laminar-v1"
    case_type: str = "cylinder-2d"
    solver: str = "icoFoam"
    physics_model: str = "incompressible_laminar"
    dimension: str = "2D"
    template_id: str = "cylinder-2d"
    required_files: tuple[str, ...] = (
        "system/blockMeshDict",
        "constant/physicalProperties",
        "0/U",
        "0/p",
        "system/controlDict",
        "system/fvSchemes",
        "system/fvSolution",
    )

    @property
    def boundary_conditions(self) -> dict[str, dict[str, str]]:
        return deepcopy(CYLINDER_BOUNDARIES)

    @property
    def file_bindings(self) -> dict[str, str]:
        return dict(CYLINDER_FILE_BINDINGS)

    def support_issues(self, task: dict[str, Any]) -> list[dict[str, str]]:
        problems = []
        checks = (
            (task.get("solver") == self.solver, "UNSUPPORTED_SOLVER",
             f"{self.case_type} 当前只支持 {self.solver}", "task.solver"),
            (task.get("physics", {}).get("flow_model") == self.physics_model,
             "UNSUPPORTED_PHYSICS", f"{self.case_type} 当前只支持 {self.physics_model}",
             "task.physics.flow_model"),
            (task.get("geometry", {}).get("dimension") == self.dimension,
             "UNSUPPORTED_DIMENSION", f"{self.case_type} 当前只支持 {self.dimension}",
             "task.geometry.dimension"),
            (task.get("boundary_conditions") == CYLINDER_BOUNDARIES,
             "UNSUPPORTED_BOUNDARY", "当前只支持圆柱基准的边界条件组合",
             "task.boundary_conditions"),
            (task.get("mesh", {}).get("generator", "manual-template") == "manual-template",
             "UNSUPPORTED_MESH", "当前圆柱适配器只实现 manual-template 网格",
             "task.mesh.generator"),
        )
        for valid, code, message, location in checks:
            if not valid:
                problems.append({"code": code, "message": message, "location": location})
        capability = get_capability(self.solver)
        if not capability.generated_case_support:
            problems.append({
                "code": "GENERATION_UNAVAILABLE",
                "message": f"求解器 {self.solver} 未开放 case 生成功能",
                "location": "task.solver",
            })
        return problems

    def physical_issues(self, task: dict[str, Any]) -> list[dict[str, str]]:
        geometry, physics, mesh = task["geometry"], task["physics"], task.get("mesh", {})
        diameter = geometry["cylinder_diameter"]
        problems = []
        if diameter >= min(geometry["domain_length"], geometry["domain_height"]):
            problems.append(_problem("GEOMETRY_RANGE", "圆柱直径必须小于计算域长和高", "task.geometry"))
        upstream, downstream = geometry.get("upstream_length"), geometry.get("downstream_length")
        if (upstream is None) != (downstream is None):
            problems.append(_problem("GEOMETRY_RANGE", "上游和下游长度必须同时提供", "task.geometry"))
        elif upstream is not None and not _close(upstream + downstream, geometry["domain_length"]):
            problems.append(_problem("GEOMETRY_RANGE", "上游与下游长度之和必须等于计算域长度", "task.geometry"))
        if "near_field_radius" in mesh:
            outer = min(upstream or geometry["domain_length"] / 2,
                        downstream or geometry["domain_length"] / 2,
                        geometry["domain_height"] / 2)
            if not diameter / 2 < mesh["near_field_radius"] < outer:
                problems.append(_problem("MESH_RANGE", "近场半径必须位于圆柱和外边界之间",
                                         "task.mesh.near_field_radius"))
        expected = physics["inlet_velocity"] * diameter / physics["reynolds_number"]
        if not _close(physics["kinematic_viscosity"], expected):
            problems.append(_problem("REYNOLDS_MISMATCH", "黏度不满足 nu=U*D/Re", "task.physics"))
        return problems

    def task_warnings(self, task: dict[str, Any]) -> list[dict[str, str]]:
        if "target_cells" not in task.get("mesh", {}):
            return []
        return [_problem("MESH_TARGET_ROUNDED", "目标单元数是规划值；实际数量以生成网格为准",
                         "task.mesh.target_cells")]

    def command_plan(self, task: dict[str, Any]) -> tuple[str, ...]:
        return ("blockMesh", "checkMesh", self.solver)

    def to_case_spec(self, task: dict[str, Any]) -> CaseSpec:
        return CaseSpec.from_task(task)

    def generate(self, task: dict[str, Any], **options) -> dict[str, Any]:
        from .cylinder_generation import generate_cylinder_case
        return generate_cylinder_case(task, **options)

    def validate(self, task: dict[str, Any], case_dir: Path | str) -> dict:
        from .cylinder_validation import validate_cylinder_case
        return validate_cylinder_case(task, case_dir)

    def collect_mesh_evidence(self, case, task, default):
        return default(case, task)

    def collect_field_evidence(self, case, task, mesh, default):
        return default(case, task, mesh)

    def collect_physical_evidence(self, case, task, default):
        return default(case, task)

    def public_status(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "case_type": self.case_type,
            "solver": self.solver,
            "physics_model": self.physics_model,
            "dimension": self.dimension,
            "template_id": self.template_id,
            "required_files": list(self.required_files),
            "generated_case_support": True,
        }


@dataclass(frozen=True)
class Cavity2DAdapter:
    adapter_id: str = "cavity-2d-laminar-v1"
    case_type: str = "cavity-2d"
    solver: str = "icoFoam"
    physics_model: str = "incompressible_laminar"
    dimension: str = "2D"
    template_id: str = "cavity-2d"
    required_files: tuple[str, ...] = Cylinder2DAdapter.required_files

    @property
    def boundary_conditions(self) -> dict[str, dict[str, str]]:
        return deepcopy(CAVITY_BOUNDARIES)

    @property
    def file_bindings(self) -> dict[str, str]:
        return dict(CAVITY_FILE_BINDINGS)

    def support_issues(self, task: dict[str, Any]) -> list[dict[str, str]]:
        generator = task.get("mesh", {}).get("generator", "manual-template")
        reference = task.get("tutorial_reference")
        checks = (
            (task.get("solver") == self.solver, "UNSUPPORTED_SOLVER",
             "cavity-2d 当前只支持 icoFoam", "task.solver"),
            (task.get("physics", {}).get("flow_model") == self.physics_model,
             "UNSUPPORTED_PHYSICS", "cavity-2d 当前只支持不可压缩层流",
             "task.physics.flow_model"),
            (task.get("geometry", {}).get("dimension") == self.dimension,
             "UNSUPPORTED_DIMENSION", "cavity-2d 当前只支持 2D", "task.geometry.dimension"),
            (task.get("boundary_conditions") == CAVITY_BOUNDARIES,
             "UNSUPPORTED_BOUNDARY", "当前只支持方腔基准的边界条件组合",
             "task.boundary_conditions"),
            (generator in {"manual-template", "tutorial-template", "gmsh"},
             "UNSUPPORTED_MESH", "方腔适配器只实现本地模板、受控教程或 Gmsh 网格",
             "task.mesh.generator"),
            (generator != "tutorial-template" or (
                isinstance(reference, dict)
                and reference.get("tutorial_id") == CAVITY_TUTORIAL_ID
                and reference.get("openfoam_version") == "10"
                and reference.get("confirmed") is True
            ), "TUTORIAL_NOT_APPROVED", "受控方腔网格必须来自已批准的 OpenFOAM 10 cavity",
             "task.tutorial_reference"),
            (reference is None or generator == "tutorial-template",
             "TUTORIAL_SOURCE_MISMATCH", "带教程来源的方腔必须使用 tutorial-template",
             "task.mesh.generator"),
        )
        return [_problem(code, message, location)
                for valid, code, message, location in checks if not valid]

    def physical_issues(self, task: dict[str, Any]) -> list[dict[str, str]]:
        geometry, physics = task["geometry"], task["physics"]
        expected = physics["lid_velocity"] * geometry["width"] / physics["reynolds_number"]
        if not _close(physics["kinematic_viscosity"], expected):
            return [_problem("REYNOLDS_MISMATCH",
                             "黏度不满足 nu=U_lid*L/Re（L 取方腔宽度）", "task.physics")]
        return []

    def task_warnings(self, task: dict[str, Any]) -> list[dict[str, str]]:
        return [_problem("MESH_RESOLUTION_PLANNED",
                         "方腔网格分辨率是规划值；实际数量以生成网格为准", "task.mesh")]

    def command_plan(self, task: dict[str, Any]) -> tuple[str, ...]:
        return ("blockMesh", "checkMesh", self.solver)

    def to_case_spec(self, task: dict[str, Any]) -> CaseSpec:
        reference = task.get("tutorial_reference")
        return CaseSpec.from_task(
            task,
            source_type="tutorial" if reference else "generated",
            source_path=reference["tutorial_id"] if reference else None,
        )

    def generate(self, task: dict[str, Any], **options) -> dict[str, Any]:
        from .cavity_generation import generate_cavity_case
        return generate_cavity_case(task, **options)

    def validate(self, task: dict[str, Any], case_dir: Path | str) -> dict:
        from .cavity_validation import validate_cavity_case
        return validate_cavity_case(task, case_dir)

    def collect_mesh_evidence(self, case, task, default):
        from .cavity_evidence import cavity_mesh_evidence
        return cavity_mesh_evidence(case, task)

    def collect_field_evidence(self, case, task, mesh, default):
        return default(case, task, mesh)

    def collect_physical_evidence(self, case, task, default):
        return None

    def public_status(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "case_type": self.case_type,
            "solver": self.solver,
            "physics_model": self.physics_model,
            "dimension": self.dimension,
            "template_id": self.template_id,
            "required_files": list(self.required_files),
            "generated_case_support": True,
        }


@dataclass(frozen=True)
class BackwardStep2DAdapter:
    """One reviewed tutorial-derived benchmark; not a generic tutorial adapter."""

    adapter_id: str = "backward-step-2d-rans-pitzdaily-v1"
    case_type: str = "backward-step-2d"
    solver: str = "simpleFoam"
    physics_model: str = "incompressible_rans"
    dimension: str = "2D"
    template_id: str = PITZDAILY_TUTORIAL_ID
    required_files: tuple[str, ...] = (
        "system/blockMeshDict", "constant/physicalProperties",
        "constant/momentumTransport", "0/U", "0/p", "0/k", "0/epsilon",
        "0/nut", "system/controlDict", "system/fvSchemes", "system/fvSolution",
    )

    @property
    def boundary_conditions(self) -> dict[str, dict[str, str]]:
        return deepcopy(BACKWARD_STEP_BOUNDARIES)

    @property
    def file_bindings(self) -> dict[str, str]:
        return {
            "inlet_velocity": "0/U",
            "kinematic_viscosity": "constant/physicalProperties",
            "time_control": "system/controlDict",
            "tutorial_geometry": "system/blockMeshDict",
            "tutorial_provenance": "constant/tutorial-provenance.json",
        }

    def support_issues(self, task: dict[str, Any]) -> list[dict[str, str]]:
        reference = task.get("tutorial_reference", {})
        checks = (
            (task.get("solver") == self.solver, "UNSUPPORTED_SOLVER",
             "backward-step-2d 当前只支持 simpleFoam", "task.solver"),
            (task.get("physics", {}).get("flow_model") == self.physics_model,
             "UNSUPPORTED_PHYSICS", "后台阶基准当前只支持不可压缩 RANS",
             "task.physics.flow_model"),
            (task.get("geometry", {}).get("dimension") == self.dimension,
             "UNSUPPORTED_DIMENSION", "后台阶基准当前只支持 2D", "task.geometry.dimension"),
            (task.get("boundary_conditions") == BACKWARD_STEP_BOUNDARIES,
             "UNSUPPORTED_BOUNDARY", "边界条件必须保持官方 pitzDaily 基准组合",
             "task.boundary_conditions"),
            (task.get("mesh", {}).get("generator") == "tutorial-template",
             "UNSUPPORTED_MESH", "后台阶基准只允许受控教程网格",
             "task.mesh.generator"),
            (reference.get("tutorial_id") == PITZDAILY_TUTORIAL_ID,
             "TUTORIAL_NOT_APPROVED", "只批准 OpenFOAM 10 simpleFoam/pitzDaily",
             "task.tutorial_reference.tutorial_id"),
            (reference.get("openfoam_version") == "10", "TUTORIAL_VERSION",
             "教程版本必须为 Foundation OpenFOAM 10",
             "task.tutorial_reference.openfoam_version"),
            (reference.get("confirmed") is True, "TUTORIAL_NOT_CONFIRMED",
             "教程提案必须经过显式确认", "task.tutorial_reference.confirmed"),
        )
        return [_problem(code, message, location)
                for valid, code, message, location in checks if not valid]

    def physical_issues(self, task: dict[str, Any]) -> list[dict[str, str]]:
        geometry, physics = task["geometry"], task["physics"]
        expected = (physics["inlet_velocity"] * geometry["reference_length"]
                    / physics["reynolds_number"])
        if not _close(physics["kinematic_viscosity"], expected):
            return [_problem(
                "REYNOLDS_MISMATCH",
                "黏度不满足 nu=U*L_ref/Re（L_ref 为入口高度）",
                "task.physics",
            )]
        return []

    def task_warnings(self, task: dict[str, Any]) -> list[dict[str, str]]:
        return [_problem(
            "TUTORIAL_BENCHMARK_ONLY",
            "当前只复现官方 pitzDaily 工程流程，尚未完成物理精度验收",
            "task.tutorial_reference",
        )]

    def command_plan(self, task: dict[str, Any]) -> tuple[str, ...]:
        return ("blockMesh", "checkMesh", self.solver)

    def to_case_spec(self, task: dict[str, Any]) -> CaseSpec:
        return CaseSpec.from_task(
            task, source_type="tutorial",
            source_path=task["tutorial_reference"]["tutorial_id"],
        )

    def generate(self, task: dict[str, Any], **options) -> dict[str, Any]:
        from .backward_step_generation import generate_backward_step_case
        return generate_backward_step_case(task, **options)

    def validate(self, task: dict[str, Any], case_dir: Path | str) -> dict:
        from .backward_step_validation import validate_backward_step_case
        return validate_backward_step_case(task, case_dir)

    def collect_mesh_evidence(self, case, task, default):
        from .backward_step_evidence import backward_step_mesh_evidence
        return backward_step_mesh_evidence(case, task)

    def collect_field_evidence(self, case, task, mesh, default):
        from .backward_step_evidence import backward_step_field_evidence
        return backward_step_field_evidence(case, task, mesh)

    def collect_physical_evidence(self, case, task, default):
        return None

    def public_status(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "case_type": self.case_type,
            "solver": self.solver,
            "physics_model": self.physics_model,
            "dimension": self.dimension,
            "template_id": self.template_id,
            "required_files": list(self.required_files),
            "generated_case_support": True,
        }


_ADAPTERS: tuple[CaseAdapter, ...] = (
    Cylinder2DAdapter(), Cavity2DAdapter(), BackwardStep2DAdapter(),
)


def get_case_adapter(task: dict[str, Any]) -> CaseAdapter:
    if not isinstance(task, dict):
        raise ValueError("task 必须是对象")
    case_type = task.get("case_type")
    for adapter in _ADAPTERS:
        if adapter.case_type == case_type:
            return adapter
    supported = ", ".join(adapter.case_type for adapter in _ADAPTERS)
    raise ValueError(f"不支持 case_type {case_type!r}；当前适配器：{supported}")


def list_case_adapters() -> list[dict[str, Any]]:
    return [adapter.public_status() for adapter in _ADAPTERS]


__all__ = [
    "BACKWARD_STEP_BOUNDARIES", "CAVITY_BOUNDARIES", "CYLINDER_BOUNDARIES",
    "CAVITY_TUTORIAL_ID", "PITZDAILY_TUTORIAL_ID", "BackwardStep2DAdapter", "CaseAdapter",
    "Cavity2DAdapter", "Cylinder2DAdapter",
    "get_case_adapter", "list_case_adapters",
]
