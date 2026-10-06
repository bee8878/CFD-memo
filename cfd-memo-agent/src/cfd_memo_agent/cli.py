"""Command line entry point for CFD-Memo Agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cfd_memo_agent.benchmark import DEFAULT_MANIFEST, audit_benchmark
from cfd_memo_agent.benchmark_analysis import analyze_benchmark
from cfd_memo_agent.benchmark_run import run_benchmark
from cfd_memo_agent.capabilities import list_capabilities
from cfd_memo_agent.case_adapters import list_case_adapters
from cfd_memo_agent.case_import import import_case, load_case_spec, validate_imported_case
from cfd_memo_agent.diagnoser import diagnose_log
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.gmsh_import import import_gmsh_mesh
from cfd_memo_agent.models import ModelConfigurationError, ModelSettings
from cfd_memo_agent.memory import MemoryManager
from cfd_memo_agent.memory.episodes import write_record
from cfd_memo_agent.memory.transfer import evaluate_transfer_manifest, run_real_transfer_study
from cfd_memo_agent.memory_study import analyze_memory_study, run_memory_study
from cfd_memo_agent.mesh_capabilities import list_mesh_capabilities
from cfd_memo_agent.orchestrator import plan_description
from cfd_memo_agent.physical_study import continue_physical_study, run_physical_study
from cfd_memo_agent.preflight import build_preflight, confirm_preflight
from cfd_memo_agent.release_bundle import DEFAULT_OUTPUT as DEFAULT_RELEASE_OUTPUT, build_release_bundle
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.runner.execution import COMMANDS, SCENARIOS
from cfd_memo_agent.tutorials import (
    DEFAULT_DISTRIBUTION, DEFAULT_OPENFOAM_VERSION, build_tutorial_index,
    load_tutorial_index, save_tutorial_index, search_tutorials, wsl_tutorial_root,
)
from cfd_memo_agent.tutorial_builder import build_tutorial_case
from cfd_memo_agent.tutorial_capabilities import list_tutorial_capabilities
from cfd_memo_agent.tutorial_resume import resume_tutorial_workflow
from cfd_memo_agent.correction import FAULTS
from cfd_memo_agent.workflow import run_workflow
from cfd_memo_agent.workbench import (
    create_task, explain_record, inspect_record, list_history,
)
from cfd_memo_agent.validator import validate_case
from cfd_memo_agent.validator.foam import read_json
from cfd_memo_agent.validator.task import issue, new_report


def main() -> None:
    parser = argparse.ArgumentParser(description="CFD-Memo Agent utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser(
        "new", help="Create and save a reviewed task without running CFD")
    new_parser.add_argument("description", help="Natural language CFD task description")
    new_parser.add_argument("--output", "-o", type=Path,
                            help="Task JSON path; defaults to the local task workspace")
    new_parser.add_argument("--fallback", choices=("stop", "rules"), default="stop")
    new_parser.add_argument("--tutorial-index", type=Path)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Show one task or workflow through a unified status view")
    inspect_parser.add_argument("path", type=Path, help="Task JSON, episode JSON, or workflow directory")

    history_parser = subparsers.add_parser(
        "history", help="List recent workflow records without browsing internal folders")
    history_parser.add_argument("--runs-dir", type=Path)
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.add_argument("--status")

    explain_parser = subparsers.add_parser(
        "explain", help="Explain one task or workflow record in beginner-oriented Chinese")
    explain_parser.add_argument("path", type=Path, help="Task JSON, episode JSON, or workflow directory")

    plan_parser = subparsers.add_parser("plan", help="Convert text into a structured CFD task")
    plan_parser.add_argument("description", help="Natural language CFD task description")
    plan_parser.add_argument("--output", "-o", type=Path, help="Optional path for the generated task JSON")
    plan_parser.add_argument("--details", action="store_true",
                             help="Show shared planning state instead of only task JSON")
    plan_parser.add_argument("--fallback", choices=("stop", "rules"), default="stop",
                             help="Explicit behavior when an LLM planner fails")
    plan_parser.add_argument("--tutorial-index", type=Path,
                             help="Optional local OpenFOAM tutorial index")

    generate_parser = subparsers.add_parser("generate", help="Generate an unverified case scaffold")
    generate_parser.add_argument("description", nargs="?", help="Natural language task")
    generate_parser.add_argument("--task", type=Path, help="Read a task JSON instead of text")
    generate_parser.add_argument("--runs-dir", type=Path, help="Parent directory for new runs")
    generate_parser.add_argument("--template-dir", type=Path, help="Cylinder scaffold directory")
    generate_parser.add_argument("--planner-fallback", choices=("stop", "rules"), default="stop")
    generate_parser.add_argument("--tutorial-index", type=Path)

    validate_parser = subparsers.add_parser("validate", help="Check task and case consistency")
    validate_parser.add_argument("--run", required=True, type=Path, help="C2 run directory")
    validate_parser.add_argument("--output", type=Path, help="Save a new report without overwriting")

    import_parser = subparsers.add_parser(
        "import-case", help="Inspect and copy an existing OpenFOAM case safely")
    import_parser.add_argument("source", type=Path, help="Existing OpenFOAM case directory")
    import_parser.add_argument("--runs-dir", type=Path, help="Parent directory for imported runs")

    mesh_import_parser = subparsers.add_parser(
        "import-mesh", help="Bind a validated Gmsh 2.2 ASCII mesh to a task")
    mesh_import_parser.add_argument("source", type=Path, help="External .msh file")
    mesh_import_parser.add_argument("--task", required=True, type=Path,
                                    help="Registered cavity task JSON")
    mesh_import_parser.add_argument("--boundary-map", type=Path,
                                    help="Optional JSON mapping from physical names to task boundaries")
    mesh_import_parser.add_argument("--runs-dir", type=Path)

    subparsers.add_parser("capabilities", help="List supported solver capabilities")

    tutorial_parser = subparsers.add_parser(
        "tutorials", help="Build or query a read-only OpenFOAM tutorial index")
    tutorial_actions = tutorial_parser.add_subparsers(dest="tutorial_action", required=True)
    tutorial_default = Path(__file__).resolve().parents[2] / "cases/tutorial-index/openfoam10.json"
    tutorial_index = tutorial_actions.add_parser("index", help="Index local tutorial dictionaries")
    tutorial_index.add_argument("--root", type=Path,
                                help="Tutorial root; defaults to the configured WSL OpenFOAM 10 path")
    tutorial_index.add_argument("--distribution", default=DEFAULT_DISTRIBUTION)
    tutorial_index.add_argument("--version", default=DEFAULT_OPENFOAM_VERSION)
    tutorial_index.add_argument("--output", type=Path, default=tutorial_default)
    tutorial_index.add_argument("--force", action="store_true",
                                help="Atomically replace an existing local index")
    tutorial_search = tutorial_actions.add_parser("search", help="Search a saved tutorial index")
    tutorial_search.add_argument("--index", type=Path, default=tutorial_default)
    tutorial_search.add_argument("--query")
    tutorial_search.add_argument("--solver")
    tutorial_search.add_argument("--physics-model")
    tutorial_search.add_argument("--field", action="append", default=[])
    tutorial_search.add_argument("--boundary-type", action="append", default=[])
    tutorial_search.add_argument("--mesh-tool")
    tutorial_search.add_argument("--limit", type=int, default=10)
    tutorial_build = tutorial_actions.add_parser(
        "build", help="Build the reviewed pitzDaily proposal without running it")
    tutorial_build.add_argument("--proposal", required=True, type=Path)
    tutorial_build.add_argument("--index", type=Path, default=tutorial_default)
    tutorial_build.add_argument("--runs-dir", type=Path)

    resume_parser = subparsers.add_parser(
        "resume", help="Approve, build, and run one proposal-ready tutorial workflow")
    resume_parser.add_argument("--workflow", required=True, type=Path,
                               help="I2 proposal-ready workflow directory")
    resume_parser.add_argument("--approve-tutorial", action="store_true",
                               help="Explicitly approve the saved tutorial proposal")
    resume_parser.add_argument("--runner", required=True, choices=("real",),
                               help="I4 only accepts real OpenFOAM execution")
    resume_parser.add_argument("--timeout", type=float, default=300.0,
                               help="Timeout per real OpenFOAM command in seconds")
    resume_parser.add_argument("--tutorial-index", type=Path, default=tutorial_default)

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
    run_parser.add_argument("--resume-attempt", type=Path,
                            help="Create a new attempt from a verified failed attempt checkpoint")
    run_parser.add_argument("--minimum-free-mb", type=int, default=100,
                            help="Minimum free disk space required before execution")
    run_parser.add_argument("--max-log-mb", type=int, default=100,
                            help="Maximum bytes written by one command log, in MiB")
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
    run_parser.add_argument("--tutorial-index", type=Path,
                            help="Optional local OpenFOAM tutorial index for planning")
    run_parser.add_argument("--preview", action="store_true",
                            help="Show the frozen task, memory sources, and risks without running")
    run_parser.add_argument("--confirm-plan",
                            help="Task-bound confirmation token returned by --preview")

    diagnose_parser = subparsers.add_parser("diagnose", help="Diagnose an existing log without running commands")
    diagnose_parser.add_argument("--log", required=True, type=Path)
    diagnose_parser.add_argument("--returncode", required=True, type=int, help="Recorded process exit code")
    diagnose_parser.add_argument(
        "--stage", choices=(*COMMANDS, "gmshToFoam", "simpleFoam"), default="icoFoam")
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

    memory_parser = subparsers.add_parser(
        "memory", help="Inspect and control evidence-backed local experience")
    memory_actions = memory_parser.add_subparsers(dest="memory_action", required=True)
    memory_extract = memory_actions.add_parser(
        "extract", help="Extract candidate experience from a saved episode")
    memory_extract.add_argument("--episode", required=True, type=Path)
    memory_extract.add_argument("--memory-dir", required=True, type=Path)
    memory_list = memory_actions.add_parser("list", help="List stored experience records")
    memory_list.add_argument("--memory-dir", required=True, type=Path)
    for action in ("disable", "enable", "approve", "reject"):
        control = memory_actions.add_parser(action, help=f"{action} one experience")
        control.add_argument("experience_id")
        control.add_argument("--memory-dir", required=True, type=Path)
        control.add_argument("--note")
    memory_delete = memory_actions.add_parser("delete", help="Delete one local experience")
    memory_delete.add_argument("experience_id")
    memory_delete.add_argument("--memory-dir", required=True, type=Path)
    memory_delete.add_argument("--confirm", action="store_true",
                               help="Confirm permanent deletion of this local record")
    memory_audit = memory_actions.add_parser(
        "audit", help="Detect incompatible active experience records")
    memory_audit.add_argument("--memory-dir", required=True, type=Path)
    memory_resolve = memory_actions.add_parser(
        "resolve", help="Resolve a conflict by explicitly choosing one experience")
    memory_resolve.add_argument("preferred_id")
    memory_resolve.add_argument("rejected_id")
    memory_resolve.add_argument("--memory-dir", required=True, type=Path)
    memory_resolve.add_argument("--note", required=True)
    memory_acceptance = memory_actions.add_parser(
        "acceptance", help="Evaluate real cross-task experience transfer")
    memory_acceptance.add_argument("--manifest", required=True, type=Path)
    memory_acceptance.add_argument("--memory-dir", required=True, type=Path)
    memory_acceptance.add_argument("--output", type=Path)
    memory_collect = memory_actions.add_parser(
        "collect-transfer", help="Run an isolated real repair and reuse acceptance pair")
    memory_collect.add_argument("--task", required=True, type=Path)
    memory_collect.add_argument("--memory-dir", required=True, type=Path)
    memory_collect.add_argument("--runs-dir", type=Path)
    memory_collect.add_argument("--fault", choices=FAULTS, default="missing-boundary")
    memory_collect.add_argument("--timeout", type=float, default=300.0)

    benchmark_parser = subparsers.add_parser(
        "benchmark", help="Audit frozen cross-task evaluation manifests")
    benchmark_actions = benchmark_parser.add_subparsers(
        dest="benchmark_action", required=True)
    benchmark_audit = benchmark_actions.add_parser(
        "audit", help="Validate task diversity, pairings, and registered capabilities")
    benchmark_audit.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    benchmark_run = benchmark_actions.add_parser(
        "run", help="Prepare or resume the frozen paired benchmark")
    benchmark_run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    benchmark_run.add_argument("--output-dir", type=Path)
    benchmark_run.add_argument("--resume", type=Path)
    benchmark_run.add_argument("--runner", required=True, choices=("simulated", "real"))
    benchmark_run.add_argument("--limit", type=int,
                               help="Run at most this many pending evaluations")
    benchmark_run.add_argument("--tutorial-root", type=Path,
                               help="OpenFOAM tutorials root containing pitzDaily")
    benchmark_analyze = benchmark_actions.add_parser(
        "analyze", help="Recompute M3 statistics from a completed M2 study")
    benchmark_analyze.add_argument("--study", required=True, type=Path)
    benchmark_analyze.add_argument("--force", action="store_true",
                                   help="Atomically replace existing M3 outputs")
    benchmark_package = benchmark_actions.add_parser(
        "package", help="Build a sanitized M4 reproducibility bundle")
    benchmark_package.add_argument("--study", required=True, type=Path)
    benchmark_package.add_argument("--output-dir", type=Path, default=DEFAULT_RELEASE_OUTPUT)
    benchmark_package.add_argument("--force", action="store_true",
                                   help="Replace an existing sanitized bundle")

    subparsers.add_parser("model-info", help="Show redacted Stage D model configuration")

    args = parser.parse_args()

    if args.command in {
        "plan", "generate", "run", "diagnose", "validate", "physics-study",
        "memory-study", "model-info", "import-case", "import-mesh", "capabilities",
        "tutorials", "resume", "memory", "benchmark",
        "new", "inspect", "history", "explain",
    }:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")

    if args.command == "benchmark":
        try:
            if args.benchmark_action == "audit":
                result = audit_benchmark(args.manifest)
            elif args.benchmark_action == "run":
                if args.resume is not None and args.output_dir is not None:
                    parser.error("--resume 与 --output-dir 不能同时使用")
                result = run_benchmark(
                    manifest_path=args.manifest, output_dir=args.output_dir,
                    resume=args.resume, runner=args.runner, limit=args.limit,
                    tutorial_root=args.tutorial_root,
                )
            elif args.benchmark_action == "analyze":
                result = analyze_benchmark(args.study, overwrite=args.force)
            else:
                result = build_release_bundle(
                    args.study, output_dir=args.output_dir, overwrite=args.force,
                )
        except (OSError, UnicodeError, ValueError, KeyError, TypeError,
                ModelConfigurationError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.benchmark_action == "run":
            raise SystemExit(0 if result["status"] in {"completed", "partial"} else 1)
        return

    if args.command in {"new", "inspect", "history", "explain"}:
        try:
            if args.command == "new":
                result = create_task(
                    args.description, output=args.output, fallback=args.fallback,
                    tutorial_index_path=args.tutorial_index,
                )
            elif args.command == "inspect":
                result = inspect_record(args.path)
            elif args.command == "history":
                result = list_history(args.runs_dir, limit=args.limit, status=args.status)
            else:
                result = explain_record(args.path)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "new":
            raise SystemExit(0 if result["status"] == "ready" else 1)
        return

    if args.command == "model-info":
        try:
            status = ModelSettings.from_env().public_status()
        except ModelConfigurationError as exc:
            parser.error(str(exc))
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return

    if args.command == "capabilities":
        print(json.dumps({
            "capabilities": list_capabilities(),
            "case_adapters": list_case_adapters(),
            "mesh_capabilities": list_mesh_capabilities(),
            "tutorial_capabilities": list_tutorial_capabilities(),
        }, ensure_ascii=False, indent=2))
        return

    if args.command == "tutorials":
        try:
            if args.tutorial_action == "index":
                root = args.root or wsl_tutorial_root(args.distribution, args.version)
                index = build_tutorial_index(root, version=args.version)
                target = save_tutorial_index(index, args.output, overwrite=args.force)
                result = {
                    "status": "indexed", "index_path": str(target),
                    "tutorial_root": index["tutorial_root"],
                    "openfoam_version": index["openfoam_version"],
                    "case_count": index["case_count"],
                    "skipped_count": index["skipped_count"],
                    "scripts_executed": False,
                }
            elif args.tutorial_action == "search":
                index = load_tutorial_index(args.index)
                result = search_tutorials(
                    index, query=args.query, solver=args.solver,
                    physics_model=args.physics_model, fields=args.field,
                    boundary_types=args.boundary_type, mesh_tool=args.mesh_tool,
                    limit=args.limit,
                )
            else:
                result = build_tutorial_case(
                    args.proposal, index_path=args.index, runs_dir=args.runs_dir)
        except (OSError, UnicodeError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.tutorial_action == "build":
            raise SystemExit(0 if result["status"] == "built" else 1)
        return

    if args.command == "resume":
        try:
            result = resume_tutorial_workflow(
                args.workflow, approved=args.approve_tutorial, mode=args.runner,
                timeout=args.timeout, tutorial_index_path=args.tutorial_index,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "completed" else 1)

    if args.command == "import-case":
        try:
            result = import_case(args.source, runs_dir=args.runs_dir)
        except (ValueError, OSError, UnicodeError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["validation"]["config_valid"] else 1)

    if args.command == "import-mesh":
        try:
            result = import_gmsh_mesh(
                args.source, task_path=args.task, boundary_map=args.boundary_map,
                runs_dir=args.runs_dir,
            )
        except (ValueError, OSError, UnicodeError, KeyError, TypeError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["validation"]["config_valid"] else 1)

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

    if args.command == "memory":
        manager = MemoryManager(args.memory_dir)
        try:
            if args.memory_action == "extract":
                result = manager.extract_episode_file(args.episode)
            elif args.memory_action == "list":
                result = {"records": manager.list_experiences()}
            elif args.memory_action == "delete":
                if not args.confirm:
                    parser.error("memory delete 必须显式提供 --confirm")
                manager.delete_experience(args.experience_id)
                result = {"deleted": args.experience_id}
            elif args.memory_action == "audit":
                result = manager.audit_conflicts()
            elif args.memory_action == "resolve":
                result = manager.resolve_conflict(
                    args.preferred_id, args.rejected_id, note=args.note)
            elif args.memory_action == "acceptance":
                result = evaluate_transfer_manifest(args.manifest, manager)
                if args.output is not None:
                    write_record(args.output, result)
            elif args.memory_action == "collect-transfer":
                result = run_real_transfer_study(
                    args.task, memory_dir=args.memory_dir, runs_dir=args.runs_dir,
                    fault=args.fault, timeout=args.timeout)
            else:
                controls = {
                    "disable": {"state": "disabled"},
                    "enable": {"state": "active"},
                    "approve": {"approved": True},
                    "reject": {"approved": False, "state": "disabled"},
                }
                result = manager.set_user_control(
                    args.experience_id, note=args.note, **controls[args.memory_action])
        except (OSError, UnicodeError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.memory_action in {"acceptance", "collect-transfer"}:
            raise SystemExit(0 if result["status"] == "passed" else 1)
        if args.memory_action == "audit":
            raise SystemExit(0 if result["status"] == "clear" else 1)
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
        if args.run is not None and (args.preview or args.confirm_plan is not None):
            parser.error("--preview/--confirm-plan 只用于从 task 启动的新工作流")
        if args.run is None and args.scenario is not None:
            parser.error("--scenario 仅用于 C4 的 --run；工作流故障演示请使用 --fault")
        if args.run is None and (
            args.resume_attempt is not None or args.minimum_free_mb != 100
            or args.max_log_mb != 100
        ):
            parser.error("恢复与资源限制选项目前只用于 --run 单次真实执行")
        if args.preview and args.task is None:
            parser.error("--preview 需要 --task；先用 new 把自然语言冻结为任务")
        if args.description is not None and args.runner == "real":
            parser.error("真实执行必须先用 new 保存 task，再用 run --task --preview 核对")
        if args.confirm_plan is not None and (args.task is None or args.runner != "real"):
            parser.error("--confirm-plan 只用于 --task 的真实执行")
        try:
            confirmed_preflight = None
            if args.task is not None and (args.preview or args.runner == "real"):
                preview = build_preflight(
                    args.task, runner_mode=args.runner, memory_mode=args.memory_mode,
                    memory_dir=args.memory_dir, max_corrections=args.max_corrections,
                    timeout=args.timeout,
                )
                if args.preview:
                    print(json.dumps(preview, ensure_ascii=False, indent=2))
                    raise SystemExit(0 if preview["status"] == "ready" else 1)
                if args.confirm_plan is None:
                    print(json.dumps(preview, ensure_ascii=False, indent=2))
                    raise SystemExit(1)
                confirmed_preflight = confirm_preflight(preview, args.confirm_plan)
            if args.run is not None:
                result = run_case(
                    args.run, mode=args.runner, scenario=args.scenario, timeout=args.timeout,
                    resume_attempt=args.resume_attempt,
                    minimum_free_mb=args.minimum_free_mb, max_log_mb=args.max_log_mb,
                )
            else:
                result = run_workflow(args.description, task_path=args.task, mode=args.runner,
                                      runs_dir=args.runs_dir, max_corrections=args.max_corrections,
                                      timeout=args.timeout, fault=args.fault,
                                      planner_fallback=args.planner_fallback,
                                      memory_mode=args.memory_mode,
                                      memory_dir=args.memory_dir,
                                      memory_learning=not args.freeze_memory,
                                      tutorial_index_path=args.tutorial_index,
                                      confirmed_preflight=confirmed_preflight)
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
        if (run_dir / "import.json").is_file() and (run_dir / "case-spec.json").is_file():
            try:
                spec = load_case_spec(run_dir / "case-spec.json")
                report = validate_imported_case(spec, run_dir / "case")
            except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
                report = new_report()
                report["errors"].append(issue(
                    "CASE_SPEC_READ", f"CaseSpec 无法读取或格式错误：{exc}",
                    str(run_dir / "case-spec.json")))
        else:
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
                state = plan_description(
                    args.description, fallback=args.planner_fallback,
                    tutorial_index_path=args.tutorial_index)
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
            state = plan_description(
                args.description, fallback=args.fallback,
                tutorial_index_path=args.tutorial_index)
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

