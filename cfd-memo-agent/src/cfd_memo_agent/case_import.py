"""Inspect and import existing OpenFOAM cases without trusting their scripts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from cfd_memo_agent.capabilities import get_capability
from cfd_memo_agent.case_spec import CaseSpec
from cfd_memo_agent.validator.foam import parse_foam, read_json
from cfd_memo_agent.validator.task import issue, new_report

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MESH_FILES = ("boundary", "faces", "neighbour", "owner", "points")


def _single(data: dict, key: str, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], str):
        raise ValueError(f"{location} 缺少单值配置项 {key}")
    return value[0]


def _finite_number(data: dict, key: str, location: str) -> float:
    text = _single(data, key, location)
    try:
        value = float(text)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{location}.{key} 必须是数字") from None
    if not math.isfinite(value):
        raise ValueError(f"{location}.{key} 必须是有限数字")
    return value


def _read_dictionary(path: Path) -> dict:
    try:
        return parse_foam(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"无法读取 OpenFOAM 字典 {path}: {exc}") from exc


def _initial_directory(case_dir: Path, start_time: float) -> Path:
    candidates: list[tuple[float, Path]] = []
    for path in case_dir.iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if math.isfinite(value) and math.isclose(value, start_time, rel_tol=1e-9, abs_tol=1e-12):
            candidates.append((value, path))
    if len(candidates) != 1:
        raise ValueError(f"找不到唯一的初始时间目录 startTime={start_time:g}")
    return candidates[0][1]


def _field_boundaries(path: Path, expected_object: str) -> dict[str, str]:
    data = _read_dictionary(path)
    header = data.get("FoamFile")
    if not isinstance(header, dict) or _single(header, "object", str(path)) != expected_object:
        raise ValueError(f"字段文件对象名与文件名不一致：{path}")
    boundary = data.get("boundaryField")
    if not isinstance(boundary, dict) or not boundary:
        raise ValueError(f"字段缺少 boundaryField：{path}")
    result = {}
    for name, config in boundary.items():
        if not isinstance(config, dict):
            raise ValueError(f"边界配置损坏：{path}:{name}")
        result[name] = _single(config, "type", f"{path}:{name}")
    return result


def inspect_case(case_dir: Path | str, *, task_id: str = "imported-case") -> CaseSpec:
    """Read a supported case into one generic description without changing it."""
    root = Path(case_dir).resolve()
    if not root.is_dir():
        raise ValueError(f"case 目录不存在：{root}")
    for folder in ("0", "constant", "system"):
        if not (root / folder).is_dir():
            raise ValueError(f"缺少 OpenFOAM 目录：{folder}/")

    controls = _read_dictionary(root / "system/controlDict")
    solver = _single(controls, "application", "system/controlDict")
    capability = get_capability(solver)
    if not capability.imported_case_support:
        raise ValueError(f"求解器 {solver} 尚不支持导入")

    start_time = _finite_number(controls, "startTime", "system/controlDict")
    initial = _initial_directory(root, start_time)
    field_boundaries = {
        field: _field_boundaries(initial / field, field)
        for field in capability.required_fields
    }
    boundary_names = set(next(iter(field_boundaries.values())))
    for field, boundaries in field_boundaries.items():
        if set(boundaries) != boundary_names:
            raise ValueError(f"字段 {field} 的边界名称与其他字段不一致")
    boundaries = {
        name: {field: field_boundaries[field][name] for field in capability.required_fields}
        for name in sorted(boundary_names)
    }
    dimension = "2D" if any(
        kind == "empty" for values in boundaries.values() for kind in values.values()
    ) else "unknown"
    poly_mesh = root / "constant/polyMesh"
    if all((poly_mesh / name).is_file() for name in MESH_FILES):
        mesh_source = "polyMesh"
    elif (root / "system/blockMeshDict").is_file():
        mesh_source = "blockMesh"
    else:
        mesh_source = "unknown"

    return CaseSpec(
        schema_version=1,
        task_id=task_id,
        source_type="imported",
        case_type="imported-openfoam-case",
        solver=solver,
        physics_model=capability.physics_model,
        dimension=dimension,
        mesh_source=mesh_source,
        fields=capability.required_fields,
        boundaries=boundaries,
        time_control={
            "start_time": start_time,
            "end_time": _finite_number(controls, "endTime", "system/controlDict"),
            "delta_t": _finite_number(controls, "deltaT", "system/controlDict"),
            "write_interval": _finite_number(controls, "writeInterval", "system/controlDict"),
        },
        source_path=str(root),
    )


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for folder in ("0", "constant", "system"):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                digest.update(path.read_bytes())
    return digest.hexdigest()


def _reject_links(root: Path) -> None:
    if root.is_symlink():
        raise ValueError("不允许导入符号链接 case")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"不允许导入符号链接：{path.relative_to(root)}")


def validate_imported_case(spec: CaseSpec | dict[str, Any], case_dir: Path | str) -> dict:
    """Validate an imported case against its saved CaseSpec."""
    report = new_report()
    report["task_valid"] = True
    root = Path(case_dir)
    try:
        expected = spec if isinstance(spec, CaseSpec) else CaseSpec.from_dict(spec)
        actual = inspect_case(root, task_id=expected.task_id)
        capability = get_capability(expected.solver)
        for relative in capability.required_files:
            if not (root / relative).is_file():
                report["errors"].append(issue("FILE_MISSING", "缺少求解器必要文件", relative))
        comparable = ("solver", "physics_model", "mesh_source", "fields", "boundaries", "time_control")
        for name in comparable:
            if getattr(actual, name) != getattr(expected, name):
                report["errors"].append(issue(
                    "CASE_SPEC_MISMATCH", f"导入 case 与 CaseSpec 的 {name} 不一致", name))
        if expected.mesh_source == "unknown":
            report["runtime_blockers"].append(issue(
                "MESH_SOURCE_UNKNOWN", "没有可用 polyMesh 或 blockMeshDict", "case"))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        report["errors"].append(issue("IMPORTED_CASE_INVALID", str(exc), str(root)))
    report["config_valid"] = not report["errors"]
    report["mesh_verified"] = False
    return report


def load_case_spec(path: Path | str) -> CaseSpec:
    return CaseSpec.from_dict(read_json(Path(path)))


def import_case(source: Path | str, *, runs_dir: Path | None = None) -> dict[str, Any]:
    """Create immutable-by-convention original and writable case copies."""
    source_path = Path(source).resolve()
    _reject_links(source_path)
    run_id = f"import-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    spec = inspect_case(source_path, task_id=run_id)
    destination = (Path(runs_dir) if runs_dir is not None else PROJECT_ROOT / "cases/runs").resolve()
    if source_path == destination or source_path in destination.parents or destination in source_path.parents:
        raise ValueError("导入输出目录不能与源 case 相同或互相包含")
    destination.mkdir(parents=True, exist_ok=True)
    run_dir = destination / run_id
    if run_dir.exists():
        raise FileExistsError(f"导入目录已存在：{run_dir}")
    source_hash = _tree_hash(source_path)
    try:
        run_dir.mkdir()
        shutil.copytree(source_path, run_dir / "original")
        shutil.copytree(source_path, run_dir / "case")
        (run_dir / "case-spec.json").write_text(
            json.dumps(spec.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = {
            "status": "imported",
            "run_id": run_id,
            "run_path": str(run_dir),
            "original_path": str(run_dir / "original"),
            "case_path": str(run_dir / "case"),
            "case_spec_path": str(run_dir / "case-spec.json"),
            "source_sha256": source_hash,
            "validation": validate_imported_case(spec, run_dir / "case"),
            "scripts_executed": False,
        }
        (run_dir / "import.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
    except BaseException:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        raise


__all__ = [
    "import_case", "inspect_case", "load_case_spec", "validate_imported_case",
]
