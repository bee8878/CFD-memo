"""Run and assess the fixed Re=100 physical-validation matrix."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from uuid import uuid4

from cfd_memo_agent.validator import validate_task
from cfd_memo_agent.validator.foam import read_json
from cfd_memo_agent.workflow import run_workflow

PROJECT = Path(__file__).resolve().parents[2]
VARIANTS = (
    ("baseline", 10000, 0.005),
    ("mesh-coarse", 5000, 0.005),
    ("mesh-fine", 20000, 0.005),
    ("mesh-extra-fine", 40000, 0.005),
    ("mesh-local-fine", 29440, 0.005),
    ("mesh-local-extra-fine", 47200, 0.005),
    ("time-coarse", 10000, 0.01),
    ("time-fine", 10000, 0.0025),
)
METRIC_LIMITS = {"mean_cd": 0.03, "strouhal": 0.03, "cl_amplitude": 0.05}
LOCAL_MESH_CONTROLS = {
    "mesh-local-fine": {
        "near_field_radius": 10, "angular_cells": 40, "wake_angular_cells": 64,
        "near_radial_cells": 64, "far_radial_cells": 16,
        "near_radial_grading": 20, "far_radial_grading": 10,
    },
    "mesh-local-extra-fine": {
        "near_field_radius": 10, "angular_cells": 52, "wake_angular_cells": 80,
        "near_radial_cells": 80, "far_radial_cells": 20,
        "near_radial_grading": 20, "far_radial_grading": 10,
    },
}


def _write(path: Path, value: dict) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def _variant(base: dict, name: str, cells: int, delta_t: float) -> dict:
    task = deepcopy(base)
    task["task_id"] = f"{base['task_id']}-{name}"
    task["mesh"]["target_cells"] = cells
    if name in LOCAL_MESH_CONTROLS:
        task["mesh"].update(LOCAL_MESH_CONTROLS[name])
    task["time_control"]["delta_t"] = delta_t
    task["convergence"]["max_corrections"] = 0
    report = validate_task(task)
    if not report["task_valid"]:
        raise ValueError(f"Invalid physical-study task {name}: {report['errors']}")
    return task


def _episode_record(name: str, expected_task: dict, episode: dict) -> dict:
    if episode.get("runner_mode") != "real" or episode.get("status") != "completed":
        raise ValueError(f"Physical-study case did not complete: {name}")
    actual = deepcopy(episode.get("task"))
    if actual is None:
        raise ValueError(f"Physical-study episode has no task: {name}")
    actual["task_id"] = expected_task["task_id"]
    if actual != expected_task:
        raise ValueError(f"Physical-study episode task mismatch: {name}")
    if not episode.get("rounds"):
        raise ValueError(f"Physical-study episode has no execution round: {name}")
    result = episode["rounds"][-1].get("result_evidence")
    mesh = episode["rounds"][-1].get("mesh_evidence")
    force = result.get("force_coefficients") if result else None
    if not mesh or not result or not force:
        raise ValueError(f"Physical-study episode lacks fresh evidence: {name}")
    return {
        "name": name,
        "task": expected_task,
        "episode_path": episode["episode_path"],
        "report_path": episode["report_path"],
        "actual_cells": mesh["actual_cells"],
        "force_coefficients": force,
    }


def _relative(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1e-30)
    return abs(a - b) / scale


def assess_physical_study(records: list[dict]) -> dict:
    by_name = {record["name"]: record for record in records}
    missing = [name for name, _, _ in VARIANTS if name not in by_name]
    if missing:
        return {"physical_validated": False, "missing_cases": missing,
                "reference_passed": False, "signal_passed": False,
                "mesh_independence": None, "time_step_independence": None}
    signal_passed = all(record["force_coefficients"]["signal_valid"] for record in records)
    reference = by_name["mesh-local-extra-fine"]["force_coefficients"].get("reference_comparison")

    def comparison(first: str, second: str) -> dict:
        changes = {}
        for metric, limit in METRIC_LIMITS.items():
            left = by_name[first]["force_coefficients"][metric]
            right = by_name[second]["force_coefficients"][metric]
            if left is None or right is None or not all(math.isfinite(v) for v in (left, right)):
                changes[metric] = None
            else:
                changes[metric] = _relative(left, right)
        return {"first": first, "second": second, "relative_changes": changes,
                "limits": METRIC_LIMITS,
                "passed": all(changes[metric] is not None and changes[metric] <= limit
                              for metric, limit in METRIC_LIMITS.items())}

    mesh = comparison("mesh-local-fine", "mesh-local-extra-fine")
    time_step = comparison("baseline", "time-fine")
    reference_passed = bool(reference and reference.get("passed"))
    passed = signal_passed and reference_passed and mesh["passed"] and time_step["passed"]
    return {"physical_validated": passed, "missing_cases": [],
            "reference_passed": reference_passed, "signal_passed": signal_passed,
            "mesh_independence": mesh, "time_step_independence": time_step}


def _report(path: Path, study: dict) -> None:
    assessment = study.get("assessment")
    lines = ["# CFD-Memo C6 物理验收报告", "",
             f"- 状态：{study['status']}",
             f"- 物理验证：{study['physical_validated']}",
             "- 固定算例：二维圆柱绕流，Re=100", "",
             "## 算例结果", "",
             "| 变体 | 单元数 | deltaT | Cd | Cl 振幅 | St | 周期有效 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for record in study["cases"]:
        force = record["force_coefficients"]
        st = "-" if force["strouhal"] is None else f"{force['strouhal']:.6g}"
        lines.append(f"| {record['name']} | {record['actual_cells']} | "
                     f"{record['task']['time_control']['delta_t']} | {force['mean_cd']:.6g} | "
                     f"{force['cl_amplitude']:.6g} | {st} | {force['signal_valid']} |")
    if assessment:
        lines.extend(["", "## 验收门槛", "",
                      f"- 参考区间：{assessment['reference_passed']}",
                      f"- 所有周期信号有效：{assessment['signal_passed']}"])
        for label, key in (("网格独立性", "mesh_independence"),
                           ("时间步独立性", "time_step_independence")):
            item = assessment[key]
            if item:
                lines.append(f"- {label}：{item['passed']}；相对变化 {item['relative_changes']}")
    lines.extend(["", "单次命令完成不等于物理验证；完整原始证据见各 episode。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_physical_study(*, task_path: Path | str, runs_dir: Path | str | None = None,
                       baseline_episode: Path | str | None = None,
                       timeout: float = 1800) -> dict:
    base = read_json(Path(task_path))
    if (base.get("case_type") != "cylinder-2d"
            or not math.isclose(base.get("physics", {}).get("reynolds_number", 0), 100)
            or base.get("time_control", {}).get("end_time", 0) < 100):
        raise ValueError("C6 物理研究只接受 endTime>=100 的 Re=100 二维圆柱任务")
    parent = Path(runs_dir).resolve() if runs_dir else PROJECT / "cases/runs"
    root = parent / ("physical-study-" + uuid4().hex)
    root.mkdir(parents=True)
    study = {
        "study_id": root.name, "status": "running", "physical_validated": False,
        "started_at": datetime.now(timezone.utc).isoformat(), "study_path": str(root),
        "report_path": str(root / "report.md"), "cases": [], "assessment": None,
        "protocol": {"independence_limits": METRIC_LIMITS,
                     "metrics": list(METRIC_LIMITS), "variants": [name for name, _, _ in VARIANTS]},
    }
    _write(root / "study.json", study)
    try:
        for name, cells, delta_t in VARIANTS:
            task = _variant(base, name, cells, delta_t)
            task_file = root / f"task-{name}.json"
            _write(task_file, task)
            if name == "baseline" and baseline_episode is not None:
                episode = read_json(Path(baseline_episode))
            else:
                episode = run_workflow(task_path=task_file, mode="real", runs_dir=root / "runs",
                                       max_corrections=0, timeout=timeout)
            study["cases"].append(_episode_record(name, task, episode))
            _write(root / "study.json", study)
    except KeyboardInterrupt:
        study["status"] = "interrupted"
        _write(root / "study.json", study)
        _report(root / "report.md", study)
        return study
    except (OSError, ValueError, KeyError, TypeError) as exc:
        study["status"] = "failed"
        study["error"] = str(exc)
        _write(root / "study.json", study)
        _report(root / "report.md", study)
        return study
    study["assessment"] = assess_physical_study(study["cases"])
    study["physical_validated"] = study["assessment"]["physical_validated"]
    study["status"] = "validated" if study["physical_validated"] else "not_validated"
    _write(root / "study.json", study)
    _report(root / "report.md", study)
    return study


def reassess_physical_study(study_path: Path | str) -> dict:
    root = Path(study_path).resolve()
    study = read_json(root / "study.json")
    study["assessment"] = assess_physical_study(study["cases"])
    study["physical_validated"] = study["assessment"]["physical_validated"]
    study["status"] = "validated" if study["physical_validated"] else "not_validated"
    study.pop("error", None)
    _write(root / "study.json", study)
    _report(root / "report.md", study)
    return study


def continue_physical_study(*, study_path: Path | str, task_path: Path | str,
                            timeout: float = 1800) -> dict:
    root = Path(study_path).resolve()
    study = read_json(root / "study.json")
    base = read_json(Path(task_path))
    existing = {record["name"] for record in study.get("cases", [])}
    study["status"] = "running"
    study["physical_validated"] = False
    study["assessment"] = None
    study.pop("error", None)
    study["protocol"] = {"independence_limits": METRIC_LIMITS,
                         "metrics": list(METRIC_LIMITS),
                         "variants": [name for name, _, _ in VARIANTS]}
    _write(root / "study.json", study)
    try:
        for name, cells, delta_t in VARIANTS:
            if name in existing:
                continue
            task = _variant(base, name, cells, delta_t)
            task_file = root / f"task-{name}.json"
            _write(task_file, task)
            episode = run_workflow(task_path=task_file, mode="real", runs_dir=root / "runs",
                                   max_corrections=0, timeout=timeout)
            study["cases"].append(_episode_record(name, task, episode))
            _write(root / "study.json", study)
    except KeyboardInterrupt:
        study["status"] = "interrupted"
        _write(root / "study.json", study)
        _report(root / "report.md", study)
        return study
    except (OSError, ValueError, KeyError, TypeError) as exc:
        study["status"] = "failed"
        study["error"] = str(exc)
        _write(root / "study.json", study)
        _report(root / "report.md", study)
        return study
    return reassess_physical_study(root)


__all__ = ["assess_physical_study", "continue_physical_study",
           "reassess_physical_study", "run_physical_study"]
