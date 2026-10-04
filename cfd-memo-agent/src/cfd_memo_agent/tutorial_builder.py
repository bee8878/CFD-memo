"""Turn one reviewed tutorial proposal into a bounded, runnable case."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cfd_memo_agent.case_adapters import get_case_adapter
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.tutorial_capabilities import (
    TutorialCapability, get_tutorial_capability, tutorial_manifest,
)
from cfd_memo_agent.tutorials import DEFAULT_INDEX_PATH, load_tutorial_index
from cfd_memo_agent.validator import validate_case, validate_task
from cfd_memo_agent.validator.foam import read_json

PITZDAILY_TUTORIAL_ID = "incompressible/simpleFoam/pitzDaily"
CAVITY_TUTORIAL_ID = "incompressible/icoFoam/cavity/cavity"


def _proposal(value: dict[str, Any]) -> TutorialCapability:
    if value.get("proposal_status") != "reference_only":
        raise ValueError("只接受 I2 的 reference_only 提案")
    if value.get("source_type") != "tutorial_reference" or value.get("executable") is not False:
        raise ValueError("提案必须是尚未执行的教程参考")
    tutorial_ids = value.get("tutorial_ids")
    if not isinstance(tutorial_ids, list) or len(tutorial_ids) != 1:
        raise ValueError("受控 Builder 每次只接受一个教程 ID")
    capability = get_tutorial_capability(tutorial_ids[0])
    if value.get("solver") != capability.solver:
        raise ValueError("提案求解器与已批准教程能力不一致")
    return capability


def _source(index: dict[str, Any], capability: TutorialCapability) -> Path:
    if index.get("openfoam_version") != capability.openfoam_version:
        raise ValueError(
            f"教程能力要求 Foundation OpenFOAM {capability.openfoam_version}")
    matches = [item for item in index["cases"]
               if item.get("tutorial_id") == capability.tutorial_id]
    if len(matches) != 1:
        raise ValueError(f"教程索引中找不到唯一的已批准教程：{capability.tutorial_id}")
    entry = matches[0]
    if (entry.get("solver") != capability.solver
            or not set(capability.mesh_tools) <= set(entry.get("mesh_tools", []))
            or not set(capability.required_fields) <= set(entry.get("fields", []))):
        raise ValueError("教程索引能力与批准清单不一致")
    root = Path(index["tutorial_root"]).resolve()
    source = (root / capability.tutorial_id).resolve()
    if root not in source.parents or not source.is_dir():
        raise ValueError("教程来源路径超出索引根目录或不存在")
    return source


def build_backward_step_task(manifest_sha256: str) -> dict[str, Any]:
    """Compatibility helper backed by the installed capability data."""
    return get_tutorial_capability(PITZDAILY_TUTORIAL_ID).build_task(manifest_sha256)


def build_cavity_tutorial_task(manifest_sha256: str) -> dict[str, Any]:
    return get_tutorial_capability(CAVITY_TUTORIAL_ID).build_task(manifest_sha256)


def build_tutorial_case(
    proposal: Path | str | dict[str, Any], *,
    index_path: Path | str = DEFAULT_INDEX_PATH,
    runs_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Validate provenance, generate a bounded copy, and stop before execution."""
    value = read_json(Path(proposal)) if not isinstance(proposal, dict) else proposal
    capability = _proposal(value)
    index = load_tutorial_index(index_path)
    source = _source(index, capability)
    manifest, _ = tutorial_manifest(source, capability)
    task = capability.build_task(manifest)
    adapter = get_case_adapter(task)
    if adapter.adapter_id != capability.adapter_id:
        raise ValueError("教程能力与 case adapter 注册不一致")
    task_report = validate_task(task)
    if not task_report["task_valid"]:
        raise ValueError("生成的教程 task 未通过验证：" + "; ".join(
            item["message"] for item in task_report["errors"]))
    result = generate_case(
        task, runs_dir=Path(runs_dir) if runs_dir is not None else None,
        template_dir=source,
    )
    run = Path(result["run_path"])
    (run / "case-spec-proposal.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validation = validate_case(task, run / "case")
    build = {
        "status": "built" if validation["config_valid"] else "invalid",
        "run_path": str(run), "capability_id": capability.capability_id,
        "tutorial_id": capability.tutorial_id,
        "tutorial_index_path": str(Path(index_path).resolve()),
        "source_manifest_sha256": manifest,
        "scripts_executed": False, "scripts_copied": False,
        "task_path": str(run / "task.json"),
        "case_spec_path": str(run / "case-spec.json"),
        "proposal_path": str(run / "case-spec-proposal.json"),
        "validation": validation, "physical_validated": False,
    }
    (run / "tutorial-build.json").write_text(
        json.dumps(build, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return build


__all__ = [
    "CAVITY_TUTORIAL_ID", "PITZDAILY_TUTORIAL_ID",
    "build_backward_step_task", "build_cavity_tutorial_task", "build_tutorial_case",
]
