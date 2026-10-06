import json
from pathlib import Path
import shutil

import pytest

from cfd_memo_agent.benchmark import DEFAULT_MANIFEST, load_benchmark_manifest
from cfd_memo_agent.benchmark_analysis import analyze_benchmark


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _study(tmp_path: Path) -> Path:
    root = tmp_path / "study"
    root.mkdir()
    shutil.copyfile(DEFAULT_MANIFEST, root / "protocol.json")
    manifest = load_benchmark_manifest()
    successful_tasks = {
        "cylinder-05-re150-boundary", "cylinder-06-re180-transport",
        "cavity-05-re100-boundary",
    }
    evaluations = []
    for index, frozen in enumerate(manifest["real_evaluations"]):
        success = frozen["task_id"] in successful_tasks
        memo = frozen["group"] == "cfd_memo"
        first_pass = success and memo
        rounds = ([{"index": 0, "status": "completed", "config_valid": True,
                    "log_paths": ["blockMesh.log"]}]
                  if first_pass else
                  ([{"index": 0, "status": "validation_failed", "config_valid": False,
                     "log_paths": []},
                    {"index": 1, "status": "completed", "config_valid": True,
                     "log_paths": ["blockMesh.log"]}]
                   if success else
                   [{"index": 0, "status": "validation_failed", "config_valid": False,
                     "log_paths": []}]))
        episode_path = root / "evaluation" / frozen["id"] / "episode.json"
        episode = {
            "task_id": frozen["task_id"], "runner_mode": "real",
            "status": "completed" if success else "failed", "rounds": rounds,
            "corrections": [] if first_pass or not success else [{"change": True}],
            "preventions": [{"files": ["0/U"]}] if first_pass else [],
            "memory": {"cache_hit": False, "uses": ([{"outcome": "effective"}]
                                                        if first_pass else [])},
            "metrics": {"elapsed_seconds": 5 + index},
            "stop_reason": {"code": "COMPLETED" if success else "UNSUPPORTED_ERROR"},
            "physical_validated": False,
        }
        _write(episode_path, episode)
        evaluations.append({
            **frozen, "status": "completed" if success else "failed",
            "episode_path": str(episode_path),
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            "estimated_cost_usd": {"off_peak": 0.001, "peak": 0.002},
        })
    _write(root / "state.json", {
        "study_id": "test-study", "status": "completed", "pending_count": 0,
        "evaluations": evaluations,
    })
    return root


def test_m3_recomputes_group_and_paired_metrics(tmp_path):
    root = _study(tmp_path)
    result = analyze_benchmark(root)

    assert result["status"] == "completed"
    assert result["groups"]["no_memory"]["engineering_success"]["successes"] == 3
    assert result["groups"]["no_memory"]["first_pass"]["successes"] == 0
    assert result["groups"]["cfd_memo"]["first_pass"]["successes"] == 3
    assert result["groups"]["cfd_memo"]["corrections"]["mean"] == 0
    assert result["totals"]["total_tokens"] == 2400
    assert result["totals"]["physical_validated"] == 0
    analysis = json.loads(Path(result["analysis_path"]).read_text(encoding="utf-8"))
    assert analysis["paired_vs_no_memory"]["cfd_memo"]["first_pass"][
        "comparison_only"] == 3
    assert Path(result["report_path"]).is_file()
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "McNemar" in report
    assert "失败案例" in report


def test_m3_rejects_incomplete_study_and_existing_output(tmp_path):
    root = _study(tmp_path)
    analyze_benchmark(root)
    with pytest.raises(ValueError, match="输出已存在"):
        analyze_benchmark(root)
    analyze_benchmark(root, overwrite=True)

    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    state["status"] = "partial"
    _write(root / "state.json", state)
    with pytest.raises(ValueError, match="已完成"):
        analyze_benchmark(root, overwrite=True)
