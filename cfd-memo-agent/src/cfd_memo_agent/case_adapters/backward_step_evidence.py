"""Fresh mesh evidence for the approved pitzDaily-derived case."""

from __future__ import annotations

import math
from pathlib import Path

from cfd_memo_agent.runner.evidence import fingerprint, foam_list, mesh_fingerprint
from cfd_memo_agent.validator.foam import parse_foam
from cfd_memo_agent.validator.foam import Group


def backward_step_mesh_evidence(case, task):
    mesh = Path(case) / "constant/polyMesh"
    face_count, owner = foam_list(mesh / "owner")
    neighbour_count, neighbour = foam_list(mesh / "neighbour")
    if face_count != len(owner) or neighbour_count != len(neighbour) or not owner:
        raise ValueError("Invalid backward-step owner/neighbour lists")
    if not all(isinstance(value, str) and value.isdigit() for value in owner + neighbour):
        raise ValueError("Invalid backward-step cell ownership")
    cells = max(map(int, owner + neighbour)) + 1
    if cells != task["mesh"]["target_cells"]:
        raise ValueError("Actual backward-step cell count differs from approved tutorial")
    point_count, points = foam_list(mesh / "points")
    if point_count != len(points) or not points:
        raise ValueError("Invalid backward-step mesh points")
    for point in points:
        if (not isinstance(point, Group) or len(point.items) != 3
                or not all(math.isfinite(float(value)) for value in point.items)):
            raise ValueError("Invalid backward-step mesh coordinates")
    boundary_count, values = foam_list(mesh / "boundary")
    if boundary_count != 5 or len(values) != 10:
        raise ValueError("Expected five backward-step mesh boundaries")
    patches = {}
    for name, body in zip(values[::2], values[1::2]):
        if not isinstance(name, str) or not isinstance(body, dict) or name in patches:
            raise ValueError("Invalid backward-step mesh boundary")
        faces = body.get("nFaces", [])
        if (len(faces) != 1 or not isinstance(faces[0], str) or not faces[0].isdigit()
                or int(faces[0]) <= 0):
            raise ValueError(f"Empty backward-step boundary: {name}")
        expected_type = (
            "empty" if name == "frontAndBack"
            else "wall" if name in {"upperWall", "lowerWall"}
            else "patch"
        )
        if body.get("type") != [expected_type]:
            raise ValueError(f"Wrong backward-step patch type: {name}")
        patches[name] = int(faces[0])
    if set(patches) != set(task["boundary_conditions"]):
        raise ValueError("Backward-step mesh patch names mismatch")
    return {
        "mesh_verified": True, "actual_cells": cells, "boundary_faces": patches,
        "case_path": str(case), "input_sha256": fingerprint(case),
        "mesh_sha256": mesh_fingerprint(mesh), "physical_validated": False,
    }


def _finite_tree(value):
    if isinstance(value, Group):
        return _finite_tree(value.items)
    if isinstance(value, dict):
        return all(_finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_tree(item) for item in value)
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def backward_step_field_evidence(case, task, mesh):
    """Accept the latest written steady iteration, including residual early stop."""
    root = Path(case)
    candidates = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if math.isfinite(value) and value > task["time_control"]["start_time"]:
            candidates.append((value, path))
    if not candidates:
        raise ValueError("Missing steady-state result directory")
    iteration, result_dir = max(candidates, key=lambda item: item[0])
    if iteration > task["time_control"]["end_time"]:
        raise ValueError("Steady-state result exceeds configured endTime")
    paths = {}
    expected_boundaries = set(task["boundary_conditions"])
    for name, components in (("U", 3), ("p", 1)):
        path = result_dir / name
        doc = parse_foam(path.read_text(encoding="utf-8"))
        expected_class = "volVectorField" if name == "U" else "volScalarField"
        header = doc.get("FoamFile", {})
        if (header.get("format") != ["ascii"] or header.get("class") != [expected_class]
                or header.get("object") != [name]):
            raise ValueError(f"Invalid steady field header: {name}")
        internal = doc.get("internalField", [])
        if (len(internal) != 4 or internal[0] != "nonuniform"
                or internal[1] != ("List<vector>" if name == "U" else "List<scalar>")
                or internal[2] != str(mesh["actual_cells"])
                or not isinstance(internal[3], Group)
                or len(internal[3].items) != mesh["actual_cells"]):
            raise ValueError(f"Wrong steady field size: {name}")
        for value in internal[3].items:
            numbers = value.items if isinstance(value, Group) else [value]
            if len(numbers) != components or not all(math.isfinite(float(item)) for item in numbers):
                raise ValueError(f"Invalid steady field values: {name}")
        if not _finite_tree(doc) or set(doc.get("boundaryField", {})) != expected_boundaries:
            raise ValueError(f"Invalid steady boundary field: {name}")
        paths[name] = str(path)
    if fingerprint(root) != mesh["input_sha256"]:
        raise ValueError("Case configuration changed during steady execution")
    if mesh_fingerprint(root / "constant/polyMesh") != mesh["mesh_sha256"]:
        raise ValueError("Mesh changed after checkMesh")
    return {
        "final_iteration": iteration,
        "configured_end_iteration": task["time_control"]["end_time"],
        "stopped_before_end": iteration < task["time_control"]["end_time"],
        "fields": paths, "actual_cells": mesh["actual_cells"],
        "physical_validated": False,
    }


__all__ = ["backward_step_field_evidence", "backward_step_mesh_evidence"]
