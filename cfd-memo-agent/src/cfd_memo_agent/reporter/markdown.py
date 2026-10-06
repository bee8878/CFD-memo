"""Render evidence from an episode as a local Chinese report."""
from __future__ import annotations

from html import escape
from pathlib import Path

from .summary import build_report_summary, write_report_summary


def _text(value):
    return escape(str(value)).replace("|", "&#124;").replace("\n", "<br>")


def write_report(path: Path, episode: dict) -> None:
    summary = build_report_summary(episode)
    status_names = {"simulated_success": "模拟流程完成", "completed": "命令执行完成",
                    "proposal_ready": "教程参考提案已生成",
                    "failed": "失败", "blocked": "被阻止", "timeout": "超时",
                    "interrupted": "用户中断", "execution_error": "执行错误"}
    proposal_only = episode["status"] == "proposal_ready"
    tutorial_resume = episode.get("tutorial_resume")
    execution_line = (
        f"- 执行：未进入（命令请求 runner: {episode['runner_mode']}）"
        if proposal_only else
        f"- 执行模式：{episode['runner_mode']}；对比模式：{episode['mode']}"
    )
    if proposal_only:
        physical_line = "- 物理结果：未验证。当前仅形成参考提案，没有生成 case 或运行求解器。"
    elif episode["runner_mode"] == "real":
        physical_line = "- 物理结果：未验证。真实命令与场文件检查完成不等于物理准确性已验证。"
    else:
        physical_line = "- 物理结果：未验证。模拟日志不代表真实流场计算。"
    writer_name = ("教程 Builder" if tutorial_resume else "Case Writer")
    lines = [
        "# CFD-Memo 任务报告", "",
        f"- 记录编号：{episode['episode_id']}",
        f"- 状态：{status_names.get(episode['status'], episode['status'])}",
        execution_line,
        f"- 规划模式：{episode['planning']['actual_mode'] or '未完成'}"
        f"（请求 {episode['planning']['requested_provider'] or '未知'}）",
        f"- {writer_name}：{episode.get('case_writing', {}).get('actual_mode') or '未完成'}"
        f"（状态 {episode.get('case_writing', {}).get('status', '未记录')}）",
        physical_line,
        f"- 修正次数：{episode['diagnosis']['correction_count']} / {episode['max_corrections']}",
        f"- 总耗时：{episode['metrics']['elapsed_seconds']:.3f} 秒",
        f"- 停止原因：{_text(episode['stop_reason']['message'])}", "",
        "## 一页结论", "",
        "### 做了什么", "",
        _text(summary["task"]["objective"]), "",
        f"- task：{_text(summary['task']['task_id'] or '未形成')}；"
        f"场景：{_text(summary['task']['case_type'] or '未知')}；"
        f"求解器：{_text(summary['task']['solver'] or '未知')}", "",
        "### 是否运行完成", "",
        ("已完成。" if summary["execution"]["completed"] else "未完成。")
        + "状态：" + _text(summary["execution"]["status_label"])
        + "；停止原因：" + _text(summary["execution"]["stop_reason"]["message"]), "",
        "### 物理结果是否可信", "",
        ("已通过本任务的物理验收。" if summary["physical_result"]["validated"]
         else "尚未证明物理结果可信。")
        + _text(summary["physical_result"]["explanation"]), "",
        "### 经验起了什么作用", "",
        _text(summary["memory_contribution"]["conclusion"]),
    ]
    if summary["memory_contribution"]["effective_experience_ids"]:
        lines.append("- 有效经验：" + "、".join(
            _text(item) for item in summary["memory_contribution"]["effective_experience_ids"]
        ))
    if summary["memory_contribution"]["prevented_files"]:
        lines.append("- 防止重复错误的文件：" + "、".join(
            _text(item) for item in summary["memory_contribution"]["prevented_files"]
        ))
    lines.extend(["", "### 下一步", ""])
    lines.extend("- " + _text(item) for item in summary["next_steps"])
    lines.extend([
        "", "### 输出位置", "",
        "- episode：" + _text(summary["outputs"]["episode"] or "未保存"),
        "- 结构化摘要：" + _text(summary["outputs"]["summary"] or "未保存"),
        "- case：" + _text(summary["outputs"]["case"] or "未生成"), "",
        "## 输入与任务", "",
    ])
    source = episode["input"]
    lines.append("输入类型：" + source["kind"])
    if source["description"] is not None:
        lines.extend(["", _text(source["description"])])
    if source["task_path"] is not None:
        lines.extend(["", "任务文件：" + _text(source["task_path"])])
    if episode["task"] is None:
        lines.extend(["", "没有通过验证的任务；原始输入与检查记录保存在本次目录。"])
    if tutorial_resume:
        lines.extend([
            "", "## 教程批准链", "",
            "- 原 episode：" + _text(tutorial_resume["parent_episode_id"]),
            "- 原工作流：" + _text(tutorial_resume["parent_workflow_path"]),
            "- 显式批准记录：" + _text(tutorial_resume["approval_path"]),
            "- 受控构建记录：" + _text(tutorial_resume["build_path"] or "构建未完成"),
        ])
    planning = episode["planning"]
    if planning["fallback_used"]:
        lines.extend(["", "Planner 已显式回退到规则模式：" + _text(planning["fallback_reason"])])
    for assumption in planning["assumptions"]:
        lines.append("- Planner 假设：" + _text(assumption))
    for question in planning["questions"]:
        lines.append("- Planner 待确认：" + _text(question))
    retrieval = planning.get("tutorial_retrieval")
    if retrieval and retrieval["status"] == "ready":
        lines.append(f"- 官方教程候选：{retrieval['candidate_count']} 个；展示 "
                     f"{len(retrieval['candidates'])} 个。")
        for tutorial_id in planning.get("tutorial_ids", []):
            lines.append("- Planner 引用教程：" + _text(tutorial_id))
    proposal = planning.get("case_spec_proposal")
    if proposal:
        if tutorial_resume:
            lines.append("- CaseSpec 提案：已由用户显式批准，并交给受控教程 Builder。")
        else:
            lines.append("- CaseSpec 提案：仅供审核，不可生成或执行。")
        lines.append("- 建议求解器：" + _text(proposal["solver"]))
    if planning["error"]:
        lines.append("- Planner 错误：" + _text(planning["error"]["message"]))
    case_writing = episode.get("case_writing")
    if case_writing:
        for experience_id in case_writing.get("experience_ids", []):
            lines.append("- Case Writer 引用经验：" + _text(experience_id))
        if case_writing.get("preventive_files"):
            lines.append("- Case Writer 防错文件：" + "、".join(
                _text(item) for item in case_writing["preventive_files"]
            ))
        for reason in case_writing["rationale"]:
            lines.append("- Case Writer 决策：" + _text(reason))
        for warning in case_writing["warnings"]:
            lines.append("- Case Writer 提示：" + _text(warning))
        for blocker in case_writing["blockers"]:
            lines.append("- Case Writer 阻碍：" + _text(blocker))
        if case_writing["error"]:
            lines.append("- Case Writer 错误：" + _text(case_writing["error"]["message"]))
    if episode["injected_fault"]:
        lines.extend(["", "受控故障演示：" + episode["injected_fault"]["name"]])
    lines.extend(["", "## 逐轮记录", "",
                  "| 轮次 | 状态 | 配置通过 | 日志数 |",
                  "| --- | --- | --- | --- |"])
    for round_record in episode["rounds"]:
        lines.append(f"| {round_record['index']} | {round_record['status']} | "
                     f"{round_record['config_valid']} | {len(round_record['log_paths'])} |")
    if not episode["rounds"]:
        lines.extend(["", "未进入 case 执行轮次。"])
    for record in episode["rounds"]:
        lines.extend(["", f"### 第 {record['index']} 轮", "",
                      "记录目录：" + _text(record["run_path"])])
        if record["status"] == "validation_failed":
            lines.append("执行前配置检查失败，本轮未启动 OpenFOAM，也未播放模拟日志。")
        if record.get("environment"):
            env = record["environment"]
            lines.append(f"执行环境：{_text(env['backend'])}；OpenFOAM {_text(env['version'])}")
        if record.get("mesh_evidence"):
            lines.append(f"实际网格单元数：{record['mesh_evidence']['actual_cells']}；本轮网格检查通过。")
        if record.get("result_evidence"):
            result = record["result_evidence"]
            if "final_iteration" in result:
                lines.append(
                    f"最终迭代：{result['final_iteration']} / 配置上限 "
                    f"{result['configured_end_iteration']}；"
                    + ("求解器提前收敛停止。" if result["stopped_before_end"]
                       else "达到配置迭代上限。")
                )
            for field, output in record["result_evidence"]["fields"].items():
                lines.append(f"- 真实场文件 {_text(field)}：{_text(output)}")
            force = record["result_evidence"].get("force_coefficients")
            if force:
                lines.append(f"- 平均阻力系数 Cd：{force['mean_cd']:.6g}")
                lines.append(f"- 升力系数振幅 / RMS：{force['cl_amplitude']:.6g} / {force['cl_rms']:.6g}")
                if force["strouhal"] is not None:
                    lines.append(f"- Strouhal 数：{force['strouhal']:.6g}；完整周期：{force['complete_periods']}")
                lines.append("- 受力周期证据：" + ("达到单次信号要求" if force["signal_valid"] else "样本或周期尚不足"))
                reference = force.get("reference_comparison")
                if reference:
                    lines.append("- Re=100 参考区间：" + ("通过" if reference["passed"] else "未通过或信号不足"))
        for item in record["findings"]:
            lines.append(f"- {_text(item['code'])}：{_text(item['message'])}；"
                         f"位置：{_text(item['location'])}")
        for item in record["runtime_blockers"]:
            lines.append(f"- 真实运行阻碍：{_text(item['message'])}")
        for log in record["log_paths"]:
            lines.append("- 日志：" + _text(log))
    lines.extend(["", "## 修正与后续处理", ""])
    for review in episode.get("reviews", []):
        lines.append(
            f"- Reviewer 第 {review['round_index']} 轮：{_text(review['decision'])}；"
            f"范围 {_text(review['repair_scope'])}；{_text(review['recommendation'])}"
        )
        for experience_id in review.get("experience_ids", []):
            lines.append(f"  - 引用经验：{_text(experience_id)}")
    for correction in episode["corrections"]:
        lines.append(f"- 第 {correction['from_round']} → {correction['to_round']} 轮："
                     + "、".join(change["file"] for change in correction["changes"]))
    if not episode["corrections"]:
        lines.append("本次没有实施自动修改。")
    for prevention in episode.get("preventions", []):
        lines.append(
            f"- 记忆防错（第 {prevention['round_index']} 轮运行前）："
            + "、".join(_text(item) for item in prevention["files"])
        )
    for cause in episode["reflection"]["failure_causes"]:
        lines.append("- 已记录问题：" + _text(cause))
    for rule in episode["reflection"]["reusable_rules"]:
        lines.append("- 候选经验（未经过真实 CFD 验证）：" + _text(rule))
    lines.extend(["", "完整变更前后内容见 episode.json 和各轮 correction.json。"])
    if episode["mode"] == "no_memory":
        lines.append("本次未检索历史经验；规则修正不等于大模型反思或长期记忆。")
    elif episode["mode"] == "simple_cache":
        memory = episode["memory"]
        lines.append(
            "简单缓存：" + ("命中完全相同 task 的完整 case。" if memory["cache_hit"]
                         else "未命中完全相同 task；按普通工作流处理。")
        )
    else:
        memory = episode["memory"]
        lines.append(
            f"{episode['mode']}：检索 {len(memory['retrieved_experience_ids'])} 条，"
            f"实际引用 {len(memory['uses'])} 条，本轮学习 "
            f"{len(memory['learned_experience_ids'])} 条。"
        )
    preflight = episode.get("preflight")
    if preflight:
        lines.extend(["", "## 执行前确认", ""])
        lines.append("- 任务指纹：" + _text(preflight["task_sha256"]))
        lines.append("- 确认时间：" + _text(preflight["confirmation"]["confirmed_at"]))
        lines.append("- 执行模式：" + _text(preflight["runner_mode"]))
        for key, value in preflight["parameters"].items():
            lines.append(f"- 参数 {_text(key)}：{_text(value)}")
        for assumption in preflight["assumptions"]:
            lines.append("- 规划假设：" + _text(assumption))
        for experience in preflight["memory"]["experiences"]:
            lines.append(
                "- 执行前经验：" + _text(experience["experience_id"])
                + "；可信等级 " + _text(experience["trust_level"])
            )
        for risk in preflight["risks"]:
            lines.append("- 已确认风险：" + _text(risk["message"]))
    lines.append("")
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    write_report_summary(path.with_name("report-summary.json"), episode)
