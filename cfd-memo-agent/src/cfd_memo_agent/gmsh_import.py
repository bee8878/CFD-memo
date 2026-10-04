"""Create an isolated, auditable run from an external Gmsh mesh."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
from typing import Any

from cfd_memo_agent.case_spec import CaseSpec
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.gmsh import prepare_gmsh, sha256_bytes
from cfd_memo_agent.mesh_capabilities import mesh_spec_from_task, save_mesh_spec
from cfd_memo_agent.validator import validate_case, validate_task
from cfd_memo_agent.validator.foam import read_json


def _mapping(value: Path | str | dict[str, str] | None, boundaries: set[str]) -> dict[str, str]:
    if value is None:
        return {name: name for name in sorted(boundaries)}
    raw: Any = read_json(Path(value)) if not isinstance(value, dict) else value
    if (not isinstance(raw, dict) or not raw
            or any(not isinstance(key, str) or not isinstance(item, str)
                   for key, item in raw.items())):
        raise ValueError("边界映射必须是非空的 JSON 字符串对象")
    return dict(raw)


def import_gmsh_mesh(
    source: Path | str, *, task_path: Path | str | dict[str, Any],
    boundary_map: Path | str | dict[str, str] | None = None,
    runs_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Validate, copy and bind one external mesh to a generated cavity case."""
    original_task = read_json(Path(task_path)) if not isinstance(task_path, dict) else task_path
    if not isinstance(original_task, dict):
        raise ValueError("task JSON 顶层必须是对象")
    task = deepcopy(original_task)
    if task.get("case_type") != "cavity-2d":
        raise ValueError("J2 首版 Gmsh 导入仅支持已注册的 cavity-2d case")
    if task.get("mesh", {}).get("generator") != "manual-template":
        raise ValueError("Gmsh 导入的基础 task 必须使用 manual-template")
    if not validate_task(task)["task_valid"]:
        raise ValueError("Gmsh 导入前的基础 task 未通过验证")
    boundaries = set(task["boundary_conditions"])
    mapping = _mapping(boundary_map, boundaries)
    cells = task["mesh"]["cells_x"] * task["mesh"]["cells_y"]
    transformed, inspection, source_hash = prepare_gmsh(
        source, boundary_map=mapping, expected_boundaries=boundaries,
        geometry=task["geometry"], expected_cells=cells,
    )

    result = generate_case(
        task, runs_dir=Path(runs_dir) if runs_dir is not None else None,
    )
    run = Path(result["run_path"])
    case = Path(result["case_path"])
    try:
        mesh_dir = case / "mesh"
        mesh_dir.mkdir()
        target = mesh_dir / "mesh.msh"
        target.write_text(transformed, encoding="utf-8")
        copied_hash = sha256_bytes(target.read_bytes())
        task["mesh"].update({
            "generator": "gmsh", "source_path": "mesh/mesh.msh",
            "boundary_map": mapping, "source_sha256": source_hash,
            "copied_sha256": copied_hash,
        })
        (case / "system/blockMeshDict").unlink()
        metadata = {
            "schema_version": 1, "source_type": "external-gmsh",
            "source_path": str(Path(source).resolve()),
            "source_sha256": source_hash, "copied_sha256": copied_hash,
            "boundary_map": mapping, "inspection": inspection,
            "scripts_executed": False, "physical_validated": False,
        }
        (case / "mesh-import.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (run / "task.json").write_text(
            json.dumps(task, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        spec = CaseSpec.from_task(task)
        (run / "case-spec.json").write_text(
            json.dumps(spec.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (run / "mesh-spec.json").unlink()
        save_mesh_spec(run / "mesh-spec.json", mesh_spec_from_task(task))
        validation = validate_case(task, case)
        result.update({
            "status": "mesh_imported" if validation["config_valid"] else "invalid",
            "mesh_spec_path": str(run / "mesh-spec.json"),
            "mesh_import_path": str(case / "mesh-import.json"),
            "source_sha256": source_hash, "copied_sha256": copied_hash,
            "boundary_map": mapping, "mesh_inspection": inspection,
            "validation": validation, "physical_validated": False,
            "warnings": [
                "外部 Gmsh 网格已通过静态来源检查；仍须实际执行 gmshToFoam/checkMesh，"
                "且工程完成不代表物理准确性已验证。"
            ],
        })
        result["generated_files"] = [
            item for item in result["generated_files"] if item != "system/blockMeshDict"
        ] + ["mesh/mesh.msh", "mesh-import.json"]
        (run / "generation.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return result
    except BaseException:
        if run.exists():
            shutil.rmtree(run)
        raise


__all__ = ["import_gmsh_mesh"]
