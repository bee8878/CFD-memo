"""Frozen four-group configuration-memory experiment."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from math import sqrt
from statistics import mean, stdev
from typing import Any
from uuid import uuid4

from cfd_memo_agent.models import ModelSettings
from cfd_memo_agent.validator.foam import read_json
from cfd_memo_agent.workflow import run_workflow

PROJECT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = PROJECT / "experiments/memory-study.protocol.json"
GROUPS = ("no_memory", "simple_cache", "retrieval_only", "cfd_memo")
EXPECTED_CODES = {
    "missing-boundary": {"BOUNDARY_NAMES"},
    "bad-transport": {"CONFIG_NUMBER", "CONFIG_ENTRY", "DIMENSIONS", "VALUE_MISMATCH"},
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _task(protocol: dict[str, Any], task_id: str) -> dict[str, str]:
    for item in protocol["evaluation_tasks"]:
        if item["id"] == task_id:
            return item
    raise ValueError(f"实验协议不存在任务：{task_id}")


def _result_row(group: str, task_id: str, fault: str, repeat: int,
                episode: dict[str, Any]) -> dict[str, Any]:
    first_pass = bool(
        episode["rounds"] and episode["rounds"][0]["config_valid"]
        and episode["rounds"][0]["status"] in {"simulated_success", "completed"}
    )
    avoided = bool(episode.get("preventions")) or bool(
        episode.get("memory", {}).get("cache_hit")
    )
    observed_codes = {
        finding["code"] for record in episode["rounds"] for finding in record["findings"]
    }
    detection_applicable = not avoided
    return {
        "group": group, "task_id": task_id, "fault": fault, "repeat": repeat,
        "episode_path": episode["episode_path"], "status": episode["status"],
        "first_pass": first_pass,
        "final_success": episode["status"] in {"simulated_success", "completed"},
        "config_correct_first_round": first_pass,
        "failure_avoided": avoided,
        "detection_applicable": detection_applicable,
        "error_identified": bool(observed_codes.intersection(EXPECTED_CODES[fault]))
        if detection_applicable else None,
        "corrections": len(episode["corrections"]),
        "experience_reused": bool(episode["metrics"]["experience_reused"]),
        "elapsed_seconds": episode["metrics"]["elapsed_seconds"],
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for group in GROUPS:
        selected = [row for row in rows if row["group"] == group]
        applicable = [row for row in selected if row["detection_applicable"]]
        total = len(selected)
        groups[group] = {
            "tasks": total,
            "first_pass_rate": sum(row["first_pass"] for row in selected) / total,
            "final_success_rate": sum(row["final_success"] for row in selected) / total,
            "configuration_correctness_rate": sum(
                row["config_correct_first_round"] for row in selected
            ) / total,
            "error_identification_rate": (
                sum(bool(row["error_identified"]) for row in applicable) / len(applicable)
                if applicable else None
            ),
            "mean_corrections": mean(row["corrections"] for row in selected),
            "experience_reuse_rate": sum(row["experience_reused"] for row in selected) / total,
            "failure_avoidance_rate": sum(row["failure_avoided"] for row in selected) / total,
            "mean_elapsed_seconds": mean(row["elapsed_seconds"] for row in selected),
        }
    return groups


def _wilson(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _uncertainty(rows: list[dict[str, Any]]) -> dict[str, Any]:
    analysis: dict[str, Any] = {}
    for group in GROUPS:
        selected = [row for row in rows if row["group"] == group]
        applicable = [row for row in selected if row["detection_applicable"]]
        rates = {}
        for name, field, population in (
            ("first_pass", "first_pass", selected),
            ("final_success", "final_success", selected),
            ("experience_reuse", "experience_reused", selected),
            ("failure_avoidance", "failure_avoided", selected),
            ("error_identification", "error_identified", applicable),
        ):
            successes = sum(bool(row[field]) for row in population)
            rates[name] = {
                "successes": successes, "total": len(population),
                "estimate": successes / len(population) if population else None,
                "wilson_ci95": _wilson(successes, len(population)),
            }
        corrections = [row["corrections"] for row in selected]
        elapsed = [row["elapsed_seconds"] for row in selected]
        by_fault = {
            fault: {
                "tasks": len(items),
                "first_pass_rate": mean(row["first_pass"] for row in items),
                "failure_avoidance_rate": mean(row["failure_avoided"] for row in items),
                "mean_corrections": mean(row["corrections"] for row in items),
            }
            for fault in sorted({row["fault"] for row in selected})
            if (items := [row for row in selected if row["fault"] == fault])
        }
        analysis[group] = {
            "rates": rates,
            "corrections": {"mean": mean(corrections),
                            "sample_sd": stdev(corrections) if len(corrections) > 1 else 0.0},
            "elapsed_seconds": {"mean": mean(elapsed),
                                "sample_sd": stdev(elapsed) if len(elapsed) > 1 else 0.0},
            "by_fault": by_fault,
        }
    return analysis


def _write_analysis_report(path: Path, analysis: dict[str, Any], row_count: int) -> None:
    lines = [
        "# CFD-Memo 正式配置记忆实验分析", "",
        f"共分析 {row_count} 个评估任务。区间为二项比例的 95% Wilson 区间。", "",
        "| 对比组 | 首轮成功率（95% CI） | 失败避免率（95% CI） | 平均修正次数 ± SD |",
        "| --- | ---: | ---: | ---: |",
    ]
    for group in GROUPS:
        item = analysis[group]
        first = item["rates"]["first_pass"]
        avoided = item["rates"]["failure_avoidance"]
        first_ci = first["wilson_ci95"]
        avoided_ci = avoided["wilson_ci95"]
        lines.append(
            f"| {group} | {first['estimate']:.1%} "
            f"[{first_ci[0]:.1%}, {first_ci[1]:.1%}] | "
            f"{avoided['estimate']:.1%} [{avoided_ci[0]:.1%}, {avoided_ci[1]:.1%}] | "
            f"{item['corrections']['mean']:.3f} ± {item['corrections']['sample_sd']:.3f} |"
        )
    lines.extend([
        "", "## 解释限制", "",
        "重复运行在确定性规则与模拟后端下进行，重复间差异主要来自运行耗时，而不是模型采样。",
        "Wilson 区间描述本固定任务集上的二项比例不确定性，不能代表新几何、新求解器或真实 CFD",
        "任务总体。CFD-Memo 的 100% 避免率来自训练与评估共享的两类已知故障定义。", "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_comparison_svg(path: Path, analysis: dict[str, Any]) -> None:
    labels = {"no_memory": "No memory", "simple_cache": "Simple cache",
              "retrieval_only": "Retrieval only", "cfd_memo": "CFD-Memo"}
    colors = {"no_memory": "#59636E", "simple_cache": "#2A9D8F",
              "retrieval_only": "#E9C46A", "cfd_memo": "#E76F51"}
    panels = [
        ("First-pass success", lambda item: item["rates"]["first_pass"]["estimate"], "%"),
        ("Failure avoidance", lambda item: item["rates"]["failure_avoidance"]["estimate"], "%"),
        ("Mean corrections", lambda item: item["corrections"]["mean"], ""),
    ]
    width, height = 1200, 560
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#1E2933;letter-spacing:0}.title{font-size:22px;font-weight:700}.axis{font-size:13px}.value{font-size:14px;font-weight:700}</style>',
        '<text x="40" y="38" class="title">Configuration-memory comparison (n=24 per group)</text>',
    ]
    panel_width, chart_top, chart_height = 360, 95, 320
    for panel_index, (title, getter, suffix) in enumerate(panels):
        left = 35 + panel_index * 390
        baseline = chart_top + chart_height
        svg.extend([
            f'<text x="{left + 180}" y="72" text-anchor="middle" class="axis" font-weight="700">{title}</text>',
            f'<line x1="{left}" y1="{baseline}" x2="{left + panel_width}" y2="{baseline}" stroke="#AAB2BA"/>',
        ])
        for tick in range(0, 5):
            y = baseline - tick * chart_height / 4
            label = f"{tick * 25}%" if suffix else f"{tick / 4:.2g}"
            svg.extend([
                f'<line x1="{left}" y1="{y:.1f}" x2="{left + panel_width}" y2="{y:.1f}" stroke="#E5E9ED"/>',
                f'<text x="{left - 7}" y="{y + 4:.1f}" text-anchor="end" class="axis">{label}</text>',
            ])
        for index, group in enumerate(GROUPS):
            value = getter(analysis[group])
            bar_height = max(0.0, min(1.0, value)) * chart_height
            x = left + 22 + index * 86
            y = baseline - bar_height
            rendered = f"{value:.0%}" if suffix else f"{value:.2f}"
            svg.extend([
                f'<rect x="{x}" y="{y:.1f}" width="58" height="{bar_height:.1f}" fill="{colors[group]}"/>',
                f'<text x="{x + 29}" y="{max(chart_top + 14, y - 8):.1f}" text-anchor="middle" class="value">{rendered}</text>',
                f'<text x="{x + 29}" y="{baseline + 24}" text-anchor="middle" class="axis" transform="rotate(35 {x + 29} {baseline + 24})">{labels[group]}</text>',
            ])
    svg.extend([
        '<text x="40" y="535" class="axis">Rules provider, simulated backend, known controlled faults. Not a physical CFD validation.</text>',
        '</svg>',
    ])
    path.write_text("\n".join(svg), encoding="utf-8")


def _representative_cases(root: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        ("no-memory-repair", "no_memory", "re200-u2-d1", "missing-boundary"),
        ("cache-exact-hit", "simple_cache", "re100-u1-d1", "missing-boundary"),
        ("cache-parameter-miss", "simple_cache", "re200-u2-d1", "missing-boundary"),
        ("retrieval-after-failure", "retrieval_only", "re200-u2-d1", "missing-boundary"),
        ("memo-prevention", "cfd_memo", "re200-u2-d1", "missing-boundary"),
    ]
    cases = []
    for case_id, group, task_id, fault in specs:
        row = next(item for item in rows if item["group"] == group
                   and item["task_id"] == task_id and item["fault"] == fault)
        episode_path = Path(row["episode_path"])
        episode = read_json(episode_path)
        cases.append({
            "case_id": case_id, "group": group, "task_id": task_id, "fault": fault,
            "round_statuses": [item["status"] for item in episode["rounds"]],
            "corrections": len(episode["corrections"]),
            "review_decisions": [item["decision"] for item in episode["reviews"]],
            "experience_ids": episode.get("memory", {}).get("retrieved_experience_ids", []),
            "cache_hit": episode.get("memory", {}).get("cache_hit", False),
            "prevented_files": [file for item in episode.get("preventions", [])
                                for file in item["files"]],
            "episode_relative_path": str(episode_path.relative_to(root)),
        })
    return cases


def _write_cases_report(path: Path, cases: list[dict[str, Any]]) -> None:
    lines = ["# 五个代表性配置记忆案例", ""]
    explanations = {
        "no-memory-repair": "无历史信息；首轮验证失败，规则修复后第二轮成功。",
        "cache-exact-hit": "task 与缓存完全相同；完整 case 命中并覆盖受控漂移。",
        "cache-parameter-miss": "Re 与入口速度变化导致 task 指纹不同；缓存未命中。",
        "retrieval-after-failure": "Reviewer 在失败后引用经验，但生成前没有防错动作。",
        "memo-prevention": "Planner/Case Writer 提前使用经验，0/U 在 runner 前恢复。",
    }
    for item in cases:
        lines.extend([
            f"## {item['case_id']}", "",
            explanations[item["case_id"]], "",
            f"- 组别：`{item['group']}`；任务：`{item['task_id']}`；故障：`{item['fault']}`",
            f"- 轮次状态：`{' -> '.join(item['round_statuses'])}`；修正次数：{item['corrections']}",
            f"- Reviewer 决策：`{' -> '.join(item['review_decisions'])}`",
            f"- 缓存命中：{item['cache_hit']}；运行前恢复：{', '.join(item['prevented_files']) or '无'}",
            f"- 检索经验数：{len(item['experience_ids'])}", "",
        ])
    lines.extend([
        "这些案例来自确定性模拟配置实验，只用于说明软件决策路径，不代表真实 CFD 物理结论。", "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def analyze_memory_study(study_path: Path) -> dict[str, Any]:
    root = Path(study_path).resolve()
    stored = read_json(root / "results.json")
    rows = stored["rows"]
    if not rows or set(row["group"] for row in rows) != set(GROUPS):
        raise ValueError("实验结果缺少四个对比组")
    analysis = {
        "schema_version": 1, "study_id": stored["study"]["study_id"],
        "study_path": str(root), "row_count": len(rows),
        "groups": _uncertainty(rows),
        "analysis_path": str(root / "analysis.json"),
        "report_path": str(root / "analysis.md"), "physical_validated": False,
        "chart_path": str(root / "comparison.svg"),
        "cases_path": str(root / "case-studies.md"),
    }
    cases = _representative_cases(root, rows)
    analysis["representative_cases"] = cases
    _write_json(root / "analysis.json", analysis)
    _write_analysis_report(root / "analysis.md", analysis["groups"], len(rows))
    _write_comparison_svg(root / "comparison.svg", analysis["groups"])
    _write_json(root / "case-studies.json", cases)
    _write_cases_report(root / "case-studies.md", cases)
    return analysis


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# CFD-Memo 配置记忆对比实验", "",
        f"- 实验编号：{result['study_id']}",
        f"- 重复次数：{result['repeats']}",
        "- 后端：simulated（本实验不评价 CFD 物理准确性）", "",
        "| 对比组 | 首轮成功率 | 最终成功率 | 平均修正次数 | 经验复用率 | 失败避免率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group in GROUPS:
        item = result["summary"][group]
        lines.append(
            f"| {group} | {item['first_pass_rate']:.1%} | "
            f"{item['final_success_rate']:.1%} | {item['mean_corrections']:.3f} | "
            f"{item['experience_reuse_rate']:.1%} | {item['failure_avoidance_rate']:.1%} |"
        )
    lines.extend([
        "", "## 解释边界", "",
        "该实验只比较受控配置故障下的记忆策略。`simulated_success` 表示软件闭环通过，",
        "不表示 OpenFOAM 真实求解或物理量已经验证。完整逐任务数据见 `results.json`。", "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_memory_study(*, output_dir: Path | None = None, repeats: int | None = None,
                     task_ids: list[str] | None = None,
                     faults: list[str] | None = None) -> dict[str, Any]:
    protocol = read_json(PROTOCOL_PATH)
    repeat_count = protocol["formal_repeats"] if repeats is None else repeats
    if isinstance(repeat_count, bool) or not isinstance(repeat_count, int) or repeat_count < 1:
        raise ValueError("repeats 必须为正整数")
    selected_tasks = task_ids or [item["id"] for item in protocol["evaluation_tasks"]]
    selected_faults = faults or list(protocol["faults"])
    if any(fault not in protocol["faults"] for fault in selected_faults):
        raise ValueError("实验故障不在冻结协议中")
    tasks = [_task(protocol, task_id) for task_id in selected_tasks]
    root = Path(output_dir).resolve() if output_dir else (
        PROJECT / "cases/runs" / f"memory-study-{uuid4().hex}"
    )
    root.mkdir(parents=True, exist_ok=False)
    _write_json(root / "protocol.json", protocol)
    rules = ModelSettings()

    simple_store = root / "stores/simple-cache"
    knowledge_seed = root / "stores/knowledge-seed"
    training_task = _task(protocol, protocol["training"]["simple_cache_task_id"])
    run_workflow(
        training_task["description"], mode="simulated", runs_dir=root / "training/simple-cache",
        memory_mode="simple_cache", memory_dir=simple_store, model_settings=rules,
    )
    for fault in protocol["training"]["knowledge_faults"]:
        run_workflow(
            training_task["description"], mode="simulated", fault=fault,
            runs_dir=root / f"training/knowledge-{fault}", memory_mode="cfd_memo",
            memory_dir=knowledge_seed, model_settings=rules,
        )
    retrieval_store = root / "stores/retrieval-only"
    memo_store = root / "stores/cfd-memo"
    shutil.copytree(knowledge_seed, retrieval_store)
    shutil.copytree(knowledge_seed, memo_store)

    rows: list[dict[str, Any]] = []
    stores = {
        "no_memory": None, "simple_cache": simple_store,
        "retrieval_only": retrieval_store, "cfd_memo": memo_store,
    }
    for group in GROUPS:
        for task in tasks:
            for fault in selected_faults:
                for repeat in range(repeat_count):
                    episode = run_workflow(
                        task["description"], mode="simulated", fault=fault,
                        runs_dir=root / "evaluation" / group / task["id"] / fault,
                        memory_mode=group, memory_dir=stores[group], memory_learning=False,
                        max_corrections=protocol["correction_budget"], model_settings=rules,
                    )
                    rows.append(_result_row(group, task["id"], fault, repeat, episode))
    result = {
        "schema_version": 1, "study_id": root.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol_path": str(root / "protocol.json"), "study_path": str(root),
        "repeats": repeat_count, "task_ids": selected_tasks, "faults": selected_faults,
        "summary": _summarize(rows), "results_path": str(root / "results.json"),
        "report_path": str(root / "report.md"), "physical_validated": False,
    }
    _write_json(root / "results.json", {"study": result, "rows": rows})
    _write_report(root / "report.md", result)
    return result


__all__ = ["GROUPS", "analyze_memory_study", "run_memory_study"]
