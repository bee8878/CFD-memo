"""M3 statistics and reporting for one completed cross-task M2 study."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from math import comb, sqrt
from pathlib import Path
from statistics import mean, stdev
from typing import Any

from cfd_memo_agent.benchmark import GROUPS, load_benchmark_manifest


KNOWN_FAULTS = {"missing-boundary", "bad-transport"}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _wilson(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    estimate = successes / total
    denominator = 1 + z * z / total
    center = (estimate + z * z / (2 * total)) / denominator
    margin = z * sqrt(
        estimate * (1 - estimate) / total + z * z / (4 * total * total)
    ) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _rate(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    successes = sum(bool(row[field]) for row in rows)
    total = len(rows)
    return {
        "successes": successes, "total": total,
        "estimate": successes / total if total else None,
        "wilson_ci95": _wilson(successes, total),
    }


def _numbers(rows: list[dict[str, Any]], field: str) -> dict[str, float | int]:
    values = [float(row[field]) for row in rows]
    return {
        "count": len(values), "mean": mean(values),
        "sample_sd": stdev(values) if len(values) > 1 else 0.0,
        "min": min(values), "max": max(values),
    }


def _mcnemar_exact(left: list[bool], right: list[bool]) -> dict[str, Any]:
    left_only = sum(a and not b for a, b in zip(left, right, strict=True))
    right_only = sum(b and not a for a, b in zip(left, right, strict=True))
    discordant = left_only + right_only
    if discordant == 0:
        p_value = 1.0
    else:
        lower = min(left_only, right_only)
        p_value = min(1.0, 2 * sum(comb(discordant, k) for k in range(lower + 1))
                      / (2 ** discordant))
    return {
        "baseline_only": left_only, "comparison_only": right_only,
        "discordant_pairs": discordant, "exact_two_sided_p": p_value,
    }


def _episode_row(evaluation: dict[str, Any], spec: dict[str, Any], study: Path) -> dict[str, Any]:
    episode_path = Path(evaluation.get("episode_path") or "").resolve()
    if not episode_path.is_file() or study not in episode_path.parents:
        raise ValueError(f"评估 {evaluation['id']} 缺少 study 内的 episode")
    episode = _read(episode_path)
    if episode.get("runner_mode") != "real":
        raise ValueError(f"评估 {evaluation['id']} 不是 real runner 记录")
    if episode.get("task_id") != evaluation["task_id"]:
        raise ValueError(f"评估 {evaluation['id']} 的 task_id 与 episode 不一致")
    rounds = episode.get("rounds", [])
    if not isinstance(rounds, list) or not rounds:
        raise ValueError(f"评估 {evaluation['id']} 没有轮次记录")
    corrections = episode.get("corrections", [])
    first = rounds[0]
    first_pass = bool(
        first.get("config_valid")
        and first.get("status") == "completed"
        and not corrections
    )
    memory = episode.get("memory", {})
    uses = memory.get("uses", []) if isinstance(memory, dict) else []
    effective_use = any(item.get("outcome") == "effective" for item in uses)
    prevention = bool(episode.get("preventions")) or bool(memory.get("cache_hit", False))
    applicable = spec["fault_profile"] in KNOWN_FAULTS
    usage = evaluation.get("usage") or {}
    cost = evaluation.get("estimated_cost_usd") or {}
    return {
        "evaluation_id": evaluation["id"], "task_id": evaluation["task_id"],
        "group": evaluation["group"], "family": spec["family"],
        "partition": spec["partition"], "fault_profile": spec["fault_profile"],
        "engineering_success": episode.get("status") == "completed",
        "first_pass": first_pass,
        "corrections": len(corrections), "rounds": len(rounds),
        "elapsed_seconds": float(episode["metrics"]["elapsed_seconds"]),
        "effective_experience_use": effective_use,
        "failure_avoidance_applicable": applicable,
        "failure_avoided": bool(applicable and prevention and first_pass),
        "openfoam_started": any(record.get("log_paths") for record in rounds),
        "stop_code": episode["stop_reason"]["code"],
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "estimated_cost_off_peak_usd": float(cost.get("off_peak") or 0),
        "estimated_cost_peak_usd": float(cost.get("peak") or 0),
        "episode_path": str(episode_path),
        "physical_validated": bool(episode.get("physical_validated", False)),
    }


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = [row for row in rows if row["failure_avoidance_applicable"]]
    return {
        "tasks": len(rows),
        "engineering_success": _rate(rows, "engineering_success"),
        "first_pass": _rate(rows, "first_pass"),
        "effective_experience_use": _rate(rows, "effective_experience_use"),
        "failure_avoidance": _rate(applicable, "failure_avoided"),
        "corrections": _numbers(rows, "corrections"),
        "elapsed_seconds": _numbers(rows, "elapsed_seconds"),
        "total_tokens": _numbers(rows, "total_tokens"),
        "estimated_cost_off_peak_usd": sum(
            row["estimated_cost_off_peak_usd"] for row in rows),
        "estimated_cost_peak_usd": sum(row["estimated_cost_peak_usd"] for row in rows),
        "openfoam_started": sum(row["openfoam_started"] for row in rows),
        "stop_codes": dict(sorted(Counter(row["stop_code"] for row in rows).items())),
    }


def _paired(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_group = {group: {row["task_id"]: row for row in rows if row["group"] == group}
                for group in GROUPS}
    baseline = by_group["no_memory"]
    result = {}
    for group in GROUPS[1:]:
        if set(by_group[group]) != set(baseline):
            raise ValueError(f"{group} 与 no_memory 的 task 配对不一致")
        task_ids = sorted(baseline)
        left = [baseline[task_id] for task_id in task_ids]
        right = [by_group[group][task_id] for task_id in task_ids]
        result[group] = {
            "tasks": task_ids,
            "engineering_success": _mcnemar_exact(
                [row["engineering_success"] for row in left],
                [row["engineering_success"] for row in right],
            ),
            "first_pass": _mcnemar_exact(
                [row["first_pass"] for row in left],
                [row["first_pass"] for row in right],
            ),
            "mean_correction_difference_vs_no_memory": mean(
                b["corrections"] - a["corrections"] for a, b in zip(left, right, strict=True)
            ),
            "mean_elapsed_difference_seconds_vs_no_memory": mean(
                b["elapsed_seconds"] - a["elapsed_seconds"]
                for a, b in zip(left, right, strict=True)
            ),
        }
    return result


def _percent(rate: dict[str, Any]) -> str:
    estimate, interval = rate["estimate"], rate["wilson_ci95"]
    if estimate is None or interval is None:
        return "N/A"
    return f"{estimate:.1%} [{interval[0]:.1%}, {interval[1]:.1%}]"


def _report(path: Path, analysis: dict[str, Any]) -> None:
    lines = [
        "# CFD-Memo M3 跨任务实验分析", "",
        "本报告从 M2 的 20 个真实 runner episode 重算，不采用模拟结果。", "",
        "| 对比组 | 工程成功率（95% Wilson CI） | 首轮成功率（95% Wilson CI） | 平均修正次数 | 有效经验任务率 | 失败避免率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group in GROUPS:
        item = analysis["groups"][group]
        lines.append(
            f"| {group} | {_percent(item['engineering_success'])} | "
            f"{_percent(item['first_pass'])} | {item['corrections']['mean']:.2f} | "
            f"{_percent(item['effective_experience_use'])} | "
            f"{_percent(item['failure_avoidance'])} |"
        )
    totals = analysis["totals"]
    lines.extend([
        "", "## 与无记忆组的配对比较", "",
        "| 对比组 | 最终成功 McNemar p | 首轮成功 McNemar p | 平均修正次数差 | 平均耗时差（秒） |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for group in GROUPS[1:]:
        paired = analysis["paired_vs_no_memory"][group]
        lines.append(
            f"| {group} | {paired['engineering_success']['exact_two_sided_p']:.3f} | "
            f"{paired['first_pass']['exact_two_sided_p']:.3f} | "
            f"{paired['mean_correction_difference_vs_no_memory']:+.2f} | "
            f"{paired['mean_elapsed_difference_seconds_vs_no_memory']:+.2f} |"
        )
    avoided = [row for row in analysis["rows"] if row["failure_avoided"]]
    failures = [row for row in analysis["rows"] if not row["engineering_success"]]
    lines.extend(["", "## 提前避免案例", ""])
    if avoided:
        lines.extend(f"- `{row['evaluation_id']}`：`{row['task_id']}` / `{row['fault_profile']}`"
                     for row in avoided)
    else:
        lines.append("- 无。")
    lines.extend([
        "", "## 失败案例", "",
        "| 评估 | 对比组 | 任务 | 故障 | 停止原因 |",
        "| --- | --- | --- | --- | --- |",
    ])
    lines.extend(
        f"| {row['evaluation_id']} | {row['group']} | {row['task_id']} | "
        f"{row['fault_profile']} | {row['stop_code']} |" for row in failures
    )
    lines.extend([
        "", "## 资源消耗", "",
        f"- 输入 token：{totals['input_tokens']}",
        f"- 输出 token：{totals['output_tokens']}",
        f"- 总 token：{totals['total_tokens']}",
        f"- 估算费用：${totals['estimated_cost_off_peak_usd']:.6f}-$"
        f"{totals['estimated_cost_peak_usd']:.6f}",
        "", "## 结论边界", "",
        "四组最终工程成功率相同，当前数据不能证明记忆提高最终成功率。CFD-Memo 在固定",
        "任务集上减少了修正次数并提高首轮成功/失败避免，但每组只有五个配对任务，区间很宽；",
        "这些结果应视为原型证据，不是广泛 CFD 泛化结论。后台阶与时间控制失败需要在失败",
        "案例中单独讨论。`physical_validated=false` 的任务不能用于声称物理结果准确。", "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def analyze_benchmark(study_dir: Path | str, *, overwrite: bool = False) -> dict[str, Any]:
    study = Path(study_dir).resolve()
    state = _read(study / "state.json")
    if state.get("status") != "completed" or state.get("pending_count", 0) != 0:
        raise ValueError("M3 只接受已完成且没有 pending 的 M2 study")
    evaluations = state.get("evaluations", [])
    if len(evaluations) != 20 or any(
            item.get("status") not in {"completed", "failed"} for item in evaluations):
        raise ValueError("M3 要求 20 个已完成或业务失败的评估槽位")
    manifest = load_benchmark_manifest(study / "protocol.json")
    specs = {item["id"]: item for item in manifest["task_specs"]}
    rows = [_episode_row(item, specs[item["task_id"]], study) for item in evaluations]
    groups = {group: _group_summary([row for row in rows if row["group"] == group])
              for group in GROUPS}
    if any(item["tasks"] != 5 for item in groups.values()):
        raise ValueError("四组必须各有五个配对任务")
    analysis = {
        "schema_version": 1, "study_id": state["study_id"],
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "source_state": str(study / "state.json"),
        "groups": groups, "paired_vs_no_memory": _paired(rows),
        "totals": {
            "evaluations": len(rows),
            "engineering_successes": sum(row["engineering_success"] for row in rows),
            "business_failures": sum(not row["engineering_success"] for row in rows),
            "openfoam_started": sum(row["openfoam_started"] for row in rows),
            "input_tokens": sum(row["input_tokens"] for row in rows),
            "output_tokens": sum(row["output_tokens"] for row in rows),
            "total_tokens": sum(row["total_tokens"] for row in rows),
            "estimated_cost_off_peak_usd": sum(
                row["estimated_cost_off_peak_usd"] for row in rows),
            "estimated_cost_peak_usd": sum(row["estimated_cost_peak_usd"] for row in rows),
            "physical_validated": sum(row["physical_validated"] for row in rows),
        },
        "rows": rows,
        "limitations": [
            "Each group contains only five paired tasks.",
            "Engineering completion is not physical validation.",
            "Hosted-model latency and pricing are time-dependent.",
            "The frozen anchors cover three registered families, not arbitrary CFD cases.",
        ],
    }
    output = study / "analysis"
    result_path, report_path = output / "analysis.json", output / "report.md"
    if not overwrite and (result_path.exists() or report_path.exists()):
        raise ValueError("M3 输出已存在；使用 overwrite 显式重新生成")
    output.mkdir(parents=True, exist_ok=True)
    _write(result_path, analysis)
    _report(report_path, analysis)
    return {
        "status": "completed", "study_id": state["study_id"],
        "analysis_path": str(result_path), "report_path": str(report_path),
        "groups": groups, "totals": analysis["totals"],
        "physical_validated": False,
    }


__all__ = ["analyze_benchmark"]
