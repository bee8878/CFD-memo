import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from cfd_memo_agent.benchmark import load_benchmark_manifest, materialize_task
from cfd_memo_agent.benchmark_run import PRICE_SNAPSHOT, run_benchmark
from cfd_memo_agent.correction import FAULTS, inject_fault
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.models import ModelSettings
from cfd_memo_agent.validator import validate_case
from cfd_memo_agent.workflow import run_workflow


def _cylinder_task():
    manifest = load_benchmark_manifest()
    return materialize_task(manifest["task_specs"][0])


def test_all_m2_fault_fixtures_change_only_the_isolated_case(tmp_path):
    task = _cylinder_task()
    generated = generate_case(task, runs_dir=tmp_path / "generated")
    source = Path(generated["case_path"])
    original = {path.relative_to(source): path.read_bytes()
                for path in source.rglob("*") if path.is_file()}

    for fault in FAULTS:
        case = tmp_path / fault / "case"
        shutil.copytree(source, case)
        record = inject_fault(case, fault)
        assert record["name"] == fault
        assert len(record["changes"]) == 1
        changed = record["changes"][0]
        assert changed["before"] != changed["after"]
        if fault != "mesh-resolution-risk":
            assert not validate_case(task, case)["config_valid"]

    assert original == {path.relative_to(source): path.read_bytes()
                        for path in source.rglob("*") if path.is_file()}


def test_simulated_batch_freezes_snapshots_and_resumes(tmp_path):
    root = tmp_path / "study"
    rules = ModelSettings(provider="rules")
    first = run_benchmark(
        output_dir=root, runner="simulated", limit=1, model_settings=rules,
    )
    assert first["status"] == "partial"
    assert first["completed_count"] == 1
    assert first["pending_count"] == 19
    hashes = {group: value["sha256"] for group, value in first["snapshots"].items()
              if value is not None}

    second = run_benchmark(resume=root, runner="simulated", limit=1, model_settings=rules)
    assert second["status"] == "partial"
    assert second["completed_count"] == 2
    assert hashes == {group: value["sha256"] for group, value in second["snapshots"].items()
                      if value is not None}
    assert all(value["learning_enabled"] is False
               for value in second["snapshots"].values() if value is not None)
    assert json.loads((root / "state.json").read_text(encoding="utf-8"))["pending_count"] == 18


def test_price_snapshot_is_explicit_and_contains_no_credentials():
    rendered = json.dumps(PRICE_SNAPSHOT)
    assert PRICE_SNAPSHOT["retrieved_at"] == "2026-10-06"
    assert PRICE_SNAPSHOT["off_peak"]["output"] < PRICE_SNAPSHOT["peak"]["output"]
    assert "api_key" not in rendered.lower()


def test_simulated_cli_forces_offline_rules_even_when_environment_selects_llm(tmp_path):
    environment = os.environ.copy()
    environment.update({
        "CFD_MEMO_MODEL_PROVIDER": "deepseek",
        "CFD_MEMO_MODEL": "deepseek-flash",
        "DEEPSEEK_API_KEY": "test-only-not-a-real-key",
    })
    completed = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "benchmark", "run",
         "--runner", "simulated", "--limit", "1", "--output-dir", str(tmp_path / "cli")],
        text=True, encoding="utf-8", capture_output=True, env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["execution_model"]["provider"] == "rules"
    assert result["execution_model"]["api_key_configured"] is False


def test_unknown_fault_episode_is_saved_even_when_rule_repair_is_unavailable(tmp_path):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(_cylinder_task()), encoding="utf-8")
    episode = run_workflow(
        task_path=task_path, mode="simulated", fault="time-control-drift",
        runs_dir=tmp_path / "runs", model_settings=ModelSettings(provider="rules"),
    )
    assert episode["status"] == "failed"
    assert episode["injected_fault"]["name"] == "time-control-drift"
    assert Path(episode["episode_path"]).is_file()
