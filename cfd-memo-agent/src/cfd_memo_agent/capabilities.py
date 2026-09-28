"""Explicit OpenFOAM capabilities; unsupported requests fail closed."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SolverCapability:
    name: str
    executable: str
    physics_model: str
    time_mode: str
    required_fields: tuple[str, ...]
    required_files: tuple[str, ...]
    generated_case_support: bool
    imported_case_support: bool

    def to_dict(self) -> dict:
        value = asdict(self)
        value["required_fields"] = list(self.required_fields)
        value["required_files"] = list(self.required_files)
        return value


_COMMON_FILES = (
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
)

_CAPABILITIES = {
    "icoFoam": SolverCapability(
        name="icoFoam",
        executable="icoFoam",
        physics_model="incompressible_laminar",
        time_mode="transient",
        required_fields=("U", "p"),
        required_files=_COMMON_FILES,
        generated_case_support=True,
        imported_case_support=True,
    ),
    "simpleFoam": SolverCapability(
        name="simpleFoam",
        executable="simpleFoam",
        physics_model="incompressible_rans",
        time_mode="steady",
        required_fields=("U", "p"),
        required_files=_COMMON_FILES,
        generated_case_support=False,
        imported_case_support=False,
    ),
}


def get_capability(solver: str) -> SolverCapability:
    """Return one supported solver capability or explain the missing capability."""
    try:
        return _CAPABILITIES[solver]
    except KeyError:
        supported = ", ".join(sorted(_CAPABILITIES))
        raise ValueError(f"不支持求解器 {solver!r}；当前能力：{supported}") from None


def list_capabilities() -> list[dict]:
    return [_CAPABILITIES[name].to_dict() for name in sorted(_CAPABILITIES)]


__all__ = ["SolverCapability", "get_capability", "list_capabilities"]
