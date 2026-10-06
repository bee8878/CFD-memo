"""Build a sanitized, reproducible M4 release bundle from local study evidence."""
from __future__ import annotations

from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import re
import shutil
import tomllib
from typing import Any


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT / "docs/results/cross-task-v1"
PRIVATE_TEXT = re.compile(
    r"(?:[A-Za-z]:\\|\\\\wsl(?:\.localhost)?\\|/mnt/[a-z]/|/home/|\bsk-[A-Za-z0-9_-]{20,}\b)",
    re.IGNORECASE,
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _source_digest() -> tuple[str, int]:
    roots = [PROJECT / "src", PROJECT / "schemas"]
    files = [PROJECT / "pyproject.toml", PROJECT / "experiments/cross-task-v1.json"]
    for root in roots:
        files.extend(path for path in root.rglob("*")
                     if path.is_file() and path.suffix in {".py", ".json"})
    digest = sha256()
    for path in sorted(set(files)):
        relative = path.relative_to(PROJECT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), len(set(files))


def _installed(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _public_model(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value.get(key) for key in (
        "provider", "model", "mode", "timeout_seconds", "max_output_tokens",
    )}


def _experience_evidence(state: dict[str, Any], analysis: dict[str, Any], study: Path) -> list[dict]:
    rows = {row["evaluation_id"]: row for row in analysis["rows"]}
    evidence = []
    for evaluation in state["evaluations"]:
        row = rows[evaluation["id"]]
        if not row["effective_experience_use"] and not row["failure_avoided"]:
            continue
        episode_path = Path(evaluation["episode_path"]).resolve()
        if study not in episode_path.parents:
            raise ValueError("episode 必须位于 study 目录内")
        episode = _read(episode_path)
        memory = episode.get("memory", {})
        actions = sorted({
            (item.get("agent"), item.get("decision"), item.get("effect"), item.get("outcome"))
            for item in memory.get("uses", []) if item.get("outcome") == "effective"
        })
        preventions = [{
            "action": item.get("action"), "files": sorted(item.get("files", [])),
        } for item in episode.get("preventions", [])]
        evidence.append({
            "evaluation_id": evaluation["id"], "task_id": evaluation["task_id"],
            "group": evaluation["group"], "fault_profile": row["fault_profile"],
            "effective_experience_use": row["effective_experience_use"],
            "failure_avoided": row["failure_avoided"],
            "actions": [{"agent": item[0], "decision": item[1], "effect": item[2],
                         "outcome": item[3]} for item in actions],
            "preventions": preventions,
        })
    return evidence


def _assert_public(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        match = PRIVATE_TEXT.search(text)
        if match:
            raise ValueError(f"公开包包含本机路径或密钥格式：{path.name}")


def _readme(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    groups = summary["groups"]
    return "\n".join([
        "# CFD-Memo Cross-Task Evaluation v1", "",
        "This directory is a sanitized research artifact. It does not contain API keys, local",
        "paths, raw model responses, OpenFOAM field files, or private project documents.", "",
        "## Contents", "",
        "- `manifest.json`: frozen software, environment, model, and protocol metadata.",
        "- `summary.json`: aggregate and paired M3 statistics.",
        "- `failure-cases.json`: eight safe-stop cases without local paths.",
        "- `memory-evidence.json`: task-level effective memory actions and preventions.",
        "- `analysis-report.md`: human-readable statistical report.", "",
        "## Main Result", "",
        f"All groups completed {groups['no_memory']['engineering_success']['successes']}/5 "
        "engineering tasks. CFD-Memo reduced mean corrections from "
        f"{groups['no_memory']['corrections']['mean']:.1f} to "
        f"{groups['cfd_memo']['corrections']['mean']:.1f}, but the first-pass paired test was "
        f"p={summary['paired_vs_no_memory']['cfd_memo']['first_pass']['exact_two_sided_p']:.3f}. "
        "This small study does not establish statistical significance or physical generality.", "",
        "## Reproduce", "",
        "From the project root, create a Python 3.12 environment, install the project in editable",
        "mode, run `python -m pytest -q`, audit the frozen protocol with",
        "`python -m cfd_memo_agent.cli benchmark audit`, then analyze a completed local study",
        "with `python -m cfd_memo_agent.cli benchmark analyze --study <study-dir>`.", "",
        f"Source bundle SHA-256: `{manifest['source']['sha256']}`.", "",
        "OpenFOAM execution requires Foundation OpenFOAM 10. Raw studies remain local and are",
        "excluded by `.gitignore`. Engineering completion is not physical validation.", "",
        "This repository currently has no explicit open-source license. The artifact documents",
        "the experiment but does not grant reuse rights until the maintainer chooses a license.", "",
    ])


def build_release_bundle(
    study_dir: Path | str, *, output_dir: Path | str = DEFAULT_OUTPUT,
    overwrite: bool = False,
) -> dict[str, Any]:
    study = Path(study_dir).resolve()
    output = Path(output_dir).resolve()
    state = _read(study / "state.json")
    analysis = _read(study / "analysis/analysis.json")
    if state.get("status") != "completed" or analysis.get("totals", {}).get("evaluations") != 20:
        raise ValueError("M4 只接受已完成 M2 且包含 20 项 M3 分析的 study")
    if any(item.get("status") not in {"completed", "failed"} for item in state["evaluations"]):
        raise ValueError("M4 不接受 pending 或基础设施失败的评估")
    if output.exists():
        if not overwrite:
            raise ValueError("M4 输出目录已存在；使用 overwrite 显式重新生成")
        shutil.rmtree(output)
    output.mkdir(parents=True)

    project = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    source_hash, source_files = _source_digest()
    public_summary = {
        "schema_version": 1, "benchmark_id": "cfd-memo-cross-task-v1",
        "groups": analysis["groups"],
        "paired_vs_no_memory": analysis["paired_vs_no_memory"],
        "totals": analysis["totals"], "limitations": analysis["limitations"],
    }
    failures = [{key: row[key] for key in (
        "evaluation_id", "task_id", "group", "family", "partition",
        "fault_profile", "stop_code", "openfoam_started",
    )} for row in analysis["rows"] if not row["engineering_success"]]
    experiences = _experience_evidence(state, analysis, study)
    manifest = {
        "schema_version": 1, "release_id": "cross-task-v1",
        "project": {"name": project["name"], "version": project["version"]},
        "source": {"sha256": source_hash, "file_count": source_files,
                   "scope": ["src/", "schemas/", "pyproject.toml",
                             "experiments/cross-task-v1.json"]},
        "protocol_sha256": state["manifest_sha256"],
        "runtime": {
            "python": platform.python_version(),
            "jsonschema": _installed("jsonschema"),
            "python_dotenv": _installed("python-dotenv"),
            "openfoam_distribution": "Foundation", "openfoam_version": "10",
            "linux_backend": "WSL2 Ubuntu-22.04",
        },
        "model": _public_model(state["execution_model"]),
        "pricing_snapshot": state["pricing_snapshot"],
        "verification": {
            "test_command": "python -m pytest -q",
            "protocol_audit_command": "python -m cfd_memo_agent.cli benchmark audit",
            "analysis_command": "python -m cfd_memo_agent.cli benchmark analyze --study <study-dir>",
        },
        "privacy": {
            "raw_episodes_included": False, "field_files_included": False,
            "model_response_ids_included": False, "credentials_included": False,
            "private_research_documents_included": False,
        },
    }
    _write(output / "manifest.json", manifest)
    _write(output / "summary.json", public_summary)
    _write(output / "failure-cases.json", {"schema_version": 1, "cases": failures})
    _write(output / "memory-evidence.json", {"schema_version": 1, "records": experiences})
    shutil.copyfile(study / "analysis/report.md", output / "analysis-report.md")
    (output / "README.md").write_text(_readme(manifest, public_summary), encoding="utf-8")
    _assert_public(output)
    return {
        "status": "completed", "release_id": manifest["release_id"],
        "output_path": str(output), "source_sha256": source_hash,
        "files": sorted(path.name for path in output.iterdir() if path.is_file()),
        "failure_cases": len(failures), "memory_evidence_records": len(experiences),
        "raw_episodes_included": False, "physical_validated": False,
    }


__all__ = ["DEFAULT_OUTPUT", "build_release_bundle"]
