"""Read the limited ASCII dictionary syntax used by our bundled case."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


@dataclass
class Group:
    opening: str
    items: list


TOKEN = re.compile(
    r'\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"'
    r'|[A-Za-z_]\w*\([^()\s]*\)|[{}()\[\];]|[^\s{}()\[\];"#]+'
)


def parse_foam(text: str) -> dict:
    tokens = []
    position = 0
    while position < len(text):
        match = TOKEN.match(text, position)
        if match is None:
            raise ValueError("不支持的指令、引号或字符")
        token = match.group()
        position = match.end()
        if token.startswith("/*") and not token.endswith("*/"):
            raise ValueError("未结束的块注释")
        if token.isspace() or token.startswith("//") or token.startswith("/*"):
            continue
        if "$" in token:
            raise ValueError("不支持变量展开")
        tokens.append(token[1:-1] if token.startswith('"') else token)
    index = 0

    def take():
        nonlocal index
        if index >= len(tokens):
            raise ValueError("配置意外结束，可能缺少括号或分号")
        value = tokens[index]
        index += 1
        return value

    def item():
        token = take()
        if token == "{":
            return dictionary("}")
        if token in ("(", "["):
            closing = ")" if token == "(" else "]"
            values = []
            while index < len(tokens) and tokens[index] != closing:
                values.append(item())
            if take() != closing:
                raise ValueError("括号不匹配")
            return Group(token, values)
        if token in ("}", ")", "]", ";"):
            raise ValueError("意外的括号或分号")
        return token

    def dictionary(closing=None):
        result = {}
        while index < len(tokens) and tokens[index] != closing:
            key = take()
            if key in "{}()[];":
                raise ValueError("配置项缺少名称")
            if key in result:
                raise ValueError(f"重复配置项：{key}")
            if index < len(tokens) and tokens[index] == "{":
                take()
                result[key] = dictionary("}")
                if index < len(tokens) and tokens[index] == ";":
                    take()
            else:
                values = []
                while index < len(tokens) and tokens[index] != ";":
                    values.append(item())
                if not values or take() != ";":
                    raise ValueError(f"配置项 {key} 缺少值或分号")
                result[key] = values
        if closing is not None and take() != closing:
            raise ValueError("字典括号不匹配")
        return result

    return dictionary()


def parse_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"JSON 重复字段：{key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"JSON 不允许非有限数字：{value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def read_json(path: Path):
    return parse_json(path.read_text(encoding="utf-8-sig"))
