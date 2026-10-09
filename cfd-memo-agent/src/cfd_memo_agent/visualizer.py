"""Open a verified real OpenFOAM result in ParaView on explicit request."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable

from cfd_memo_agent.runner.backend import BASHRC, DISTRO, wsl_path

MESH_FILES = ("boundary", "faces", "neighbour", "owner", "points")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取执行证据 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"执行证据不是 JSON 对象：{path}")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _execution_candidates(root: Path) -> list[Path]:
    direct = root / "execution.json"
    paths = [direct] if direct.is_file() else list(root.rglob("execution.json"))
    safe = []
    for path in paths:
        resolved = path.resolve()
        if _inside(resolved, root) and not path.is_symlink():
            safe.append(path)
    return safe


def resolve_result_case(run_dir: Path | str) -> dict[str, Any]:
    """Select the newest completed real attempt below one run or workflow."""
    root = Path(run_dir).absolute()
    if not root.is_dir():
        raise ValueError(f"运行目录不存在：{root}")
    root = root.resolve()
    candidates = []
    for execution_path in _execution_candidates(root):
        report = _load_json(execution_path)
        if (report.get("status") == "completed" and report.get("mode") == "real"
                and report.get("simulated") is False):
            candidates.append((execution_path.stat().st_mtime_ns, execution_path, report))
    if not candidates:
        raise ValueError("没有找到已完成的真实 OpenFOAM attempt；模拟结果不能可视化")
    _, execution_path, report = max(candidates, key=lambda item: item[0])
    attempt = execution_path.parent.resolve()
    case = (attempt / "case").resolve()
    if not case.is_dir() or not _inside(case, root):
        raise ValueError("完成记录对应的 case 缺失或超出所选运行目录")
    return {
        "run_path": str(root), "attempt_path": str(attempt),
        "case_path": str(case), "execution_path": str(execution_path.resolve()),
        "execution": report,
    }


def _verify_artifact(path: Path, item: dict[str, Any]) -> None:
    if not path.is_file():
        raise ValueError(f"结果文件缺失：{path}")
    data = path.read_bytes()
    if len(data) != item.get("size_bytes"):
        raise ValueError(f"结果文件大小与 result-index 不一致：{path}")
    if hashlib.sha256(data).hexdigest() != item.get("sha256"):
        raise ValueError(f"结果文件哈希与 result-index 不一致：{path}")


def check_visualization_ready(selection: dict[str, Any]) -> dict[str, Any]:
    """Verify mesh and indexed U/p fields without claiming physical validity."""
    attempt = Path(selection["attempt_path"]).resolve()
    case = Path(selection["case_path"]).resolve()
    run_root = attempt.parent.parent.resolve()
    mesh = case / "constant/polyMesh"
    missing_mesh = [name for name in MESH_FILES if not (mesh / name).is_file()]
    if missing_mesh:
        raise ValueError("网格文件不完整：" + "、".join(missing_mesh))

    result_index = _load_json(attempt / "result-index.json")
    if result_index.get("status") != "completed":
        raise ValueError("result-index 未记录真实执行完成")
    artifacts = result_index.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("result-index 缺少 artifacts")
    fields: dict[str, Path] = {}
    for item in artifacts:
        if not isinstance(item, dict) or item.get("role") != "field":
            continue
        relative = item.get("path")
        if not isinstance(relative, str):
            continue
        path = (run_root / Path(relative)).resolve()
        if path.name in {"U", "p"} and _inside(path, case):
            _verify_artifact(path, item)
            fields[path.name] = path
    if set(fields) != {"U", "p"} or fields["U"].parent != fields["p"].parent:
        raise ValueError("result-index 中没有同一最终时间目录的可信 U/p 场文件")

    evidence = _load_json(attempt / "result-evidence.json")
    if evidence.get("actual_cells") in (None, 0) or evidence.get("end_time") is None:
        raise ValueError("结果证据缺少网格规模或结束时间")
    return {
        **selection,
        "result_index_path": str((attempt / "result-index.json").resolve()),
        "time_directory": str(fields["U"].parent),
        "fields": {name: str(path) for name, path in sorted(fields.items())},
        "actual_cells": evidence["actual_cells"],
        "end_time": evidence["end_time"],
        "physical_validated": result_index.get("physical_validated") is True,
    }


def _probe(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, capture_output=True, timeout=20, check=False)


def discover_visualizer() -> dict[str, Any]:
    """Find ParaView and a graphical display without installing anything."""
    if os.name != "nt":
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            raise OSError("未检测到图形显示环境；请在桌面会话中运行 view")
        viewer = shutil.which("paraFoam") or shutil.which("paraview")
        if not viewer:
            raise OSError("未找到 paraFoam 或 paraview")
        return {"backend": "native", "viewer": viewer,
                "viewer_name": Path(viewer).name}

    wsl = shutil.which("wsl")
    if not wsl:
        raise OSError("未找到 WSL；无法启动 Linux ParaView")
    prefix = [wsl, "--distribution", DISTRO, "--exec"]
    script = (
        'source "$1" >/dev/null || exit 20; '
        'test -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" || exit 21; '
        'if command -v paraFoam >/dev/null; then command -v paraFoam; '
        'elif command -v paraview >/dev/null; then command -v paraview; '
        'else exit 22; fi'
    )
    result = _probe(prefix + ["bash", "-c", script, "cfd-memo-view", BASHRC])
    if result.returncode == 21:
        raise OSError("WSLg 图形显示不可用；请更新 WSL 并确认 Linux GUI 可运行")
    if result.returncode == 22:
        raise OSError("Ubuntu-22.04 中未安装 paraFoam 或 paraview")
    if result.returncode:
        raise OSError("无法加载 OpenFOAM 10 可视化环境")
    viewer = result.stdout.decode("utf-8", errors="replace").strip()
    if not viewer.startswith("/"):
        raise OSError("ParaView 探测返回了异常路径")
    return {"backend": "wsl", "distribution": DISTRO, "prefix": prefix,
            "viewer": viewer, "viewer_name": Path(viewer).name}


def launch_paraview(
    ready: dict[str, Any], environment: dict[str, Any],
    *, popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> dict[str, Any]:
    """Launch the viewer asynchronously; the caller explicitly requested this side effect."""
    case = Path(ready["case_path"]).resolve()
    marker = case / "CFD-Memo.foam"
    marker.touch(exist_ok=True)
    viewer = environment["viewer"]
    if environment["backend"] == "wsl":
        linux_case = wsl_path(environment, case)
        linux_marker = wsl_path(environment, marker)
        if environment["viewer_name"] == "paraFoam":
            arguments = [viewer, "-builtin", "-case", linux_case]
        else:
            arguments = [viewer, linux_marker]
        command = environment["prefix"] + [
            "bash", "-c", 'source "$1" >/dev/null || exit; shift; exec "$@"',
            "cfd-memo-view", BASHRC, *arguments,
        ]
        cwd = Path(environment["prefix"][0]).resolve().parent
    else:
        command = ([viewer, "-builtin", "-case", str(case)]
                   if environment["viewer_name"] == "paraFoam"
                   else [viewer, str(marker)])
        cwd = case
    options: dict[str, Any] = {
        "cwd": cwd, "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
        "shell": False,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    process = popen(command, **options)
    return {"pid": process.pid, "command": command, "marker_path": str(marker)}


def open_result(
    run_dir: Path | str, *,
    discoverer: Callable[[], dict[str, Any]] | None = None,
    launcher: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve, verify, and explicitly open one completed real result."""
    ready = check_visualization_ready(resolve_result_case(run_dir))
    environment = (discoverer or discover_visualizer)()
    launched = (launcher or launch_paraview)(ready, environment)
    return {
        "status": "launched", "viewer": environment["viewer_name"],
        "backend": environment["backend"], "pid": launched["pid"],
        "run_path": ready["run_path"], "attempt_path": ready["attempt_path"],
        "case_path": ready["case_path"], "time_directory": ready["time_directory"],
        "fields": ready["fields"], "actual_cells": ready["actual_cells"],
        "end_time": ready["end_time"], "marker_path": launched["marker_path"],
        "physical_validated": ready["physical_validated"],
    }
