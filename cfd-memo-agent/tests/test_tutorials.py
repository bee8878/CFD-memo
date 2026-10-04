import json
from pathlib import Path
import subprocess
import sys

import pytest

from cfd_memo_agent import tutorials


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def field(name: str, cls: str, boundaries: dict[str, str]) -> str:
    patches = "\n".join(
        f"    {patch} {{ type {boundary_type}; }}"
        for patch, boundary_type in boundaries.items()
    )
    dimensions = "[0 1 -1 0 0 0 0]" if name == "U" else "[0 2 -2 0 0 0 0]"
    internal = "uniform (0 0 0)" if name == "U" else "uniform 0"
    return f"""FoamFile
{{
    format ascii;
    class {cls};
    object {name};
}}
dimensions {dimensions};
internalField {internal};
boundaryField
{{
{patches}
}}
"""


def make_case(root: Path, relative: str, *, solver="icoFoam", mesh="blockMesh") -> Path:
    case = root / relative
    write(case / "system/controlDict", f"application {solver};\n")
    write(case / "0/U", field("U", "volVectorField", {
        "movingWall": "fixedValue", "fixedWalls": "noSlip", "frontAndBack": "empty",
    }))
    write(case / "0/p", field("p", "volScalarField", {
        "movingWall": "zeroGradient", "fixedWalls": "zeroGradient",
        "frontAndBack": "empty",
    }))
    if mesh == "blockMesh":
        write(case / "system/blockMeshDict", "FoamFile { object blockMeshDict; }\n")
    write(case / "Allrun", "exit 99\n")
    return case


def test_build_index_reads_dictionaries_without_executing_or_reading_scripts(tmp_path, monkeypatch):
    root = tmp_path / "tutorials"
    make_case(root, "incompressible/icoFoam/cavity/cavity")
    make_case(root, "incompressible/simpleFoam/RAS/pitzDaily", solver="simpleFoam")
    reads = []
    original = tutorials._read_text

    def observed(path):
        reads.append(Path(path).name)
        return original(path)

    monkeypatch.setattr(tutorials, "_read_text", observed)
    index = tutorials.build_tutorial_index(root)

    assert index["case_count"] == 2
    assert index["safety"]["read_only"] is True
    assert index["safety"]["scripts_executed"] is False
    assert "Allrun" not in reads
    cavity = next(item for item in index["cases"] if item["solver"] == "icoFoam")
    assert cavity["physics_model"] == "incompressible_laminar"
    assert cavity["fields"] == ["U", "p"]
    assert cavity["boundaries"]["movingWall"] == ["fixedValue", "zeroGradient"]
    assert cavity["boundary_types"] == ["empty", "fixedValue", "noSlip", "zeroGradient"]
    assert cavity["mesh_tools"] == ["blockMesh"]
    assert "方腔" in cavity["aliases"]


def test_index_skips_broken_case_and_searches_with_hard_filters(tmp_path):
    root = tmp_path / "tutorials"
    make_case(root, "incompressible/icoFoam/cavity/cavity")
    write(root / "broken/system/controlDict", "startTime 0;\n")
    index = tutorials.build_tutorial_index(root)

    assert index["case_count"] == 1 and index["skipped_count"] == 1
    result = tutorials.search_tutorials(
        index, query="顶盖驱动方腔", solver="icoFoam",
        physics_model="incompressible_laminar", fields=["U", "p"],
        boundary_types=["fixedValue", "empty"], mesh_tool="blockMesh",
    )
    assert result["total_matches"] == 1
    assert result["results"][0]["tutorial_id"].endswith("cavity/cavity")
    assert result["results"][0]["match_reasons"]
    assert tutorials.search_tutorials(index, solver="simpleFoam")["total_matches"] == 0


def test_search_prefers_full_flow_solver_for_exact_benchmark(tmp_path):
    root = tmp_path / "tutorials"
    make_case(root, "basic/potentialFoam/pitzDaily", solver="potentialFoam", mesh="none")
    make_case(root, "incompressible/simpleFoam/RAS/pitzDaily", solver="simpleFoam")
    result = tutorials.search_tutorials(
        tutorials.build_tutorial_index(root), query="做一个二维后台阶流动",
    )

    assert result["results"][0]["solver"] == "simpleFoam"
    assert "exact-benchmark:pitzdaily" in result["results"][0]["match_reasons"]
    assert "flow-solver:simpleFoam" in result["results"][0]["match_reasons"]
    assert "mesh-source:blockMesh" in result["results"][0]["match_reasons"]


def test_index_round_trip_and_validation(tmp_path):
    root = tmp_path / "tutorials"
    make_case(root, "incompressible/icoFoam/cavity/cavity")
    path = tutorials.save_tutorial_index(tutorials.build_tutorial_index(root), tmp_path / "index.json")
    loaded = tutorials.load_tutorial_index(path)
    assert loaded["case_count"] == 1
    with pytest.raises(FileExistsError):
        tutorials.save_tutorial_index(loaded, path)
    tutorials.save_tutorial_index(loaded, path, overwrite=True)
    with pytest.raises(ValueError, match="limit"):
        tutorials.search_tutorials(loaded, limit=0)


def test_wsl_root_rejects_unsafe_names():
    assert str(tutorials.wsl_tutorial_root()).replace("\\", "/").endswith(
        "/Ubuntu-22.04/opt/openfoam10/tutorials")
    with pytest.raises(ValueError):
        tutorials.wsl_tutorial_root("Ubuntu;echo bad", "10")


def test_cli_indexes_and_searches_fixture_from_other_directory(tmp_path):
    root = tmp_path / "tutorials"
    make_case(root, "incompressible/icoFoam/cavity/cavity")
    output = tmp_path / "tutorial-index.json"
    indexed = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "tutorials", "index",
         "--root", str(root), "--output", str(output)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert indexed.returncode == 0, indexed.stderr
    summary = json.loads(indexed.stdout)
    assert summary["case_count"] == 1 and summary["scripts_executed"] is False
    searched = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "tutorials", "search",
         "--index", str(output), "--query", "方腔", "--solver", "icoFoam",
         "--field", "U", "--field", "p"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert searched.returncode == 0, searched.stderr
    assert json.loads(searched.stdout)["total_matches"] == 1
    repeated = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "tutorials", "index",
         "--root", str(root), "--output", str(output)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert repeated.returncode == 2 and "Traceback" not in repeated.stderr
