"""Local structured memory with evidence-backed knowledge and procedures."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from cfd_memo_agent.validator.foam import read_json

from .vector import VECTOR_VERSION, cosine, experience_text, reynolds_similarity, task_text

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(prefix: str, value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return f"{prefix}-{sha256(encoded).hexdigest()[:16]}"


def _write_json(path: Path, value: Any, *, replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if not replace:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
        return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@lru_cache(maxsize=2)
def _validator(name: str) -> Draft202012Validator:
    schema = read_json(SCHEMAS / name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


class MemoryManager:
    """Own working, episodic, knowledge, and procedural memory boundaries."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.episodes_dir = self.root / "episodes"
        self.knowledge_dir = self.root / "knowledge"
        self.procedures_dir = self.root / "procedures"
        for directory in (self.episodes_dir, self.knowledge_dir, self.procedures_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.working: dict[str, Any] = {
            "task_id": None, "retrieved_experience_ids": [], "retrievals": [], "uses": [],
        }

    def start_task(self, task: dict[str, Any]) -> None:
        self.working["task_id"] = task["task_id"]

    @staticmethod
    def _trust(record: dict[str, Any]) -> dict[str, Any]:
        return record.get("trust", {
            "positive_outcomes": len(record["evidence"]), "negative_outcomes": 0,
            "confidence": 1.0, "evaluated_episode_ids": [],
        })

    def _remember_retrieval(self, *, stage: str, query: str,
                            selected: list[dict[str, Any]]) -> None:
        ids = self.working["retrieved_experience_ids"]
        for item in selected:
            if item["experience_id"] not in ids:
                ids.append(item["experience_id"])
        self.working["retrievals"].append({
            "stage": stage, "query": query[:500], "vector_version": VECTOR_VERSION,
            "matches": [{
                "experience_id": item["experience_id"],
                "vector_score": item["retrieval_score"]["vector"],
                "reynolds_score": item["retrieval_score"]["reynolds"],
                "confidence": item["retrieval_score"]["confidence"],
                "final_score": item["retrieval_score"]["final"],
            } for item in selected],
        })

    def retrieve_for_planning(self, description: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Rank verified memories from natural language before a task exists."""
        if not isinstance(description, str) or not description.strip():
            return []
        matches: list[dict[str, Any]] = []
        for path in sorted(self.knowledge_dir.glob("*.json")):
            record = read_json(path)
            errors = list(_validator("experience.schema.json").iter_errors(record))
            if errors:
                raise ValueError(f"损坏的经验记录：{path}: {errors[0].message}")
            if record["status"] != "verified":
                continue
            vector_score = cosine(description, experience_text(record))
            if vector_score <= 0:
                continue
            copy = deepcopy(record)
            confidence = float(self._trust(record)["confidence"])
            copy["retrieval_score"] = {
                "vector": vector_score, "reynolds": 0.0, "confidence": confidence,
                "final": 0.8 * vector_score + 0.2 * confidence,
            }
            matches.append(copy)
        matches.sort(key=lambda item: (-item["retrieval_score"]["final"], item["experience_id"]))
        selected = matches[:limit]
        self._remember_retrieval(stage="planner", query=description, selected=selected)
        return selected

    def retrieve(self, task: dict[str, Any], *, problem_codes: list[str] | None = None,
                 query_text: str = "", stage: str = "reviewer",
                 limit: int = 5) -> list[dict[str, Any]]:
        """Hard-filter applicability, then rank verified memories with local vectors."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("memory retrieval limit 必须为正整数")
        wanted = set(problem_codes or [])
        matches: list[dict[str, Any]] = []
        for path in sorted(self.knowledge_dir.glob("*.json")):
            record = read_json(path)
            errors = list(_validator("experience.schema.json").iter_errors(record))
            if errors:
                raise ValueError(f"损坏的经验记录：{path}: {errors[0].message}")
            applicability = record["applicability"]
            if record["status"] != "verified":
                continue
            if any((
                applicability["case_type"] != task["case_type"],
                applicability["solver"] != task["solver"],
                applicability["flow_model"] != task["physics"]["flow_model"],
                applicability["dimension"] != task["geometry"]["dimension"],
            )):
                continue
            overlap = len(wanted.intersection(record["problem_codes"]))
            if wanted and not overlap:
                continue
            copy = deepcopy(record)
            vector_score = cosine(task_text(task, query_text), experience_text(record))
            re_score = reynolds_similarity(
                applicability.get("source_reynolds_number"),
                task["physics"].get("reynolds_number"),
            )
            confidence = float(self._trust(record)["confidence"])
            code_score = 1.0 if overlap else 0.0
            final = 0.45 * vector_score + 0.2 * re_score + 0.25 * code_score + 0.1 * confidence
            copy["retrieval_score"] = {
                "vector": vector_score, "reynolds": re_score,
                "confidence": confidence, "final": final,
            }
            matches.append(copy)
        matches.sort(key=lambda item: (-item["retrieval_score"]["final"], item["experience_id"]))
        selected = matches[:limit]
        self._remember_retrieval(
            stage=stage, query=query_text or " ".join(sorted(wanted)), selected=selected,
        )
        return selected

    @staticmethod
    def context(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip storage metadata before an experience enters an Agent prompt."""
        return [{
            "experience_id": item["experience_id"],
            "problem_codes": item["problem_codes"],
            "applicability": item["applicability"],
            "action": item["action"],
            "verification_scope": item["verification_scope"],
            "retrieval_score": item.get("retrieval_score"),
        } for item in records]

    def note_use(self, experience_ids: list[str], *, agent: str, round_index: int,
                 decision: str, effect: str = "supported_evidence_based_repair") -> None:
        for experience_id in experience_ids:
            use = {"experience_id": experience_id, "agent": agent,
                   "round_index": round_index, "decision": decision,
                   "effect": effect}
            if use not in self.working["uses"]:
                self.working["uses"].append(use)

    def learn_from_episode(self, episode: dict[str, Any]) -> dict[str, list[str]]:
        """Extract compact repair knowledge; never store full file contents as knowledge."""
        task = episode.get("task")
        if not isinstance(task, dict):
            return {"experience_ids": [], "procedure_ids": []}
        experience_ids: list[str] = []
        procedure_ids: list[str] = []
        rounds = {item["index"]: item for item in episode["rounds"]}
        for correction in episode["corrections"]:
            files = sorted(change["file"] for change in correction["changes"])
            codes = sorted(set(correction["reason_codes"]))
            identity = {"codes": codes, "files": files, "case_type": task["case_type"],
                        "solver": task["solver"],
                        "flow_model": task["physics"]["flow_model"],
                        "dimension": task["geometry"]["dimension"]}
            experience_id = _digest("exp", identity)
            target = rounds.get(correction["to_round"])
            verified = bool(target and target["config_valid"])
            evidence = {"episode_id": episode["episode_id"],
                        "from_round": correction["from_round"],
                        "to_round": correction["to_round"],
                        "outcome_status": target["status"] if target else "missing"}
            path = self.knowledge_dir / f"{experience_id}.json"
            timestamp = _now()
            if path.exists():
                record = read_json(path)
                _validator("experience.schema.json").validate(record)
                if evidence not in record["evidence"]:
                    record["evidence"].append(evidence)
                if verified:
                    record["status"] = "verified"
                record["updated_at"] = timestamp
            else:
                record = {
                    "schema_version": 1, "experience_id": experience_id,
                    "record_type": "repair_knowledge",
                    "status": "verified" if verified else "candidate",
                    "problem_codes": codes,
                    "applicability": {"case_type": task["case_type"],
                        "solver": task["solver"],
                        "flow_model": task["physics"]["flow_model"],
                        "dimension": task["geometry"]["dimension"],
                        "source_reynolds_number": task["physics"]["reynolds_number"]},
                    "action": {"type": "restore_from_trusted_reference", "files": files},
                    "evidence": [evidence], "verification_scope": "configuration",
                    "trust": {"positive_outcomes": 1 if verified else 0,
                              "negative_outcomes": 0,
                              "confidence": 2 / 3 if verified else 0.5,
                              "evaluated_episode_ids": [episode["episode_id"]] if verified else []},
                    "created_at": timestamp, "updated_at": timestamp,
                }
            _validator("experience.schema.json").validate(record)
            _write_json(path, record, replace=path.exists())
            experience_ids.append(experience_id)
            if verified:
                procedure_ids.append(self._save_procedure(files, experience_id))
        return {"experience_ids": list(dict.fromkeys(experience_ids)),
                "procedure_ids": list(dict.fromkeys(procedure_ids))}

    def record_use_outcomes(self, episode: dict[str, Any]) -> None:
        """Downweight reused knowledge when the task does not end with valid configuration."""
        success = episode["status"] in {"simulated_success", "completed"} and bool(
            episode["metrics"].get("config_valid")
        )
        for experience_id in dict.fromkeys(
            item["experience_id"] for item in self.working["uses"]
        ):
            path = self.knowledge_dir / f"{experience_id}.json"
            if not path.exists():
                continue
            record = read_json(path)
            _validator("experience.schema.json").validate(record)
            trust = deepcopy(self._trust(record))
            if episode["episode_id"] in trust["evaluated_episode_ids"]:
                continue
            key = "positive_outcomes" if success else "negative_outcomes"
            trust[key] += 1
            trust["evaluated_episode_ids"].append(episode["episode_id"])
            trust["confidence"] = (
                trust["positive_outcomes"] + 1
            ) / (trust["positive_outcomes"] + trust["negative_outcomes"] + 2)
            record["trust"] = trust
            if trust["negative_outcomes"] >= 2 and trust["confidence"] < 0.5:
                record["status"] = "candidate"
            record["updated_at"] = _now()
            _validator("experience.schema.json").validate(record)
            _write_json(path, record, replace=True)

    def _save_procedure(self, files: list[str], experience_id: str) -> str:
        procedure_id = _digest("proc", {"action": "restore", "files": files})
        path = self.procedures_dir / f"{procedure_id}.json"
        ids = [experience_id]
        if path.exists():
            previous = read_json(path)
            _validator("procedure.schema.json").validate(previous)
            ids = list(dict.fromkeys([*previous["experience_ids"], experience_id]))
        record = {"schema_version": 1, "procedure_id": procedure_id,
                  "record_type": "verified_procedure", "status": "verified",
                  "action_type": "restore_from_trusted_reference", "files": files,
                  "steps": ["在新的隔离轮次中复制上一轮 case。",
                            "仅从可信 reference 恢复允许的目标文件。",
                            "重新执行确定性配置验证。",
                            "验证通过后才允许再次执行 runner。"],
                  "experience_ids": ids, "updated_at": _now()}
        _validator("procedure.schema.json").validate(record)
        _write_json(path, record, replace=path.exists())
        return procedure_id

    def archive_episode(self, episode: dict[str, Any]) -> Path:
        path = self.episodes_dir / f"{episode['episode_id']}.json"
        _write_json(path, episode)
        return path


__all__ = ["MemoryManager"]
