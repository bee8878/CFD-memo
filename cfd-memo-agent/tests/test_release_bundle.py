import json
from pathlib import Path

import pytest

from cfd_memo_agent.benchmark import GROUPS, load_benchmark_manifest
from cfd_memo_agent.release_bundle import build_release_bundle


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _completed_study(tmp_path: Path, *, private_effect: str | None = None) -> Path:
    root = tmp_path / "study"
    root.mkdir()
    manifest = load_benchmark_manifest()
    (root / "protocol.json").write_text(json.dumps(manifest), encoding="utf-8")
    successful = {
        "cylinder-05-re150-boundary", "cylinder-06-re180-transport",
        "cavity-05-re100-boundary",
    }
    specs = {item["id"]: item for item in manifest["task_specs"]}
    evaluations, rows = [], []
    for frozen in manifest["real_evaluations"]:
        spec = specs[frozen["task_id"]]
        success = frozen["task_id"] in successful
        effective = frozen["group"] == "cfd_memo" and frozen["task_id"].startswith("cylinder")
        episode_path = root / "evaluation" / frozen["id"] / "episode.json"
        _write(episode_path, {
            "memory": {"uses": ([{
                "agent": "case_writer", "decision": "prepare_with_memory",
                "effect": private_effect or "added_preventive_file_checks",
                "outcome": "effective",
            }] if effective else [])},
            "preventions": ([{"action": "restore_from_trusted_reference", "files": ["0/U"]}]
                            if effective else []),
        })
        evaluations.append({
            **frozen, "status": "completed" if success else "failed",
            "episode_path": str(episode_path),
        })
        rows.append({
            "evaluation_id": frozen["id"], "task_id": frozen["task_id"],
            "group": frozen["group"], "family": spec["family"],
            "partition": spec["partition"], "fault_profile": spec["fault_profile"],
            "engineering_success": success, "effective_experience_use": effective,
            "failure_avoided": effective, "stop_code": "COMPLETED" if success else "UNSUPPORTED_ERROR",
            "openfoam_started": success,
        })
    rate = {"successes": 3, "total": 5, "estimate": 0.6,
            "wilson_ci95": [0.23, 0.88]}
    groups = {group: {
        "engineering_success": rate, "first_pass": rate,
        "effective_experience_use": rate, "failure_avoidance": rate,
        "corrections": {"mean": 0.2 if group == "cfd_memo" else 0.6},
    } for group in GROUPS}
    paired = {group: {
        "engineering_success": {"exact_two_sided_p": 1.0},
        "first_pass": {"exact_two_sided_p": 0.5},
        "mean_correction_difference_vs_no_memory": -0.4,
        "mean_elapsed_difference_seconds_vs_no_memory": -1.0,
    } for group in GROUPS[1:]}
    _write(root / "state.json", {
        "status": "completed", "manifest_sha256": "a" * 64,
        "execution_model": {"provider": "deepseek", "model": "deepseek-flash",
                            "mode": "llm", "timeout_seconds": 60,
                            "max_output_tokens": 2000, "api_key_env": "SECRET"},
        "pricing_snapshot": {"source": "https://example.invalid", "model": "deepseek-flash"},
        "evaluations": evaluations,
    })
    _write(root / "analysis/analysis.json", {
        "groups": groups, "paired_vs_no_memory": paired,
        "totals": {"evaluations": 20, "physical_validated": 0},
        "limitations": ["small sample"], "rows": rows,
    })
    (root / "analysis/report.md").write_text("# Public report\n", encoding="utf-8")
    return root


def test_m4_bundle_is_sanitized_and_reproducible(tmp_path):
    study = _completed_study(tmp_path)
    output = tmp_path / "public"
    result = build_release_bundle(study, output_dir=output)

    assert result["status"] == "completed"
    assert result["failure_cases"] == 8
    assert result["memory_evidence_records"] == 2
    assert result["raw_episodes_included"] is False
    assert set(result["files"]) == {
        "README.md", "analysis-report.md", "failure-cases.json",
        "manifest.json", "memory-evidence.json", "summary.json",
    }
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())
    assert str(tmp_path) not in rendered
    assert "api_key_env" not in rendered
    with pytest.raises(ValueError, match="输出目录已存在"):
        build_release_bundle(study, output_dir=output)
    build_release_bundle(study, output_dir=output, overwrite=True)


def test_m4_rejects_private_paths_in_exported_evidence(tmp_path):
    study = _completed_study(tmp_path, private_effect="C:\\private\\record")
    with pytest.raises(ValueError, match="本机路径或密钥"):
        build_release_bundle(study, output_dir=tmp_path / "public")
