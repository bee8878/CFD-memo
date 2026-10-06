"""Bounded repairs for the bundled template, backed by static evidence."""
from __future__ import annotations

from pathlib import Path
import re

from cfd_memo_agent.validator import validate_case, validate_task
from cfd_memo_agent.validator.foam import parse_foam

FAULTS = (
    "missing-boundary", "bad-transport", "time-control-drift",
    "mesh-resolution-risk", "boundary-semantics-transfer",
    "transport-relation-transfer",
)
FIELDS = ("0/U", "0/p")
TRANSPORT = "constant/physicalProperties"
CONTROL = "system/controlDict"
MESH = "system/blockMeshDict"
FIELD_CODES = {"BOUNDARY_NAMES", "BOUNDARY_TYPE", "FIELD_VALUE", "VALUE_MISMATCH",
               "CONFIG_STRUCTURE", "CONFIG_ENTRY"}
TRANSPORT_CODES = {"CONFIG_NUMBER", "CONFIG_ENTRY", "DIMENSIONS", "VALUE_MISMATCH"}


def _change(relative, before, after):
    return {"file": relative, "before": before, "after": after}


def inject_fault(case_dir: Path, fault: str) -> dict:
    """Modify only a fresh workflow-owned first-round copy."""
    if fault not in FAULTS:
        raise ValueError("未知受控故障")
    relative = {
        "missing-boundary": "0/U",
        "bad-transport": TRANSPORT,
        "time-control-drift": CONTROL,
        "mesh-resolution-risk": MESH,
        "boundary-semantics-transfer": "0/U",
        "transport-relation-transfer": TRANSPORT,
    }[fault]
    path = case_dir / relative
    before = path.read_text(encoding="utf-8")
    document = parse_foam(before)
    if fault == "missing-boundary":
        names = document.get("boundaryField", {})
        target = next((name for name in ("outlet", "movingWall", "inlet") if name in names), None)
        if target is None:
            raise ValueError("当前 case 没有可用于缺边界夹具的标准边界")
        # This mutation deliberately targets the bundled template, not arbitrary dictionaries.
        after, count = re.subn(rf"\b{re.escape(target)}\s*\{{[^{{}}]*\}}", "", before)
    elif fault == "bad-transport":
        after, count = re.subn(r"(?m)^nu\s+[^;]+;", "nu [0 2 -1 0 0 0 0] -1;", before)
    elif fault == "time-control-drift":
        match = re.search(r"(?m)^endTime\s+([-+0-9.eE]+)\s*;", before)
        if match is None:
            raise ValueError("controlDict 缺少唯一 endTime")
        changed = format(float(match.group(1)) * 1.5 + 1, ".12g")
        after, count = re.subn(
            r"(?m)^endTime\s+[-+0-9.eE]+\s*;", f"endTime    {changed};", before,
        )
    elif fault == "mesh-resolution-risk":
        pattern = r"(hex\s*\([^)]*\)\s*\()([1-9][0-9]*)(\s+[1-9][0-9]*\s+[1-9][0-9]*\))"
        after, count = re.subn(pattern, r"\g<1>1\g<3>", before, count=1)
    elif fault == "boundary-semantics-transfer":
        names = document.get("boundaryField", {})
        target = next((name for name in ("inlet", "movingWall") if name in names), None)
        if target is None:
            raise ValueError("当前 case 没有可用于边界语义迁移夹具的入口边界")
        block = re.search(rf"\b{re.escape(target)}\s*\{{[^{{}}]*\}}", before)
        if block is None:
            raise ValueError("入口边界结构无法唯一定位")
        replacement, type_count = re.subn(
            r"\btype\s+fixedValue\s*;", "type zeroGradient;", block.group(),
        )
        replacement = re.sub(r"(?m)^\s*value\s+[^;]+;\s*", "", replacement)
        after = before[:block.start()] + replacement + before[block.end():]
        count = type_count
    else:
        match = re.search(r"(?m)^nu\s+(?:\[[^\]]+\]\s+)?([-+0-9.eE]+)\s*;", before)
        if match is None:
            raise ValueError("physicalProperties 缺少数值 nu")
        wrong = format(float(match.group(1)) * 2, ".12g")
        after, count = re.subn(
            r"(?m)^(nu\s+(?:\[[^\]]+\]\s+)?)[-+0-9.eE]+(\s*;)",
            rf"\g<1>{wrong}\g<2>", before,
        )
    if count != 1:
        raise ValueError("受控故障要求唯一的默认模板配置项")
    parse_foam(after)
    path.write_text(after, encoding="utf-8")
    return {"name": fault, "changes": [_change(relative, before, after)]}


def propose_repairs(task: dict, case_dir: Path, reference_dir: Path, findings: list[dict]) -> dict:
    """Return replacements only when every error belongs to an implemented rule."""
    if not validate_task(task)["task_valid"]:
        return {"changes": [], "stop_code": "TASK_INVALID"}
    if not validate_case(task, reference_dir)["config_valid"]:
        return {"changes": [], "stop_code": "REFERENCE_INVALID"}
    static = validate_case(task, case_dir)
    allowed_runtime = {"MISSING_BOUNDARY", "BAD_TRANSPORT"}
    static_codes = {item["code"] for item in static["errors"]}
    if any(item["code"] not in static_codes | allowed_runtime for item in findings):
        return {"changes": [], "stop_code": "UNSUPPORTED_ERROR"}
    if not static["errors"]:
        return {"changes": [], "stop_code": "NO_EFFECTIVE_CHANGE"}
    targets = set()
    for problem in static["errors"]:
        code, location = problem["code"], problem["location"]
        if code in FIELD_CODES and any(location == f or location.startswith(f + ".") for f in FIELDS):
            targets.add(next(f for f in FIELDS if location == f or location.startswith(f + ".")))
        elif code in TRANSPORT_CODES and (location == TRANSPORT or location.startswith(TRANSPORT + ".")):
            targets.add(TRANSPORT)
        else:
            return {"changes": [], "stop_code": "UNSUPPORTED_ERROR"}
    changes = []
    for relative in sorted(targets):
        try:
            before = (case_dir / relative).read_text(encoding="utf-8")
            after = (reference_dir / relative).read_text(encoding="utf-8")
            current, reference = parse_foam(before), parse_foam(after)
        except (OSError, UnicodeError, ValueError, RecursionError):
            return {"changes": [], "stop_code": "UNSUPPORTED_ERROR"}
        key = "nu" if relative == TRANSPORT else "boundaryField"
        current.pop(key, None)
        reference.pop(key, None)
        # Whole-file replacement is safe only if all other parsed settings agree.
        if current != reference:
            return {"changes": [], "stop_code": "UNSUPPORTED_ERROR"}
        if before != after:
            changes.append(_change(relative, before, after))
    return {"changes": changes, "stop_code": None if changes else "NO_EFFECTIVE_CHANGE"}


def apply_repairs(case_dir: Path, changes: list[dict]) -> None:
    """Apply reviewed replacements to an already isolated next-round copy."""
    for change in changes:
        if change["file"] not in {*FIELDS, TRANSPORT}:
            raise ValueError("修正规则不允许修改此文件")
        path = case_dir / change["file"]
        if path.read_text(encoding="utf-8") != change["before"]:
            raise ValueError("待修改文件与诊断时内容不同")
    for change in changes:
        (case_dir / change["file"]).write_text(change["after"], encoding="utf-8")
