"""Frozen cross-task benchmark loading, task materialization, and auditing."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from cfd_memo_agent.tutorial_capabilities import get_tutorial_capability
from cfd_memo_agent.validator import validate_task
from cfd_memo_agent.validator.foam import read_json


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT / "experiments/cross-task-v1.json"
SCHEMA_PATH = PROJECT / "schemas/benchmark-manifest.schema.json"
GROUPS = ("no_memory", "simple_cache", "retrieval_only", "cfd_memo")
PARTITIONS = (
    "same_family_new_parameters", "known_fault",
    "unknown_fault", "cross_family_transfer",
)


@lru_cache(maxsize=1)
def manifest_validator() -> Draft202012Validator:
    schema = read_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_benchmark_manifest(path: Path | str = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest = read_json(Path(path).resolve())
    if not isinstance(manifest, dict):
        raise ValueError("benchmark manifest 顶层必须是对象")
    errors = sorted(manifest_validator().iter_errors(manifest), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(map(str, first.path)) or "manifest"
        raise ValueError(f"benchmark manifest schema 无效：{location}: {first.message}")
    return manifest


def _base_task(family: str) -> dict[str, Any]:
    if family == "cylinder-2d":
        return read_json(PROJECT / "examples/task.cylinder-2d.json")
    if family == "cavity-2d":
        return read_json(PROJECT / "examples/task.cavity-2d.json")
    if family == "backward-step-2d":
        capability = get_tutorial_capability("incompressible/simpleFoam/pitzDaily")
        return capability.build_task("0" * 64)
    raise ValueError(f"未注册 benchmark family：{family}")


def materialize_task(spec: dict[str, Any]) -> dict[str, Any]:
    """Turn one compact frozen spec into the exact task that M2 will execute."""
    family = spec["family"]
    parameters = spec["parameters"]
    task = deepcopy(_base_task(family))
    task["task_id"] = spec["id"]
    if task["solver"] != spec["solver"]:
        raise ValueError(f"{spec['id']} 的 solver 与注册能力不一致")
    if family == "cylinder-2d":
        required = {"reynolds_number", "inlet_velocity", "diameter", "target_cells",
                    "end_time", "delta_t"}
        if set(parameters) != required:
            raise ValueError(f"{spec['id']} 的圆柱参数字段不完整")
        task["geometry"]["cylinder_diameter"] = parameters["diameter"]
        task["physics"].update({
            "reynolds_number": parameters["reynolds_number"],
            "inlet_velocity": parameters["inlet_velocity"],
            "kinematic_viscosity": (
                parameters["inlet_velocity"] * parameters["diameter"]
                / parameters["reynolds_number"]
            ),
        })
        task["mesh"]["target_cells"] = int(parameters["target_cells"])
        task["time_control"].update({
            "end_time": parameters["end_time"], "delta_t": parameters["delta_t"],
            "write_interval": parameters["end_time"],
        })
    elif family == "cavity-2d":
        required = {"reynolds_number", "lid_velocity", "cells_x", "cells_y",
                    "end_time", "delta_t"}
        if set(parameters) != required:
            raise ValueError(f"{spec['id']} 的方腔参数字段不完整")
        width = task["geometry"]["width"]
        task["physics"].update({
            "reynolds_number": parameters["reynolds_number"],
            "lid_velocity": parameters["lid_velocity"],
            "kinematic_viscosity": (
                parameters["lid_velocity"] * width / parameters["reynolds_number"]
            ),
        })
        task["mesh"].update({
            "cells_x": int(parameters["cells_x"]),
            "cells_y": int(parameters["cells_y"]),
        })
        task["time_control"].update({
            "end_time": parameters["end_time"], "delta_t": parameters["delta_t"],
            "write_interval": parameters["end_time"],
        })
    else:
        required = {"reynolds_number", "inlet_velocity", "end_iteration"}
        if set(parameters) != required:
            raise ValueError(f"{spec['id']} 的后台阶参数字段不完整")
        reference = task["geometry"]["reference_length"]
        task["physics"].update({
            "reynolds_number": parameters["reynolds_number"],
            "inlet_velocity": parameters["inlet_velocity"],
            "kinematic_viscosity": (
                parameters["inlet_velocity"] * reference
                / parameters["reynolds_number"]
            ),
        })
        task["time_control"].update({
            "end_time": parameters["end_iteration"], "delta_t": 1,
            "write_interval": 100,
        })
    return task


def _fingerprint(task: dict[str, Any]) -> str:
    value = deepcopy(task)
    value.pop("task_id", None)
    reference = value.get("tutorial_reference")
    if reference:
        reference.pop("manifest_sha256", None)
    rendered = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def audit_benchmark(path: Path | str = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Fail closed unless all frozen tasks and paired real slots are auditable."""
    source = Path(path).resolve()
    manifest = load_benchmark_manifest(source)
    specs = manifest["task_specs"]
    ids = [item["id"] for item in specs]
    if len(specs) != 30 or len(ids) != len(set(ids)):
        raise ValueError("M1 必须恰好包含 30 个唯一 task spec")
    families = sorted({item["family"] for item in specs})
    solvers = sorted({item["solver"] for item in specs})
    if len(families) < 3 or len(solvers) < 2:
        raise ValueError("M1 至少需要三个任务族和两个求解器")
    family_counts = {family: sum(item["family"] == family for item in specs)
                     for family in families}
    partition_counts = {partition: sum(item["partition"] == partition for item in specs)
                        for partition in PARTITIONS}
    if any(count < 1 for count in partition_counts.values()):
        raise ValueError("M1 四类数据分区都必须非空")

    tasks = []
    fingerprints = []
    for spec in specs:
        task = materialize_task(spec)
        report = validate_task(task)
        if not report["task_valid"]:
            codes = ", ".join(item["code"] for item in report["errors"])
            raise ValueError(f"{spec['id']} 未通过 task 验证：{codes}")
        fingerprint = _fingerprint(task)
        fingerprints.append(fingerprint)
        tasks.append({
            "id": spec["id"], "family": spec["family"], "solver": spec["solver"],
            "partition": spec["partition"], "fault_profile": spec["fault_profile"],
            "fingerprint": fingerprint,
            "warning_codes": [item["code"] for item in report["warnings"]],
        })
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("task spec 中存在参数完全相同的重复样本")

    evaluations = manifest["real_evaluations"]
    evaluation_ids = [item["id"] for item in evaluations]
    pairs = [(item["task_id"], item["group"]) for item in evaluations]
    if len(evaluations) != 20 or len(evaluation_ids) != len(set(evaluation_ids)):
        raise ValueError("正式核心必须恰好冻结 20 个唯一 real evaluation")
    if len(pairs) != len(set(pairs)) or any(task_id not in ids for task_id, _ in pairs):
        raise ValueError("real evaluation 含重复配对或未知 task")
    anchors = {item["id"] for item in specs if item["real_anchor"]}
    by_group = {
        group: {item["task_id"] for item in evaluations if item["group"] == group}
        for group in GROUPS
    }
    if any(task_ids != anchors for task_ids in by_group.values()) or len(anchors) != 5:
        raise ValueError("四个对比组必须共享同一组五个 real anchor")

    raw_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "status": "passed", "benchmark_id": manifest["benchmark_id"],
        "manifest_path": str(source), "manifest_sha256": raw_hash,
        "task_count": len(specs), "family_count": len(families),
        "solver_count": len(solvers), "family_counts": family_counts,
        "partition_counts": partition_counts,
        "real_evaluation_count": len(evaluations),
        "real_anchor_ids": sorted(anchors),
        "group_evaluation_counts": {group: len(by_group[group]) for group in GROUPS},
        "tasks": tasks, "physical_validated": False,
        "execution_started": False,
    }


__all__ = [
    "DEFAULT_MANIFEST", "audit_benchmark", "load_benchmark_manifest",
    "manifest_validator", "materialize_task",
]
