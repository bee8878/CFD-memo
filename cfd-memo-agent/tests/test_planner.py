from cfd_memo_agent.planner import plan_task


def test_plan_task_parses_default_cylinder_flow():
    task = plan_task("做 Re=100 的二维圆柱绕流")

    assert task["task_id"] == "task-cylinder-2d-re100"
    assert task["case_type"] == "cylinder-2d"
    assert task["solver"] == "icoFoam"
    assert task["geometry"]["dimension"] == "2D"
    assert task["physics"]["reynolds_number"] == 100
    assert task["physics"]["kinematic_viscosity"] == 0.01


def test_plan_task_updates_reynolds_velocity_and_diameter():
    task = plan_task("做 Re=200 的 cylinder flow，入口速度=2，D=1")

    assert task["task_id"] == "task-cylinder-2d-re200"
    assert task["physics"]["reynolds_number"] == 200
    assert task["physics"]["inlet_velocity"] == 2
    assert task["geometry"]["cylinder_diameter"] == 1
    assert task["physics"]["kinematic_viscosity"] == 0.01


def test_plan_task_rejects_unsupported_case():
    try:
        plan_task("做管道流动")
    except ValueError as exc:
        assert "2D cylinder flow" in str(exc)
    else:
        raise AssertionError("Expected unsupported case to raise ValueError.")
