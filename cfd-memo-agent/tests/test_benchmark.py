from copy import deepcopy
import json
import re
import subprocess
import sys

import pytest

from cfd_memo_agent.benchmark import (
    DEFAULT_MANIFEST, audit_benchmark, load_benchmark_manifest, materialize_task,
)
from cfd_memo_agent.validator import validate_task


def write_manifest(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_frozen_benchmark_has_required_diversity_and_paired_real_slots():
    result = audit_benchmark()

    assert result["status"] == "passed"
    assert result["task_count"] == 30
    assert result["family_count"] == 3
    assert result["solver_count"] == 2
    assert result["real_evaluation_count"] == 20
    assert result["group_evaluation_counts"] == {
        "no_memory": 5, "simple_cache": 5,
        "retrieval_only": 5, "cfd_memo": 5,
    }
    assert len({item["fingerprint"] for item in result["tasks"]}) == 30
    assert result["execution_started"] is False
    assert result["physical_validated"] is False


def test_every_materialized_spec_passes_registered_task_validation():
    manifest = load_benchmark_manifest()

    for spec in manifest["task_specs"]:
        task = materialize_task(spec)
        report = validate_task(task)
        assert report["task_valid"], (spec["id"], report["errors"])
        assert task["task_id"] == spec["id"]
        assert task["solver"] == spec["solver"]


def test_audit_rejects_duplicate_tasks_and_unbalanced_real_pairing(tmp_path):
    original = load_benchmark_manifest()
    duplicated = deepcopy(original)
    duplicated["task_specs"][1]["parameters"] = deepcopy(
        duplicated["task_specs"][0]["parameters"])
    duplicate_path = tmp_path / "duplicate.json"
    write_manifest(duplicate_path, duplicated)
    with pytest.raises(ValueError, match="重复样本"):
        audit_benchmark(duplicate_path)

    unbalanced = deepcopy(original)
    unbalanced["real_evaluations"][0]["task_id"] = "cylinder-03-re100"
    unbalanced_path = tmp_path / "unbalanced.json"
    write_manifest(unbalanced_path, unbalanced)
    with pytest.raises(ValueError, match="同一组五个"):
        audit_benchmark(unbalanced_path)


def test_manifest_contains_no_secret_and_cli_audits_from_other_directory(tmp_path):
    manifest = load_benchmark_manifest()
    rendered = json.dumps(manifest)
    assert re.search(r"\bsk-[A-Za-z0-9_-]{20,}\b", rendered) is None
    assert manifest["model"]["secrets_in_manifest"] is False
    assert manifest["model"]["credential_source"] == "DEEPSEEK_API_KEY"

    completed = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "benchmark", "audit",
         "--manifest", str(DEFAULT_MANIFEST)],
        cwd=tmp_path, text=True, encoding="utf-8", capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["task_count"] == 30
