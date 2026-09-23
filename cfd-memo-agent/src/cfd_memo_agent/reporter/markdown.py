"""Render evidence from an episode as a local Chinese report."""
from __future__ import annotations

from html import escape
from pathlib import Path


def _text(value):
    return escape(str(value)).replace("|", "&#124;").replace("\n", "<br>")


def write_report(path: Path, episode: dict) -> None:
    status_names = {"simulated_success": "模拟流程完成", "completed": "命令执行完成",
                    "failed": "失败", "blocked": "被阻止", "timeout": "超时",
                    "interrupted": "用户中断", "execution_error": "执行错误"}
    lines = [
        "# CFD-Memo 任务报告", "",
        f"- 记录编号：{episode['episode_id']}",
        f"- 状态：{status_names.get(episode['status'], episode['status'])}",
        f"- 执行模式：{episode['runner_mode']}；对比模式：no_memory",
        "- 物理结果：未验证。模拟日志不代表真实流场计算。",
        f"- 修正次数：{episode['diagnosis']['correction_count']} / {episode['max_corrections']}",
        f"- 总耗时：{episode['metrics']['elapsed_seconds']:.3f} 秒",
        f"- 停止原因：{_text(episode['stop_reason']['message'])}", "",
        "## 输入与任务", "",
    ]
    source = episode["input"]
    lines.append("输入类型：" + source["kind"])
    if source["description"] is not None:
        lines.extend(["", _text(source["description"])])
    if source["task_path"] is not None:
        lines.extend(["", "任务文件：" + _text(source["task_path"])])
    if episode["task"] is None:
        lines.extend(["", "没有通过验证的任务；原始输入与检查记录保存在本次目录。"])
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
            for field, output in record["result_evidence"]["fields"].items():
                lines.append(f"- 真实场文件 {_text(field)}：{_text(output)}")
        for item in record["findings"]:
            lines.append(f"- {_text(item['code'])}：{_text(item['message'])}；"
                         f"位置：{_text(item['location'])}")
        for item in record["runtime_blockers"]:
            lines.append(f"- 真实运行阻碍：{_text(item['message'])}")
        for log in record["log_paths"]:
            lines.append("- 日志：" + _text(log))
    lines.extend(["", "## 修正与后续处理", ""])
    for correction in episode["corrections"]:
        lines.append(f"- 第 {correction['from_round']} → {correction['to_round']} 轮："
                     + "、".join(change["file"] for change in correction["changes"]))
    if not episode["corrections"]:
        lines.append("本次没有实施自动修改。")
    for cause in episode["reflection"]["failure_causes"]:
        lines.append("- 已记录问题：" + _text(cause))
    for rule in episode["reflection"]["reusable_rules"]:
        lines.append("- 候选经验（未经过真实 CFD 验证）：" + _text(rule))
    lines.extend(["", "完整变更前后内容见 episode.json 和各轮 correction.json。",
                  "本次未检索历史经验；规则修正不等于大模型反思或长期记忆。", ""])
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
