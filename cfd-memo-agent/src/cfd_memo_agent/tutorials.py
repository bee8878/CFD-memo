"""Read-only indexing and retrieval for local OpenFOAM tutorials."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Iterable

from cfd_memo_agent.validator.foam import parse_foam, parse_json

INDEX_SCHEMA_VERSION = 1
DEFAULT_DISTRIBUTION = "Ubuntu-22.04"
DEFAULT_OPENFOAM_VERSION = "10"
DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[2] / "cases/tutorial-index/openfoam10.json"
MAX_DICTIONARY_BYTES = 2_000_000
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+\Z")
_COMMENTS = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*")
_WORD = re.compile(r"[\w.-]+", re.UNICODE)
_CASE_ALIASES = {
    "cavity": ("lid-driven", "方腔", "顶盖驱动"),
    "pitzdaily": ("backward-facing-step", "后台阶"),
    "cylinder": ("cylinder-flow", "圆柱绕流"),
    "channel": ("channel-flow", "通道流"),
}
_PRIMARY_FLOW_SOLVERS = {"icoFoam", "pimpleFoam", "pisoFoam", "simpleFoam"}


def wsl_tutorial_root(
    distribution: str = DEFAULT_DISTRIBUTION,
    version: str = DEFAULT_OPENFOAM_VERSION,
) -> Path:
    """Return the Windows UNC path for one explicitly named WSL installation."""
    if not _SAFE_NAME.fullmatch(distribution) or not _SAFE_NAME.fullmatch(version):
        raise ValueError("WSL distribution 和 OpenFOAM version 只能包含安全名称字符")
    return Path(f"//wsl.localhost/{distribution}/opt/openfoam{version}/tutorials")


def _read_text(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_DICTIONARY_BYTES:
        raise ValueError(f"文件超过索引读取上限：{size} bytes")
    return path.read_text(encoding="utf-8", errors="strict")


def _foam_header(text: str) -> tuple[str | None, str | None]:
    clean = _COMMENTS.sub("", text)
    match = re.search(r"\bFoamFile\s*\{([\s\S]*?)\}", clean)
    if not match:
        return None, None
    body = match.group(1)
    cls = re.search(r"\bclass\s+([^;\s]+)\s*;", body)
    obj = re.search(r"\bobject\s+([^;\s]+)\s*;", body)
    return (cls.group(1) if cls else None, obj.group(1) if obj else None)


def _application(control_text: str) -> str:
    clean = _COMMENTS.sub("", control_text)
    match = re.search(r"\bapplication\s+([^;\s]+)\s*;", clean)
    if not match:
        raise ValueError("controlDict 缺少 application")
    return match.group(1)


def _field_metadata(path: Path) -> tuple[str | None, dict[str, str], str | None]:
    text = _read_text(path)
    cls, obj = _foam_header(text)
    if not cls or not cls.startswith("vol"):
        return None, {}, None
    warning = None
    boundaries: dict[str, str] = {}
    try:
        document = parse_foam(text)
        boundary = document.get("boundaryField", {})
        if isinstance(boundary, dict):
            for name, body in boundary.items():
                if isinstance(body, dict) and body.get("type"):
                    value = body["type"]
                    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
                        boundaries[name] = value[0]
    except ValueError as exc:
        warning = f"{path.name} 边界未完全解析：{exc}"
    return obj or path.name, boundaries, warning


def _initial_directories(case: Path) -> list[Path]:
    candidates = []
    for name in ("0", "0.orig"):
        path = case / name
        if path.is_dir():
            candidates.append(path)
    return candidates


def _physics_model(relative: str, solver: str) -> str:
    parts = {part.lower() for part in relative.split("/")}
    if "ras" in parts:
        return "incompressible_rans"
    if "les" in parts:
        return "incompressible_les"
    if "laminar" in parts or solver == "icoFoam":
        return "incompressible_laminar"
    return "unknown"


def _aliases(relative: str) -> list[str]:
    lowered = relative.lower()
    aliases = []
    for marker, values in _CASE_ALIASES.items():
        if marker in lowered:
            aliases.extend(values)
    return aliases


def _ranking_evidence(case: dict[str, Any], query_text: str) -> tuple[int, list[str]]:
    """Prefer an exact benchmark and a complete flow solver without hiding the heuristic."""
    score = 0
    reasons = []
    leaf = case["tutorial_id"].rsplit("/", 1)[-1].lower()
    for marker, aliases in _CASE_ALIASES.items():
        if leaf == marker and any(alias.lower() in query_text for alias in aliases):
            score += 3
            reasons.append(f"exact-benchmark:{marker}")
    if any(word in query_text for word in ("流", "flow", "cfd")):
        if case["solver"] in _PRIMARY_FLOW_SOLVERS:
            score += 4
            reasons.append(f"flow-solver:{case['solver']}")
        if {"U", "p"} <= set(case["fields"]):
            score += 2
            reasons.append("flow-fields:U,p")
        if case["physics_model"] != "unknown":
            score += 1
            reasons.append(f"known-physics:{case['physics_model']}")
    if case["mesh_tools"]:
        score += 2
        reasons.append("mesh-source:" + ",".join(case["mesh_tools"]))
    return score, reasons


def _mesh_tools(case: Path) -> list[str]:
    tools = []
    for filename, tool in (
        ("blockMeshDict", "blockMesh"),
        ("snappyHexMeshDict", "snappyHexMesh"),
        ("topoSetDict", "topoSet"),
        ("createPatchDict", "createPatch"),
    ):
        if (case / "system" / filename).is_file():
            tools.append(tool)
    if (case / "constant/polyMesh/boundary").is_file():
        tools.append("existing-polyMesh")
    return tools


def _case_entry(case: Path, root: Path) -> dict[str, Any]:
    relative = case.relative_to(root).as_posix()
    warnings = []
    solver = _application(_read_text(case / "system/controlDict"))
    fields = set()
    boundaries: dict[str, set[str]] = {}
    for initial in _initial_directories(case):
        for path in sorted(initial.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file():
                continue
            try:
                field, patch_types, warning = _field_metadata(path)
            except (OSError, UnicodeError, ValueError) as exc:
                warnings.append(f"{path.relative_to(case).as_posix()} 无法读取：{exc}")
                continue
            if field:
                fields.add(field)
            for name, boundary_type in patch_types.items():
                boundaries.setdefault(name, set()).add(boundary_type)
            if warning:
                warnings.append(f"{path.relative_to(case).as_posix()}：{warning}")
    config_files = []
    for folder in ("system", "constant"):
        directory = case / folder
        if directory.is_dir():
            config_files.extend(
                path.relative_to(case).as_posix()
                for path in sorted(directory.iterdir(), key=lambda item: item.name.lower())
                if path.is_file()
            )
    return {
        "tutorial_id": relative,
        "case_path": str(case),
        "solver": solver,
        "physics_model": _physics_model(relative, solver),
        "fields": sorted(fields),
        "boundaries": {name: sorted(types) for name, types in sorted(boundaries.items())},
        "boundary_types": sorted({value for values in boundaries.values() for value in values}),
        "mesh_tools": _mesh_tools(case),
        "config_files": config_files,
        "aliases": _aliases(relative),
        "warnings": warnings,
    }


def build_tutorial_index(root: Path | str, *, version: str = DEFAULT_OPENFOAM_VERSION) -> dict:
    """Scan dictionary files only; never read or execute tutorial scripts."""
    source = Path(root).resolve()
    if not source.is_dir():
        raise ValueError(f"OpenFOAM tutorials 目录不存在：{source}")
    entries = []
    skipped = []
    controls = sorted(source.glob("**/system/controlDict"),
                      key=lambda path: path.as_posix().lower())
    for control in controls:
        case = control.parent.parent
        try:
            entries.append(_case_entry(case, source))
        except (OSError, UnicodeError, ValueError) as exc:
            skipped.append({
                "tutorial_id": case.relative_to(source).as_posix(),
                "reason": str(exc),
            })
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "source": "OpenFOAM Foundation tutorials",
        "openfoam_version": str(version),
        "tutorial_root": str(source),
        "case_count": len(entries),
        "skipped_count": len(skipped),
        "cases": entries,
        "skipped": skipped,
        "safety": {
            "read_only": True,
            "scripts_executed": False,
            "files_scanned": ["system/controlDict", "0/*", "0.orig/*",
                              "system/*", "constant/*"],
        },
    }


def save_tutorial_index(index: dict, path: Path | str, *, overwrite: bool = False) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(index, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if overwrite:
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(target)
    else:
        with target.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
    return target


def load_tutorial_index(path: Path | str) -> dict:
    index = parse_json(Path(path).read_text(encoding="utf-8-sig"))
    if index.get("schema_version") != INDEX_SCHEMA_VERSION or not isinstance(index.get("cases"), list):
        raise ValueError("教程索引版本或结构不受支持")
    return index


def _terms(text: str | None) -> list[str]:
    return [value.lower() for value in _WORD.findall(text or "") if value.strip(".-")]


def search_tutorials(
    index: dict,
    *,
    query: str | None = None,
    solver: str | None = None,
    physics_model: str | None = None,
    fields: Iterable[str] = (),
    boundary_types: Iterable[str] = (),
    mesh_tool: str | None = None,
    limit: int = 10,
) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
        raise ValueError("limit 必须是 1 到 100 的整数")
    required_fields = set(fields)
    required_boundaries = set(boundary_types)
    terms = _terms(query)
    query_text = (query or "").lower()
    matches = []
    for case in index["cases"]:
        if solver and case["solver"] != solver:
            continue
        if physics_model and case["physics_model"] != physics_model:
            continue
        if not required_fields <= set(case["fields"]):
            continue
        if not required_boundaries <= set(case["boundary_types"]):
            continue
        if mesh_tool and mesh_tool not in case["mesh_tools"]:
            continue
        haystack = " ".join([
            case["tutorial_id"], case["solver"], case["physics_model"],
            *case["fields"], *case["boundary_types"], *case["mesh_tools"], *case["aliases"],
        ]).lower()
        matched = [term for term in terms if term in haystack]
        alias_matches = [alias for alias in case["aliases"] if alias.lower() in query_text]
        if terms and not matched and not alias_matches:
            continue
        score = (len(matched) + len(alias_matches)) * 10
        if query and query.lower() in haystack:
            score += 5
        ranking_score, ranking_reasons = _ranking_evidence(case, query_text)
        score += ranking_score
        result = dict(case)
        result["score"] = score
        result["match_reasons"] = [f"query:{term}" for term in matched]
        result["match_reasons"].extend(f"alias:{alias}" for alias in alias_matches)
        result["match_reasons"].extend(ranking_reasons)
        if solver:
            result["match_reasons"].append(f"solver:{solver}")
        if physics_model:
            result["match_reasons"].append(f"physics:{physics_model}")
        matches.append(result)
    matches.sort(key=lambda item: (-item["score"], item["tutorial_id"].lower()))
    return {
        "query": query,
        "filters": {
            "solver": solver, "physics_model": physics_model,
            "fields": sorted(required_fields),
            "boundary_types": sorted(required_boundaries), "mesh_tool": mesh_tool,
        },
        "total_matches": len(matches),
        "results": matches[:limit],
    }


def _candidate(item: dict[str, Any]) -> dict[str, Any]:
    """Keep only traceable configuration evidence suitable for a model prompt."""
    return {
        key: item[key] for key in (
            "tutorial_id", "case_path", "solver", "physics_model", "fields",
            "boundaries", "boundary_types", "mesh_tools", "config_files",
            "warnings", "score", "match_reasons",
        )
    }


class TutorialRetriever:
    """Load one frozen local index and return a compact, serializable context."""

    def __init__(self, index_path: Path | str = DEFAULT_INDEX_PATH):
        self.index_path = Path(index_path).resolve()

    def retrieve(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        context = {
            "status": "unavailable", "query": query,
            "index_path": str(self.index_path), "candidate_count": 0,
            "candidates": [], "error": None,
        }
        if not isinstance(query, str) or not query.strip():
            context["status"] = "no_match"
            return context
        try:
            index = load_tutorial_index(self.index_path)
            result = search_tutorials(index, query=query, limit=limit)
        except (OSError, UnicodeError, ValueError) as exc:
            context["error"] = {"type": type(exc).__name__, "message": str(exc)}
            return context
        context["candidate_count"] = result["total_matches"]
        context["candidates"] = [_candidate(item) for item in result["results"]]
        context["status"] = "ready" if context["candidates"] else "no_match"
        return context


def build_case_spec_proposal(description: str, tutorial_context: dict[str, Any]) -> dict[str, Any]:
    """Create a non-executable proposal from the highest-ranked official tutorial."""
    candidates = tutorial_context.get("candidates", [])
    if not candidates:
        raise ValueError("没有教程候选，无法建立 CaseSpec 提案")
    primary = candidates[0]
    return {
        "schema_version": 1,
        "proposal_status": "reference_only",
        "requested_description": description.strip(),
        "source_type": "tutorial_reference",
        "case_type": "unregistered",
        "solver": primary["solver"],
        "physics_model": primary["physics_model"],
        "dimension": "unknown",
        "mesh_tools": list(primary["mesh_tools"]),
        "fields": list(primary["fields"]),
        "boundaries": [
            {"name": name, "types": list(types)}
            for name, types in sorted(primary["boundaries"].items())
        ],
        "tutorial_ids": [primary["tutorial_id"]],
        "executable": False,
        "limitations": [
            "尚未注册生成适配器，不能生成或运行 OpenFOAM case。",
            "教程相似不代表用户物理参数、几何和边界条件已经确认。",
            "后续生成必须重新经过 task schema、validator、网格及 runner 检查。",
        ],
    }


__all__ = [
    "DEFAULT_DISTRIBUTION", "DEFAULT_INDEX_PATH", "DEFAULT_OPENFOAM_VERSION",
    "INDEX_SCHEMA_VERSION", "TutorialRetriever", "build_case_spec_proposal",
    "build_tutorial_index", "load_tutorial_index", "save_tutorial_index",
    "search_tutorials", "wsl_tutorial_root",
]
