"""Local episode records; cross-task retrieval is not implemented yet."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from cfd_memo_agent.validator.foam import read_json

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas/episode.schema.json"


def write_record(path: Path, value) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(rendered + "\n")


@lru_cache(maxsize=1)
def episode_validator():
    schema = read_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def save_episode(path: Path, episode: dict) -> None:
    episode_validator().validate(episode)
    write_record(path, episode)
