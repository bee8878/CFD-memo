"""Cross-check generated ASCII case files without invoking OpenFOAM."""
from __future__ import annotations

from pathlib import Path
from cfd_memo_agent.mesh import cylinder_mesh

from .foam import Group, parse_foam, read_json
from .task import BOUNDARIES, close, finite, issue, validate_task

FILES = {
    "0/U": ("U", "volVectorField"),
    "0/p": ("p", "volScalarField"),
    "constant/physicalProperties": ("physicalProperties", "dictionary"),
    "system/controlDict": ("controlDict", "dictionary"),
    "system/blockMeshDict": ("blockMeshDict", "dictionary"),
    "system/fvSchemes": ("fvSchemes", "dictionary"),
    "system/fvSolution": ("fvSolution", "dictionary"),
}


class CheckError(ValueError):
    def __init__(self, code, message, location):
        self.problem = issue(code, message, location)
        super().__init__(message)


def require(condition, code, message, location):
    if not condition:
        raise CheckError(code, message, location)


def mapping(value, location):
    require(isinstance(value, dict), "CONFIG_STRUCTURE", "需要配置字典", location)
    return value


def entry(data, key, location):
    value = mapping(data, location).get(key)
    require(isinstance(value, list) and bool(value), "CONFIG_ENTRY",
            f"缺少或损坏配置项：{key}", f"{location}.{key}")
    return value


def scalar(data, key, location):
    value = entry(data, key, location)
    require(len(value) == 1 and isinstance(value[0], str), "CONFIG_ENTRY",
            f"{key} 必须是单个值", f"{location}.{key}")
    return value[0]


def number(value, location):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise CheckError("CONFIG_NUMBER", "配置值必须是数字", location) from None
    require(finite(result), "CONFIG_NUMBER", "配置数字必须有限", location)
    return result


def group(value, opening, location):
    require(isinstance(value, Group) and value.opening == opening,
            "CONFIG_STRUCTURE", "列表或量纲括号错误", location)
    return value.items


def compare(actual, expected, location):
    require(close(actual, expected), "VALUE_MISMATCH",
            f"配置值 {actual} 与任务预期 {expected} 不一致", location)


def dimensions(value, expected, location):
    values = group(value, "[", location)
    require(len(values) == 7, "DIMENSIONS", "量纲必须有七个指数", location)
    require([number(v, location) for v in values] == expected,
            "DIMENSIONS", "物理量量纲不正确", location)


def named_patches(value, location):
    items = group(value, "(", location)
    require(len(items) % 2 == 0, "BOUNDARY_STRUCTURE", "边界列表必须包含名称与配置", location)
    result = {}
    for index in range(0, len(items), 2):
        name, body = items[index:index + 2]
        require(isinstance(name, str), "BOUNDARY_STRUCTURE", "边界名称无效", location)
        require(name not in result, "DUPLICATE_BOUNDARY", "边界名称重复", location)
        result[name] = mapping(body, f"{location}.{name}")
    return result


def equivalent(actual, expected):
    """Compare parsed generated topology, accepting numeric formatting differences."""
    if isinstance(expected, Group):
        return (isinstance(actual, Group) and actual.opening == expected.opening
                and equivalent(actual.items, expected.items))
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and actual.keys() == expected.keys()
                and all(equivalent(actual[k], v) for k, v in expected.items()))
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(equivalent(a, b) for a, b in zip(actual, expected)))
    if actual == expected:
        return True
    try:
        return close(float(actual), float(expected))
    except (ValueError, TypeError):
        return False


