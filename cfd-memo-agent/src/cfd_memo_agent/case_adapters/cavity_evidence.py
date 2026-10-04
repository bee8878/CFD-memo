"""Fresh mesh evidence for the registered cavity topology."""

from __future__ import annotations

import math
from pathlib import Path

from cfd_memo_agent.runner.evidence import (
    fingerprint, foam_list, mesh_fingerprint,
)
from cfd_memo_agent.validator.foam import Group


def cavity_mesh_evidence(case, task):
    mesh = Path(case) / "constant/polyMesh"
    face_count, owner = foam_list(mesh / "owner")
    neighbour_count, neighbour = foam_list(mesh / "neighbour")
    if face_count != len(owner) or neighbour_count != len(neighbour) or not owner:
        raise ValueError("Invalid cavity owner/neighbour lists")
    if not all(isinstance(value, str) and value.isdigit() for value in owner + neighbour):
        raise ValueError("Invalid cavity cell ownership")
    cells = max(map(int, owner + neighbour)) + 1
    expected_cells = task["mesh"]["cells_x"] * task["mesh"]["cells_y"]
    if cells != expected_cells:
        raise ValueError("Actual cavity cell count differs from task resolution")
    point_count, points = foam_list(mesh / "points")
    if point_count != len(points) or not points:
        raise ValueError("Invalid cavity mesh points")
    for point in points:
        if (not isinstance(point, Group) or len(point.items) != 3
                or not all(math.isfinite(float(value)) for value in point.items)):
            raise ValueError("Invalid cavity mesh coordinates")
    boundary_count, values = foam_list(mesh / "boundary")
    if boundary_count != 3 or len(values) != 6:
        raise ValueError("Expected three cavity mesh boundaries")
    patches = {}
    for name, body in zip(values[::2], values[1::2]):
        if not isinstance(name, str) or not isinstance(body, dict) or name in patches:
            raise ValueError("Invalid cavity mesh boundary")
        faces = body.get("nFaces", [])
        if (len(faces) != 1 or not isinstance(faces[0], str) or not faces[0].isdigit()
                or int(faces[0]) <= 0):
            raise ValueError(f"Empty cavity boundary: {name}")
        expected_type = "empty" if name == "frontAndBack" else "wall"
        if body.get("type") != [expected_type]:
            raise ValueError(f"Wrong cavity patch type: {name}")
        patches[name] = int(faces[0])
    if set(patches) != set(task["boundary_conditions"]):
        raise ValueError("Cavity mesh patch names mismatch")
    return {
        "mesh_verified": True, "actual_cells": cells, "boundary_faces": patches,
        "case_path": str(case), "input_sha256": fingerprint(case),
        "mesh_sha256": mesh_fingerprint(mesh), "physical_validated": False,
    }


__all__ = ["cavity_mesh_evidence"]
