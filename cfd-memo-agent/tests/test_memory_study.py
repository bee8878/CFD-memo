import json
from pathlib import Path

from cfd_memo_agent.memory_study import GROUPS, analyze_memory_study, run_memory_study


def test_memory_study_runs_four_frozen_groups(tmp_path):
    result = run_memory_study(
        output_dir=tmp_path / "study", repeats=1,
        task_ids=["re100-u1-d1", "re200-u2-d1"], faults=["missing-boundary"],
    )

    assert tuple(result["summary"]) == GROUPS
    assert result["summary"]["no_memory"]["first_pass_rate"] == 0
    assert result["summary"]["no_memory"]["mean_corrections"] == 1
    assert result["summary"]["simple_cache"]["first_pass_rate"] == 0.5
    assert result["summary"]["retrieval_only"]["first_pass_rate"] == 0
    assert result["summary"]["retrieval_only"]["experience_reuse_rate"] == 1
    assert result["summary"]["cfd_memo"]["first_pass_rate"] == 1
    assert result["summary"]["cfd_memo"]["mean_corrections"] == 0
    assert not result["physical_validated"]
    assert Path(result["results_path"]).is_file()
    assert Path(result["report_path"]).is_file()
    rows = json.loads(Path(result["results_path"]).read_text(encoding="utf-8"))["rows"]
    assert len(rows) == 8
    analysis = analyze_memory_study(tmp_path / "study")
    assert analysis["row_count"] == 8
    assert analysis["groups"]["cfd_memo"]["rates"]["first_pass"]["estimate"] == 1
    assert analysis["groups"]["no_memory"]["rates"]["first_pass"]["estimate"] == 0
    assert Path(analysis["analysis_path"]).is_file()
    assert Path(analysis["report_path"]).is_file()
    assert Path(analysis["chart_path"]).read_text(encoding="utf-8").startswith("<svg")
    assert len(analysis["representative_cases"]) == 5


def test_memory_study_rejects_invalid_protocol_selection(tmp_path):
    try:
        run_memory_study(
            output_dir=tmp_path / "study", repeats=1,
            task_ids=["missing-task"], faults=["missing-boundary"],
        )
    except ValueError as exc:
        assert "不存在任务" in str(exc)
    else:
        raise AssertionError("invalid task should be rejected")
