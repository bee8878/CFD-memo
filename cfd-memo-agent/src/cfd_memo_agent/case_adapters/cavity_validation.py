"""Static validation for the registered lid-driven cavity template."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from cfd_memo_agent.validator.foam import Group, parse_foam, read_json
from cfd_memo_agent.validator.task import close, issue, validate_task
from cfd_memo_agent.tutorial_capabilities import get_tutorial_capability

REQUIRED = {
    "system/blockMeshDict": ("blockMeshDict", "dictionary"),
    "constant/physicalProperties": ("physicalProperties", "dictionary"),
    "0/U": ("U", "volVectorField"),
    "0/p": ("p", "volScalarField"),
    "system/controlDict": ("controlDict", "dictionary"),
    "system/fvSchemes": ("fvSchemes", "dictionary"),
    "system/fvSolution": ("fvSolution", "dictionary"),
}


def _one(doc: dict, key: str) -> Any:
    value = doc.get(key)
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError(f"配置项 {key} 必须且只能出现一个值")
    return value[0]


def _number(doc: dict, key: str) -> float:
    value = _one(doc, key)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"配置项 {key} 必须是数字") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"配置项 {key} 必须是有限数字")
    return parsed


def _group(doc: dict, key: str, opening: str = "(") -> Group:
    value = _one(doc, key)
    if not isinstance(value, Group) or value.opening != opening:
        raise ValueError(f"配置项 {key} 的括号结构错误")
    return value


def _header(doc: dict, obj: str, cls: str) -> None:
    header = doc.get("FoamFile")
    if not isinstance(header, dict):
        raise ValueError("缺少 FoamFile 头")
    if header.get("format") != ["ascii"] or header.get("object") != [obj]:
        raise ValueError("FoamFile format/object 不一致")
    if header.get("class") != [cls]:
        raise ValueError("FoamFile class 不一致")


def _boundaries(doc: dict) -> dict:
    value = doc.get("boundaryField")
    if not isinstance(value, dict):
        raise ValueError("缺少 boundaryField")
    result = {}
    for name, body in value.items():
        if not isinstance(body, dict) or not isinstance(body.get("type"), list):
            raise ValueError(f"边界 {name} 缺少 type")
        result[name] = _one(body, "type")
    return result


def _dimensions(doc: dict, expected: list[str]) -> None:
    group = _group(doc, "dimensions", "[")
    if group.items != expected:
        raise ValueError("物理量纲不正确")


def _validate_mesh(doc: dict, task: dict[str, Any]) -> None:
    geometry, mesh = task["geometry"], task["mesh"]
    if not close(_number(doc, "convertToMeters"), 1):
        raise ValueError("convertToMeters 必须为 1")
    vertices = _group(doc, "vertices").items
    if len(vertices) != 8 or not all(isinstance(value, Group) for value in vertices):
        raise ValueError("方腔网格必须有八个顶点")
    expected = [
        (0, 0, 0), (geometry["width"], 0, 0),
        (geometry["width"], geometry["height"], 0), (0, geometry["height"], 0),
        (0, 0, geometry["depth"]), (geometry["width"], 0, geometry["depth"]),
        (geometry["width"], geometry["height"], geometry["depth"]),
        (0, geometry["height"], geometry["depth"]),
    ]
    for actual, wanted in zip(vertices, expected):
        if len(actual.items) != 3 or any(
            not close(float(value), target) for value, target in zip(actual.items, wanted)
        ):
            raise ValueError("方腔网格顶点与任务几何不一致")
    blocks = _group(doc, "blocks").items
    if len(blocks) != 5 or blocks[0] != "hex" or not isinstance(blocks[2], Group):
        raise ValueError("方腔必须使用一个 hex block")
    if blocks[2].items != [str(mesh["cells_x"]), str(mesh["cells_y"]), "1"]:
        raise ValueError("方腔网格分辨率与任务不一致")
    boundary = _group(doc, "boundary").items
    if len(boundary) != 6:
        raise ValueError("方腔网格必须有三个边界")
    patches = {}
    for name, body in zip(boundary[::2], boundary[1::2]):
        if not isinstance(name, str) or not isinstance(body, dict):
            raise ValueError("网格边界结构损坏")
        faces = _group(body, "faces").items
        if not faces:
            raise ValueError(f"网格边界 {name} 为空")
        patches[name] = _one(body, "type")
    if patches != {"movingWall": "wall", "fixedWalls": "wall", "frontAndBack": "empty"}:
        raise ValueError("网格边界名称或类型不正确")


def validate_cavity_case(task: dict[str, Any], case_dir: Path | str) -> dict:
    report = validate_task(task)
    if not report["task_valid"]:
        return report
    root = Path(case_dir)
    report["runtime_blockers"] = [issue(
        "MESH_NOT_VERIFIED", "尚未执行本次 case 的 blockMesh/checkMesh，不能据此证明网格可运行",
        "system/blockMeshDict")]
    reference = task.get("tutorial_reference")
    if reference is not None:
        capability = get_tutorial_capability(reference["tutorial_id"])
        expected_files = {
            *capability.whitelist, "constant/geometry.json",
            "constant/tutorial-provenance.json",
        }
        try:
            actual_files = set()
            for path in root.rglob("*"):
                if path.is_symlink():
                    raise ValueError(f"不允许教程副本包含链接：{path.relative_to(root)}")
                if path.is_file():
                    actual_files.add(path.relative_to(root).as_posix())
            unexpected = actual_files - expected_files
            missing = expected_files - actual_files
            if unexpected:
                raise ValueError("包含非白名单文件：" + ", ".join(sorted(unexpected)))
            if missing:
                raise ValueError("缺少白名单文件：" + ", ".join(sorted(missing)))
        except (OSError, ValueError) as exc:
            report["errors"].append(issue("TUTORIAL_WHITELIST", str(exc), str(root)))
            return report
    documents = {}
    for relative, (obj, cls) in REQUIRED.items():
        path = root / relative
        try:
            document = parse_foam(path.read_text(encoding="utf-8"))
            _header(document, obj, cls)
            documents[relative] = document
        except (OSError, UnicodeError, ValueError) as exc:
            report["errors"].append(issue("CONFIG_READ", f"配置缺失或格式错误：{exc}", relative))
    if report["errors"]:
        return report

    expected_boundaries = task["boundary_conditions"]
    checks = []
    try:
        u = documents["0/U"]
        _dimensions(u, ["0", "1", "-1", "0", "0", "0", "0"])
        if _boundaries(u) != {name: fields["U"] for name, fields in expected_boundaries.items()}:
            raise ValueError("U 边界类型与任务不一致")
        moving = u["boundaryField"]["movingWall"]
        value = moving.get("value", [])
        if (len(value) != 2 or value[0] != "uniform" or not isinstance(value[1], Group)
                or len(value[1].items) != 3
                or not close(float(value[1].items[0]), task["physics"]["lid_velocity"])
                or value[1].items[1:] != ["0", "0"]):
            raise ValueError("顶盖速度与任务不一致")
    except (KeyError, TypeError, ValueError) as exc:
        checks.append(("VALUE_MISMATCH", str(exc), "0/U"))
    try:
        pressure = documents["0/p"]
        _dimensions(pressure, ["0", "2", "-2", "0", "0", "0", "0"])
        if _boundaries(pressure) != {name: fields["p"] for name, fields in expected_boundaries.items()}:
            raise ValueError("p 边界类型与任务不一致")
    except (KeyError, TypeError, ValueError) as exc:
        checks.append(("BOUNDARY_MISMATCH", str(exc), "0/p"))
    try:
        physical = documents["constant/physicalProperties"]
        nu = physical.get("nu", [])
        if (len(nu) != 2 or not isinstance(nu[0], Group)
                or nu[0].items != ["0", "2", "-1", "0", "0", "0", "0"]
                or not close(float(nu[1]), task["physics"]["kinematic_viscosity"])):
            raise ValueError("nu 量纲或数值与任务不一致")
    except (KeyError, TypeError, ValueError) as exc:
        checks.append(("VALUE_MISMATCH", str(exc), "constant/physicalProperties"))
    try:
        control = documents["system/controlDict"]
        if _one(control, "application") != task["solver"]:
            raise ValueError("求解器与任务不一致")
        if _one(control, "writeControl") != "runTime":
            raise ValueError("writeControl 必须为 runTime，write_interval 才表示物理时间")
        for key, task_key in (("startTime", "start_time"), ("endTime", "end_time"),
                              ("deltaT", "delta_t"), ("writeInterval", "write_interval")):
            if not close(_number(control, key), task["time_control"][task_key]):
                raise ValueError(f"{key} 与任务不一致")
    except (KeyError, TypeError, ValueError) as exc:
        checks.append(("VALUE_MISMATCH", str(exc), "system/controlDict"))
    try:
        _validate_mesh(documents["system/blockMeshDict"], task)
    except (KeyError, TypeError, ValueError) as exc:
        checks.append(("MESH_MISMATCH", str(exc), "system/blockMeshDict"))
    try:
        metadata = read_json(root / "constant/geometry.json")
        geometry = task["geometry"]
        for key in ("width", "height", "depth"):
            if not close(metadata[key], geometry[key]):
                raise ValueError(f"几何元数据 {key} 与任务不一致")
        if metadata.get("planned_cells") != task["mesh"]["cells_x"] * task["mesh"]["cells_y"]:
            raise ValueError("几何元数据 planned_cells 不一致")
    except (OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
        checks.append(("GEOMETRY_READ", str(exc), "constant/geometry.json"))
    if reference is not None:
        try:
            provenance = read_json(root / "constant/tutorial-provenance.json")
            if (provenance.get("tutorial_id") != reference["tutorial_id"]
                    or provenance.get("source_manifest_sha256") != reference["manifest_sha256"]
                    or provenance.get("scripts_executed") is not False
                    or provenance.get("scripts_copied") is not False):
                raise ValueError("教程来源记录与 task 不一致")
        except (OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
            checks.append(("TUTORIAL_PROVENANCE", str(exc),
                           "constant/tutorial-provenance.json"))
    for code, message, location in checks:
        report["errors"].append(issue(code, message, location))
    report["config_valid"] = not report["errors"]
    return report


__all__ = ["validate_cavity_case"]
