"""The shared description used by generated and imported OpenFOAM cases."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from cfd_memo_agent.capabilities import get_capability


@dataclass(frozen=True)
class CaseSpec:
    schema_version: int
    task_id: str
    source_type: str
    case_type: str
    solver: str
    physics_model: str
    dimension: str
    mesh_source: str
    fields: tuple[str, ...]
    boundaries: dict[str, dict[str, str]]
    time_control: dict[str, float]
    source_path: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("CaseSpec schema_version 必须为 1")
        if self.source_type not in {"generated", "imported", "tutorial"}:
            raise ValueError("CaseSpec source_type 必须为 generated、imported 或 tutorial")
        if self.dimension not in {"2D", "3D", "unknown"}:
            raise ValueError("CaseSpec dimension 必须为 2D、3D 或 unknown")
        if self.mesh_source not in {"blockMesh", "polyMesh", "unknown"}:
            raise ValueError("CaseSpec mesh_source 不受支持")
        capability = get_capability(self.solver)
        if self.physics_model != capability.physics_model:
            raise ValueError("CaseSpec 物理模型与求解器能力不一致")
        missing = set(capability.required_fields) - set(self.fields)
        if missing:
            raise ValueError(f"CaseSpec 缺少求解字段：{', '.join(sorted(missing))}")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["fields"] = list(self.fields)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CaseSpec":
        data = dict(value)
        data["fields"] = tuple(data.get("fields", ()))
        return cls(**data)

    @classmethod
    def from_task(
        cls, task: dict[str, Any], *, source_type: str = "generated",
        source_path: str | None = None,
    ) -> "CaseSpec":
        capability = get_capability(task["solver"])
        mesh = task.get("mesh", {})
        generator = mesh.get("generator", "blockMesh")
        mesh_source = (
            "blockMesh"
            if generator in {"blockMesh", "manual-template", "tutorial-template"}
            else "unknown"
        )
        return cls(
            schema_version=1,
            task_id=task["task_id"],
            source_type=source_type,
            case_type=task["case_type"],
            solver=task["solver"],
            physics_model=task["physics"]["flow_model"],
            dimension=task["geometry"].get("dimension", "unknown"),
            mesh_source=mesh_source,
            fields=capability.required_fields,
            boundaries={name: dict(fields) for name, fields in task["boundary_conditions"].items()},
            time_control={
                "start_time": float(task["time_control"]["start_time"]),
                "end_time": float(task["time_control"]["end_time"]),
                "delta_t": float(task["time_control"]["delta_t"]),
                "write_interval": float(task["time_control"]["write_interval"]),
            },
            source_path=source_path,
        )


__all__ = ["CaseSpec"]
