"""Small, explicit rules for current OpenFOAM logs and C3 reports."""
from __future__ import annotations

import math
import re


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
RESIDUAL = re.compile(
    rf"Solving for (\w+),\s*Initial residual = ({NUMBER}),\s*"
    rf"Final residual = ({NUMBER}),\s*No Iterations (\d+)"
)


def finding(code, message, location, evidence, action, suggestion):
    return {
        "code": code, "message": message, "location": location,
        "evidence": evidence, "action": action, "suggestion": suggestion,
    }


def diagnose_validation(report: dict) -> dict:
    findings = []
    for problem in report["errors"] + report["runtime_blockers"]:
        code, location = problem["code"], problem["location"]
        if location.startswith("task"):
            action = "user_clarification"
            suggestion = "核对任务参数和物理意图，确认后重新生成配置。"
        elif report["task_valid"] and (
            code in {
                "VALUE_MISMATCH", "BOUNDARY_NAMES", "BOUNDARY_TYPE",
                "FIELD_VALUE", "CONFIG_STRUCTURE",
            }
            or (
                location.startswith("constant/physicalProperties")
                and code in {"CONFIG_NUMBER", "CONFIG_ENTRY", "DIMENSIONS"}
            )
        ):
            action = "rule_candidate"
            suggestion = "核对有效任务与配置差异，后续可按任务重新生成对应字段并复验。"
        else:
            action = "manual_required"
            suggestion = "检查证据与原文件；网格及几何阻碍需在 C6 完成真实验证。"
        findings.append(finding(code, problem["message"], location,
                                problem["message"], action, suggestion))
    return {"status": "blocked" if findings else "clear", "findings": findings}


def diagnose_log(text: str, *, returncode: int | None, timed_out: bool = False,
                 stage: str = "icoFoam", simulated: bool = False) -> dict:
    """Report process/log evidence, never physical validity or convergence."""
    findings, residuals, times = [], [], []
    current_time = None
    lines = text.splitlines()
    for line_number, line in enumerate(lines, 1):
        time_match = re.fullmatch(rf"\s*Time\s*=\s*({NUMBER})\s*s?\s*", line)
        if time_match:
            value = float(time_match[1])
            if math.isfinite(value):
                current_time = value
                times.append(value)
        match = RESIDUAL.search(line)
        if match:
            initial, final = float(match[2]), float(match[3])
            if math.isfinite(initial) and math.isfinite(final):
                residuals.append({"time": current_time, "field": match[1],
                                  "initial": initial, "final": final,
                                  "iterations": int(match[4])})
        location = f"{stage}.log:{line_number}"
        missing = re.search(r"Cannot find patchField entry for\s+([\w.-]+)", line)
        if missing:
            findings.append(finding(
                "MISSING_BOUNDARY", f"缺少边界字段：{missing[1]}", location, line.strip(),
                "rule_candidate", "按有效 task 的边界设置补齐对应字段，再重新验证；不得猜测边界物理含义。"))
        if re.search(r"(?:expected scalar value for nu|keyword nu is undefined|negative viscosity)",
                     line, re.IGNORECASE):
            findings.append(finding(
                "BAD_TRANSPORT", "黏度配置缺失、格式错误或非法", location, line.strip(),
                "rule_candidate", "核对 physicalProperties 的 nu、量纲及有效 task；按 nu=U*D/Re 复核后重新生成。"))

    fatal = next((line.strip() for line in lines if re.search(
        r"FOAM FATAL|FOAM exiting|FOAM aborting|^\s*Floating point exception"
        r"(?:\s*\(core dumped\))?\s*$|Segmentation fault|\b(?:nan|inf|infinity)\b",
        line, re.IGNORECASE)), None)
    if timed_out:
        findings.append(finding("TIMEOUT", "命令执行超时，已停止进程", stage,
                                "timeout", "manual_required", "检查最后日志、网格规模及时间步设置后决定是否重试。"))
    elif fatal and not findings:
        findings.append(finding("UNKNOWN_FAILURE", "日志包含未分类的运行错误", stage,
                                fatal, "manual_required", "保留完整日志，人工定位原因后再决定修改。"))
    elif returncode != 0 and not findings:
        findings.append(finding("UNKNOWN_FAILURE", "进程没有正常退出", stage,
                                f"returncode={returncode}", "manual_required", "查看完整日志与执行环境。"))

    end_seen = any(line.strip() == "End" for line in lines)
    mesh_ok = any(line.strip() == "Mesh OK." for line in lines)
    completion_seen = mesh_ok if stage == "checkMesh" else end_seen
    if not timed_out and returncode == 0 and not findings and not completion_seen:
        findings.append(finding("INCOMPLETE_LOG", "缺少本阶段的完成标记", stage,
                                "Mesh OK. not found" if stage == "checkMesh" else "End not found",
                                "manual_required", "检查日志是否截断，不能仅凭退出码认定执行完成。"))
    if stage == "checkMesh" and re.search(r"Failed\s+[1-9]\d*\s+mesh checks", text):
        findings.append(finding("MESH_CHECK_FAILED", "网格检查报告失败", stage,
                                "Failed mesh checks", "manual_required", "修复网格并重新执行 checkMesh。"))
    return {
        "status": "timeout" if timed_out else "failed" if findings else "completed",
        "simulated": simulated, "stage": stage, "returncode": returncode,
        "timed_out": timed_out, "end_seen": end_seen, "mesh_check_passed": mesh_ok and not findings,
        "last_time": times[-1] if times else None, "residuals": residuals,
        "findings": findings, "physical_validated": False,
    }