def validate_case(task, case_dir) -> dict:
    report = validate_task(task)
    errors = report["errors"]
    root = Path(case_dir)
    documents = {}
    report["runtime_blockers"].append(issue(
        "MESH_NOT_VERIFIED", "尚未执行真实网格质量和几何验证，不能据此启动求解",
        "case"))

    def capture(action):
        try:
            action()
        except CheckError as exc:
            errors.append(exc.problem)

    for relative, (object_name, class_name) in FILES.items():
        path = root / relative
        try:
            document = parse_foam(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError) as exc:
            errors.append(issue("FILE_READ", f"文件缺失或无法读取：{exc}", relative))
            continue
        except (ValueError, RecursionError) as exc:
            errors.append(issue("FOAM_SYNTAX", f"配置格式错误：{exc}", relative))
            continue
        documents[relative] = document

        def header_check():
            header = mapping(document.get("FoamFile"), f"{relative}.FoamFile")
            for key, expected in (("object", object_name), ("class", class_name), ("format", "ascii")):
                require(scalar(header, key, relative) == expected, "HEADER_MISMATCH",
                        f"文件头 {key} 应为 {expected}", f"{relative}.FoamFile.{key}")
        capture(header_check)

    for relative, expected in (("0/U", [0, 1, -1, 0, 0, 0, 0]),
                               ("0/p", [0, 2, -2, 0, 0, 0, 0])):
        if relative not in documents:
            continue
        doc = documents[relative]

        def field_check():
            dims = entry(doc, "dimensions", relative)
            require(len(dims) == 1, "DIMENSIONS", "量纲格式错误", relative)
            dimensions(dims[0], expected, f"{relative}.dimensions")
            patches = mapping(doc.get("boundaryField"), f"{relative}.boundaryField")
            require(set(patches) == set(BOUNDARIES), "BOUNDARY_NAMES",
                    "边界名称必须与任务的六个边界一致", f"{relative}.boundaryField")
            field = relative[-1]
            for name, expected_types in BOUNDARIES.items():
                patch = mapping(patches[name], f"{relative}.{name}")
                require(scalar(patch, "type", relative) == expected_types[field], "BOUNDARY_TYPE",
                        "边界字段类型与任务不一致", f"{relative}.boundaryField.{name}.type")
            initial = entry(doc, "internalField", relative)
            require(len(initial) == 2 and initial[0] == "uniform", "FIELD_VALUE",
                    "当前仅支持 uniform 初始场", f"{relative}.internalField")
            if field == "U":
                vector = group(initial[1], "(", relative)
                require(len(vector) == 3, "FIELD_VALUE", "速度需要三个分量", relative)
                for v in vector:
                    number(v, relative)
                inlet = entry(patches["inlet"], "value", relative)
                require(len(inlet) == 2 and inlet[0] == "uniform", "FIELD_VALUE",
                        "入口速度必须为 uniform 向量", f"{relative}.inlet")
                values = group(inlet[1], "(", relative)
                require(len(values) == 3, "FIELD_VALUE", "入口速度需要三个分量", relative)
                actual = [number(v, relative) for v in values]
                if report["task_valid"]:
                    for actual_value, wanted in zip(actual, [task["physics"]["inlet_velocity"], 0, 0]):
                        compare(actual_value, wanted, f"{relative}.boundaryField.inlet.value")
            else:
                number(initial[1], relative)
                outlet = entry(patches["outlet"], "value", relative)
                require(len(outlet) == 2 and outlet[0] == "uniform", "FIELD_VALUE",
                        "出口压力必须为 uniform 标量", relative)
                compare(number(outlet[1], relative), 0, f"{relative}.boundaryField.outlet.value")
        capture(field_check)

    if "constant/physicalProperties" in documents:
        def transport_check():
            relative = "constant/physicalProperties"
            doc = documents[relative]
            nu = entry(doc, "nu", relative)
            require(len(nu) == 2, "CONFIG_ENTRY", "nu 必须包含量纲和数值", relative)
            dimensions(nu[0], [0, 2, -1, 0, 0, 0, 0], f"{relative}.nu.dimensions")
            actual = number(nu[1], relative)
            require(actual > 0, "CONFIG_NUMBER", "黏度必须为正", f"{relative}.nu")
            if report["task_valid"]:
                compare(actual, task["physics"]["kinematic_viscosity"], f"{relative}.nu")
        capture(transport_check)

    if "system/controlDict" in documents:
        def control_check():
            relative = "system/controlDict"
            doc = documents[relative]
            for key, expected in (("application", "icoFoam"), ("startFrom", "startTime"),
                                  ("stopAt", "endTime"), ("writeControl", "runTime")):
                require(scalar(doc, key, relative) == expected, "CONTROL_SETTING",
                        f"{key} 必须为 {expected}", f"{relative}.{key}")
            for key, task_key in (("startTime", "start_time"), ("endTime", "end_time"),
                                  ("deltaT", "delta_t"), ("writeInterval", "write_interval")):
                actual = number(scalar(doc, key, relative), f"{relative}.{key}")
                if report["task_valid"]:
                    compare(actual, task["time_control"][task_key], f"{relative}.{key}")
        capture(control_check)

    if "system/blockMeshDict" in documents:
        def mesh_check():
            relative = "system/blockMeshDict"
            doc = documents[relative]
            compare(number(scalar(doc, "convertToMeters", relative), relative), 1, relative)
            vertex_list = entry(doc, "vertices", relative)
            if len(vertex_list) == 1 and len(group(vertex_list[0], "(", relative)) != 8:
                require(report["task_valid"], "GEOMETRY", "任务无效，无法核对圆柱网格", relative)
                expected_mesh = parse_foam(cylinder_mesh(task["geometry"], task.get("mesh", {}).get("target_cells", 10000)))
                boundary_items = entry(doc, "boundary", relative)
                require(len(boundary_items) == 1, "BOUNDARY_STRUCTURE", "边界列表无效", relative)
                named_patches(boundary_items[0], f"{relative}.boundary")
                for section in ("vertices", "blocks", "edges", "boundary", "mergePatchPairs"):
                    require(equivalent(doc.get(section), expected_mesh[section]), "MESH_TOPOLOGY",
                            "网格与任务对应的圆柱 O-grid 不一致", f"{relative}.{section}")
                return
            report["runtime_blockers"].append(issue(
                "LEGACY_MESH", "旧矩形占位网格不能作为圆柱求解网格", relative))
            boundary = entry(doc, "boundary", relative)
            require(len(boundary) == 1, "BOUNDARY_STRUCTURE", "边界列表无效", relative)
            patches = named_patches(boundary[0], f"{relative}.boundary")
            require(set(patches) == set(BOUNDARIES), "BOUNDARY_NAMES",
                    "网格边界必须与任务、U 和 p 的六个边界一致", relative)
            types = {"inlet": "patch", "outlet": "patch", "cylinder": "wall",
                     "top": "patch", "bottom": "patch", "frontAndBack": "empty"}
            for name, expected in types.items():
                patch = patches[name]
                require(scalar(patch, "type", relative) == expected, "MESH_BOUNDARY_TYPE",
                        f"当前模板的 {name} 网格类型应为 {expected}", f"{relative}.boundary.{name}")
                faces = entry(patch, "faces", relative)
                require(len(faces) == 1, "BOUNDARY_STRUCTURE", "faces 列表无效", relative)
                values = group(faces[0], "(", relative)
                if not values:
                    report["runtime_blockers"].append(issue(
                        "EMPTY_BOUNDARY", f"{name} 边界没有面，不能用于真实仿真",
                        f"{relative}.boundary.{name}.faces"))
                for face in values:
                    indices = group(face, "(", relative)
                    require(len(indices) == 4, "MESH_FACE", "当前模板边界面需要四个顶点", relative)
                    require(all(isinstance(i, str) and i.isdigit() and 0 <= int(i) < 8
                                for i in indices), "MESH_FACE", "边界顶点索引无效", relative)
            blocks_entry = entry(doc, "blocks", relative)
            require(len(blocks_entry) == 1, "MESH_BLOCK", "blocks 列表无效", relative)
            block = group(blocks_entry[0], "(", relative)
            require(len(block) == 5 and block[0] == "hex" and block[3] == "simpleGrading",
                    "MESH_BLOCK", "当前仅支持单个 hex / simpleGrading 模板", relative)
            require(group(block[1], "(", relative) == [str(i) for i in range(8)],
                    "MESH_BLOCK", "块顶点顺序与当前模板不一致", relative)
            cells = group(block[2], "(", relative)
            require(len(cells) == 3 and all(isinstance(n, str) and n.isdigit() and int(n) > 0
                                          for n in cells),
                    "MESH_CELLS", "网格划分需要三个正整数", relative)
            require(cells[2] == "1", "MESH_2D", "二维模板厚度方向必须只有一层网格", relative)
            grading = group(block[4], "(", relative)
            require(len(grading) == 3 and all(number(v, relative) > 0 for v in grading),
                    "MESH_GRADING", "simpleGrading 需要三个正数", relative)
            vertices_entry = entry(doc, "vertices", relative)
            require(len(vertices_entry) == 1, "GEOMETRY", "vertices 列表无效", relative)
            vertices = group(vertices_entry[0], "(", relative)
            require(len(vertices) == 8, "GEOMETRY", "C3 仅检查当前八顶点矩形模板", relative)
            coordinates = []
            for vertex in vertices:
                components = group(vertex, "(", relative)
                require(len(components) == 3, "GEOMETRY", "顶点需要三个坐标", relative)
                coordinates.append([number(v, relative) for v in components])
            if report["task_valid"]:
                length, height = task["geometry"]["domain_length"], task["geometry"]["domain_height"]
                expected = [[x, y, z] for z in (-0.05, 0.05)
                            for x, y in ((-length/2, -height/2), (length/2, -height/2),
                                         (length/2, height/2), (-length/2, height/2))]
                for actual, wanted in zip(coordinates, expected):
                    for a, b in zip(actual, wanted):
                        compare(a, b, f"{relative}.vertices")
        capture(mesh_check)

    if "system/fvSchemes" in documents:
        def schemes_check():
            relative = "system/fvSchemes"
            doc = documents[relative]
            for section in ("ddtSchemes", "gradSchemes", "divSchemes", "laplacianSchemes",
                            "interpolationSchemes", "snGradSchemes"):
                entry(mapping(doc.get(section), f"{relative}.{section}"), "default",
                      f"{relative}.{section}")
            entry(doc["divSchemes"], "div(phi,U)", relative)
        capture(schemes_check)

    if "system/fvSolution" in documents:
        def solution_check():
            relative = "system/fvSolution"
            doc = documents[relative]
            solvers = mapping(doc.get("solvers"), relative)
            for field in ("U", "p", "pFinal"):
                solver = mapping(solvers.get(field), f"{relative}.solvers.{field}")
                scalar(solver, "solver", relative)
                tolerance = number(scalar(solver, "tolerance", relative), relative)
                require(tolerance > 0, "SOLVER_TOLERANCE", "求解容差必须为正", relative)
                relative_tolerance = number(scalar(solver, "relTol", relative), relative)
                require(relative_tolerance >= 0, "SOLVER_TOLERANCE", "相对容差不能为负", relative)
            piso = mapping(doc.get("PISO"), f"{relative}.PISO")
            for key, minimum in (("nCorrectors", 1), ("nNonOrthogonalCorrectors", 0)):
                value = number(scalar(piso, key, relative), relative)
                require(value.is_integer() and value >= minimum, "PISO_SETTING",
                        "PISO 修正次数不合法", f"{relative}.{key}")
        capture(solution_check)

    try:
        metadata = read_json(root / "constant/geometry.json")
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        errors.append(issue("GEOMETRY_READ", f"几何记录缺失或格式错误：{exc}", "constant/geometry.json"))
    else:
        def geometry_check():
            relative = "constant/geometry.json"
            mapping(metadata, relative)
            require(metadata.get("dimension") == "2D" and metadata.get("units") == "m",
                    "GEOMETRY_METADATA", "几何元数据需要 2D 和米单位", relative)
            for key in ("cylinder_diameter", "domain_length", "domain_height"):
                actual = metadata.get(key)
                require(finite(actual) and actual > 0, "GEOMETRY_METADATA",
                        "几何尺寸必须为有限正数", f"{relative}.{key}")
                if report["task_valid"]:
                    compare(actual, task["geometry"][key], f"{relative}.{key}")
        capture(geometry_check)

    report["config_valid"] = report["task_valid"] and not errors
    return report
