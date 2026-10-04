import json

import pytest

from cfd_memo_agent.case_spec import CaseSpec
from cfd_memo_agent.mesh_capabilities import (
    MeshSpec, external_gmsh_spec, list_mesh_capabilities, load_mesh_spec,
    mesh_command_plan, mesh_spec_from_case_spec, mesh_spec_from_task, save_mesh_spec,
)
from cfd_memo_agent.planner import plan_task


def test_generated_and_tutorial_tasks_have_explicit_block_mesh_plans():
    generated = mesh_spec_from_task(plan_task("cylinder flow Re=100"))
    tutorial_task = plan_task("lid-driven cavity Re=100")
    tutorial_task["mesh"]["generator"] = "tutorial-template"
    tutorial_task["tutorial_reference"] = {"tutorial_id": "icoFoam/cavity/cavity"}
    tutorial = mesh_spec_from_task(tutorial_task)

    assert generated.source_type == "generated-template"
    assert tutorial.source_type == "tutorial-template"
    assert tutorial.source_path == "icoFoam/cavity/cavity"
    assert mesh_command_plan(generated, "icoFoam") == (
        "blockMesh", "checkMesh", "icoFoam",
    )


def test_existing_poly_mesh_skips_mesh_generation():
    task = plan_task("cylinder flow Re=100")
    value = CaseSpec.from_task(task, source_type="imported").to_dict()
    value["case_type"] = "imported-openfoam-case"
    value["mesh_source"] = "polyMesh"
    spec = mesh_spec_from_case_spec(CaseSpec.from_dict(value))

    assert spec.source_type == "existing-polyMesh"
    assert spec.preparation_commands == ()
    assert mesh_command_plan(spec, "icoFoam") == ("checkMesh", "icoFoam")


def test_gmsh_has_a_fixed_conversion_and_verification_plan():
    capabilities = {item["name"]: item for item in list_mesh_capabilities()}
    spec = external_gmsh_spec("mesh/example.msh")

    assert capabilities["gmsh"]["execution_ready"] is True
    assert spec.preparation_commands == ("gmshToFoam",)
    assert mesh_command_plan(spec, "icoFoam") == (
        "gmshToFoam", "checkMesh", "icoFoam",
    )


def test_saved_mesh_plan_cannot_inject_an_unregistered_command(tmp_path):
    spec = mesh_spec_from_task(plan_task("cylinder flow"))
    path = save_mesh_spec(tmp_path / "mesh-spec.json", spec)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["preparation_commands"] = ["dangerous-command"]

    with pytest.raises(ValueError, match="可信能力注册表"):
        MeshSpec.from_dict(value)
    assert load_mesh_spec(path) == spec
