"""Static validation for the reviewed OpenFOAM 10 pitzDaily derivative."""

from __future__ import annotations

import math
from pathlib import Path
import re

from cfd_memo_agent.validator.foam import Group, parse_foam, read_json
from cfd_memo_agent.validator.task import close, issue, validate_task
from .backward_step_generation import TUTORIAL_ID, WHITELIST

EXPECTED_FILES = {*WHITELIST, "constant/geometry.json", "constant/tutorial-provenance.json"}
FIELD_TYPES = {
    "U": {
        "inlet": "fixedValue", "outlet": "zeroGradient", "upperWall": "noSlip",
        "lowerWall": "noSlip", "frontAndBack": "empty",
    },
    "p": {
        "inlet": "zeroGradient", "outlet": "fixedValue", "upperWall": "zeroGradient",
        "lowerWall": "zeroGradient", "frontAndBack": "empty",
    },
    "k": {
        "inlet": "fixedValue", "outlet": "zeroGradient",
        "upperWall": "kqRWallFunction", "lowerWall": "kqRWallFunction",
        "frontAndBack": "empty",
    },
    "epsilon": {
        "inlet": "fixedValue", "outlet": "zeroGradient",
        "upperWall": "epsilonWallFunction", "lowerWall": "epsilonWallFunction",
        "frontAndBack": "empty",
    },
    "nut": {
        "inlet": "calculated", "outlet": "calculated",
        "upperWall": "nutkWallFunction", "lowerWall": "nutkWallFunction",
        "frontAndBack": "empty",
    },
}


def _one(doc: dict, key: str):
    value = doc.get(key)
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError(f"配置项 {key} 必须且只能有一个值")
    return value[0]


def _number(doc: dict, key: str) -> float:
    try:
        value = float(_one(doc, key))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"配置项 {key} 必须是数字") from exc
    if not math.isfinite(value):
        raise ValueError(f"配置项 {key} 必须是有限数字")
    return value


def _header(doc: dict, obj: str, cls: str) -> None:
    header = doc.get("FoamFile")
    if (not isinstance(header, dict) or header.get("format") != ["ascii"]
            or header.get("object") != [obj] or header.get("class") != [cls]):
        raise ValueError(f"FoamFile 头与 {obj}/{cls} 不一致")


def _boundaries(doc: dict) -> dict[str, str]:
    boundary = doc.get("boundaryField")
    if not isinstance(boundary, dict):
        raise ValueError("缺少 boundaryField")
    return {name: _one(body, "type") for name, body in boundary.items()
            if isinstance(body, dict)}


def _validate_block_mesh(text: str) -> None:
    clean = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", text)
    if not re.search(r"\bconvertToMeters\s+0\.001\s*;", clean):
        raise ValueError("convertToMeters 必须保持官方值 0.001")
    if len(re.findall(r"\bhex\s*\(", clean)) != 5:
        raise ValueError("后台阶网格必须保持五个 hex block")
    for name in ("inlet", "outlet", "upperWall", "lowerWall", "frontAndBack"):
        if len(re.findall(rf"(?m)^\s*{name}\s*\{{", clean)) != 1:
            raise ValueError(f"网格边界 {name} 缺失或重复")
    if not re.search(r"frontAndBack\s*\{\s*type\s+empty\s*;", clean):
        raise ValueError("frontAndBack 必须是 empty")


