"""Bounded parsing and transformation for supported Gmsh 2.2 ASCII meshes."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
from typing import Any

MAX_GMSH_BYTES = 50 * 1024 * 1024
_PHYSICAL = re.compile(r'^(\d+)\s+(\d+)\s+"([^"\r\n]+)"$')


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _section(lines: list[str], name: str) -> tuple[int, int, list[str]]:
    start_token, end_token = f"${name}", f"$End{name}"
    starts = [index for index, line in enumerate(lines) if line.strip() == start_token]
    ends = [index for index, line in enumerate(lines) if line.strip() == end_token]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise ValueError(f"Gmsh 网格缺少唯一的 {name} 区段")
    return starts[0], ends[0], lines[starts[0] + 1:ends[0]]


def _counted(section: list[str], name: str) -> list[str]:
    if not section:
        raise ValueError(f"Gmsh {name} 区段为空")
    try:
        count = int(section[0].strip())
    except ValueError:
        raise ValueError(f"Gmsh {name} 数量无效") from None
    values = section[1:]
    if count < 1 or len(values) != count:
        raise ValueError(f"Gmsh {name} 声明数量与实际内容不一致")
    return values


def inspect_gmsh_text(
    text: str, *, expected_boundaries: set[str], geometry: dict[str, Any],
    expected_cells: int,
) -> dict[str, Any]:
    """Validate one narrow, deterministic Gmsh format before OpenFOAM sees it."""
    lines = text.splitlines()
    _, _, mesh_format = _section(lines, "MeshFormat")
    if [line.strip() for line in mesh_format] != ["2.2 0 8"]:
        raise ValueError("仅支持 Gmsh 2.2 ASCII（MeshFormat 2.2 0 8）")

    _, _, physical_section = _section(lines, "PhysicalNames")
    physical_lines = _counted(physical_section, "PhysicalNames")
    physical: dict[int, tuple[int, str]] = {}
    for line in physical_lines:
        match = _PHYSICAL.fullmatch(line.strip())
        if not match:
            raise ValueError("Gmsh PhysicalNames 格式无效")
        dimension, tag, name = int(match[1]), int(match[2]), match[3]
        if tag in physical or not name.strip():
            raise ValueError("Gmsh 物理组标签重复或名称为空")
        physical[tag] = (dimension, name)

    _, _, node_section = _section(lines, "Nodes")
    node_lines = _counted(node_section, "Nodes")
    nodes: dict[int, tuple[float, float, float]] = {}
    for line in node_lines:
        parts = line.split()
        if len(parts) != 4:
            raise ValueError("Gmsh 节点行格式无效")
        try:
            node_id = int(parts[0])
            point = tuple(float(value) for value in parts[1:])
        except ValueError:
            raise ValueError("Gmsh 节点编号或坐标无效") from None
        if node_id <= 0 or node_id in nodes or not all(math.isfinite(v) for v in point):
            raise ValueError("Gmsh 节点重复或坐标不是有限数")
        nodes[node_id] = point  # type: ignore[assignment]

    _, _, element_section = _section(lines, "Elements")
    element_lines = _counted(element_section, "Elements")
    cell_count = 0
    boundary_faces = {name: 0 for name in expected_boundaries}
    seen_elements: set[int] = set()
    for line in element_lines:
        parts = line.split()
        if len(parts) < 4:
            raise ValueError("Gmsh 单元行格式无效")
        try:
            element_id, element_type, tag_count = map(int, parts[:3])
            tags = [int(value) for value in parts[3:3 + tag_count]]
            node_ids = [int(value) for value in parts[3 + tag_count:]]
        except ValueError:
            raise ValueError("Gmsh 单元编号、类型或标签无效") from None
        if element_id <= 0 or element_id in seen_elements or tag_count < 1:
            raise ValueError("Gmsh 单元编号重复或缺少物理标签")
        seen_elements.add(element_id)
        if any(node not in nodes for node in node_ids):
            raise ValueError("Gmsh 单元引用了不存在的节点")
        group = physical.get(tags[0])
        if group is None:
            raise ValueError("Gmsh 单元引用了未声明的物理组")
        dimension, name = group
        if element_type == 5:
            if dimension != 3 or len(node_ids) != 8:
                raise ValueError("三维计算单元必须是带体物理组的八节点六面体")
            cell_count += 1
        elif element_type == 3:
            if dimension != 2 or len(node_ids) != 4 or name not in boundary_faces:
                raise ValueError("边界单元必须是属于已声明任务边界的四节点面")
            boundary_faces[name] += 1
        else:
            raise ValueError(f"当前不支持 Gmsh 单元类型 {element_type}")

    if cell_count != expected_cells:
        raise ValueError(f"Gmsh 六面体数量 {cell_count} 与任务规划 {expected_cells} 不一致")
    empty = sorted(name for name, count in boundary_faces.items() if count == 0)
    if empty:
        raise ValueError("Gmsh 物理边界没有面：" + ", ".join(empty))
    bounds = {
        "min": [min(point[axis] for point in nodes.values()) for axis in range(3)],
        "max": [max(point[axis] for point in nodes.values()) for axis in range(3)],
    }
    expected_max = [geometry["width"], geometry["height"], geometry["depth"]]
    if any(not math.isclose(value, 0.0, abs_tol=1e-12) for value in bounds["min"]):
        raise ValueError("Gmsh 方腔坐标最小值必须为 (0, 0, 0)")
    if any(not math.isclose(value, wanted, rel_tol=1e-9, abs_tol=1e-12)
           for value, wanted in zip(bounds["max"], expected_max)):
        raise ValueError("Gmsh 网格范围与任务方腔几何不一致")
    surface_names = {name for dimension, name in physical.values() if dimension == 2}
    if surface_names != expected_boundaries:
        raise ValueError("Gmsh 表面物理组与任务边界名称不一致")
    return {
        "format": "2.2-ascii", "node_count": len(nodes),
        "element_count": len(element_lines), "cell_count": cell_count,
        "boundary_faces": boundary_faces, "bounds": bounds,
        "physical_names": sorted(name for _, name in physical.values()),
    }


def prepare_gmsh(
    source: Path | str, *, boundary_map: dict[str, str],
    expected_boundaries: set[str], geometry: dict[str, Any], expected_cells: int,
) -> tuple[str, dict[str, Any], str]:
    path = Path(source).resolve()
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".msh":
        raise ValueError("Gmsh 来源必须是普通 .msh 文件，不能是链接")
    data = path.read_bytes()
    if not data or len(data) > MAX_GMSH_BYTES or b"\x00" in data:
        raise ValueError("Gmsh 文件为空、过大或不是 ASCII 文本")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Gmsh 2.2 ASCII 文件必须使用 UTF-8/ASCII") from None
    _, _, mesh_format = _section(text.splitlines(), "MeshFormat")
    if [line.strip() for line in mesh_format] != ["2.2 0 8"]:
        raise ValueError("仅支持 Gmsh 2.2 ASCII（MeshFormat 2.2 0 8）")
    if set(boundary_map.values()) != expected_boundaries or len(boundary_map) != len(expected_boundaries):
        raise ValueError("边界映射目标必须与任务边界一一对应")
    if len(set(boundary_map.values())) != len(boundary_map):
        raise ValueError("边界映射不能把多个物理组合并为同一边界")
    for old, new in boundary_map.items():
        if not old or not new or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", new):
            raise ValueError("边界映射名称必须是安全的 OpenFOAM 标识符")
        text, count = re.subn(
            rf'(?m)^(\s*2\s+\d+\s+)"{re.escape(old)}"\s*$',
            lambda match: f'{match[1]}"{new}"', text,
        )
        if count != 1:
            raise ValueError(f"Gmsh 中找不到唯一的二维物理边界：{old}")
    inspection = inspect_gmsh_text(
        text, expected_boundaries=expected_boundaries, geometry=geometry,
        expected_cells=expected_cells,
    )
    return text.rstrip() + "\n", inspection, sha256_bytes(data)


def normalize_openfoam_boundary(case_dir: Path | str, expected_types: dict[str, str]) -> dict[str, Any]:
    """Set trusted patch types after gmshToFoam, then parse the file again."""
    from cfd_memo_agent.runner.evidence import foam_list

    path = Path(case_dir) / "constant/polyMesh/boundary"
    count, values = foam_list(path)
    names = values[::2]
    if count != len(expected_types) or set(names) != set(expected_types):
        raise ValueError("gmshToFoam 生成的边界名称与任务不一致")
    text = path.read_text(encoding="utf-8")
    changed: dict[str, dict[str, str]] = {}
    for name, wanted in expected_types.items():
        block = re.compile(
            rf'(?ms)(^\s*{re.escape(name)}\s*\n\s*\{{.*?^\s*type\s+)([A-Za-z][A-Za-z0-9_]*)(\s*;)',
        )
        matches = list(block.finditer(text))
        if len(matches) != 1:
            raise ValueError(f"无法唯一定位转换后的边界类型：{name}")
        previous = matches[0][2]
        text = block.sub(rf'\g<1>{wanted}\g<3>', text, count=1)
        changed[name] = {"before": previous, "after": wanted}
    path.write_text(text, encoding="utf-8")
    _, checked = foam_list(path)
    for name, body in zip(checked[::2], checked[1::2]):
        if body.get("type") != [expected_types[name]]:
            raise ValueError(f"边界类型写入后复验失败：{name}")
    return {"boundary_file": str(path), "changes": changed}


__all__ = [
    "inspect_gmsh_text", "normalize_openfoam_boundary", "prepare_gmsh", "sha256_bytes",
]
