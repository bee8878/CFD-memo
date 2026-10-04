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
from .trust import (
    TRUST_LEVELS, derive_level, file_evidence, strongest_level, verify_result_index,
)

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"
_SCOPE_BY_LEVEL = {
    "candidate": "candidate", "config_verified": "configuration",
    "run_verified": "run", "physics_verified": "physics",
}


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

    @staticmethod
    def _migrate_legacy(record: dict[str, Any]) -> dict[str, Any]:
        """Make v1 records readable without pretending their unhashed evidence is current."""
        if record.get("schema_version") != 1:
            return record
        migrated = deepcopy(record)
        migrated["schema_version"] = 2
        migrated["status"] = "candidate"
        migrated["verification_scope"] = "candidate"
        migrated["evidence"] = [{
            **item, "source_type": "legacy_episode", "runner_mode": "unknown",
            "derived_level": "candidate", "integrity_verified": False, "artifacts": [],
        } for item in migrated.get("evidence", [])]
        migrated["user_control"] = {
            "state": "active", "approved": False,
            "note": "旧版记录缺少文件哈希，需从原始 episode 重新提炼。",
            "updated_at": migrated.get("updated_at", _now()),
        }
        return migrated

    def _load_experience(self, path: Path) -> dict[str, Any]:
        record = self._migrate_legacy(read_json(path))
        errors = list(_validator("experience.schema.json").iter_errors(record))
        if errors:
            raise ValueError(f"损坏的经验记录：{path}: {errors[0].message}")
        return record

    @staticmethod
    def _retrievable(record: dict[str, Any]) -> bool:
        return (
            record["status"] != "candidate"
            and record["user_control"]["state"] == "active"
        )

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
            record = self._load_experience(path)
            if not self._retrievable(record):
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
            record = self._load_experience(path)
            applicability = record["applicability"]
            if not self._retrievable(record):
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
            "trust_level": item["status"],
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

    @staticmethod
    def _evidence_from_round(episode: dict[str, Any], correction: dict[str, Any],
                             target: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
        artifacts: list[dict[str, Any]] = []
        config_valid = False
        result_index = None
        if target is not None:
            validation_path = target.get("validation_path")
            execution_path = target.get("execution_path")
            round_root = Path(target.get("run_path", "")).resolve()
            attempt = Path(execution_path).resolve().parent if execution_path else None
            trusted_attempt = bool(
                attempt and round_root.is_dir()
                and attempt.parent == round_root / "attempts"
                and attempt.name.startswith("attempt-")
                and Path(validation_path or "").resolve().parent == attempt
            )
            if trusted_attempt and validation_path and Path(validation_path).is_file():
                validation = read_json(Path(validation_path))
                config_valid = bool(target.get("config_valid") and validation.get("config_valid"))
                artifacts.append(file_evidence(validation_path, role="validation"))
            if trusted_attempt and execution_path and Path(execution_path).is_file():
                artifacts.append(file_evidence(execution_path, role="execution"))
                index_path = Path(execution_path).parent / "result-index.json"
                if index_path.is_file():
                    try:
                        result_index = verify_result_index(index_path)
                    except ValueError:
                        result_index = None
                    else:
                        artifacts.append(file_evidence(index_path, role="result_index"))
        level = derive_level(
            runner_mode=episode.get("runner_mode", "unknown"),
            config_valid=config_valid,
            outcome_status=target.get("status", "missing") if target else "missing",
            result_index=result_index,
            physical_validated=bool(
                target and (target.get("result_evidence") or {}).get("physical_validated")
            ),
        )
        evidence = {
            "source_type": "episode_attempt", "episode_id": episode["episode_id"],
            "from_round": correction["from_round"], "to_round": correction["to_round"],
            "outcome_status": target.get("status", "missing") if target else "missing",
            "runner_mode": episode.get("runner_mode", "unknown"),
            "derived_level": level, "integrity_verified": bool(artifacts),
            "artifacts": artifacts,
        }
        return evidence, level

    def learn_from_episode(self, episode: dict[str, Any]) -> dict[str, list[str]]:
        """Extract repair knowledge from deterministic round and attempt evidence."""
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
            evidence, level = self._evidence_from_round(episode, correction, target)
            path = self.knowledge_dir / f"{experience_id}.json"
            timestamp = _now()
            if path.exists():
                record = self._load_experience(path)
                if evidence not in record["evidence"]:
                    record["evidence"].append(evidence)
                record["status"] = strongest_level(record["status"], level)
                record["verification_scope"] = _SCOPE_BY_LEVEL[record["status"]]
                record["updated_at"] = timestamp
            else:
                record = {
                    "schema_version": 2, "experience_id": experience_id,
                    "record_type": "repair_knowledge",
                    "status": level,
                    "problem_codes": codes,
                    "applicability": {"case_type": task["case_type"],
                        "solver": task["solver"],
                        "flow_model": task["physics"]["flow_model"],
                        "dimension": task["geometry"]["dimension"],
                        "source_reynolds_number": task["physics"]["reynolds_number"]},
                    "action": {"type": "restore_from_trusted_reference", "files": files},
                    "evidence": [evidence], "verification_scope": _SCOPE_BY_LEVEL[level],
                    "trust": {"positive_outcomes": 1 if level != "candidate" else 0,
                              "negative_outcomes": 0,
                              "confidence": 2 / 3 if level != "candidate" else 0.5,
                              "evaluated_episode_ids": (
                                  [episode["episode_id"]] if level != "candidate" else [])},
                    "user_control": {"state": "active", "approved": False,
                                     "note": None, "updated_at": timestamp},
                    "created_at": timestamp, "updated_at": timestamp,
                }
            _validator("experience.schema.json").validate(record)
            _write_json(path, record, replace=path.exists())
            experience_ids.append(experience_id)
            if level != "candidate":
                procedure_ids.append(self._save_procedure(files, experience_id))
        return {"experience_ids": list(dict.fromkeys(experience_ids)),
                "procedure_ids": list(dict.fromkeys(procedure_ids))}

    def extract_episode_file(self, path: Path | str) -> dict[str, Any]:
        source = Path(path).resolve()
        episode = read_json(source)
        if (not isinstance(episode.get("episode_id"), str)
                or episode.get("runner_mode") not in {"simulated", "real"}
                or not isinstance(episode.get("task"), dict)
                or not isinstance(episode.get("rounds"), list)
                or not isinstance(episode.get("corrections"), list)):
            raise ValueError("episode 缺少提炼经验所需的稳定字段")
        for correction in episode["corrections"]:
            if (not isinstance(correction, dict)
                    or not isinstance(correction.get("from_round"), int)
                    or not isinstance(correction.get("to_round"), int)
                    or not isinstance(correction.get("reason_codes"), list)
                    or not isinstance(correction.get("changes"), list)):
                raise ValueError("episode correction 结构无效")
        learned = self.learn_from_episode(episode)
        return {"episode_path": str(source), **learned,
                "records": [self.get_experience(item) for item in learned["experience_ids"]]}

    def get_experience(self, experience_id: str) -> dict[str, Any]:
        path = self.knowledge_dir / f"{experience_id}.json"
        if not path.is_file():
            raise ValueError(f"经验不存在：{experience_id}")
        return self._load_experience(path)

    def list_experiences(self) -> list[dict[str, Any]]:
        return [self._load_experience(path)
                for path in sorted(self.knowledge_dir.glob("*.json"))]

    def set_user_control(self, experience_id: str, *, state: str | None = None,
                         approved: bool | None = None, note: str | None = None) -> dict[str, Any]:
        if state not in {None, "active", "disabled"}:
            raise ValueError("经验状态必须为 active 或 disabled")
        path = self.knowledge_dir / f"{experience_id}.json"
        record = self.get_experience(experience_id)
        if state is not None:
            record["user_control"]["state"] = state
        if approved is not None:
            record["user_control"]["approved"] = approved
        if note is not None:
            record["user_control"]["note"] = note
        timestamp = _now()
        record["user_control"]["updated_at"] = timestamp
        record["updated_at"] = timestamp
        _validator("experience.schema.json").validate(record)
        _write_json(path, record, replace=True)
        return record

    def delete_experience(self, experience_id: str) -> None:
        path = self.knowledge_dir / f"{experience_id}.json"
        if not path.is_file():
            raise ValueError(f"经验不存在：{experience_id}")
        path.unlink()

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
            record = self._load_experience(path)
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
                record["verification_scope"] = "candidate"
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
