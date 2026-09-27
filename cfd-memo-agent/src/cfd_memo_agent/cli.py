"""Command line entry point for CFD-Memo Agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cfd_memo_agent.diagnoser import diagnose_log
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.models import ModelConfigurationError, ModelSettings
from cfd_memo_agent.memory_study import analyze_memory_study, run_memory_study
from cfd_memo_agent.orchestrator import plan_description
from cfd_memo_agent.physical_study import continue_physical_study, run_physical_study
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.runner.execution import COMMANDS, SCENARIOS
from cfd_memo_agent.correction import FAULTS
from cfd_memo_agent.workflow import run_workflow
from cfd_memo_agent.validator import validate_case
from cfd_memo_agent.validator.foam import read_json
from cfd_memo_agent.validator.task import issue, new_report


def main() -> None:
    parser = argparse.ArgumentParser(description="CFD-Memo Agent utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Convert text into a structured CFD task")
    plan_parser.add_argument("description", help="Natural language CFD task description")
    plan_parser.add_argument("--output", "-o", type=Path, help="Optional path for the generated task JSON")
    plan_parser.add_argument("--details", action="store_true",
                             help="Show shared planning state instead of only task JSON")
    plan_parser.add_argument("--fallback", choices=("stop", "rules"), default="stop",
                             help="Explicit behavior when an LLM planner fails")

    generate_parser = subparsers.add_parser("generate", help="Generate an unverified case scaffold")
    generate_parser.add_argument("description", nargs="?", help="Natural language task")
    generate_parser.add_argument("--task", type=Path, help="Read a task JSON instead of text")
    generate_parser.add_argument("--runs-dir", type=Path, help="Parent directory for new runs")
    generate_parser.add_argument("--template-dir", type=Path, help="Cylinder scaffold directory")
    generate_parser.add_argument("--planner-fallback", choices=("stop", "rules"), default="stop")

    validate_parser = subparsers.add_parser("validate", help="Check task and case consistency")
    validate_parser.add_argument("--run", required=True, type=Path, help="C2 run directory")
    validate_parser.add_argument("--output", type=Path, help="Save a new report without overwriting")

    run_parser = subparsers.add_parser("run", help="Run a workflow or execute one saved C2 run")
    run_parser.add_argument("description", nargs="?", help="Natural language task for a workflow")
    run_parser.add_argument("--task", type=Path, help="Task JSON for a workflow")
    run_parser.add_argument("--run", type=Path, help="Existing C2 run directory (single execution)")
    run_parser.add_argument("--runs-dir", type=Path, help="Parent directory for new workflows")
    run_parser.add_argument("--max-corrections", type=int, help="Maximum automatic corrections")
    run_parser.add_argument("--fault", choices=FAULTS, help="Inject a first-round simulated configuration fault")
    run_parser.add_argument("--runner", required=True, choices=("simulated", "real"))
    run_parser.add_argument("--scenario", choices=tuple(SCENARIOS), help="Simulated log scenario")
    run_parser.add_argument("--timeout", type=float, default=300.0, help="Timeout per real command in seconds")
    run_parser.add_argument("--planner-fallback", choices=("stop", "rules"), default="stop",
                            help="Explicit behavior when an LLM planner fails")
    run_parser.add_argument("--memory-mode",
                            choices=("no_memory", "simple_cache", "retrieval_only", "cfd_memo"),
                            default="no_memory",
                            help="Use no history or the local structured memory store")
    run_parser.add_argument("--memory-dir", type=Path,
                            help="Local history store; invalid only with no_memory")
    run_parser.add_argument("--freeze-memory", action="store_true",
                            help="Read existing history without learning from this evaluation task")

    diagnose_parser = subparsers.add_parser("diagnose", help="Diagnose an existing log without running commands")
    diagnose_parser.add_argument("--log", required=True, type=Path)
    diagnose_parser.add_argument("--returncode", required=True, type=int, help="Recorded process exit code")
    diagnose_parser.add_argument("--stage", choices=COMMANDS, default="icoFoam")
    diagnose_parser.add_argument("--timed-out", action="store_true")
    diagnose_parser.add_argument("--simulated", action="store_true", help="Label synthetic log evidence")
    diagnose_parser.add_argument("--output", type=Path, help="Save a new report without overwriting")

    study_parser = subparsers.add_parser("physics-study", help="Run the fixed C6 Re=100 validation matrix")
    study_parser.add_argument("--task", type=Path,
                              default=Path(__file__).resolve().parents[2] / "examples/task.cylinder-2d-physical.json")
    study_parser.add_argument("--baseline-episode", type=Path,
                              help="Reuse a matching completed real baseline episode")
    study_parser.add_argument("--resume", type=Path,
                              help="Continue an existing study and run only missing variants")
    study_parser.add_argument("--runs-dir", type=Path, help="Parent directory for the study")
    study_parser.add_argument("--timeout", type=float, default=1800.0,
                              help="Timeout per OpenFOAM command in seconds")

    memory_study_parser = subparsers.add_parser(
        "memory-study", help="Run the frozen four-group configuration-memory experiment",
    )
    memory_study_parser.add_argument("--output-dir", type=Path)
    memory_study_parser.add_argument("--repeats", type=int,
                                     help="Override the frozen formal repeat count")
    memory_study_parser.add_argument("--analyze", type=Path,
                                     help="Analyze an existing memory-study directory")

    subparsers.add_parser("model-info", help="Show redacted Stage D model configuration")

    args = parser.parse_args()

    if args.command in {
        "plan", "generate", "run", "diagnose", "validate", "physics-study",
        "memory-study", "model-info",
    }:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")

    if args.command == "model-info":
        try:
            status = ModelSettings.from_env().public_status()
        except ModelConfigurationError as exc:
            parser.error(str(exc))
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return

    if args.command == "memory-study":
        try:
            if args.analyze is not None:
                if args.output_dir is not None or args.repeats is not None:
                    parser.error("--analyze 不接受 --output-dir 或 --repeats")
                result = analyze_memory_study(args.analyze)
            else:
                result = run_memory_study(output_dir=args.output_dir, repeats=args.repeats)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "run":
        if sum(value is not None for value in (args.description, args.task, args.run)) != 1:
            parser.error("run 必须选择需求、--task 或 --run 中的一种输入")
        if args.run is not None and any(value is not None for value in
                                        (args.fault, args.max_corrections, args.runs_dir)):
            parser.error("--run 是 C4 单次执行，不接受 --fault、--max-corrections 或 --runs-dir")
        if args.run is not None and (
            args.memory_mode != "no_memory" or args.memory_dir is not None or args.freeze_memory
        ):
            parser.error("--run 是 C4 单次执行，不接受长期记忆选项")
        if args.run is None and args.scenario is not None:
            parser.error("--scenario 仅用于 C4 的 --run；工作流故障演示请使用 --fault")
        try:
            if args.run is not None:
                result = run_case(args.run, mode=args.runner, scenario=args.scenario, timeout=args.timeout)
            else:
                result = run_workflow(args.description, task_path=args.task, mode=args.runner,
                                      runs_dir=args.runs_dir, max_corrections=args.max_corrections,
                                      timeout=args.timeout, fault=args.fault,
                                      planner_fallback=args.planner_fallback,
                                      memory_mode=args.memory_mode,
                                      memory_dir=args.memory_dir,
                                      memory_learning=not args.freeze_memory)
        except (ValueError, OSError, ModelConfigurationError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] in {"completed", "simulated_success"} else 1)

    if args.command == "physics-study":
        if args.resume is not None and any(value is not None for value in
                                           (args.baseline_episode, args.runs_dir)):
            parser.error("--resume 不接受 --baseline-episode 或 --runs-dir")
        try:
            if args.resume is not None:
                result = continue_physical_study(study_path=args.resume, task_path=args.task,
                                                 timeout=args.timeout)
            else:
                result = run_physical_study(task_path=args.task, runs_dir=args.runs_dir,
                                            baseline_episode=args.baseline_episode, timeout=args.timeout)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["physical_validated"] else 1)

    if args.command == "diagnose":
        try:
            result = diagnose_log(args.log.read_text(encoding="utf-8-sig"),
                                  returncode=args.returncode, timed_out=args.timed_out,
                                  stage=args.stage, simulated=args.simulated)
            rendered = json.dumps(result, ensure_ascii=False, indent=2)
            if args.output:
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(rendered + "\n")
        except (OSError, UnicodeError) as exc:
            parser.error(f"无法读取日志或新建报告：{exc}")
        print(rendered)
        raise SystemExit(0 if result["status"] == "completed" else 1)

    if args.command == "validate":
        run_dir = args.run.resolve()
        try:
            task = read_json(run_dir / "task.json")
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            report = new_report()
            report["errors"].append(issue("TASK_READ", f"任务文件无法读取或格式错误：{exc}",
                                          str(run_dir / "task.json")))
        else:
            report = validate_case(task, run_dir / "case")
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            try:
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(rendered + "\n")
            except OSError as exc:
                parser.error(f"无法新建报告；不会覆盖已有文件：{exc}")
        print(rendered)
        raise SystemExit(0 if report["config_valid"] else 1)

    if args.command == "generate":
        if bool(args.description) == bool(args.task):
            parser.error("generate requires either a description or --task, but not both.")
        try:
            task = (
                json.loads(args.task.read_text(encoding="utf-8"))
                if args.task else None
            )
            if task is None:
                state = plan_description(args.description, fallback=args.planner_fallback)
                if state["status"] != "ready":
                    detail = "；".join(state["questions"])
                    if not detail and state["error"]:
                        detail = state["error"]["message"]
                    raise ValueError(f"任务规划未完成：{detail}")
                task = state["task"]
            result = generate_case(task, runs_dir=args.runs_dir, template_dir=args.template_dir)
        except (ValueError, KeyError, TypeError, OSError, ModelConfigurationError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "plan":
        try:
            state = plan_description(args.description, fallback=args.fallback)
        except ModelConfigurationError as exc:
            parser.error(str(exc))
        value = state if args.details or state["status"] != "ready" else state["task"]
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        if state["status"] != "ready":
            raise SystemExit(1)


if __name__ == "__main__":
    main()