def validate_backward_step_case(task: dict, case_dir: Path | str) -> dict:
    report = validate_task(task)
    if not report["task_valid"]:
        return report
    root = Path(case_dir)
    report["runtime_blockers"] = [issue(
        "MESH_NOT_VERIFIED",
        "尚未对本次教程副本执行 blockMesh/checkMesh",
        "system/blockMeshDict",
    )]
    actual_files = set()
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"不允许教程副本包含链接：{path.relative_to(root)}")
            if path.is_file():
                actual_files.add(path.relative_to(root).as_posix())
        unexpected = actual_files - EXPECTED_FILES
        missing = EXPECTED_FILES - actual_files
        if unexpected:
            raise ValueError("包含非白名单文件：" + ", ".join(sorted(unexpected)))
        if missing:
            raise ValueError("缺少白名单文件：" + ", ".join(sorted(missing)))
    except (OSError, ValueError) as exc:
        report["errors"].append(issue("TUTORIAL_WHITELIST", str(exc), str(root)))
        return report

    documents = {}
    typed = {
        **{f"0/{field}": (field, "volVectorField" if field == "U" else "volScalarField")
           for field in FIELD_TYPES},
        "constant/physicalProperties": ("physicalProperties", "dictionary"),
        "constant/momentumTransport": ("momentumTransport", "dictionary"),
        "system/controlDict": ("controlDict", "dictionary"),
    }
    for relative, (obj, cls) in typed.items():
        try:
            document = parse_foam((root / relative).read_text(encoding="utf-8"))
            _header(document, obj, cls)
            documents[relative] = document
        except (OSError, UnicodeError, ValueError) as exc:
            report["errors"].append(issue("CONFIG_READ", str(exc), relative))
    for relative, obj in (("system/fvSchemes", "fvSchemes"),
                          ("system/fvSolution", "fvSolution")):
        try:
            text = (root / relative).read_text(encoding="utf-8")
            if not re.search(rf"\bobject\s+{obj}\s*;", text):
                raise ValueError(f"FoamFile object 不是 {obj}")
        except (OSError, UnicodeError, ValueError) as exc:
            report["errors"].append(issue("CONFIG_READ", str(exc), relative))
    try:
        _validate_block_mesh((root / "system/blockMeshDict").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        report["errors"].append(issue("MESH_MISMATCH", str(exc), "system/blockMeshDict"))
    if report["errors"]:
        return report

    for field, expected in FIELD_TYPES.items():
        try:
            actual = _boundaries(documents[f"0/{field}"])
            if actual != expected:
                raise ValueError(f"{field} 边界类型与已批准教程不一致")
        except (KeyError, TypeError, ValueError) as exc:
            report["errors"].append(issue("BOUNDARY_MISMATCH", str(exc), f"0/{field}"))
    try:
        inlet = documents["0/U"]["boundaryField"]["inlet"].get("value", [])
        if (len(inlet) != 2 or inlet[0] != "uniform" or not isinstance(inlet[1], Group)
                or len(inlet[1].items) != 3
                or not close(float(inlet[1].items[0]), task["physics"]["inlet_velocity"])
                or inlet[1].items[1:] != ["0", "0"]):
            raise ValueError("入口速度与 task 不一致")
    except (KeyError, TypeError, ValueError) as exc:
        report["errors"].append(issue("VALUE_MISMATCH", str(exc), "0/U"))
    try:
        if not close(_number(documents["constant/physicalProperties"], "nu"),
                     task["physics"]["kinematic_viscosity"]):
            raise ValueError("nu 与 task 不一致")
        transport = documents["constant/momentumTransport"]
        if _one(transport, "simulationType") != "RAS" or _one(transport["RAS"], "model") != "kEpsilon":
            raise ValueError("只允许 RAS/kEpsilon")
    except (KeyError, TypeError, ValueError) as exc:
        report["errors"].append(issue(
            "VALUE_MISMATCH", str(exc), "constant/momentumTransport"))
    try:
        control = documents["system/controlDict"]
        if _one(control, "application") != "simpleFoam":
            raise ValueError("application 必须为 simpleFoam")
        for key, task_key in (("startTime", "start_time"), ("endTime", "end_time"),
                              ("deltaT", "delta_t"), ("writeInterval", "write_interval")):
            if not close(_number(control, key), task["time_control"][task_key]):
                raise ValueError(f"{key} 与 task 不一致")
    except (KeyError, TypeError, ValueError) as exc:
        report["errors"].append(issue("VALUE_MISMATCH", str(exc), "system/controlDict"))
    try:
        provenance = read_json(root / "constant/tutorial-provenance.json")
        geometry = read_json(root / "constant/geometry.json")
        reference = task["tutorial_reference"]
        if (provenance.get("tutorial_id") != TUTORIAL_ID
                or provenance.get("source_manifest_sha256") != reference["manifest_sha256"]
                or provenance.get("scripts_executed") is not False
                or provenance.get("scripts_copied") is not False):
            raise ValueError("教程来源记录与 task 不一致")
        if (geometry.get("reference_length") != task["geometry"]["reference_length"]
                or geometry.get("planned_cells") != task["mesh"]["target_cells"]):
            raise ValueError("几何记录与 task 不一致")
    except (OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
        report["errors"].append(issue(
            "TUTORIAL_PROVENANCE", str(exc), "constant/tutorial-provenance.json"))
    report["config_valid"] = not report["errors"]
    return report


__all__ = ["validate_backward_step_case"]
